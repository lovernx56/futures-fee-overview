'use strict';
const $=s=>document.querySelector(s),app=$('#app');
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dt=s=>s?new Date(s).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}).replaceAll('/','-'):'—';
const short=s=>s?String(s).slice(0,10):'未披露';
const range=(a,b,suffix='')=>a==null?'—':a===b||!b?`${esc(a)}${suffix}`:`${esc(a)}–${esc(b)}${suffix}`;
const badge=s=>`<span class="badge ${['成功','人工核验'].includes(s)?'good':['抓取失败','解析失败','连续采集失败'].includes(s)?'bad':['解析待核验','入口待确认','官网限制·人工核验','待补录','URL 缺失'].includes(s)?'warn':''}">${esc(s||'待更新')}</span>`;
const link=(url,label)=>/^https?:\/\//i.test(url||'')?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${label} ↗</a>`:`<span class="dim">${label==='公示页'?'入口待确认':'未提供'}</span>`;
const metric=(label,value,unit,note,cls='',icon='↗')=>`<article class="metric ${cls}"><div class="metric-label">${label}<span class="metric-icon">${icon}</span></div><div class="metric-number">${value}<small>${unit}</small></div><div class="metric-note">${note}</div></article>`;
const state={page:1,size:50,q:'',filter:'',sort:'-ratio'};
let payload=null;

