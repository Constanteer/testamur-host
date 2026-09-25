/* Durable import-task UI. The server advances active tasks in the background.
   This drawer observes progress and exposes pause/retry/budget controls; closing
   it never stops the server worker. */
const importTaskState={open:false,loading:false,error:'',tasks:[],selected:'',running:new Set(),stopRequested:new Set()};
let importTaskPollTimer=null;

function importTaskById(id){return importTaskState.tasks.find(task=>task.task_id===id)||null;}
function upsertImportTask(task){
  if(!task?.task_id)return;
  const index=importTaskState.tasks.findIndex(item=>item.task_id===task.task_id);
  if(index>=0)importTaskState.tasks[index]={...importTaskState.tasks[index],...task};else importTaskState.tasks.unshift(task);
  importTaskState.tasks.sort((a,b)=>String(b.updated_at||'').localeCompare(String(a.updated_at||'')));
}
function stopLocalImportRunners(){for(const id of importTaskState.running)importTaskState.stopRequested.add(id);}

async function loadImportTasks(){
  importTaskState.loading=true;importTaskState.error='';renderImportTasks();
  try{
    const result=await apiJson('/api/import-tasks?limit=40');
    importTaskState.tasks=result.tasks||[];
    if(importTaskState.selected&&!importTaskById(importTaskState.selected))importTaskState.selected='';
    if(!importTaskState.selected&&importTaskState.tasks.length)importTaskState.selected=importTaskState.tasks[0].task_id;
    if(importTaskState.selected)await fetchImportTask(importTaskState.selected);
  }catch(error){importTaskState.error=error.message||String(error);}
  finally{importTaskState.loading=false;renderImportTasks();}
}

async function fetchImportTask(taskId){
  const task=await apiJson(`/api/import-tasks/${encodeURIComponent(taskId)}`);upsertImportTask(task);return task;
}

