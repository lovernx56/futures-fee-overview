"""Explicit company fee multiples, including Chinese numerals and additive wording."""
import re
from decimal import Decimal


_DIGITS={'零':0,'〇':0,'一':1,'壹':1,'二':2,'两':2,'贰':2,'三':3,'叁':3,'四':4,'肆':4,
         '五':5,'伍':5,'六':6,'陆':6,'七':7,'柒':7,'八':8,'捌':8,'九':9,'玖':9}


def _number(token):
    token=re.sub(r'\s+','',token)
    if re.fullmatch(r'\d+(?:\.\d+)?',token):return str(Decimal(token))
    if token in _DIGITS:return str(_DIGITS[token])
    ten='十' if '十' in token else '拾' if '拾' in token else ''
    if ten:
        left,right=token.split(ten,1)
        value=(_DIGITS.get(left,1)*10)+(_DIGITS.get(right,0) if right else 0)
        return str(value)
    return None


def _variant(value,evidence,shanghai=False,additive=False):
    label=('上海期货交易所期货统一标准（含黄金；金额按维护基准换算）'
           if shanghai else '商品期货统一标准（含黄金；金额按维护基准换算）')
    wording=('交易所标准基础上加收，合计为 '+value+' 倍' if additive else '交易所标准的 '+value+' 倍')
    return {'contract':label,'fee':None,'fee_type':'exchange_multiple','exchange_multiplier':value,
            'evidence':evidence+'；官网公示为'+wording+'，金额随维护基准换算，不是官网直接报价。'}


def parse_multiple(doc):
    num=r'[零〇一壹二两贰三叁四肆五伍六陆七柒八捌九玖十拾\d.]+'
    evidence=[]
    # Keep support for compact tables that explicitly scope the rule to commodity futures.
    for table in doc.tables:
        for row in table:
            text=' | '.join(dict.fromkeys(row))
            scope=any(v=='商品期货' for v in row) or '所有期货品种' in text
            if not scope or '手续费' not in text:continue
            for m in re.finditer(r'交易所手续费(?:收取)?(?:标准)?(?:的)?[【\[]?('+num+r')[】\]]?倍',text):
                value=_number(m[1])
                if value:evidence.append((value,text,False,False))
    compact=re.sub(r'\s+','',doc.text)
    # Company-wide or exchange-specific prose. Requiring company/maximum/default
    # wording avoids treating an unrelated exchange notice as the company's tariff.
    markers=('公司','我司','默认','最高收取标准','基准标准','收取标准')
    additive=r'交易所(?:手续费|费率)(?:收取)?(?:标准)?(?:的)?基础上加收?('+num+r')倍'
    direct=r'交易所(?:手续费|费率)(?:收取)?(?:标准)?(?:的)?('+num+r')倍'
    for pattern,is_additive in ((additive,True),(direct,False)):
        for m in re.finditer(pattern,compact):
            context=compact[max(0,m.start()-120):min(len(compact),m.end()+80)]
            if '手续费' not in context or not any(x in context for x in markers):continue
            base=_number(m[1])
            if not base:continue
            value=str(Decimal(base)+1) if is_additive else base
            shanghai='上海期货交易所' in context or '上期所' in context
            evidence.append((value,context,shanghai,is_additive))
    # Prefer an explicit Shanghai Futures Exchange rule because gold belongs to SHFE.
    shanghai=[x for x in evidence if x[2]]
    selected=shanghai or evidence
    keys={(x[0],x[2]) for x in selected}
    if not selected or len(keys)!=1:return []
    value,text,is_shanghai,is_additive=selected[0]
    return [_variant(value,text,is_shanghai,is_additive)]
