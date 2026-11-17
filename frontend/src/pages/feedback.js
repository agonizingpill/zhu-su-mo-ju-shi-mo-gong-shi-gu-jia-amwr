import {SAMPLE_STATES,SAMPLE_VERSION_STATES,STEELS} from '../constants.js';
import {api,send} from '../api.js';
import {esc,number} from '../utils/format.js';
import {notify,empty} from '../components/common.js';
const versionCache={};
export async function loadVersions(id){
 if(versionCache[id]==null)versionCache[id]=await api('/training-samples/'+id+'/versions');
 return versionCache[id];
}
const FILTERS=[['all','全部'],['pending_evidence','待补证'],['pending','待回灌'],['ingested','已入库'],['quarantined','非正隔离'],['withdrawn','已撤回']];
function counts(s){return Object.fromEntries(Object.keys(SAMPLE_STATES).map(k=>[k,s.samples.filter(x=>x.status===k).length]))}
export function render(s){
 const c=counts(s);const filter=s.sampleFilter||'all';
 const rows=s.samples.filter(x=>filter==='all'||x.status===filter);
 const cur=s.samples.find(x=>x.id===Number(s.sampleSelected))||rows[0]||null;
 return `<div class="pagehead"><div><div class="kicker">10 / TRAINING FEEDBACK</div><h1>实绩回灌训练样本</h1><p>开工冻结工艺规格与准备工时；签结后以实际工时扣除准备工时作为训练目标，非正值隔离。训练固定当时有效样本版本，历史估价依据可回查。</p></div><button class="small" id="backfill" ${s.user.role==='viewer'?'disabled':''}>扫描续跑（补投中断任务）</button></div>
 <div class="ribbon">${Object.entries(SAMPLE_STATES).map(([k,v])=>`<div>${v}<strong class="mono">${c[k]||0}</strong></div>`).join('')}</div>
 <div class="grid2"><section class="section"><h2>样本台账</h2><div class="actions">${FILTERS.map(([k,v])=>`<button class="small ${filter===k?'selected':''}" data-filter="${k}">${v}</button>`).join('')}</div>
 <div class="execution-list">${rows.map(x=>`<button class="execution-row ${cur&&cur.id===x.id?'selected':''}" data-sample="${x.id}"><strong>${esc(x.estimate_code)}</strong><span class="status ${x.status}">${SAMPLE_STATES[x.status]}</span><small>目标 ${x.target_hours==null?'—':number(x.target_hours)+'h'} · v${x.active_version||0} · ${esc(x.steel)} ${x.cavities}腔</small>${x.reason?`<small class="muted">${esc(x.reason)}</small>`:''}</button>`).join('')||empty('暂无签结实绩；结算签结后样本自动进入回灌通道。')}</div></section>
 <section class="section">${cur?detail(s,cur):empty('选择左侧样本查看冻结依据、版本链与处理动作。')}</section></div>`;
}
function detail(s,x){
 const canAdmin=s.user.role==='admin';const canWrite=s.user.role!=='viewer';
 return `<h2>${esc(x.estimate_code)} · 样本 #${x.id}</h2>
 <div class="ribbon"><div>状态<strong><span class="status ${x.status}">${SAMPLE_STATES[x.status]}</span></strong></div><div>活动版本<strong class="mono">v${x.active_version||'—'} / 共 ${x.version_count} 版</strong></div><div>训练目标<strong class="mono">${x.target_hours==null?'—':number(x.target_hours)+' h'}</strong></div></div>
 ${x.reason?`<p class="rule">原因：${esc(x.reason)}</p>`:''}
 <h2>冻结依据（开工时固定）</h2>
 <div class="formgrid"><div>模腔<strong class="mono">${x.cavities}</strong></div><div>滑块<strong class="mono">${x.slides}</strong></div><div>钢材<strong class="mono">${esc(x.steel)}</strong></div><div>重量<strong class="mono">${number(x.weight_kg)} kg</strong></div><div>准备工时<strong class="mono">${number(x.setup_hours)} h</strong></div><div>实际工时<strong class="mono">${number(x.actual_hours)} h</strong></div></div>
 <p class="note">训练目标 = 实际工时 − 准备工时；规格与准备工时在开工快照冻结，后续模具/估单修改不影响本样本。建档人 ${esc(x.created_by)}${x.withdrawn_by?' · 撤回人 '+esc(x.withdrawn_by):''}</p>
 ${x.status==='pending_evidence'?evidenceBlock(s,x):''}
 ${x.status==='pending'?`<p class="rule">已进入回灌队列，worker 将自动入库；若进程中断可点右侧兜底。</p><div class="actions"><button class="small" data-ingest="${x.id}" ${canWrite?'':'disabled'}>同步入库（兜底）</button><button class="small" data-correct="${x.id}" ${canAdmin?'':'disabled'}>负责人更正（保留旧版）</button><button class="small" data-withdraw="${x.id}" ${canAdmin?'':'disabled'}>负责人撤回</button></div>`:''}
 ${(x.status==='ingested'||x.status==='quarantined')?`<div class="actions"><button class="small" data-correct="${x.id}" ${canAdmin?'':'disabled'}>负责人更正（保留旧版）</button><button class="small" data-withdraw="${x.id}" ${canAdmin?'':'disabled'}>负责人撤回</button></div>`:''}
 ${x.status==='pending_evidence'?`<div class="actions"><button class="small" data-withdraw="${x.id}" ${canAdmin?'':'disabled'}>负责人撤回</button></div>`:''}
 <div id="correct-form"></div><div id="withdraw-form"></div>
 <h2>版本链与历史依据</h2><div id="versions" data-id="${x.id}"><p class="muted">加载中…</p></div>`;
}
function evidenceBlock(s,x){
 return `<h2>待补证</h2><p class="rule">旧实绩缺少开工冻结依据，补齐工艺规格/工时凭据并经负责人确认后才进入回灌；补证不改动已签结算。</p>
 <form id="evidence-form" data-id="${x.id}"><label>补证依据（工艺卡、点检表等凭据说明，至少4字）<textarea name="evidence_note" minlength="4" required>${esc(x.evidence_note||'')}</textarea></label><div class="actions"><button ${s.user.role==='viewer'?'disabled':''}>登记补证</button><button type="button" id="confirm-evidence" ${s.user.role!=='admin'?'disabled':''}>负责人确认并入回灌</button></div>${x.evidence_confirmed_by?`<p class="note">已确认：${esc(x.evidence_confirmed_by)}</p>`:''}</form>`;
}
function correctForm(x){
 const willVersion=x.status!=='pending';
 return `<form id="correct-form-inner" data-id="${x.id}"><h2>负责人更正（旧版本保留）</h2><div class="fields">
 <label>模腔<input name="cavities" type="number" min="1" max="64" value="${x.cavities}" required></label>
 <label>滑块<input name="slides" type="number" min="0" max="32" value="${x.slides}" required></label>
 <label>钢材<select name="steel">${STEELS.map(v=>`<option value="${v}" ${v===x.steel?'selected':''}>${v}</option>`).join('')}</select></label>
 <label>重量 kg<input name="weight_kg" type="number" step="0.1" min="0.01" value="${x.weight_kg}" required></label>
 <label>准备工时 h<input name="setup_hours" type="number" step="0.1" min="0" max="48" value="${x.setup_hours}" required></label>
 <label>实际工时 h<input name="actual_hours" type="number" step="0.1" min="0.01" max="168" value="${x.actual_hours}" required></label></div>
 <label>更正原因（至少4字）<textarea name="change_note" minlength="4" required placeholder="说明新凭据来源；更正后目标为实际-准备，非正将拒绝"></textarea></label>
 <div class="actions"><button class="primary">${willVersion?'提交更正并生成新版本':'提交更正（入库时按新依据生成 v1）'}</button><button type="button" id="cancel-correct">取消</button></div><p class="note">更正只影响后续训练取数；已完成估价的模型证据保持原版本不变。</p></form>`;
}
function withdrawForm(x){
 return `<form id="withdraw-form-inner" data-id="${x.id}"><h2>负责人撤回样本</h2><label>撤回原因（至少4字）<textarea name="reason" minlength="4" required placeholder="撤回后活动版本失效，训练不再取入；在途回灌任务不会复活样本"></textarea></label><div class="actions"><button class="primary">确认撤回</button><button type="button" id="cancel-withdraw">取消</button></div></form>`;
}
export function bind(s,draw){
 const act=async(fn,msg)=>{try{await fn();await draw();notify(msg)}catch(e){notify(e.message)}};
 document.querySelector('#backfill').onclick=async()=>{try{const r=await send('/training-samples/backfill');Object.keys(versionCache).forEach(k=>delete versionCache[k]);await draw();notify(`续跑完成：新建档 ${r.created} 条，补投 ${r.enqueued} 条`)}catch(e){notify(e.message)}};
 document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{s.sampleFilter=b.dataset.filter;draw()});
 document.querySelectorAll('[data-sample]').forEach(b=>b.onclick=()=>{s.sampleSelected=Number(b.dataset.sample);draw()});
 const ef=document.querySelector('#evidence-form');
 if(ef)ef.onsubmit=async e=>{e.preventDefault();const d=Object.fromEntries(new FormData(ef));await act(()=>send('/training-samples/'+ef.dataset.id+'/evidence',d),'补证依据已登记')};
 const ce=document.querySelector('#confirm-evidence');
 if(ce)ce.onclick=async e=>{e.preventDefault();const d=Object.fromEntries(new FormData(ef));await act(()=>send('/training-samples/'+ef.dataset.id+'/confirm-evidence',d),'负责人已确认，样本进入待回灌')};
 document.querySelectorAll('[data-ingest]').forEach(b=>b.onclick=()=>act(()=>send('/training-samples/'+b.dataset.ingest+'/ingest'),'同步入库完成（幂等）'));
 document.querySelectorAll('[data-correct]').forEach(b=>b.onclick=()=>{const x=s.samples.find(v=>v.id===Number(b.dataset.correct));document.querySelector('#correct-form').innerHTML=correctForm(x);document.querySelector('#cancel-correct').onclick=()=>{document.querySelector('#correct-form').innerHTML=''};const f=document.querySelector('#correct-form-inner');f.onsubmit=async e=>{e.preventDefault();const d=Object.fromEntries(new FormData(f));for(const k of ['cavities','slides','weight_kg','setup_hours','actual_hours'])d[k]=Number(d[k]);await act(async()=>{await send('/training-samples/'+f.dataset.id+'/correct',d);delete versionCache[f.dataset.id]},'更正已提交，旧版本已保留')}});
 document.querySelectorAll('[data-withdraw]').forEach(b=>b.onclick=()=>{const x=s.samples.find(v=>v.id===Number(b.dataset.withdraw));document.querySelector('#withdraw-form').innerHTML=withdrawForm(x);document.querySelector('#cancel-withdraw').onclick=()=>{document.querySelector('#withdraw-form').innerHTML=''};const f=document.querySelector('#withdraw-form-inner');f.onsubmit=async e=>{e.preventDefault();const d=Object.fromEntries(new FormData(f));await act(async()=>{await send('/training-samples/'+f.dataset.id+'/withdraw',d);delete versionCache[f.dataset.id]},'样本已撤回，活动版本失效')}});
 const box=document.querySelector('#versions');
 if(box){const id=Number(box.dataset.id);loadVersions(id).then(vs=>{box.innerHTML=vs.length?`<div class="tablewrap"><table><thead><tr><th>版本</th><th>状态</th><th>规格（腔/滑/钢/重）</th><th>准备 h</th><th>实际 h</th><th>目标 h</th><th>说明</th></tr></thead><tbody>${vs.map(v=>`<tr><td class="mono">v${v.version}</td><td>${SAMPLE_VERSION_STATES[v.state]}</td><td class="mono">${v.cavities}/${v.slides}/${esc(v.steel)}/${number(v.weight_kg)}</td><td class="mono">${number(v.setup_hours)}</td><td class="mono">${number(v.actual_hours)}</td><td class="mono">${number(v.target_hours)}</td><td>${esc(v.change_note)}</td></tr>`).join('')}</tbody></table></div>`:empty('该样本尚无入库版本（待回灌/待补证/隔离/撤回）。')}).catch(e=>box.innerHTML=empty(e.message))}
}
