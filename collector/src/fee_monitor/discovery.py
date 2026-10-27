"""Discover fee disclosures from public navigation and embedded documents."""
import re
from urllib.parse import urljoin, urlsplit


def candidates(base, doc, seen=()):
    found={}
    host=(urlsplit(base).hostname or '').removeprefix('www.')
    title=getattr(doc,'title','')
    assets=list(doc.links)
    if '手续费' in title:
        assets += [(src,alt or '手续费公示图片') for src,alt in getattr(doc,'images',[])]
    for href,label in assets:
        url=urljoin(base,href)
        if url in seen or not url.startswith(('http://','https://')):continue
        same=(urlsplit(url).hostname or '').removeprefix('www.')==host
        binary=bool(re.search(r'\.(pdf|xlsx?)(?:\?|$)',url,re.I))
        image=bool(re.search(r'\.(png|jpe?g|webp)(?:\?|$)',url,re.I))
        is_fee='手续费' in label
        # Fee pages often contain many decorative assets whose missing alt text
        # would otherwise inherit the generic fee-image label and crowd the real
        # disclosure out of the bounded traversal queue.
        if image and re.search(r'(?:^|[/_.-])(logo|icon|banner|background|bg|loading|arrow|btn|qrcode|wechat|wx)(?:[/_.-]|$)',url,re.I):
            continue
        if not (same and is_fee or (binary or image) and (is_fee or same and '手续费' in title)):continue
        if any(w in label for w in ('仓储','损耗','交割标准','调整','关于','申报费','【','研究','报告','合同','销户')):continue
        if '期权' in label and '期货' not in label:continue
        score=10*int(binary)+9*int(image)+8*int('标准' in label or '公示' in label)+2*int('期货' in label)
        dates=re.findall(r'(20\d{2})[-年/]?(\d{2})[-月/]?(\d{2})',label+' '+url)
        date=''.join(dates[-1]) if dates else ''
        found[url]=(date,score,url,label)
    return [(url,label) for _,_,url,label in sorted(found.values(),key=lambda x:(x[0],x[1]),reverse=True)]


def collection_status(success, accessible, attempted):
    if success:return '成功'
    if accessible:return '解析待核验'
    if attempted:return '抓取失败'
    return '入口待确认'


def completion_status(completed, total, succeeded):
    if completed != total:return '未完成'
    return '完成' if succeeded==total else '已复核·待处理'
