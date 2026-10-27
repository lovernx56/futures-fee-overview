"""Conservative disclosure parsers. Never infer a fee from an unrelated number."""
import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html.parser import HTMLParser


def number(value, positive=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValueError('请输入有效数字') from None
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError('数值必须为有限非负数，基准、价格和乘数必须大于零')
    return result


def money(value):
    return str(number(value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def calculate(rate, multiplier=None, price=None, charge_type='fixed'):
    if charge_type == 'fixed': return money(rate)
    if multiplier is None or price is None: return None
    return money(number(rate) * number(multiplier, True) * number(price, True))


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.tables = []; self.links = []; self.images = []; self.texts = []
        match=re.search(r'<(?:title|h1)[^>]*>(.*?)</(?:title|h1)>',html,re.I|re.S)
        self.title=match[1].strip() if match else ''
        self.table = None; self.row = None; self.cell = None; self.anchor = None
        self.ignore = 0; self.table_stack=[]
        self.feed(html)
        self.text = ' '.join(self.texts)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ('script', 'style'): self.ignore += 1
        if tag == 'table':
            self.table_stack.append((self.table,self.row,self.cell))
            self.table=[];self.row=None;self.cell=None
        if tag == 'tr' and self.table is not None: self.row = []
        if tag in ('td', 'th') and self.row is not None:
            def span(k):
                try: return min(100, max(1, int(a.get(k, 1))))
                except ValueError: return 1
            self.cell = ['', span('rowspan'), span('colspan')]
        if tag in ('iframe','embed','object'):
            target=a.get('src') or a.get('data')
            if target:self.links.append((target,'公示内嵌附件'))
        if tag == 'img' and a.get('src'):
            self.images.append((a['src'],a.get('alt','')))
        if tag == 'a': self.anchor = [a.get('href', ''), '']

    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.ignore = max(0, self.ignore - 1)
        if tag in ('td', 'th') and self.cell is not None:
            self.cell[0] = re.sub(r'\s+', '', self.cell[0]); self.row.append(self.cell); self.cell = None
        if tag == 'tr' and self.row is not None:
            self.table.append(self.row); self.row = None
        if tag == 'table' and self.table is not None:
            self.tables.append(self.expand(self.table))
            self.table,self.row,self.cell=self.table_stack.pop() if self.table_stack else (None,None,None)
        if tag == 'a' and self.anchor is not None:
            self.links.append(tuple(self.anchor)); self.anchor = None

    def handle_data(self, data):
        if self.ignore: return
        if data.strip(): self.texts.append(data.strip())
        if self.cell is not None: self.cell[0] += data
        if self.anchor is not None: self.anchor[1] += data.strip()

    @staticmethod
    def expand(rows):
        grid = {}; output = []
        for r, row in enumerate(rows):
            c = 0
            for text, rs, cs in row:
                while (r,c) in grid: c += 1
                for dr in range(rs):
                    for dc in range(cs): grid[r+dr,c+dc] = text
                c += cs
            width = max([j+1 for i,j in grid if i == r], default=0)
            output.append([grid.get((r,j), '') for j in range(width)])
        return output


def source_date(doc):
    # Only a labelled disclosure date; copyright years / unrelated links are not dates.
    found = re.search(r'(?:更新时间|更新日期|发布日期|发布时间|时间|日期|更新于)\s*[：:]?\s*(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})', doc.text)
    if found:
        try: return date(*map(int,found.groups())).isoformat()
        except ValueError: pass
    # A disclosure title beginning with an effective date is also explicit evidence.
    if '手续费' in doc.title:
        found=re.search(r'^(20\d{2})年(\d{1,2})月(\d{1,2})日',re.sub(r'\s+','',doc.title))
        if found:
            try:return date(*map(int,found.groups())).isoformat()
            except ValueError:pass
    return None


def parse_rate(text, unit=''):
    cleaned = text.strip().replace(',', '')
    if cleaned in ('免收', '免', '免费'): return ('fixed', '0', '免收')
    # Reject combined formulae, ranges and footnotes for manual review.
    match = re.fullmatch(r'(?:成交金额的?|成交额的?)?(?:万分之)?\s*(\d+(?:\.\d+)?)\s*(%|％|元/手|元／手|元)?', cleaned)
    if not match: return ('unknown', None, text)
    value = number(match[1]); scope = unit + text
    if '万分之' in scope: return ('proportional', str(value/10000), unit+text)
    if '%' in scope or '％' in scope: return ('proportional', str(value/100), unit+text)
    if '元' in scope or unit == 'fixed': return ('fixed', str(value), text if '元' in text else text+' 元/手')
    return ('unknown', None, text)


def parse_htqh(html, market):
    doc = Document(html); output = []
    for table in doc.tables:
        if not table or '手续费' not in ''.join(table[0]): continue
        exercise = '行权' in ''.join(table[0]) or '履约' in ''.join(table[0]); delivery_exchange = None
        for row in table[1:]:
            joined=''.join(row)
            if '交割手续费收取标准' in joined:
                previous=next((r for r in reversed(output) if r['market']==market),None)
                delivery_exchange=previous['exchange'] if previous else None
                continue
            if delivery_exchange:
                if row and row[0] and row[0] not in (delivery_exchange,'品种'):
                    delivery_exchange=None
                else:
                    continue
            if '公司行权' in ''.join(row) or '履约）手续费收取标准' in ''.join(row):
                exercise = True
                continue
            if len(row) < 6 or '元/手' not in row[3] and '万分之' not in row[3]: continue
            if market == 'futures':
                if len(row) < 8: continue
                contract, symbol = row[1:3]
                sessions = ([('overnight', row[5]), ('intraday', row[7])]
                    if row[4] in ('开平','开仓','平仓') and row[6] == '平今'
                    else [('overnight', row[4]), ('intraday', row[6])])
            else:
                symbol = row[1]; contract = ''
                sessions = [('exercise' if exercise else 'all', row[4])]
            for session, value in sessions:
                kind, rate, raw = parse_rate(value, row[3])
                output.append(dict(market=market, symbol=symbol, contract=contract,
                    exchange=row[0], session_type=session, charge_type=kind,
                    rate_text=raw, rate_value=rate, unit=row[3], source_updated_at=source_date(doc),
                    notes=('行权／履约费用' if exercise else '') if kind!='unknown' else '复合费率，待人工核对原文',
                    evidence=' | '.join(dict.fromkeys(row))))
    return output


def parse_gold(html, host=''):
    doc = Document(html); variants = []
    if 'cnhtqh.com.cn' in host:
        rows = parse_htqh(html, 'futures')
        for row in rows:
            if row['symbol']=='黄金' and row['session_type']=='overnight' and row['charge_type']=='fixed':
                variants.append(dict(contract=row['contract'], fee=row['rate_value'], evidence=row['evidence']))
    elif 'futures.fcsc.com' in host:
        for table in doc.tables:
            for row in table:
                if len(row)==5 and row[2]=='黄金' and row[3]=='期货':
                    kind,value,_=parse_rate(row[4],'fixed')
                    if kind=='fixed': variants.append(dict(contract=row[1],fee=value,evidence=' | '.join(row)))
    elif 'futuresonline.com.cn' in host:
        for table in doc.tables:
            if not table: continue
            header=table[0]
            required=('品种代号','品种名称','备注')
            if not all(h in header for h in required): continue
            fee_cols=[i for i,h in enumerate(header) if h in ('手续费收取标准（*/手）','手续费收取标准(*/手)','手续费收取标准（元/手）')]
            if len(fee_cols)!=1: continue
            code_i,name_i,note_i=(header.index(h) for h in required)
            fee_i=fee_cols[0]
            for row in table[1:]:
                if len(row)<=max(code_i,name_i,note_i,fee_i): continue
                if row[code_i].upper()!='AU' or row[name_i]!='黄金': continue
                # This official table uses %% for proportional fees and bare numeric
                # values for fixed fees. Limit the mixed-unit convention to this adapter.
                kind,value,_=parse_rate(row[fee_i],'fixed')
                if kind!='fixed': continue
                note=row[note_i]; evidence=' | '.join(row)
                variants.append(dict(contract='AU 常规合约（非日内；特殊月份见备注）',fee=value,evidence=evidence))
                factors=re.findall(r'交易手续费为(\d+(?:\.\d+)?)倍',note)
                if factors and len(set(factors))==1 and ('月合约' in note):
                    factor=number(factors[0],True)
                    variants.append(dict(contract='AU 特殊月份：'+note.split('所有合约')[0].rstrip('；'),
                        fee=str(number(value)*factor),
                        evidence=evidence+'；特殊档位依据本行备注计算：'+value+' × '+str(factor)))
    else:
        from .gold_tables import parse_tables
        variants=parse_tables(doc.tables,host)
    if not variants:
        from .multiple_rules import parse_multiple
        variants=parse_multiple(doc)
    return {'variants':variants, 'source_updated_at':source_date(doc)}


def classify(url, content_type='', html=''):
    if 'pdf' in content_type or re.search(r'\.pdf(?:\?|$)',url,re.I): return 'PDF · 待人工'
    if content_type.startswith('image/') or re.search(r'\.(png|jpe?g|webp)(?:\?|$)',url,re.I): return '图片 · 待人工'
    if '#/' in url or 'enable javascript' in html.lower() or 'doesn\'t work properly without javascript' in html.lower(): return 'JS 渲染 · 待人工'
    return 'HTML'