async function createImportTask(payload,{open=true,run=false}={}){
  const task=await apiJson('/api/import-tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  upsertImportTask(task);importTaskState.selected=task.task_id;
  if(open){importTaskState.open=true;renderImportTasks();}
  window.dispatchEvent(new CustomEvent('mathhub:import-task-created',{detail:{task}}));
  if(run)void runImportTask(task.task_id);
  return task;
}

async function importTaskAction(taskId,action,body=null){
  const options={method:'POST'};
  if(body!==null){options.headers={'Content-Type':'application/json'};options.body=JSON.stringify(body);}
  const task=await apiJson(`/api/import-tasks/${encodeURIComponent(taskId)}/${action}`,options);
  upsertImportTask(task);window.dispatchEvent(new CustomEvent('mathhub:import-task-progress',{detail:{task,action}}));return task;
}

async function refreshReadsAfterTaskStep(task){
  try{argumentState?.cache?.clear?.();}catch(_){}
  try{registryState?.details?.clear?.();}catch(_){}
  try{if(typeof loadCatalogPage==='function')await loadCatalogPage({reset:true});}catch(_){}
  if(task?.status==='completed'||task?.status==='paused'||task?.status==='cancelled'){
    try{if(state?.selected&&typeof loadArgument==='function')await loadArgument(state.selected,{force:true});}catch(_){}
  }
}

async function runImportTask(taskId,{oneStep=false}={}){
  if(importTaskState.running.has(taskId))return;
  importTaskState.stopRequested.delete(taskId);importTaskState.running.add(taskId);importTaskState.error='';renderImportTasks();
  try{
    let task=importTaskById(taskId)||await fetchImportTask(taskId);
    while(task.status==='active'){
      task=await importTaskAction(taskId,'step');
      await refreshReadsAfterTaskStep(task);renderImportTasks();
      if(oneStep||importTaskState.stopRequested.has(taskId)||task.status!=='active')break;
      if(Number(task.frontier_count||0)+Number(task.classification_pending_count||0)<=0)break;
    }
  }catch(error){importTaskState.error=error.message||String(error);try{await fetchImportTask(taskId);}catch(_){} }
  finally{importTaskState.stopRequested.delete(taskId);importTaskState.running.delete(taskId);renderImportTasks();}
}

function taskStatusLabel(task){
  if(importTaskState.running.has(task.task_id))return 'running';
  if(task.leased)return 'worker leased';
  return task.status||'unknown';
}
function taskProgressText(task){
  const processed=Number(task.processed_claims||0),scheduled=Number(task.scheduled_claims||0),frontier=Number(task.frontier_count||0),deferred=Number(task.deferred_count||0),classifying=Number(task.classification_pending_count||0);
  return `${processed.toLocaleString()} processed · ${scheduled.toLocaleString()} scheduled · ${frontier.toLocaleString()} frontier${classifying?` · ${classifying.toLocaleString()} awaiting classification`:''}${deferred?` · ${deferred.toLocaleString()} deferred`:''}`;
}
function taskTitle(task){const roots=task.roots||[];return roots.length===1?roots[0]:`${roots[0]||'Import'} + ${Math.max(0,roots.length-1)} roots`;}

function taskListHtml(){
  if(importTaskState.loading&&!importTaskState.tasks.length)return '<div class="task-empty">Loading import tasks…</div>';
  if(!importTaskState.tasks.length)return '<div class="task-empty"><strong>No materialization tasks yet.</strong><span>These appear only when you explicitly materialize existing Lean declarations into durable workspace records.</span></div>';
  return importTaskState.tasks.map(task=>`<button class="task-list-item ${task.task_id===importTaskState.selected?'selected':''}" data-task-select="${esc(task.task_id)}"><div class="task-list-title"><code>${esc(taskTitle(task))}</code><span class="task-status ${esc(task.status||'')}">${esc(taskStatusLabel(task))}</span></div><small>${esc(taskProgressText(task))}</small></button>`).join('');
}

function taskFailureHtml(task){
  const failed=task.failed_items||[];if(!failed.length)return '';
  return `<details class="task-failures"><summary>${Number(task.failed||failed.length).toLocaleString()} failed theorem${Number(task.failed||failed.length)===1?'':'s'}</summary>${failed.slice(0,40).map(item=>`<div><code>${esc(item.declaration_name||'')}</code><span>${esc(item.error||'failed')}</span></div>`).join('')}${task.failed_items_truncated?'<div class="task-note">Failure list is truncated in this view.</div>':''}</details>`;
}

function taskDetailHtml(task){
  if(!task)return '<div class="task-detail-empty">Select an import task.</div>';
  const running=importTaskState.running.has(task.task_id),active=task.status==='active',paused=task.status==='paused',terminal=['completed','cancelled'].includes(task.status),leased=Boolean(task.leased);
  const roots=task.roots||[];const progressBase=Math.max(Number(task.scheduled_claims||0),1);const pct=Math.min(100,Math.round(100*Number(task.processed_claims||0)/progressBase));
  const lastError=task.last_error?`<div class="task-error"><strong>Last step stopped:</strong> ${esc(task.last_error)}</div>`:'';
  const leaseNote=paused&&leased?'<div class="task-note">A step is still finishing under its worker lease. Pause prevents another step from starting after it commits.</div>':'';
  return `<div class="task-detail"><header><div><div class="eyebrow">${esc(task.task_id)}</div><h3>${esc(taskTitle(task))}</h3><p>${esc(taskProgressText(task))}</p></div><span class="task-status large ${esc(task.status||'')}">${esc(taskStatusLabel(task))}</span></header>${lastError}${leaseNote}<div class="task-meter"><span style="width:${pct}%"></span></div><div class="task-stats"><div><strong>${Number(task.imported||0).toLocaleString()}</strong><span>imported</span></div><div><strong>${Number(task.already_registered||0).toLocaleString()}</strong><span>already known</span></div><div><strong>${Number(task.failed||0).toLocaleString()}</strong><span>failed</span></div><div><strong>${Number(task.lean_invocations||0).toLocaleString()}</strong><span>Lean processes</span></div><div><strong>${Number(task.substrate_ref_count||0).toLocaleString()}</strong><span>substrate refs</span></div><div><strong>${Number(task.steps||0).toLocaleString()}</strong><span>durable steps</span></div></div><div class="task-budget"><span>Depth ≤ <strong>${task.max_depth}</strong></span><span>Claims ≤ <strong>${Number(task.max_claims||0).toLocaleString()}</strong></span>${task.truncated?'<span class="task-boundary">boundary reached</span>':''}</div><div class="task-actions">${active?`<button class="btn" data-task-pause="${esc(task.task_id)}">Pause</button>`:''}${paused&&!leased?`<button class="btn btn-primary" data-task-resume="${esc(task.task_id)}">Resume background import</button>`:''}${leased&&!running?`<button class="btn" data-task-recover="${esc(task.task_id)}">Recover stale lease</button>`:''}${task.failed?`<button class="btn" data-task-retry="${esc(task.task_id)}" ${leased?'disabled':''}>Retry failed</button>`:''}${task.max_depth<5?`<button class="btn" data-task-depth="${esc(task.task_id)}" ${leased?'disabled':''}>+1 depth</button>`:''}${task.max_claims<5000?`<button class="btn" data-task-budget="${esc(task.task_id)}" ${leased?'disabled':''}>+160 Claim budget</button>`:''}${!terminal?`<button class="btn danger" data-task-cancel="${esc(task.task_id)}">Cancel</button>`:''}</div><details class="task-roots" open><summary>${roots.length} root theorem${roots.length===1?'':'s'}</summary>${roots.slice(0,60).map(name=>`<code>${esc(name)}</code>`).join('')}${roots.length>60?`<span>${roots.length-60} more…</span>`:''}</details>${taskFailureHtml(task)}${task.deferred_count?`<div class="task-note">${Number(task.deferred_count).toLocaleString()} theorem dependencies are preserved beyond the current depth/Claim budget. Increase the budget to continue without rescanning roots.</div>`:''}${task.classification_pending_count?`<div class="task-note">${Number(task.classification_pending_count).toLocaleString()} unresolved constants are queued for bounded Lean classification in later durable steps.</div>`:''}</div>`;
}

function importTasksHtml(){
  const selected=importTaskById(importTaskState.selected);
  return `<div class="task-backdrop" data-task-close-backdrop><section class="task-drawer" role="dialog" aria-modal="true" aria-label="Import tasks"><header class="task-drawer-head"><div><div class="eyebrow">Background activity</div><h2>Library imports</h2><p>MathHub continues active imports on the server, even after you close this panel. Technical controls are here only when you need them.</p></div><button class="task-close" data-task-close aria-label="Close">×</button></header>${importTaskState.error?`<div class="task-error">${esc(importTaskState.error)}</div>`:''}<div class="task-layout"><aside class="task-list">${taskListHtml()}</aside><main>${taskDetailHtml(selected)}</main></div></section></div>`;
}

function closeImportTasks(){stopLocalImportRunners();if(importTaskPollTimer){clearInterval(importTaskPollTimer);importTaskPollTimer=null;}importTaskState.open=false;renderImportTasks();}
function renderImportTasks(){
  document.querySelector('#mathhub-import-tasks')?.remove();mountImportTasksButton();if(!importTaskState.open)return;
  const host=document.createElement('div');host.id='mathhub-import-tasks';host.innerHTML=importTasksHtml();document.body.appendChild(host);bindImportTasks(host);
}

function bindImportTasks(host){
  host.querySelector('[data-task-close]')?.addEventListener('click',closeImportTasks);
  host.querySelector('[data-task-close-backdrop]')?.addEventListener('click',event=>{if(event.target===event.currentTarget)closeImportTasks();});
  host.querySelectorAll('[data-task-select]').forEach(button=>button.addEventListener('click',async()=>{const id=button.dataset.taskSelect||'';importTaskState.selected=id;renderImportTasks();try{await fetchImportTask(id);}catch(error){importTaskState.error=error.message||String(error);}renderImportTasks();}));
  host.querySelectorAll('[data-task-run]').forEach(button=>button.addEventListener('click',()=>runImportTask(button.dataset.taskRun||'')));
  host.querySelectorAll('[data-task-step]').forEach(button=>button.addEventListener('click',()=>runImportTask(button.dataset.taskStep||'',{oneStep:true})));
  host.querySelectorAll('[data-task-pause]').forEach(button=>button.addEventListener('click',async()=>{try{const id=button.dataset.taskPause||'';importTaskState.stopRequested.add(id);upsertImportTask(await importTaskAction(id,'pause'));renderImportTasks();}catch(error){importTaskState.error=error.message||String(error);renderImportTasks();}}));
  host.querySelectorAll('[data-task-resume]').forEach(button=>button.addEventListener('click',async()=>{try{const id=button.dataset.taskResume||'';upsertImportTask(await importTaskAction(id,'resume'));renderImportTasks();}catch(error){importTaskState.error=error.message||String(error);renderImportTasks();}}));
  host.querySelectorAll('[data-task-recover]').forEach(button=>button.addEventListener('click',async()=>{const id=button.dataset.taskRecover||'';if(!window.confirm('Clear this worker lease only if the previous server/process step is no longer running. Continue?'))return;try{upsertImportTask(await importTaskAction(id,'recover'));renderImportTasks();}catch(error){importTaskState.error=error.message||String(error);renderImportTasks();}}));
  host.querySelectorAll('[data-task-retry]').forEach(button=>button.addEventListener('click',async()=>{try{const id=button.dataset.taskRetry||'';upsertImportTask(await importTaskAction(id,'retry'));renderImportTasks();}catch(error){importTaskState.error=error.message||String(error);renderImportTasks();}}));
  host.querySelectorAll('[data-task-depth]').forEach(button=>button.addEventListener('click',async()=>{try{const id=button.dataset.taskDepth||'',task=importTaskById(id);upsertImportTask(await importTaskAction(id,'extend',{max_depth:Number(task?.max_depth||0)+1}));renderImportTasks();}catch(error){importTaskState.error=error.message||String(error);renderImportTasks();}}));
  host.querySelectorAll('[data-task-budget]').forEach(button=>button.addEventListener('click',async()=>{try{const id=button.dataset.taskBudget||'',task=importTaskById(id);upsertImportTask(await importTaskAction(id,'extend',{max_claims:Number(task?.max_claims||0)+160}));renderImportTasks();}catch(error){importTaskState.error=error.message||String(error);renderImportTasks();}}));
  host.querySelectorAll('[data-task-cancel]').forEach(button=>button.addEventListener('click',async()=>{try{const id=button.dataset.taskCancel||'';importTaskState.stopRequested.add(id);upsertImportTask(await importTaskAction(id,'cancel'));renderImportTasks();}catch(error){importTaskState.error=error.message||String(error);renderImportTasks();}}));
}

function openImportTasks(taskId=''){importTaskState.open=true;if(taskId)importTaskState.selected=taskId;renderImportTasks();void loadImportTasks();if(importTaskPollTimer)clearInterval(importTaskPollTimer);importTaskPollTimer=setInterval(()=>{if(importTaskState.open&&!importTaskState.loading)void loadImportTasks();},2500);}
function mountImportTasksButton(){
  // Background task controls are intentionally kept out of the primary navigation.
  // They remain available from Browse library -> Advanced Lean settings.
  return;
}
const importTaskBaseBind=bind;
bind=function bindImportTasksGlobal(){importTaskBaseBind();mountImportTasksButton();};
window.addEventListener('keydown',event=>{if(event.key==='Escape'&&importTaskState.open)closeImportTasks();});
mountImportTasksButton();
