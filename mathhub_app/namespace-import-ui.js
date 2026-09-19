/* Whole-area ingestion is planned from the cached theorem index, then split into
   ordinary durable ImportTasks. The server background worker advances them. */
const namespaceImportState={planning:false,creating:false,queueRunning:false,error:'',notice:'',created:0,totalTasks:0};
const NAMESPACE_IMPORT_CHUNK_SIZE=400;

async function loadImportTasks(){
  importTaskState.loading=true;importTaskState.error='';renderImportTasks();
  try{
    const result=await apiJson('/api/import-tasks?limit=100');
    importTaskState.tasks=result.tasks||[];
    if(importTaskState.selected&&!importTaskById(importTaskState.selected))importTaskState.selected='';
    if(!importTaskState.selected&&importTaskState.tasks.length)importTaskState.selected=importTaskState.tasks[0].task_id;
    if(importTaskState.selected)await fetchImportTask(importTaskState.selected);
  }catch(error){importTaskState.error=error.message||String(error);}
  finally{importTaskState.loading=false;renderImportTasks();}
}

function namespaceImportStatusText(){
  if(namespaceImportState.planning)return 'Preparing this area…';
  if(namespaceImportState.creating)return `Adding in the background ${namespaceImportState.created}/${namespaceImportState.totalTasks||'…'}…`;
  return namespaceImportState.error||namespaceImportState.notice||'';
}

function mountNamespaceImportControl(){
  const result=libraryBrowserState.result;
  const actions=document.querySelector('.library-selection-actions');
  if(!result||!actions||actions.querySelector('[data-library-import-namespace]'))return;
  const wrapper=document.createElement('div');wrapper.className='namespace-import-control';
  const button=document.createElement('button');button.className='btn';button.dataset.libraryImportNamespace='';
  const topic=typeof libraryTopicForNamespace==='function'?libraryTopicForNamespace(result.namespace):null;
  button.textContent=`Add all from this area (${Number(result.total_theorems||0).toLocaleString()})`;
  button.title=result.index_complete?`Add every unregistered theorem from ${topic?.label||'this area'} in the background`:'This area is too broad for a complete local index. Choose a more specific area under Advanced settings.';
  button.disabled=!result.index_complete||libraryBrowserState.importing||namespaceImportState.planning||namespaceImportState.creating;
  wrapper.appendChild(button);
  const status=document.createElement('span');status.className='namespace-import-status';status.textContent=namespaceImportStatusText();wrapper.appendChild(status);
  actions.prepend(wrapper);
  button.addEventListener('click',()=>importWholeNamespace());
}

const namespaceImportBaseRenderLibraryBrowser=renderLibraryBrowser;
renderLibraryBrowser=function renderLibraryBrowserWithNamespaceImport(){
  namespaceImportBaseRenderLibraryBrowser();
  mountNamespaceImportControl();
};

async function namespaceIngestionPlan(){
  return apiJson('/api/library/namespace',{
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      namespace:libraryBrowserState.namespace,
      imports:libraryImports(),
      ingestion_plan:true,
      chunk_size:NAMESPACE_IMPORT_CHUNK_SIZE,
    }),
  });
}

async function importWholeNamespace(){
  if(namespaceImportState.planning||namespaceImportState.creating)return;
  namespaceImportState.planning=true;namespaceImportState.error='';namespaceImportState.notice='';libraryBrowserState.error='';renderLibraryBrowser();
  let plan;
  try{
    plan=await namespaceIngestionPlan();
    if(!plan.ok)throw new Error(plan.error||'Namespace ingestion plan failed');
  }catch(error){
    namespaceImportState.error=error.message||String(error);libraryBrowserState.error=namespaceImportState.error;namespaceImportState.planning=false;renderLibraryBrowser();return;
  }
  namespaceImportState.planning=false;
  if(!Number(plan.unregistered_roots||0)){
    namespaceImportState.error=`${plan.namespace} is already fully registered in this Lean environment.`;renderLibraryBrowser();return;
  }

  const taskCount=Number(plan.task_count||0),roots=Number(plan.unregistered_roots||0),known=Number(plan.already_registered||0);
  const topic=typeof libraryTopicForNamespace==='function'?libraryTopicForNamespace(plan.namespace):null;
  const accepted=window.confirm(
    `Add all available statements from ${topic?.label||'this area'}?\n\n`+
    `${roots.toLocaleString()} new theorem statements will be queued in ${taskCount} background batches.\n`+
    `${known.toLocaleString()} are already in MathHub and will be skipped.\n\n`+
    `You can close this page after the queue is created; MathHub will continue automatically.`
  );
  if(!accepted){renderLibraryBrowser();return;}

  namespaceImportState.creating=true;namespaceImportState.created=0;namespaceImportState.totalTasks=taskCount;libraryBrowserState.importing=true;renderLibraryBrowser();
  const taskIds=[];
  try{
    for(const chunk of plan.root_chunks||[]){
      if(!chunk.length)continue;
      const task=await createImportTask({
        declaration_names:chunk,
        imports:plan.imports||libraryImports(),
        max_depth:2,
        max_claims:Math.max(chunk.length,Number(plan.suggested_claim_budget_per_task||chunk.length)),
      },{open:false,run:false});
      if(plan.environment_hash&&task.environment_hash&&plan.environment_hash!==task.environment_hash){
        try{await importTaskAction(task.task_id,'cancel');}catch(_){}
        throw new Error('Lean environment changed while namespace tasks were being created; stop and create a fresh namespace plan.');
      }
      taskIds.push(task.task_id);namespaceImportState.created=taskIds.length;renderLibraryBrowser();
    }
    namespaceImportState.creating=false;libraryBrowserState.importing=false;
    namespaceImportState.notice=taskIds.length
      ?`Added ${taskIds.length} background batch${taskIds.length===1?'':'es'}. MathHub will keep importing even if you leave this page.`
      :'Nothing new needed to be queued.';
    renderLibraryBrowser();
    try{if(typeof loadImportTasks==='function')void loadImportTasks();}catch(_){}
  }catch(error){
    namespaceImportState.creating=false;libraryBrowserState.importing=false;namespaceImportState.error=error.message||String(error);libraryBrowserState.error=namespaceImportState.error;renderLibraryBrowser();
  }
}

mountNamespaceImportControl();
