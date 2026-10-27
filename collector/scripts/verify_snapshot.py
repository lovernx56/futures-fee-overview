import json
from pathlib import Path

path=Path(__file__).resolve().parents[2]/'site/data/fees.json'
data=json.loads(path.read_text(encoding='utf-8'))
rows=data.get('rows',[])
assert len(rows)==150, f'expected 150 companies, got {len(rows)}'
assert len({r['company_name'] for r in rows})==150, 'duplicate company names'
assert data['stats']['total']==150
assert data['stats']['with_data']==sum(bool(r.get('variants')) for r in rows)
assert data['coverage']['recognized']==data['stats']['with_data']
assert all(str(r.get('fee_page_url') or '').startswith(('http://','https://','')) for r in rows)
print({'companies':len(rows),'recognized':data['coverage']['recognized']})
