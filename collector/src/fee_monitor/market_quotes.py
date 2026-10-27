"""Official SHFE/INE closing prices and explicit price-basis selection."""
import json,re
from datetime import datetime,timedelta
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from .store import DATA,TZ
from .parsers import number

CONFIG_URL='https://www.shfe.com.cn/data/config/product_config.dat'
DAILY_URL='https://www.shfe.com.cn/data/tradedata/future/dailydata/kx{day}.dat'

def fetch_json(url):
    with urlopen(Request(url,headers={'User-Agent':'FeeMonitor/1.2','Accept':'application/json'}),timeout=15) as response:
        raw=response.read(3_000_001)
        if len(raw)>3_000_000:raise ValueError('行情响应超过大小限制')
        return json.loads(raw)

def select_price(q):
    """Closing price is explicit; never substitute settlement or a stale last tick."""
    if q.get('market_status')=='closed':
        if not q.get('close_timestamp'):raise ValueError('收盘行情必须提供 close_timestamp')
        return number(q.get('close_price'),True),q['close_timestamp'],'close'
    basis=q.get('price_basis','last')
    if basis not in ('last','close'):raise ValueError('price_basis 必须为 last 或 close')
    return number(q['price'],True),q['timestamp'],basis

def matches_rule(row,product,month,day,has_special):
    contract=row['contract'].upper(); product=product.upper()
    if not contract.startswith(product):return False
    if re.match(r'[A-Z]+',contract)[0]!=product:return False
    year=2000+int(month[:2]); delivery=int(month[2:]); near=0<=(year-day.year)*12+delivery-day.month<=2
    if product=='AG' and has_special:
        special=delivery in (6,12) or near
        return not special if '除6/12' in contract else special if contract==product else False
    if product in ('FU','HC','RB') and has_special:
        special=delivery in ((1,5,9) if product=='FU' else (1,5,10)) or near
        return special if contract!=product else not special
    return contract==product or contract==product+month

def build_closing_quotes(payload,config,rows,day):
    entries=[]
    for item in payload.get('o_curinstrument',[]):
        month=str(item.get('DELIVERYMONTH','')).strip();product=str(item.get('PRODUCTGROUPID','')).strip()
        if not re.fullmatch(r'\d{4}',month) or not re.fullmatch('[a-zA-Z]{1,3}',product):continue
        spec=config.get(item.get('PRODUCTID'),{})
        try:price=number(item.get('CLOSEPRICE'),True);multiplier=number(spec.get('contract_number'),True)
        except ValueError:continue
        eligible=[r for r in rows if r['exchange'] in ('上期所','能源中心') and r['market']=='futures' and r['charge_type']=='proportional' and re.match('[A-Za-z]+',r['contract']) and re.match('[A-Za-z]+',r['contract'])[0].lower()==product.lower()]
        has_special=any(r['contract'].upper()!=product.upper() for r in eligible)
        for row in eligible:
            if matches_rule(row,product,month,day,has_special):
                entries.append({'fee_rule_id':row['id'],'contract':product.upper()+month,'price':str(price),'multiplier':str(multiplier),'timestamp':day.isoformat()+'T15:00:00+08:00','price_basis':'close','source_url':DAILY_URL.format(day=day.strftime('%Y%m%d'))})
    if not entries:raise ValueError('官方日行情没有可映射的有效收盘价')
    return {'quotes':entries}

def official_close_quotes(rows,at=None,loader=fetch_json):
    at=at or datetime.now(TZ); cache=DATA/'market';cache.mkdir(parents=True,exist_ok=True)
    # Reuse successful daily evidence; a no-data holiday is retried on the next run.
    config_file=cache/'product_config.json'
    if not config_file.exists() or at.timestamp()-config_file.stat().st_mtime>86400:
        config=loader(CONFIG_URL);config_file.write_text(json.dumps(config,ensure_ascii=False),encoding='utf-8')
    else:config=json.loads(config_file.read_text(encoding='utf-8'))
    for offset in range(15):
        day=at.date()-timedelta(days=offset)
        if day.weekday()>=5 or offset==0 and at.hour<16:continue
        path=cache/(day.isoformat()+'.json')
        try:
            payload=json.loads(path.read_text(encoding='utf-8')) if path.exists() else loader(DAILY_URL.format(day=day.strftime('%Y%m%d')))
        except HTTPError as e:
            if e.code==404:continue
            raise
        if not payload.get('o_curinstrument'):continue
        path.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
        return build_closing_quotes(payload,config,rows,day)
    raise ValueError('近15天没有可用官方收盘行情，保留上次结果')
