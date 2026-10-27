"""Rate-limited, robots-aware collection and persistent execution records."""
import base64, hashlib, ipaddress, json, os, re, smtplib, socket, threading, time
from http.cookiejar import CookieJar
from pathlib import Path
from datetime import datetime, timedelta
from email.message import EmailMessage
from urllib import request, parse, error, robotparser
from .store import DATA, TZ, now
from .documents import document_html, image_html
from .market_quotes import select_price, official_close_quotes
from .source_scan import scan
from .discovery import completion_status
from .parsers import Document, parse_gold, parse_htqh, classify, calculate, number


def validate_url(url, resolve=False):
    p=parse.urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:
        raise ValueError('网址必须是公开的 HTTP(S) 地址，不能含账号密码')
    if p.port not in (None,80,443,4433): raise ValueError('不支持此网址端口')
    if p.hostname.lower() in ('localhost',) or p.hostname.endswith(('.local','.localhost')):
        raise ValueError('不允许访问本地或内网地址')
    try:
        addr=ipaddress.ip_address(p.hostname)
        if not addr.is_global: raise ValueError('不允许访问本地或内网地址')
    except ValueError as exc:
        if '不允许' in str(exc): raise
    if resolve:
        for info in socket.getaddrinfo(p.hostname,p.port or (443 if p.scheme=='https' else 80)):
            if not ipaddress.ip_address(info[4][0]).is_global: raise ValueError('域名解析到非公网地址')
    return url


