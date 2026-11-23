'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="skillctl-token"]').content;
const labels = {current:'與上游一致','update-available':'有上游更新','local-drift':'本機有修改',customized:'保留自訂內容',
  'unmanaged-identical':'尚未接管','unmanaged-conflict':'未接管・內容不同','unmanaged-local':'尚未接管',
  'local-only':'僅本機管理','source-unbound':'尚未建立上游基準',missing:'尚未安裝',nested:'巢狀目錄',linked:'連結目錄',invalid:'需要檢查'};
const reasons = {current:'本機檔案與上游、安裝收據一致。','update-available':'上游有新內容，可預覽合併更新。',
  'local-drift':'更新被擋：本機內容已不同於收據。直接安裝會覆蓋尚未處理的修改。',
  customized:'一般安裝會被擋下，以保護已保留的本機修改。請預覽保留或合併。',
  'unmanaged-identical':'目前內容與上游一致，但尚無管理收據。接管會保留原檔並開始追蹤。',
  'unmanaged-conflict':'更新被擋：既有內容不同於上游，且沒有收據。先檢查差異，再原地接管。',
  'unmanaged-local':'沒有選定的上游來源。接管後可追蹤本機修改與還原紀錄。',
  'local-only':'已接管本機副本。要比較上游，請以包含此技能的 --source 重新啟動管理頁。',
  'source-unbound':'此副本尚未建立上游基準。檢查差異後，使用保留建立基準。',
  missing:'此位置尚未安裝。請先使用 skillctl install 安裝技能。'};
