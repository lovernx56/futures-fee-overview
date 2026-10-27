"""Recollect all registry entries on GitHub Actions and export a static snapshot."""
import json, os, sys
from pathlib import Path

REPO=Path(__file__).resolve().parents[2]
COLLECTOR=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(COLLECTOR/'src'))

from fee_monitor.collector import Collector
from fee_monitor.store import Store, DATA, now, next_weekly

OUTPUT=REPO/'site/data/fees.json'
DB=DATA/'fees.sqlite3'

def hydrate(store):
    if not OUTPUT.exists(): return
    previous=json.loads(OUTPUT.read_text(encoding='utf-8'))
    for row in previous.get('rows',[]):
        variants=row.get('variants') or []
        if variants:
            store.save_gold(row['company_name'],{'variants':variants,'source_updated_at':row.get('source_updated_at')},row.get('source_url') or row.get('fee_page_url') or '',stamp=row.get('fetched_at') or now(),status=row.get('status') or '人工核验',evidence=row.get('evidence') or '')
        store.execute('''UPDATE gold_fee_ratio SET status=?,last_attempt_at=?,latest_collection_status=?,latest_collection_error=? WHERE company_name=?''',
          (row.get('status') or '待更新',row.get('last_attempt_at'),row.get('latest_collection_status') or row.get('status'),row.get('latest_collection_error'),row['company_name']))

def export(store):
    rows=store.companies(); baseline=store.baseline()
    latest=max((r.get('fetched_at') or '' for r in rows),default='') or None
    payload={
      'generated_at':now(),'last_updated':latest,'next_weekly':next_weekly(),'baseline':baseline,
      'stats':{'total':len(rows),'with_data':sum(bool(r['variants']) for r in rows),'missing':sum(not r['fee_page_url'] for r in rows),'needs_review':sum(not r['variants'] for r in rows)},
      'coverage':{'attempted':sum(bool(r['last_attempt_at']) for r in rows),'unattempted':sum(not r['last_attempt_at'] for r in rows),'recognized':sum(bool(r['variants']) for r in rows),'pending':sum(r.get('latest_collection_status')=='解析待核验' for r in rows),'restricted':sum(r.get('latest_collection_status')=='官网限制·人工核验' for r in rows),'failed':sum(r.get('latest_collection_status') in ('抓取失败','连续采集失败') for r in rows)},
      'rows':rows}
    if len(rows)!=150: raise RuntimeError(f'名录数量异常：{len(rows)}，拒绝发布')
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(payload,ensure_ascii=False,separators=(',',':')),encoding='utf-8')

def main():
    organization=os.environ.get('FEE_ORGANIZATION','').strip()
    email=os.environ.get('FEE_CONTACT_EMAIL','').strip()
    if not organization or '@' not in email: raise SystemExit('缺少 FEE_ORGANIZATION / FEE_CONTACT_EMAIL')
    if DB.exists(): DB.unlink()
    store=Store(DB)
    inventory=json.loads((DATA/'companies.json').read_text(encoding='utf-8'))
    store.initialize_inventory(inventory);hydrate(store)
    store.set_setting('organization',organization);store.set_setting('contact_email',email)
    collector=Collector(store);rows=store.query('SELECT * FROM company_url_config WHERE is_active=1')
    completed=0
    for _ in range(2):
        pending=rows[completed:]
        if not pending: break
        job=store.execute('INSERT INTO jobs(kind,status,started_at,completed,total,message) VALUES (?,?,?,?,?,?)',('全量周更','运行中',now(),completed,len(rows),'GitHub Actions 定时采集'))
        if not collector.lock.acquire(False): raise RuntimeError('采集锁异常')
        collector.run(job,pending,completed_before=completed,total=len(rows))
        record=store.query('SELECT * FROM jobs WHERE id=?',(job,))[0]
        completed=record['completed']
    if completed!=len(rows): raise RuntimeError(f'全量采集未完成：{completed}/{len(rows)}，保留上次已发布版本')
    export(store)
    print(json.dumps({'completed':completed,'recognized':sum(bool(r['variants']) for r in store.companies())},ensure_ascii=False))

if __name__=='__main__': main()
