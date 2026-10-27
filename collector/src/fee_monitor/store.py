import json, sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone, timedelta
from .parsers import money, number, classify

TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT/'data/fee_module'


def now(): return datetime.now(TZ).isoformat(timespec='seconds')


def next_weekly(at=None):
    at=at or datetime.now(TZ)
    target=at.replace(hour=8,minute=0,second=0,microsecond=0)+timedelta(days=(7-at.weekday())%7)
    if target<=at: target+=timedelta(days=7)
    return target.isoformat(timespec='seconds')


class Store:
    def __init__(self,path=None):
        self.path=Path(path or DATA/'fees.sqlite3'); self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS company_url_config (
              id INTEGER PRIMARY KEY, company_name TEXT UNIQUE NOT NULL, fee_page_url TEXT,
              parent_url TEXT, is_single_url INTEGER, is_active INTEGER DEFAULT 1,
              original_fee_entry TEXT, notes TEXT, updated_at TEXT, classification TEXT,
              attempts INTEGER DEFAULT 0, failures INTEGER DEFAULT 0, paused_until TEXT);
            CREATE TABLE IF NOT EXISTS exchange_fee_constant (
              product TEXT, exchange TEXT, value_yuan TEXT, effective_from TEXT,
              PRIMARY KEY(product,exchange,effective_from));
            CREATE TABLE IF NOT EXISTS fee_disclosure_snapshot (
              id INTEGER PRIMARY KEY,company_name TEXT,url_type TEXT,url TEXT,http_status INTEGER,
              fetched_at TEXT,source_updated_at TEXT,raw_html_path TEXT,parse_status TEXT,
              error_msg TEXT,evidence TEXT,duration_ms INTEGER,classification TEXT);
            CREATE TABLE IF NOT EXISTS gold_fee_ratio (
              company_name TEXT PRIMARY KEY,variants TEXT DEFAULT '[]',source_updated_at TEXT,
              fetched_at TEXT,status TEXT,last_attempt_at TEXT,source_url TEXT,evidence TEXT);
            CREATE TABLE IF NOT EXISTS htqh_fee_raw (
              id INTEGER PRIMARY KEY,market TEXT,symbol TEXT,contract TEXT,exchange TEXT,
              session_type TEXT,charge_type TEXT,rate_text TEXT,rate_value TEXT,unit TEXT,
              source_updated_at TEXT,fetched_at TEXT,source_url TEXT,notes TEXT,evidence TEXT);
            CREATE TABLE IF NOT EXISTS htqh_fee_history (
              id INTEGER PRIMARY KEY,market TEXT,fetched_at TEXT,source_url TEXT,payload TEXT);
            CREATE TABLE IF NOT EXISTS htqh_fee_calc_snapshot (
              id INTEGER PRIMARY KEY,raw_id INTEGER,contract TEXT,last_price TEXT,multiplier TEXT,
              company_fee_yuan TEXT,price_ts TEXT,calc_ts TEXT);
            CREATE INDEX IF NOT EXISTS calc_raw_id ON htqh_fee_calc_snapshot(raw_id,id);
            CREATE INDEX IF NOT EXISTS calc_latest_by_contract ON htqh_fee_calc_snapshot(raw_id,contract,id DESC);
            CREATE TABLE IF NOT EXISTS jobs (
              id INTEGER PRIMARY KEY,kind TEXT,status TEXT,started_at TEXT,finished_at TEXT,
              total INTEGER DEFAULT 0,completed INTEGER DEFAULT 0,succeeded INTEGER DEFAULT 0,message TEXT);
            CREATE TABLE IF NOT EXISTS weekly_outcomes (
              company_name TEXT,week TEXT,success INTEGER,PRIMARY KEY(company_name,week));
            CREATE TABLE IF NOT EXISTS alerts (
              id INTEGER PRIMARY KEY,created_at TEXT,company_name TEXT,message TEXT,status TEXT DEFAULT '待处理');
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT);
            ''')
            columns={row[1] for row in c.execute('PRAGMA table_info(htqh_fee_calc_snapshot)')}
            for key,definition in [('price_basis',"TEXT DEFAULT 'last'"),('quote_source_url',"TEXT DEFAULT ''")]:
                if key not in columns:c.execute('ALTER TABLE htqh_fee_calc_snapshot ADD COLUMN '+key+' '+definition)
            gold_columns={row[1] for row in c.execute('PRAGMA table_info(gold_fee_ratio)')}
            for key in ('latest_collection_status','latest_collection_error'):
                if key not in gold_columns:c.execute('ALTER TABLE gold_fee_ratio ADD COLUMN '+key+' TEXT')
            c.execute("UPDATE gold_fee_ratio SET latest_collection_status=COALESCE(latest_collection_status,status)")
            c.execute("""UPDATE gold_fee_ratio SET status='人工核验'
              WHERE variants IS NOT NULL AND variants NOT IN ('','[]')
                AND status NOT IN ('成功','人工核验')""")
            c.execute("INSERT OR IGNORE INTO exchange_fee_constant VALUES ('AU','SHFE','10.00','2026-09-04')")
            defaults={'organization':'','contact_email':'','failure_threshold':0.3,'notifications_enabled':False,'quotes_url':'','quotes_name':'','quotes_provider':'custom','scheduler_enabled':True,'last_scheduled_week':'','last_monthly_review':''}
            for k,v in defaults.items(): c.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',(k,json.dumps(v)))

    @contextmanager
    def connect(self):
        c=sqlite3.connect(self.path,timeout=15); c.row_factory=sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA foreign_keys=ON')
        try:
            with c: yield c
        finally: c.close()

    def query(self,sql,args=()):
        with self.connect() as c: return [dict(r) for r in c.execute(sql,args)]

    def execute(self,sql,args=()):
        with self.connect() as c: return c.execute(sql,args).lastrowid

    def settings(self): return {r['key']:json.loads(r['value']) for r in self.query('SELECT * FROM settings')}

    def set_setting(self,key,value):
        self.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,json.dumps(value,ensure_ascii=False)))

    def initialize_inventory(self,rows):
        with self.connect() as c:
            for x in rows:
                c.execute('''INSERT OR IGNORE INTO company_url_config
                  (company_name,fee_page_url,parent_url,is_single_url,original_fee_entry,notes,updated_at,classification)
                  VALUES (?,?,?,?,?,?,?,?)''',(x['company_name'],x['fee_page_url'],x['parent_url'],
                  bool(x['fee_page_url'] and x['fee_page_url']==x['parent_url']),x['original_fee_entry'],x['notes'],now(),classify(x['fee_page_url']) if x['fee_page_url'] else 'URL 缺失'))
                c.execute('INSERT OR IGNORE INTO gold_fee_ratio(company_name,status) VALUES (?,?)',
                    (x['company_name'],'待更新' if x['fee_page_url'] else '待补录' if '华闻' in x['company_name'] else 'URL 缺失'))

    def baseline(self,at=None):
        day=(at or datetime.now(TZ)).date().isoformat()
        rows=self.query("SELECT * FROM exchange_fee_constant WHERE product='AU' AND exchange='SHFE' AND effective_from<=? ORDER BY effective_from DESC LIMIT 1",(day,))
        return rows[0] if rows else {'value_yuan':'10.00','effective_from':None,'product':'AU','exchange':'SHFE'}

    def companies(self):
        baseline=self.baseline()['value_yuan']; rows=self.query('''SELECT c.*,g.variants,g.source_updated_at,
          g.fetched_at,g.status,g.last_attempt_at,g.source_url,g.evidence,
          g.latest_collection_status,g.latest_collection_error FROM company_url_config c
          JOIN gold_fee_ratio g USING(company_name) ORDER BY c.id''')
        for x in rows:
            x['variants']=json.loads(x['variants'] or '[]')
            x['fee_basis']='官网金额'
            for v in x['variants']:
                if v.get('fee_type')=='exchange_multiple':
                    v['fee']=money(number(v['exchange_multiplier'],True)*number(baseline,True))
                    x['fee_basis']='按维护基准换算'
            values=[number(v['fee']) for v in x['variants']]
            x['exchange_fee_yuan']=baseline
            x['company_fee_yuan']=money(min(values)) if values else None
            x['company_fee_max']=money(max(values)) if values else None
            x['ratio']=money(min(values)/number(baseline,True)) if values else None
            x['ratio_max']=money(max(values)/number(baseline,True)) if values else None
            x['below_baseline']=bool(values and min(values)<number(baseline))
        return rows

    def save_gold(self,name,result,url,stamp=None,status='成功',evidence=''):
        stamp=stamp or now()
        if not result['variants']: raise ValueError('没有可核验的黄金费率')
        self.execute('''UPDATE gold_fee_ratio SET variants=?,source_updated_at=?,fetched_at=?,status=?,
          last_attempt_at=?,source_url=?,evidence=?,latest_collection_status='成功',latest_collection_error=''
          WHERE company_name=?''',
          (json.dumps(result['variants'],ensure_ascii=False),result.get('source_updated_at'),stamp,status,stamp,url,evidence,name))

    def save_htqh(self,market,rows,url,stamp=None):
        if not rows: raise ValueError('未识别有效品种表；保留上次成功记录')
        stamp=stamp or now()
        with self.connect() as c:
            c.execute('INSERT INTO htqh_fee_history(market,fetched_at,source_url,payload) VALUES (?,?,?,?)',
                (market,stamp,url,json.dumps(rows,ensure_ascii=False)))
            keys=('market','symbol','contract','exchange','session_type')
            existing={tuple(r[k] for k in keys):r['id'] for r in c.execute('SELECT * FROM htqh_fee_raw WHERE market=?',(market,))}
            max_raw=c.execute('SELECT COALESCE(MAX(id),0) FROM htqh_fee_raw').fetchone()[0]
            max_calc=c.execute('SELECT COALESCE(MAX(raw_id),0) FROM htqh_fee_calc_snapshot').fetchone()[0]
            next_id=max(max_raw,max_calc)+1
            fields=['id','market','symbol','contract','exchange','session_type','charge_type','rate_text','rate_value','unit','source_updated_at','fetched_at','source_url','notes','evidence']
            kept=[]
            for row in rows:
                identity=tuple(row.get(k) for k in keys)
                rid=existing.get(identity)
                if rid is None: rid=next_id; next_id+=1
                kept.append(rid)
                row={**row,'id':rid,'fetched_at':stamp,'source_url':url}
                c.execute('INSERT OR REPLACE INTO htqh_fee_raw('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')',[row.get(k) for k in fields])
            c.execute('DELETE FROM htqh_fee_raw WHERE market=? AND id NOT IN ('+','.join('?' for _ in kept)+')',[market,*kept])

    def snapshot(self,name,url,status,error='',**extra):
        row=dict(company_name=name,url_type='fee_page',url=url,fetched_at=now(),parse_status=status,error_msg=error,**extra)
        fields=list(row)
        return self.execute('INSERT INTO fee_disclosure_snapshot('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')',list(row.values()))
