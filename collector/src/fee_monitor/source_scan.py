"""Bounded disclosure traversal shared by manual and scheduled collection."""
from .parsers import Document
import re
from .discovery import candidates, collection_status


def scan(collector, fetcher, company):
    queue=[(u,k,0) for u,k in ((company['fee_page_url'],'fee_page'),(company['parent_url'],'parent_page')) if u]
    seen=set();accessible=False;errors=[];last='';result=None;found=[]
    while queue and len(seen)<8:
        url,kind,depth=queue.pop(0)
        if url in seen:continue
        try:
            parsed,doc,final=collector.page(fetcher,company,url,kind,seen)
            accessible=True;last=final
            if parsed and parsed['variants']:
                found.append((parsed,final))
            if depth<2:
                children=[(u,'discovered',depth+1) for u,_ in candidates(final,doc,seen)]
                queue=children[:4]+queue
        except Exception as exc:errors.append(str(exc))
    if found:
        def rank(item):
            parsed,url=item
            stamp=parsed.get('source_updated_at') or ''
            hints=re.findall(r'(20\d{2})[-/]?(\d{2})[-/]?(\d{2})',url)
            hint='-'.join(hints[-1]) if hints else ''
            return stamp or hint
        result,last=max(found,key=rank)
    status=collection_status(bool(result),accessible,bool(seen))
    return result,last,status,seen,errors