let inventory, selected, plan, busy = false;
let fresh = false;
const target = () => inventory?.targets.find(t => t.id === $('target').value);
function node(tag, text, cls) { const el = document.createElement(tag); if(text !== undefined) el.textContent = text; if(cls) el.className = cls; return el; }
function notice(text, error=false) { $('notice').textContent = text; $('notice').className = error ? 'error' : ''; }
function clearPlan() { plan = null; $('preview').hidden = true; $('apply').disabled = true; }
function controls() {
  $('refresh').disabled = busy; $('target').disabled = busy;
  document.querySelectorAll('#rows button').forEach(b => b.disabled = busy || !fresh || b.dataset.unavailable === 'true');
  const ready = fresh && selected && !['missing','nested','linked','invalid'].includes(selected.status);
  $('adopt').disabled = busy || !ready || selected?.managed;
  $('keep').disabled = busy || !ready || !selected?.managed || !selected?.has_source;
  $('merge').disabled = busy || !ready || !selected?.managed || !selected?.has_source;
  $('apply').disabled = busy || !plan?.plan_id; $('cancel').disabled = busy;
}
async function api(path, body) {
  const controller = new AbortController();
  const timer = setTimeout(()=>controller.abort(),30000);
  try {
    const response = await fetch(path, {signal:controller.signal,method:body ? 'POST':'GET', headers:{'X-Skillctl-Token':token, ...(body ? {'Content-Type':'application/json'}:{})}, ...(body ? {body:JSON.stringify(body)}:{})});
    const data = await response.json(); if(!response.ok) throw new Error(data.error || '請求失敗'); return data;
  } catch(error) {
    if(error.name === 'AbortError') throw new Error('等候超過 30 秒。請確認本機服務仍在執行，再按「重新掃描」；若剛才正在套用，結果尚未確認，請先掃描確認');
    if(error instanceof TypeError) throw new Error('無法連線到本機服務。請確認服務仍在執行，再按「重新掃描」');
    throw error;
  } finally { clearTimeout(timer); }
}
async function task(work) {
  if(busy) return; busy = true;
  try { controls(); await work(); } catch(error) {
    clearPlan();
    notice(error.message + '；請重新檢查並預覽。' + (!fresh && target()?.skills ? ' 下方為上次結果，尚未更新。' : ''), true);
  }
  finally { busy = false; controls(); }
}
function renderRows() {
  const t = target(); $('rows').replaceChildren(); $('target-path').textContent = t?.path || '';
  const query = $('query').value.toLocaleLowerCase().trim();
  const rows = (t?.skills || []).filter(r => [r.name,r.description,labels[r.status]].join(' ').toLocaleLowerCase().includes(query));
  $('count').textContent = t?.skills ? `顯示 ${rows.length} / ${t.skills.length}` : '待掃描';
  $('empty').hidden = !t?.skills || rows.length > 0;
  const c = t?.counts;
  $('stats').textContent = c ? `本機已有 ${c.installed} · 已納管 ${c.managed} · 未納管 ${c.unmanaged} · 尚未安裝 ${c.missing} · 需檢查 ${c.attention}` : '尚未完成掃描，數量待確認。';
  if(t?.error) notice(t.error, true);
  for(const row of rows) {
    const tr = node('tr', undefined, selected?.path === row.path ? 'active-row':'');
    const cell = node('td'); cell.append(node('div',row.name,'skill-label'),node('div',row.description || row.error || '', 'description'));
    const status = node('td'); status.append(node('span',labels[row.status] || row.status, 'badge ' + (row.status === 'invalid' ? 'bad' : /drift|conflict|customized/.test(row.status) ? 'warn':'')));
    const action = node('td'); const button = node('button','檢視'); button.type = 'button'; button.setAttribute('aria-label','檢視 ' + row.name);
    button.dataset.unavailable = String(['nested','linked','invalid'].includes(row.status));
    button.addEventListener('click', () => task(() => selectSkill(row)));
    if(button.dataset.unavailable === 'true') button.title = row.advice || row.error || '請使用正確的目錄或修復技能後再掃描';
    action.append(button); tr.append(cell,status,action); $('rows').append(tr);
  }
  controls();
}
function patch(container, title, content, open=false) {
  const detail = node('details'); detail.open = open; detail.append(node('summary', title));
  const pre = node('pre');
  for(const line of (content || '（沒有文字差異；二進位或過大的檔案請比較雜湊。）').split('\n')) {
    pre.append(node('span',line + '\n',line.startsWith('+') ? 'diff-added' : line.startsWith('-') ? 'diff-removed' : line.startsWith('@@') ? 'diff-hunk':''));
  }
  detail.append(pre); container.append(detail);
}
function renderDiff(comparison) {
  $('diffs').replaceChildren();
  if(!comparison) { $('diffs').append(node('p','目前沒有選定的上游來源。','muted')); return; }
  if(!comparison.files.length) $('diffs').append(node('p','沒有檔案內容差異。','muted'));
  if(!comparison.baseline_available) $('diffs').append(node('p','尚無可驗證的歷史基準；目前顯示本機與上游的直接比較。','muted'));
  for(const file of comparison.files) {
    const section = node('section'); section.append(node('h3',file.file));
    for(const [key,title] of [['local_vs_upstream','本機 → 上游'],['base_to_local','基準 → 本機'],['base_to_upstream','基準 → 上游']]) {
      if(file[key]) patch(section,title,file[key].diff,key === 'local_vs_upstream');
    }
    $('diffs').append(section);
  }
}
async function selectSkill(row) {
  selected = row; clearPlan(); $('welcome').hidden = true; $('selected').hidden = false;
  $('skill-name').textContent = row.name; $('skill-path').textContent = row.path || '';
  $('reason').textContent = reasons[row.status] || row.status; $('revision').textContent = '';
  $('diffs').textContent = '讀取差異中…'; $('hashes').replaceChildren(); renderRows();
  const data = await api('/api/detail?' + new URLSearchParams({target:target().id,name:row.name}));
  renderDiff(data.comparison);
  $('revision').textContent = '安裝來源版本：' + (data.receipt?.source_revision?.commit || '未記錄 Git 版本');
  $('hashes').append(node('pre',JSON.stringify({local:data.local_files,upstream:data.upstream_files,receipt:data.receipt},null,2)));
}
async function load(preserve=false) {
  const oldName = preserve ? selected?.name : null;
  clearPlan(); selected = null; fresh = false;
  if(!inventory) {
    notice('正在連線並載入管理位置…');
    inventory = await api('/api/config');
    $('source').textContent = inventory.source; $('target').replaceChildren();
    for(const t of inventory.targets) { const option = node('option',t.label); option.value = t.id; $('target').append(option); }
    try {
      const previous = sessionStorage.getItem('skillctl-target-path');
      const remembered = inventory.targets.find(t=>t.path === previous);
      if(remembered) $('target').value = remembered.id;
    } catch(_) { /* Storage may be disabled; scanning still works. */ }
  }
  const t = target();
  try { sessionStorage.setItem('skillctl-target-path',t.path); } catch(_) {}
  $('welcome').hidden = false; $('selected').hidden = true; renderRows();
  notice(`正在掃描 ${t.label} 並核對檔案雜湊…${t.skills ? ' 下方為上次結果，完成後更新。' : ' 數量待確認，尚未載入不代表沒有技能。'}`);
  const result = await api('/api/inventory?' + new URLSearchParams({target:t.id}));
  const scanned = result.targets[0];
  if(scanned.error) throw new Error(scanned.error);
  Object.assign(t,scanned); fresh=true; renderRows();
  notice(`已完成 ${t.label} 掃描（${new Date().toLocaleTimeString()}）。統計僅涵蓋此位置。`);
  const row = target()?.skills.find(r => r.name === oldName);
  if(row) await selectSkill(row);
}
async function preview(action) {
  clearPlan(); notice('正在計算預覽，尚未修改技能…');
  const result = await api('/api/preview',{target:target().id,name:selected.name,action});
  plan = result; $('preview').hidden = false; $('preview').classList.toggle('blocked',!result.plan_id);
  $('plan-title').textContent = ({adopt:'接管現有技能',keep:'保留全部本機內容',merge:'合併上游更新'})[action];
  const ok = !!result.plan_id;
  $('plan-summary').textContent = !ok ? '合併被擋：以下衝突需要先處理。尚未修改任何技能檔案。' : action === 'adopt' ?
    '建立收據與備份，原檔案內容不變。選定的上游將成為接管當下的比較基準。' : action === 'keep' ?
    '全部本機內容不變；這次上游差異會略過，並建立新的比較基準。之後仍保護自訂內容。' :
    `將套用 ${result.result.changes.length} 個檔案變更，保留不衝突的本機修改，並建立 rollback 備份。`;
  $('plan-changes').replaceChildren();
  for(const change of result.result.changes || []) patch($('plan-changes'),change.file,change.diff,true);
  for(const conflict of result.result.conflicts || []) {
    $('plan-changes').append(node('p',conflict.file + '：' + conflict.reason,'conflict'));
    if(conflict.preview) patch($('plan-changes'),'衝突內容',conflict.preview,true);
  }
  if(result.comparison) renderDiff(result.comparison);
  $('plan-expiry').textContent = ok ? '預覽 10 分鐘內有效。套用前會重新核對檔案與收據；內容有變就必須重新預覽。' : '可取消後選擇保留本機，或在本機手動處理衝突後重新預覽。';
  notice(ok ? '預覽已完成；檢查下方結果後，才會開放套用。' : '預覽發現衝突，無法套用。',!ok);
  $('preview').scrollIntoView({block:'nearest',behavior:'smooth'}); controls();
}
$('query').addEventListener('input',renderRows);
$('target').addEventListener('change',()=>task(()=>load()));
$('refresh').addEventListener('click',()=>task(async()=>{notice('');await load();}));
for(const action of ['adopt','keep','merge']) $(action).addEventListener('click',()=>task(()=>preview(action)));
$('cancel').addEventListener('click',()=>{clearPlan();controls();notice('已取消預覽；尚未套用。');});
$('apply').addEventListener('click',()=>task(async()=>{
  const id=plan.plan_id;clearPlan();
  const result=await api('/api/apply',{plan_id:id});
  await load(true);
  notice('已套用：' + ({adopted:'接管完成，原檔保留。',kept:'已保留本機內容。',merged:'合併完成，本機修改已保留。'}[result.result.action] || result.result.action));
}));
task(()=>load());