function toast(message){const el=$('#toast');el.textContent=message;el.hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.hidden=true,4000)}
function openDetail(row){
  $('#dialog-title').textContent=row.company_name;
  const latest=row.latest_collection_status||row.status;
  $('#dialog-content').innerHTML=`<div class="source-links">${link(row.fee_page_url,'公示页')}${link(row.parent_url,'上一级')}</div>
    <div class="detail-facts"><div class="fact"><label>黄金手续费 · 元/手</label><b>${range(row.company_fee_yuan,row.company_fee_max)}</b></div><div class="fact"><label>黄金倍数 · 基准 ${esc(row.exchange_fee_yuan)} 元/手</label><b>${range(row.ratio,row.ratio_max,'×')}</b></div><div class="fact"><label>官网公示更新时间</label><b>${short(row.source_updated_at)}</b></div><div class="fact"><label>最近采集检查</label><b style="font-size:12px">${dt(row.last_attempt_at)}</b></div></div>
    <h3>合约适用条件与核验依据</h3>${row.variants?.length?row.variants.map(v=>`<div class="evidence"><b>${esc(v.contract||'黄金')}</b>　${esc(v.fee)} 元/手${v.fee_type==='exchange_multiple'?'（按维护基准换算）':''}<br>${esc(v.evidence||'')}</div><hr class="detail-separator">`).join(''):'<p class="evidence">尚无核验后的黄金费率，不将缺失数据视为零。</p>'}
    <p class="evidence">数据状态：${esc(row.status)}　最近检查：${esc(latest)}${row.latest_collection_error?'<br>检查说明：'+esc(row.latest_collection_error):''}<br>备注：${esc(row.notes||'—')}</p>`;
  $('#detail-dialog').showModal();
}
function filteredRows(){
  const q=state.q.trim().toLowerCase();
  let rows=payload.rows.filter(r=>(!q||r.company_name.toLowerCase().includes(q))&&(!state.filter||(state.filter==='has_data'?r.variants?.length:state.filter==='needs_review'?r.status!=='成功':r.status===state.filter)));
  const value=r=>r.ratio==null?null:Number(r.ratio);
  rows.sort((a,b)=>state.sort==='name'?a.company_name.localeCompare(b.company_name,'zh-CN'):state.sort==='ratio'?(value(a)??Infinity)-(value(b)??Infinity):state.sort==='-ratio'?(value(b)??-Infinity)-(value(a)??-Infinity):state.sort==='updated'?String(a.source_updated_at||'9999').localeCompare(String(b.source_updated_at||'9999')):String(b.source_updated_at||'').localeCompare(String(a.source_updated_at||'')));
  return rows;
}
function renderRows(){
  const rows=filteredRows(),pages=Math.max(1,Math.ceil(rows.length/state.size));state.page=Math.min(state.page,pages);
  const shown=rows.slice((state.page-1)*state.size,state.page*state.size);
  $('#result-count').textContent=`${rows.length} 家公司`;
  $('#company-rows').innerHTML=shown.map((r,i)=>`<tr><td class="index-cell">${String((state.page-1)*state.size+i+1).padStart(2,'0')}</td><td class="company-cell"><button class="company-link" data-id="${r.id}">${esc(r.company_name)}</button><div class="company-sub">${esc(r.classification)}${r.is_single_url?' · 单一入口':''}</div></td><td class="num">${range(r.company_fee_yuan,r.company_fee_max)}</td><td><span class="${r.ratio?'ratio':'ratio-empty'}">${range(r.ratio,r.ratio_max,'×')}</span>${r.below_baseline?'<div class="below">低于基准 · 请核验</div>':''}</td><td class="date">${r.source_updated_at?short(r.source_updated_at):r.fetched_at?'官网未标注':r.last_attempt_at?'未取得有效公示':'尚未采集'}</td><td class="date">${r.fetched_at?short(r.fetched_at):'—'}<div class="company-sub">${r.fetched_at?dt(r.fetched_at).split(' ').at(-1):'尚无成功快照'}</div></td><td>${badge(r.status)}</td><td><div class="source-links">${link(r.fee_page_url,'公示页')}${link(r.parent_url,'上一级')}</div></td></tr>`).join('')||'<tr><td colspan="8" class="empty"><strong>未找到匹配公司</strong>请调整搜索或筛选条件。</td></tr>';
  document.querySelectorAll('[data-id]').forEach(button=>button.onclick=()=>openDetail(payload.rows.find(r=>String(r.id)===button.dataset.id)));
  $('#pagination').innerHTML=`<span>共 ${rows.length} 家 · 第 ${state.page} / ${pages} 页</span><div class="pages"><button id="previous" ${state.page<=1?'disabled':''}>‹ 上一页</button><button class="current" disabled>${state.page}</button><button id="next" ${state.page>=pages?'disabled':''}>下一页 ›</button></div>`;
  $('#previous').onclick=()=>{state.page--;renderRows();};$('#next').onclick=()=>{state.page++;renderRows();};
}
function csv(){
  const fields=['公司名称','黄金手续费下限（元/手）','黄金手续费上限（元/手）','倍数下限','倍数上限','官网公示更新时间','最新成功快照','数据状态','公示页面','上一级页面'];
  const values=r=>[r.company_name,r.company_fee_yuan,r.company_fee_max,r.ratio,r.ratio_max,r.source_updated_at,r.fetched_at,r.status,r.fee_page_url,r.parent_url];
  const quote=v=>'"'+String(v??'').replaceAll('"','""')+'"';
  const blob=new Blob(['\ufeff'+[fields,...filteredRows().map(values)].map(row=>row.map(quote).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='行业手续费总览.csv';a.click();URL.revokeObjectURL(a.href);toast('CSV 已生成');
}
async function init(){
  try{
    const response=await fetch('./data/fees.json',{cache:'no-cache'});if(!response.ok)throw Error(`数据文件读取失败（${response.status}）`);payload=await response.json();
    const s=payload.stats,c=payload.coverage,b=payload.baseline;
    app.innerHTML=`<div class="heading"><div><div class="eyebrow">FUTURES FEE INTELLIGENCE</div><h1>行业手续费总览</h1><p class="subtitle">聚合 ${s.total} 家期货公司黄金手续费公示，独立静态发布，公开来源可追溯。</p></div><div class="heading-actions"><button id="download-csv">↓ 下载 CSV</button></div></div>
      <section class="metric-grid">${metric('纳入监测公司',s.total,'家','完整保留登记名录','','▦')}${metric('已识别黄金公示',s.with_data,'家','含官网金额及明确倍数规则','accent','✓')}${metric('尚待核验',s.needs_review,'家','未识别不代表未公示','gold','◷')}${metric('黄金换算基准',esc(b.value_yuan),'元/手','SHFE · AU　维护基准','','Au')}</section>
      <div class="info-strip"><div><span class="live-dot"></span><strong>已检查 ${c.attempted} / ${s.total} 家 · 已识别 ${c.recognized} 家</strong><br>待核验 ${c.pending} 家 · 抓取失败 ${c.failed} 家 · 未检查 ${c.unattempted} 家（未识别不代表未公示）</div><div>数据生成：${dt(payload.generated_at||payload.last_updated)}<br>最近成功快照：${dt(payload.last_updated)}</div></div>
      <section class="panel"><div class="panel-title"><h2>公司手续费公示<span id="result-count"></span></h2><span class="chip">AU · 黄金</span></div><div class="toolbar"><div class="searchbox"><input id="company-search" type="search" placeholder="搜索期货公司名称…" aria-label="搜索期货公司"></div><select id="status-filter" aria-label="筛选数据状态"><option value="">全部状态</option><option value="has_data">已有费率数据</option><option value="成功">采集成功</option><option value="人工核验">人工核验</option><option value="解析待核验">解析待核验</option><option value="抓取失败">抓取失败</option><option value="连续采集失败">连续采集失败</option></select><select id="sort-order" aria-label="排序方式"><option value="name">公司名称排序</option><option value="ratio">倍数从低到高</option><option value="-ratio">倍数从高到低</option><option value="-updated">公示时间从新到旧</option><option value="updated">公示时间从旧到新</option></select></div><div class="table-scroll"><table class="overview-table"><thead><tr><th class="index-cell">#</th><th>期货公司</th><th>黄金手续费 <span class="dim">元/手</span></th><th>黄金倍数</th><th>公示更新时间</th><th>抓取快照</th><th>数据状态</th><th>官方来源</th></tr></thead><tbody id="company-rows"></tbody></table></div><div class="pagination" id="pagination"></div></section>
      <div class="bottom-note"><b>ⓘ 口径说明</b><span>黄金倍数 = 公司黄金手续费 ÷ 当前维护基准。多个合约标准以区间显示，点击公司查看明细；无数据以“—”显示。页面为静态快照，更新失败时保留上一版有效数据。</span></div>`;
    $('#download-csv').onclick=csv;$('#company-search').oninput=()=>{state.q=$('#company-search').value;state.page=1;renderRows();};$('#status-filter').onchange=()=>{state.filter=$('#status-filter').value;state.page=1;renderRows();};$('#sort-order').onchange=()=>{state.sort=$('#sort-order').value;state.page=1;renderRows();};$('#sort-order').value=state.sort;renderRows();
  }catch(error){app.innerHTML=`<div class="error-box">无法读取数据：${esc(error.message)} <button onclick="location.reload()">重新加载</button></div>`;}
}
$('#close-dialog').onclick=()=>$('#detail-dialog').close();
$('#detail-dialog').addEventListener('click',event=>{if(event.target===$('#detail-dialog')){const r=event.target.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)event.target.close();}});
init();