class SafeRedirect(request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        validate_url(newurl,True)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


class Fetcher:
    def __init__(self,ua):
        self.ua=ua; self.last=0.; self.robots={}; self.browser=None; self.rendered=False
        self.opener=request.build_opener(SafeRedirect(),request.HTTPCookieProcessor(CookieJar()))

    def raw(self,url):
        validate_url(url,True)
        url=parse.quote(url,safe=":/?&=%#@+;,~!$'()*[]")
        time.sleep(max(0,1-(time.monotonic()-self.last)))
        self.last=time.monotonic()
        req=request.Request(url,headers={'User-Agent':self.ua,'Accept':'text/html,application/pdf;q=0.8,*/*;q=0.2'})
        with self.opener.open(req,timeout=15) as r:
            raw=r.read(15_000_001)
            if len(raw)>15_000_000: raise ValueError('文件超过15MB，转人工核验')
            self.last_payload=raw
            charset=r.headers.get_content_charset()
            if not charset:
                m=re.search(br'charset\s*=\s*["\x27]?([a-zA-Z0-9_-]+)',raw[:8000]); charset=m[1].decode() if m else 'utf-8'
            try: text=raw.decode(charset)
            except (UnicodeError,LookupError): text=raw.decode('gb18030',errors='replace')
            return text,r.headers.get('Content-Type',''),r.status,r.url

    def get_http(self,url):
        p=parse.urlsplit(url); origin=f'{p.scheme}://{p.netloc}'
        if origin not in self.robots:
            rp=robotparser.RobotFileParser()
            try: txt,*_=self.raw(origin+'/robots.txt'); rp.parse(txt.splitlines())
            except error.HTTPError as e:
                if e.code==404: rp.parse([])
                else: raise ValueError(f'robots.txt 返回 {e.code}，保守暂停采集') from e
            except Exception as e: raise ValueError(f'无法核查 robots.txt：{e}') from e
            self.robots[origin]=rp
        if not self.robots[origin].can_fetch(self.ua,url): raise ValueError('robots.txt 不允许采集此路径')
        delay=self.robots[origin].crawl_delay(self.ua) or self.robots[origin].crawl_delay('*') or 1
        time.sleep(max(0,delay-(time.monotonic()-self.last)))
        return self.raw(url)

    def render(self,url):
        from .browser_fetch import BrowserFetcher
        if self.browser is None:self.browser=BrowserFetcher(self.ua)
        r=self.browser.get(url);self.rendered=True
        self.last_payload=base64.b64decode(r['binary_b64']) if r.get('binary_b64') else r['html'].encode('utf-8')
        return r.get('html',''),r['content_type'],r['http_status'],r['final_url']

    def get(self,url):
        self.rendered=False
        last=None
        for attempt in range(3):
            try:return self.get_http(url)
            except Exception as exc:
                last=exc; message=str(exc)
                if 'CERTIFICATE_VERIFY_FAILED' in message:return self.render(url)
                transient=any(x in message for x in ('timed out','Network is unreachable','Remote end closed','HTTP Error 500','HTTP Error 502','HTTP Error 503'))
                if not transient or attempt==2:raise
                time.sleep(2 ** attempt)
        raise last

    def close(self):
        if self.browser:self.browser.close()


class Collector:
    def __init__(self,store):
        self.store=store; self.lock=threading.Lock(); self.quote_lock=threading.Lock()

    def ready(self):
        s=self.store.settings()
        return bool(s['organization'].strip() and re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',s['contact_email']))

    def start(self,name=None):
        if not self.ready(): raise ValueError('请先在管理页填写采集标识的公司名称和联系邮箱')
        rows=self.store.query('SELECT * FROM company_url_config WHERE is_active=1'+(' AND company_name=?' if name else ''),(name,) if name else ())
        if not rows: raise ValueError('未找到启用中的公司')
        if not self.lock.acquire(False): raise ValueError('已有采集任务运行中，请等待结束')
        job=self.store.execute('INSERT INTO jobs(kind,status,started_at,total,message) VALUES (?,?,?,?,?)',('单家采集' if name else '全量周更','运行中',now(),len(rows),'已排队'))
        threading.Thread(target=self.run,args=(job,rows,0,None,bool(name)),daemon=True).start()
        return job

    def alert(self,name,message):
        if self.store.query("SELECT id FROM alerts WHERE company_name=? AND message=? AND status!='已处理'",(name,message)): return
        aid=self.store.execute('INSERT INTO alerts(created_at,company_name,message) VALUES (?,?,?)',(now(),name,message))
        s=self.store.settings()
        # Sending is opt-in and requires explicit operator configuration.
        if not s.get('notifications_enabled'): return
        recipient=os.environ.get('FEES_ALERT_TO'); host=os.environ.get('FEES_SMTP_HOST')
        if not recipient or not host:
            self.store.execute('UPDATE alerts SET status=? WHERE id=?',('邮件未配置',aid)); return
        try:
            msg=EmailMessage(); msg['Subject']='手续费公示监测提醒'; msg['From']=os.environ.get('FEES_SMTP_FROM',s['contact_email']); msg['To']=recipient
            msg.set_content(name+'\n'+message)
            with smtplib.SMTP(host,int(os.environ.get('FEES_SMTP_PORT','587')),timeout=15) as smtp:
                smtp.starttls()
                if os.environ.get('FEES_SMTP_USER'): smtp.login(os.environ['FEES_SMTP_USER'],os.environ['FEES_SMTP_PASSWORD'])
                smtp.send_message(msg)
            self.store.execute('UPDATE alerts SET status=? WHERE id=?',('邮件已发送',aid))
        except Exception as e: self.store.execute('UPDATE alerts SET status=? WHERE id=?',('邮件发送失败：'+str(e)[:160],aid))

    def page(self,fetcher,company,url,kind,seen):
        if url in seen: raise ValueError('同 URL 已抓取，跳过重复请求')
        seen.add(url); start=time.monotonic(); path=None; code=None
        try:
            html,ctype,code,final=fetcher.get(url); cls=classify(final,ctype,html)
            self.store.execute('UPDATE company_url_config SET classification=? WHERE id=?',(cls,company['id']))
            cache=DATA/'snapshots'; cache.mkdir(exist_ok=True)
            key=hashlib.sha256((url+now()).encode()).hexdigest()[:24]
            raw=getattr(fetcher,'last_payload',b'')
            if cls.startswith('图片'):
                ext=Path(parse.urlsplit(final).path).suffix.lower()
                if ext not in ('.png','.jpg','.jpeg','.webp'):ext='.img'
                asset=key+ext
                (cache/asset).write_bytes(raw)
                ocr_html=image_html(raw)
                result=parse_gold(ocr_html or '',parse.urlsplit(final).hostname or '') if ocr_html else {'variants':[],'source_updated_at':None}
                status='成功' if result['variants'] else '解析待核验'
                self.store.snapshot(company['company_name'],url,status,
                    '' if result['variants'] else '图片已保存并执行OCR，未识别可核验黄金费率',
                    http_status=code,classification='图片 OCR' if ocr_html else cls,
                    raw_html_path='snapshots/'+asset,evidence=json.dumps(result['variants'],ensure_ascii=False))
                return result,Document(ocr_html or ''),final
            if raw.startswith((b'%PDF',b'PK')):
                extracted,fmt,_=document_html(raw,ctype,final)
                if not extracted:raise ValueError('附件格式暂不支持，需核验原文')
                ext='.pdf' if fmt.startswith('PDF') else '.xlsx'
                (cache/(key+ext)).write_bytes(raw)
                html=extracted;cls=(fmt+' 文本/表格' if fmt in ('PDF','Excel') else fmt+' · 待视觉核验')
            key+='.html'
            (cache/key).write_text(html,encoding='utf-8'); path='snapshots/'+key
            result=parse_gold(html,parse.urlsplit(final).hostname or '')
            sparse=len(html)<500 or len(Document(html).text)<80
            if not result['variants'] and (sparse or '<script' in html.lower()) and not getattr(fetcher,'rendered',False) and hasattr(fetcher,'render'):
                try:
                    rendered,_,_,render_final=fetcher.render(final)
                    html=rendered;final=render_final;cls='浏览器渲染 HTML'
                    (cache/key).write_text(html,encoding='utf-8')
                    result=parse_gold(html,parse.urlsplit(final).hostname or '')
                except Exception as render_error:
                    self.store.snapshot(company['company_name'],final,'解析待核验','浏览器复核失败：'+str(render_error)[:500],raw_html_path=path)

            if '恒泰期货' in company['company_name']:
                market='options' if kind=='options' else 'futures'
                rows=parse_htqh(html,market)
                if rows: self.store.save_htqh(market,rows,final)
            image_doc=Document(html)
            for src,alt in image_doc.images:
                match=re.fullmatch(r'data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=\s]+)',src)
                if not match: continue
                try:
                    payload=base64.b64decode(match[2],validate=False)
                    if not payload or len(payload)>4_000_000: continue
                    image_path=DATA/'snapshots'/(hashlib.sha256(payload).hexdigest()[:20]+'.'+match[1])
                    image_path.parent.mkdir(parents=True,exist_ok=True)
                    image_path.write_bytes(payload)
                    self.store.snapshot(company['company_name'],final,'解析待核验','内嵌图片已存档，需视觉核验',raw_html_path=str(image_path),classification='内嵌图片 · 待人工核验')
                except (ValueError,TypeError):
                    self.store.snapshot(company['company_name'],final,'解析待核验','内嵌图片编码无效，需人工核验')
            success=bool(result['variants'])
            if kind=='options': success=bool(rows)
            self.store.snapshot(company['company_name'],url,'成功' if success else '解析待核验','' if success else '未识别可核验黄金行，检查子页面或人工补录',
                http_status=code,source_updated_at=result['source_updated_at'],raw_html_path=path,evidence=json.dumps(result['variants'],ensure_ascii=False),
                duration_ms=int((time.monotonic()-start)*1000),classification=cls)
            return result,Document(html),final
        except Exception as e:
            self.store.snapshot(company['company_name'],url,'抓取失败',str(e)[:500],http_status=getattr(e,'code',code),raw_html_path=path,duration_ms=int((time.monotonic()-start)*1000))
            raise

    def one(self,fetcher,c,respect_pause=True):
        name=c['company_name']; stamp=now()
        if respect_pause and c['paused_until'] and c['paused_until']>stamp:
            self.store.snapshot(name,c['fee_page_url'] or c['parent_url'],'已暂停','累计失败率超阈值，暂停至 '+c['paused_until'])
            return False
        result,final,status,seen,errors=scan(self,fetcher,c)
        if not result and errors:
            if any('robots.txt' in e and ('不允许' in e or '返回 403' in e or '返回 458' in e) for e in errors):
                status='官网限制·人工核验'
            elif all('网址必须是公开的 HTTP(S)' in e for e in errors):
                status='入口待确认'
        if not seen:self.store.snapshot(name,'','入口待确认','公示及上一级入口均未提供，需补充官网入口')
        success=bool(result and result['variants'])
        if success: self.store.save_gold(name,result,final)
        else:
            self.store.execute('''UPDATE gold_fee_ratio SET
              status=CASE WHEN variants IS NULL OR variants IN ('','[]') THEN ? ELSE status END,
              latest_collection_status=?,latest_collection_error=?,last_attempt_at=? WHERE company_name=?''',
              (status,status,'；'.join(errors)[:1000],stamp,name))
        at=datetime.now(TZ); week=(at.date()-timedelta(days=at.weekday())).isoformat()
        self.store.execute('''INSERT INTO weekly_outcomes VALUES (?,?,?) ON CONFLICT(company_name,week)
          DO UPDATE SET success=MAX(weekly_outcomes.success,excluded.success)''',(name,week,int(success)))
        previous=(at.date()-timedelta(days=at.weekday()+7)).isoformat()
        outcomes=self.store.query('SELECT * FROM weekly_outcomes WHERE company_name=? AND week IN (?,?)',(name,week,previous))
        if len(outcomes)==2 and not any(r['success'] for r in outcomes):
            self.store.execute("""UPDATE gold_fee_ratio SET
              status=CASE WHEN variants IS NULL OR variants IN ('','[]') THEN '连续采集失败' ELSE status END,
              latest_collection_status='连续采集失败' WHERE company_name=?""",(name,)); self.alert(name,'连续两周未抓取到有效公示，请人工核查')
        attempts=c['attempts']+1; failures=c['failures']+int(not success)
        # Full weekly runs are already rate-limited and must revisit every registry
        # entry.  The short pause only protects the single-company admin action
        # from repeated manual retries.
        pause=((at+timedelta(hours=24)).isoformat(timespec='seconds')
               if respect_pause and failures/attempts>self.store.settings()['failure_threshold'] else None)
        self.store.execute('UPDATE company_url_config SET attempts=?,failures=?,paused_until=? WHERE id=?',(attempts,failures,pause,c['id']))
        if '恒泰期货' in name:
            try: self.page(fetcher,c,'https://www.cnhtqh.com.cn/col118/36','options',seen)
            except Exception as e: self.alert(name,'期权公示采集失败：'+str(e)[:180])
        return success

    def run(self,job,rows,completed_before=0,total=None,respect_pause=False):
        total = total if total is not None else completed_before + len(rows)
        try:
            s=self.store.settings(); ua=f"FeeMonitor/1.1 ({s['organization']}; contact: {s['contact_email']})"
            # HTTP headers use Latin-1; encode Chinese organization explicitly.
            ua=ua.encode('ascii','backslashreplace').decode('ascii'); fetcher=Fetcher(ua)
            good=0; start=time.monotonic()
            for index,c in enumerate(rows):
                if time.monotonic()-start>1740:
                    completed=completed_before+index
                    self.store.execute('UPDATE jobs SET status=?,finished_at=?,completed=?,total=?,message=? WHERE id=?',
                        ('已复核·待处理',now(),completed,total,
                         f'已到29分钟运行上限，已处理 {completed}/{total} 家；下次任务从第 {completed+1} 家续采',job))
                    return
                tick=time.monotonic()
                self.store.execute('UPDATE jobs SET message=? WHERE id=?',(c['company_name'],job))
                try: good+=int(self.one(fetcher,c,respect_pause=respect_pause))
                except Exception as e: self.alert(c['company_name'],'采集异常：'+str(e)[:180])
                self.store.execute('UPDATE jobs SET completed=?,succeeded=? WHERE id=?',
                    (completed_before+index+1,good,job))
                time.sleep(max(0,1-(time.monotonic()-tick)))
            self.store.execute('UPDATE jobs SET status=?,finished_at=?,completed=?,total=?,message=? WHERE id=?',
                (completion_status(total,total,good),now(),total,total,
                 f'已检查 {total} 家；本批识别成功 {good} 家；待处理不代表无公示',job))
        except Exception as e:
            self.store.execute('UPDATE jobs SET status=?,finished_at=?,message=? WHERE id=?',('失败',now(),str(e)[:500],job))
        finally:
            if 'fetcher' in locals():fetcher.close()
            self.lock.release()

    def recalculate(self,quotes=None):
        if not self.quote_lock.acquire(False): return {'status':'已有行情任务运行中'}
        job=self.store.execute('INSERT INTO jobs(kind,status,started_at) VALUES (?,?,?)',('行情重算','运行中',now()))
        try:
            if quotes is None:
                s=self.store.settings()
                if s.get('quotes_provider') in ('shfe_close','tqsdk'):
                    quotes=official_close_quotes(self.store.query("SELECT * FROM htqh_fee_raw WHERE charge_type='proportional'"))
                elif not s['quotes_url']:
                    self.store.execute('UPDATE jobs SET status=?,finished_at=?,message=? WHERE id=?',('未接入',now(),'行情源未配置，比例费率保留原文',job)); return {'status':'行情未接入'}
                if quotes is None:
                    req=request.Request(validate_url(s['quotes_url'],True),headers={'Accept':'application/json'})
                    token=os.environ.get('FEES_QUOTES_TOKEN')
                    if token: req.add_header('Authorization','Bearer '+token)
                    with request.build_opener(SafeRedirect()).open(req,timeout=15) as r: quotes=json.loads(r.read(2_000_000))
            entries=quotes.get('quotes',[])
            if not isinstance(entries,list): raise ValueError('行情 quotes 必须为数组')
            rows=self.store.query("SELECT * FROM htqh_fee_raw WHERE charge_type='proportional'"); valid=[]
            for q in entries:
                price,stamp,basis=select_price(q)
                ts=datetime.fromisoformat(stamp)
                if ts.tzinfo is None: raise ValueError('行情时间必须含时区')
                if ts>datetime.now(TZ)+timedelta(seconds=5): raise ValueError('行情时间不能位于未来')
                multiplier=number(q['multiplier'],True)
                contract=str(q['contract']).strip()
                if not re.fullmatch(r'[A-Za-z]{1,3}\d{3,4}(?:[-A-Za-z0-9.]+)?',contract): raise ValueError('需要实际月份合约代码，不能用主连价格代替')
                raw_id=int(q['fee_rule_id']); row=next((r for r in rows if r['id']==raw_id),None)
                if not row: raise ValueError('行情费率规则不存在，请刷新规则ID')
                prefix=re.match(r'[A-Za-z]+',row['contract'])
                if prefix and not contract.upper().startswith(prefix[0].upper()): raise ValueError('合约与费率品种不匹配')
                valid.append((row['id'],contract,str(price),str(multiplier),calculate(row['rate_value'],multiplier,price,'proportional'),ts.isoformat(),now(),basis,str(q.get('source_url',''))))
            with self.store.connect() as c:
                for v in valid:
                    previous=c.execute('SELECT price_ts,last_price,multiplier,price_basis FROM htqh_fee_calc_snapshot WHERE raw_id=? AND contract=? ORDER BY id DESC LIMIT 1',(v[0],v[1])).fetchone()
                    if previous and datetime.fromisoformat(previous[0])>datetime.fromisoformat(v[5]): continue
                    if previous and tuple(previous)==(v[5],v[2],v[3],v[7]):continue
                    c.execute('INSERT INTO htqh_fee_calc_snapshot(raw_id,contract,last_price,multiplier,company_fee_yuan,price_ts,calc_ts,price_basis,quote_source_url) VALUES (?,?,?,?,?,?,?,?,?)',v)
            self.store.execute('UPDATE jobs SET status=?,finished_at=?,completed=?,total=?,message=? WHERE id=?',('完成',now(),len(valid),len(valid),'按明确规则映射重算；不自动猜测特殊月份费率',job))
            return {'calculated':len(valid)}
        except Exception as e:
            self.store.execute('UPDATE jobs SET status=?,finished_at=?,message=? WHERE id=?',('失败',now(),str(e)[:500],job))
            return {'error':str(e),'status':'重算失败，保留最近成功价格'}
        finally: self.quote_lock.release()

    def schedule_loop(self,stop):
        minute=''
        while not stop.wait(5):
            try:
                at=datetime.now(TZ); s=self.store.settings()
                if not s['scheduler_enabled']: continue
                key=at.strftime('%Y-%m-%d %H:%M')
                if key!=minute:
                    minute=key
                    if s['quotes_url'] or s.get('quotes_provider') in ('shfe_close','tqsdk'): threading.Thread(target=self.recalculate,daemon=True).start()
                monday=at.date()-timedelta(days=at.weekday())
                due=datetime.combine(monday,datetime.min.time(),TZ).replace(hour=8)
                if at>=due and s['last_scheduled_week']!=monday.isoformat() and self.ready():
                    self.start(); self.store.set_setting('last_scheduled_week',monday.isoformat())
                month=at.strftime('%Y-%m')
                if s['last_monthly_review']!=month:
                    missing=self.store.query("SELECT company_name FROM company_url_config WHERE fee_page_url='' OR classification LIKE '%待人工%'")
                    for c in missing: self.alert(c['company_name'],'月度回顾：补充或核查公示入口／人工费率')
                    self.store.set_setting('last_monthly_review',month)
            except Exception as e:
                self.store.set_setting('scheduler_error',str(e)[:300])
