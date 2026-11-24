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
  missing:'此位置尚未安裝。選擇下方目的位置，先預覽新增檔案，再套用安裝。'};
let inventory, selected, plan, busy = false;
let fresh = false;
let invocation = null;
let activeFilter = 'all', comparison = null, activeFile = null, diffMode = 'unified';
const matchesFilter = (row, filter) => ({
  all: true, source: row.has_source, unmanaged: row.present && !row.managed,
  modified: ['local-drift','customized','unmanaged-conflict'].includes(row.status),
  updates: row.status === 'update-available',
  attention: ['invalid','linked','nested'].includes(row.status),
  missing: !row.present && row.has_source && row.status !== 'linked'
})[filter];
const target = () => inventory?.targets.find(t => t.id === $('target').value);
function node(tag, text, cls) { const el = document.createElement(tag); if(text !== undefined) el.textContent = text; if(cls) el.className = cls; return el; }
function notice(text, error=false) { $('notice').textContent = text; $('notice').className = error ? 'error' : ''; }
function clearPlan() { plan = null; $('preview').hidden = true; $('apply').disabled = true; }
function controls() {
  $('refresh').disabled = busy; $('target').disabled = busy;
  $('doctor').disabled=busy||!fresh;$('doctor-skill').disabled=busy||!fresh||!selected;
  $('close-doctor').disabled=busy;$('install-target').disabled=busy||!selected;
  const elsewhere=$('install-target').value!==target()?.id;
  $('install').disabled=busy||!fresh||!selected?.has_source||(!elsewhere&&!['missing','update-available'].includes(selected?.status));
  $('install').textContent=!elsewhere&&selected?.status==='update-available'?'預覽更新':'預覽安裝';
  $('rollback').disabled=busy||!fresh||!selected?.rollback_depth||['nested','linked'].includes(selected?.status);
  $('copy-invoke').disabled=busy||!fresh||!invocation?.installed;
  $('unified').disabled=busy||!comparison;$('split').disabled=busy||!comparison;
  document.querySelectorAll('#filters button').forEach(b=>b.disabled=busy);
  $('close-detail').disabled = busy; $('reset-search').disabled = busy;
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
    const data = await response.json(); if(!response.ok) {
      const message=data.error||'請求失敗';
      const advice=[['Local drift','本機內容已修改，直接安裝或復原可能覆蓋修改。請先檢視差異，再選擇保留或合併。'],
        ['Unmanaged installation','目的位置已有未納管副本。請切換到該位置，先檢查並接管。'],
        ['Preserved local customizations','此副本保留了自訂內容。請使用保留或合併處理上游更新。'],
        ['No rollback snapshot','此技能尚無可用的復原紀錄。'],['rollback snapshot','復原備份已改變或無效，無法安全復原。'],
        ['Operation lock','此位置有其他操作進行中。請等待完成後重新預覽。']].find(([key])=>message.includes(key));
      throw new Error(advice?advice[1]+'（'+message+'）':message);
    } return data;
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
  const rows = (t?.skills || []).filter(r => matchesFilter(r,activeFilter) && [r.name,r.description,labels[r.status]].join(' ').toLocaleLowerCase().includes(query));
  document.querySelectorAll('#filters button').forEach(b=>{
    b.setAttribute('aria-pressed',String(b.dataset.filter===activeFilter));
    b.querySelector('.filter-count').textContent=t?.skills ? t.skills.filter(r=>matchesFilter(r,b.dataset.filter)).length : '—';
  });
  $('count').textContent = t?.skills ? `顯示 ${rows.length} / ${t.skills.length}` : '待掃描';
  $('empty').hidden = !t?.skills || rows.length > 0;
  const c = t?.counts;
  $('metrics').replaceChildren();
  for(const [key,title,note] of [['installed','本機已有','找到的技能'],['managed','已納管','已有管理收據'],['unmanaged','未納管','可先檢查再接管'],['missing','尚未安裝','僅在上游來源']]) {
    const card=node('div',undefined,'metric');card.append(node('span',title,'metric-label'),node('span',c ? c[key] : '—','metric-value'),node('span',note,'metric-note'));$('metrics').append(card);
  }
  $('stats').textContent = c ? `本機已有 ${c.installed} · 已納管 ${c.managed} · 未納管 ${c.unmanaged} · 尚未安裝 ${c.missing} · 需檢查 ${c.attention}` : '尚未完成掃描，數量待確認。';
  if(t?.error) notice(t.error, true);
  for(const row of rows) {
    const tr = node('tr', undefined, selected?.path === row.path ? 'active-row':'');
    const cell = node('td'); cell.title=row.name+'\n'+(row.description || row.error || '');cell.append(node('div',row.name,'skill-label'),node('div',row.description || row.error || '尚無用途說明', 'description'));
    const status = node('td'); status.append(node('span',labels[row.status] || row.status, 'badge ' + (row.status === 'invalid' ? 'bad' : /drift|conflict|customized/.test(row.status) ? 'warn':'')));
    const action = node('td'); const button = node('button','↗'); button.type = 'button'; button.setAttribute('aria-label','檢視 ' + row.name);
    button.dataset.unavailable = String(['nested','linked'].includes(row.status) || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(row.name));
    button.addEventListener('click', () => task(() => selectSkill(row)));
    tr.addEventListener('click',event=>{if(!event.target.closest('button')&&!button.disabled)button.click();});
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
function splitPatch(container,title,content,left,right) {
  if(!content || !/^@@ /m.test(content)) { patch(container,title,content,true); return; }
  const detail=node('details');detail.open=true;detail.append(node('summary',title));
  const table=node('table',undefined,'split-table');const head=node('thead'),heading=node('tr');
  heading.append(node('th',left),node('th',right));head.append(heading);table.append(head);
  const body=node('tbody');let removed=[],added=[],inHunk=false;
  const flush=()=>{for(let i=0;i<Math.max(removed.length,added.length);i++) {
    const row=node('tr');row.append(node('td',removed[i]||'',removed[i]?'diff-removed':''),node('td',added[i]||'',added[i]?'diff-added':''));body.append(row);
  }removed=[];added=[];};
  for(const line of content.split('\n')) {
    if(line.startsWith('@@') || (inHunk && line.startsWith('\\'))) {
      flush();inHunk=true;const row=node('tr'),cell=node('td',line,'diff-hunk');cell.colSpan=2;row.append(cell);body.append(row);
    } else if(inHunk && line.startsWith('-')) removed.push(line);
    else if(inHunk && line.startsWith('+')) added.push(line);
    else if(inHunk && line.startsWith(' ')) {flush();const row=node('tr');row.append(node('td',line),node('td',line));body.append(row);}
  }
  flush();table.append(body);detail.append(table);container.append(detail);
}
function renderDiff(value) {
  comparison=value;
  $('diffs').replaceChildren();
  if(!comparison) { $('diffs').append(node('p','目前沒有選定的上游來源。','muted')); return; }
  if(!comparison.files.length) $('diffs').append(node('p','沒有檔案內容差異。','muted'));
  if(!comparison.baseline_available) $('diffs').append(node('p','尚無可驗證的歷史基準；目前顯示本機與上游的直接比較。','muted'));
  if(!comparison.files.length)return;
  if(!comparison.files.some(f=>f.file===activeFile))activeFile=comparison.files[0].file;
  const layout=node('div',undefined,'diff-layout'),nav=node('nav',undefined,'file-list'),section=node('section',undefined,'file-body');nav.setAttribute('aria-label','變更檔案');
  for(const file of comparison.files) {
    const button=node('button',file.file);button.setAttribute('aria-pressed',String(file.file===activeFile));
    button.addEventListener('click',()=>{activeFile=file.file;renderDiff(comparison);Array.from($('diffs').querySelectorAll('.file-list button')).find(b=>b.textContent===activeFile)?.focus({preventScroll:true});});nav.append(button);
  }
  const file=comparison.files.find(f=>f.file===activeFile);section.append(node('h3',file.file));
  for(const [key,title,left,right] of [['local_vs_upstream','本機 → 上游','本機內容','上游內容'],['base_to_local','基準 → 本機','基準內容','本機內容'],['base_to_upstream','基準 → 上游','基準內容','上游內容']]) {
    if(file[key]) {if(diffMode==='split')splitPatch(section,title,file[key].diff,left,right);else patch(section,title,file[key].diff,key==='local_vs_upstream');}
  }
  layout.append(nav,section);$('diffs').append(layout);
}
async function selectSkill(row) {
  notice('正在讀取 '+row.name+' 的差異…');
  selected = row; activeFile=null;comparison=null;clearPlan(); $('welcome').hidden = true; $('selected').hidden = false;
  invocation=null;$('invoke-prompt').value='';$('invoke-status').textContent='尚未完成檢查。';
  $('install-target').replaceChildren();
  for(const t of inventory.targets){const option=node('option',t.label+(t.agent?' · '+t.agent:''));option.value=t.id;$('install-target').append(option);}
  $('install-target').value=target().id;installLocation();
  $('rollback-info').textContent=row.rollback_depth?`可復原 ${row.rollback_depth} 次；每次還原最近一筆變更，先預覽影響。`:'尚無可復原紀錄。';
  $('workspace').classList.add('has-selection');$('skill-description').textContent=row.description||row.error||'';
  document.querySelector('.detail').scrollTop=0;
  $('skill-name').textContent = row.name; $('skill-path').textContent = row.path || '';
  $('reason').textContent = reasons[row.status] || row.status; $('revision').textContent = '';
  $('diffs').textContent = '讀取差異中…'; $('hashes').replaceChildren(); renderRows();
  if(row.status==='invalid') {
    $('reason').textContent=row.error||'檔案結構需要檢查。';$('diffs').textContent='結構檢查未通過，請先查看診斷報告。';
    $('invoke-status').textContent='結構檢查未通過；請先修正診斷問題。未驗證 agent 載入。';await diagnose(row.name);return;
  }
  const data = await api('/api/detail?' + new URLSearchParams({target:target().id,name:row.name}));
  invocation=data.invocation;
  $('invoke-prompt').value=invocation.prompt;
  $('invoke-status').textContent=(invocation.installed?'SKILL.md 結構檢查通過':'尚未安裝或結構檢查未通過')+' · 未驗證 agent 載入。'+(invocation.agent?' 對應 '+invocation.agent+'。':' 目前目錄無法判定 harness；請核對 agent 的技能搜尋位置。');
  renderDiff(data.comparison);
  $('revision').textContent = '安裝來源版本：' + (data.receipt?.source_revision?.commit || '未記錄 Git 版本');
  $('hashes').append(node('pre',JSON.stringify({local:data.local_files,upstream:data.upstream_files,receipt:data.receipt},null,2)));
  notice('已載入 '+row.name+'；檢查差異後再預覽。');
  $('skill-name').focus({preventScroll:true});
}
async function load(preserve=false) {
  const oldName = preserve ? selected?.name : null;
  clearPlan(); selected = null; fresh = false;
  invocation=null;$('doctor-report').hidden=true;
  $('workspace').classList.remove('has-selection','expanded');$('expand-detail').setAttribute('aria-pressed','false');$('expand-detail').textContent='展開詳情';
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
function installLocation() {
  const destination=inventory?.targets.find(t=>t.id===$('install-target').value);
  $('install-location').textContent=destination ? destination.path+' / '+selected.name : '';
}
const diagnosticAdvice={...reasons,invalid:'修正以下結構或引用問題後重新診斷；此頁不會自動改寫。',linked:'這是連結目錄。請以啟動參數配置其實體位置再管理。',nested:'技能位於巢狀目錄。請配置實際包含技能的目錄。'};
async function diagnose(name=null) {
  clearPlan();notice('正在檢查檔案、收據及已配置位置的同名副本…');
  const params={target:target().id};if(name)params.name=name;
  const report=await api('/api/doctor?'+new URLSearchParams(params));
  const container=$('doctor-results');container.replaceChildren();
  container.append(node('p',report.target.label+' · 檢查 '+report.skills.length+' 個技能'),node('p',report.scope,'scope-note'));
  if(report.operation_lock)container.append(node('p','目前有操作鎖定：'+report.operation_lock+'。請確認其他工作是否正在執行。','diagnostic-error'));
  for(const skill of report.skills) {
    const item=node('details');item.open=!!skill.error||skill.copies.length>1;
    const summary=node('summary',skill.name);summary.append(node('span',labels[skill.status]||skill.status,'diagnostic-status'));item.append(summary);
    item.append(node('p',diagnosticAdvice[skill.status]||skill.advice||'請檢查此技能狀態。'));
    if(skill.error)item.append(node('p',skill.error,'diagnostic-error'));
    if(skill.copies.length>1)item.append(node('p',`找到 ${skill.copies.length} 個同名副本；${skill.duplicate_content_equal?'檔案內容一致':'檔案內容不同或無法驗證'}。請核對 agent 搜尋位置，這不代表已確認載入衝突。`));
    for(const copy of skill.copies){item.append(node('p',copy.path,'path'));if(copy.error)item.append(node('p',copy.error,'diagnostic-error'));}
    container.append(item);
  }
  $('doctor-report').hidden=false;notice('診斷完成；未執行技能或變更任何檔案。');
}
async function preview(action) {
  clearPlan(); notice('正在計算預覽，尚未修改技能…');
  const destination=action==='install'?$('install-target').value:target().id;
  const result = await api('/api/preview',{target:destination,name:selected.name,action});
  plan = result; $('preview').hidden = false; $('preview').classList.toggle('blocked',!result.plan_id&&result.result.action!=='unchanged');
  $('plan-title').textContent = ({adopt:'接管現有技能',keep:'保留全部本機內容',merge:'合併上游更新',install:result.result.action==='would-update'?'更新已安裝技能':'安裝技能',rollback:'復原最近一次變更'})[action];
  $('plan-destination').textContent='目的位置：'+result.target.label+' · '+result.target.path+' / '+result.name;
  const ok = !!result.plan_id;
  $('plan-summary').textContent = result.result.action==='unchanged' ? '內容已與上游一致，無需套用。' : !ok ? '合併被擋：以下衝突需要先處理。尚未修改任何技能檔案。' : action === 'install' ?
    `將${result.result.action==='would-update'?'更新':'安裝'} ${result.result.changes.length} 個檔案變更，建立收據與復原紀錄。請核對目的位置。` : action === 'rollback' ?
    (result.result.removes_directory?'這是首次安裝的復原：將移除此技能目錄及管理收據。':result.result.restores_managed?'將還原上一份檔案與管理收據，最近一次變更會被撤回。':'將還原接管前的檔案並取消接管；原有技能仍保留。') : action === 'adopt' ?
    '建立收據與備份，原檔案內容不變。選定的上游將成為接管當下的比較基準。' : action === 'keep' ?
    '全部本機內容不變；這次上游差異會略過，並建立新的比較基準。之後仍保護自訂內容。' :
    `將套用 ${result.result.changes.length} 個檔案變更，保留不衝突的本機修改，並建立 rollback 備份。`;
  $('plan-changes').replaceChildren();
  for(const change of result.result.changes || []) {
    patch($('plan-changes'),change.file,change.diff,true);
    if(!change.diff&&('before_sha256' in change))$('plan-changes').append(node('p','SHA-256：'+(change.before_sha256||'不存在')+' → '+(change.after_sha256||'不存在'),'path'));
  }
  for(const conflict of result.result.conflicts || []) {
    $('plan-changes').append(node('p',conflict.file + '：' + conflict.reason,'conflict'));
    if(conflict.preview) patch($('plan-changes'),'衝突內容',conflict.preview,true);
  }
  if(result.comparison) renderDiff(result.comparison);
  $('plan-expiry').textContent = ok ? '預覽 10 分鐘內有效。套用前會重新核對檔案與收據；內容有變就必須重新預覽。' : '可取消後選擇保留本機，或在本機手動處理衝突後重新預覽。';
  if(result.result.action==='unchanged')$('plan-expiry').textContent='本機已是最新內容，無需套用。';
  notice(result.result.action==='unchanged'?'本機已與上游一致。':ok ? '預覽已完成；檢查下方結果後，才會開放套用。' : '預覽發現衝突，無法套用。',!ok&&result.result.action!=='unchanged');
  $('preview').scrollIntoView({block:'nearest',behavior:'smooth'}); controls();
}
$('query').addEventListener('input',renderRows);
function closeDetail(restoreFocus=false) {
  const name=selected?.name;selected=null;invocation=null;comparison=null;activeFile=null;clearPlan();
  $('selected').hidden=true;$('welcome').hidden=false;$('workspace').classList.remove('has-selection','expanded');
  $('expand-detail').setAttribute('aria-pressed','false');$('expand-detail').textContent='展開詳情';renderRows();
  if(restoreFocus)Array.from($('rows').querySelectorAll('button')).find(b=>b.getAttribute('aria-label')==='檢視 '+name)?.focus();
}
document.querySelectorAll('#filters button').forEach(button=>button.addEventListener('click',()=>{activeFilter=button.dataset.filter;closeDetail();}));
$('reset-search').addEventListener('click',()=>{activeFilter='all';$('query').value='';closeDetail();$('query').focus();});
$('close-detail').addEventListener('click',()=>closeDetail(true));
$('expand-detail').addEventListener('click',()=>{const expanded=$('workspace').classList.toggle('expanded');$('expand-detail').setAttribute('aria-pressed',String(expanded));$('expand-detail').textContent=expanded?'回到清單':'展開詳情';});
for(const mode of ['unified','split'])$(mode).addEventListener('click',()=>{diffMode=mode;for(const key of ['unified','split'])$(key).setAttribute('aria-pressed',String(key===mode));renderDiff(comparison);});
document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();$('query').focus();}if(event.key==='Escape'&&!busy&&selected)closeDetail(true);});
$('target').addEventListener('change',()=>task(()=>load()));
$('refresh').addEventListener('click',()=>task(async()=>{notice('');await load();}));
for(const action of ['adopt','keep','merge','install','rollback']) $(action).addEventListener('click',()=>task(()=>preview(action)));
$('install-target').addEventListener('change',()=>{clearPlan();installLocation();controls();});
$('doctor').addEventListener('click',()=>task(()=>diagnose()));
$('doctor-skill').addEventListener('click',()=>task(()=>diagnose(selected.name)));
$('close-doctor').addEventListener('click',()=>{$('doctor-report').hidden=true;});
$('copy-invoke').addEventListener('click',async()=>{
  try{await navigator.clipboard.writeText($('invoke-prompt').value);notice('已複製唯讀呼叫提示。請貼到對應 agent，並檢查讀取工具紀錄。');}
  catch(_){$('invoke-prompt').focus();$('invoke-prompt').select();notice('無法自動複製；已選取提示，請按 Ctrl+C 或使用複製選單。');}
});
$('cancel').addEventListener('click',()=>{clearPlan();controls();notice('已取消預覽；尚未套用。');});
$('apply').addEventListener('click',()=>task(async()=>{
  const id=plan.plan_id,destination=plan.target_id;clearPlan();
  const result=await api('/api/apply',{plan_id:id});
  if(destination!==target().id){$('target').value=destination;activeFilter='all';$('query').value='';}
  await load(true);
  notice('已套用：' + ({adopted:'接管完成，原檔保留。',kept:'已保留本機內容。',merged:'合併完成，本機修改已保留。',installed:'安裝／更新完成，可檢查呼叫指引。','rolled-back':'已復原最近一次變更。'}[result.result.action] || result.result.action));
}));
task(()=>load());
