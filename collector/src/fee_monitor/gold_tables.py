"""Column-aware gold table parser shared by HTML, PDF and Excel extraction."""
import re
from decimal import Decimal

BAD_COLUMN=('保证金','仓储','交割','行权','履约','申报','平今','日内','交易所手续费','按金额','加收','另加')


def identity_header(text):
    return len(text)<45 and not any(w in text for w in ('手续费','保证金','仓储','费率')) and any(w in text for w in ('品种','合约','简称','代码','交易所'))


def gold_cell(text):
    text=re.sub(r'\s+','',text)
    return '期权' not in text and bool(re.match(r'^(?:沪金(?:[（(]|au|AU|\d|$)|黄金(?:期货)?(?:[（(]|au|AU|\d|$)|AU(?:黄金|沪金)?(?:[（(\[【]|\d|$))',text,re.I))


def fixed_amount(text,header,row,host=''):
    if re.search(r'(?:\+|加收|加上|另加)',text):return None
    matches=list(re.finditer(r'(?<![\d.])(\d+(?:\.\d+)?)\s*元\s*[/／]\s*手',text))
    if matches:
        # The opening/overnight amount precedes optional close-today annotations.
        first=matches[0]
        prefix=text[:first.start()]
        if '平今' in prefix or '万分' in prefix or '%' in prefix:return None
        return str(Decimal(first[1]))
    if not re.fullmatch(r'\d+(?:\.\d+)?',text):return None
    # Explicit fixed-unit headings; never assume the first numeric column is a fee.
    fixed=('按手数' in header or '元' in header or '固定' in header or '按手' in header or any(re.fullmatch(r'元[/／]手',v) or ('按手数' in v and '元' in v) for v in row) or header in ('开/平仓','开仓金额') or any('绝对值' in v for v in row))
    if fixed or (('bocifco.com' in host or 'ycfutures.cn' in host) and '手续费' in header) or ('手续费' in header and any('/万' in v or '万分' in v for v in row)):return str(Decimal(text))
    return None


def parse_tables(tables,host=''):
    results=[]
    for table in tables:
        headers=[];started=False
        for row in table:
            compact=[re.sub(r'\s+','',v) for v in row]; joined=' '.join(compact)
            is_header=(any(identity_header(v) and '交易所' not in v for v in compact) and (any('手续费' in v or '公示标准' in v or '默认标准' in v or '收费标准' in v or '公司标准' in v or '开仓金额' in v or v=='开/平仓' for v in compact) or (any('按金额' in v for v in compact) and any('按手数' in v for v in compact))))
            if is_header:
                headers=compact;started=False
                # PDF extraction leaves empty merged header cells; inherit only paired fee columns.
                for i in range(1,len(headers)):
                    if not headers[i] and headers[i-1] in ('开/平仓','平今仓','期货交割','行权/履约'):headers[i]=headers[i-1]
                continue
            if headers and not started and not any(re.search(r'\d',v) or re.fullmatch(r'[A-Za-z]{1,3}',v) for v in compact) and any(('元/手' in v or '开仓' in v or '按手数' in v) for v in compact) and not any(gold_cell(v) for v in compact) and not any(re.fullmatch(r'\d+(?:\.\d+)?',v) for v in compact):
                headers=[(' '.join(dict.fromkeys([headers[i] if i<len(headers) else '',v]))).strip() for i,v in enumerate(compact)]
                continue
            if not headers:continue
            if any(re.fullmatch(r'[A-Za-z]{1,3}(?:\d{3,4})?',v) for v in compact):started=True
            if '期权' in joined:continue
            positions=[i for i,v in enumerate(compact) if gold_cell(v) and i<len(headers) and any(w in headers[i] for w in ('品种','合约','简称','代码'))]
            if not positions:continue
            pos=max(positions)
            # In side-by-side exchange tables only use the column group containing gold.
            stop=next((i for i in range(pos+2,len(headers)) if identity_header(headers[i])),len(headers))
            # A nonzero amount-based opening component means the disclosed charge is
            # mixed (amount rate plus per-lot amount). A single yuan/lot value would
            # be incomplete, including if a later close column is numeric.
            opening_amount_cols=[i for i in range(pos+1,min(len(compact),stop)) if '按金额' in headers[i] and ('开仓' in headers[i] or '开/平仓' in headers[i])]
            if any(re.fullmatch(r'\d+(?:\.\d+)?',compact[i] or '') and Decimal(compact[i])!=0 for i in opening_amount_cols):continue
            candidates=[]
            for i in range(pos+1,min(len(compact),stop)):
                h=headers[i] if i<len(headers) else ''
                bad=any(word in h.replace('非日内','隔夜') for word in BAD_COLUMN)
                if bad or not any(word in h for word in ['手续费','公示标准','默认标准','收费标准','按手数','开仓','开仓金额','开/平仓','非日内','平昨']):continue
                value=fixed_amount(compact[i],h,compact,host)
                if value is None:continue
                rank=4*int('公司' in h or '客户' in h or '公示标准' in h)+3*int('开仓' in h or '非日内' in h)+int('按手数' in h)
                candidates.append((rank,value,compact[i],h))
            if not candidates:continue
            best=max(x[0] for x in candidates);chosen=[x for x in candidates if x[0]==best]
            if len({x[1] for x in chosen})!=1:continue
            value=chosen[0][1];fee_text=chosen[0][2]
            codes=[v for v in compact[:pos+1] if re.match(r'^au',v,re.I)]
            names=[v for v in compact[:pos+1] if v.startswith('黄金') and len(v)>2]
            contract=names[-1] if names else codes[-1] if codes else compact[pos]
            strategy=next((compact[i] for i,h in enumerate(headers) if '投机套保' in h and i<len(compact) and compact[i] in ('投机','套保')), '')
            if strategy:contract+='（'+strategy+'）'
            if re.search(r'au\d|合约为',fee_text,re.I):contract=fee_text
            if not contract:contract='AU 黄金（开仓/非日内）'
            results.append({'contract':contract,'fee':value,'evidence':'表头：'+' | '.join(headers)+'；公示行：'+' | '.join(compact),'parse_method':'同列匹配：'+chosen[0][3]})
    # Strong fallback for tables whose AJAX response omits the header row.
    for table in tables:
        for row in table:
            compact=[re.sub(r'\s+','',v) for v in row];joined=' | '.join(compact)
            gold_positions=[i for i,v in enumerate(compact) if re.match(r'^(?:au黄金|黄金au)',v,re.I)]
            if '期权' in joined or not gold_positions:continue
            # Headerless rows can contain two product groups. Only inspect cells
            # following gold so a fee from the left-hand group cannot leak in.
            pos=max(gold_positions)
            fees=[(i,re.search(r'(?<![\d.])(\d+(?:\.\d+)?)元[/／]手',v)) for i,v in enumerate(compact) if i>pos]
            fees=[(i,m) for i,m in fees if m and '平今' not in ''.join(compact[:i])]
            if not fees:continue
            code=next((re.sub(r'^M','',v,flags=re.I) for v in compact if re.match(r'^M?au\d{3,4}$',v,re.I)),'AU 黄金')
            value=str(Decimal(fees[0][1][1]))
            results.append({'contract':code,'fee':value,'evidence':'无表头接口行：'+joined,'parse_method':'品种+固定单位同行匹配'})
    seen=set();unique=[]
    for r in results:
        key=(r['contract'],r['fee'],r['evidence'])
        if key not in seen:unique.append(r);seen.add(key)
    return unique
