/* Whole-namespace ingestion is planned from the cached theorem index, then split
   into ordinary durable ImportTasks. The child tasks run sequentially so a large
   namespace never starts many Lean workers at once. */
const namespaceImportState={planning:false,creating:false,queueRunning:false,error:'',created:0,totalTasks:0};
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
  if(namespaceImportState.planning)return 'Planning namespace…';
  if(namespaceImportState.creating)return `Creating durable tasks ${namespaceImportState.created}/${namespaceImportState.totalTasks||'…'}…`;
  if(namespaceImportState.queueRunning)return 'Namespace queue running sequentially…';
  return namespaceImportState.error||'';
}

function mountNamespaceImportControl(){
  const result=libraryBrowserState.result;
  const actions=document.querySelector('.library-selection-actions');
  if(!result||!actions||actions.querySelector('[data-library-import-namespace]'))return;
  const wrapper=document.createElement('div');wrapper.className='namespace-import-control';
  const button=document.createElement('button');button.className='btn';button.dataset.libraryImportNamespace='';
  button.textContent=`Import namespace (${Number(result.total_theorems||0).toLocaleString()})`;
  button.title=result.index_complete?`Create durable tasks for all unregistered theorem declarations under ${result.namespace}`:'Whole-namespace import requires a complete theorem index; use a narrower namespace.';
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

async function runNamespaceTaskQueue(taskIds){
  if(namespaceImportState.queueRunning)return;
  namespaceImportState.queueRunning=true;namespaceImportState.error='';renderLibraryBrowser();
  try{
    for(const taskId of taskIds){
      if(!importTaskState.open)break;
      importTaskState.selected=taskId;renderImportTasks();
      await runImportTask(taskId);
      const task=await fetchImportTask(taskId);
      if(!importTaskState.open||task.status==='paused'||task.status==='cancelled')break;
    }
  }catch(error){namespaceImportState.error=error.message||String(error);importTaskState.error=namespaceImportState.error;}
  finally{namespaceImportState.queueRunning=false;renderImportTasks();renderLibraryBrowser();}
}

async function importWholeNamespace(){
  if(namespaceImportState.planning||namespaceImportState.creating)return;
  namespaceImportState.planning=true;namespaceImportState.error='';libraryBrowserState.error='';renderLibraryBrowser();
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
  const accepted=window.confirm(
    `Import the entire ${plan.namespace} namespace?\n\n`+
    `${roots.toLocaleString()} unregistered theorem roots will be split into ${taskCount} durable tasks.\n`+
    `${known.toLocaleString()} theorem declarations are already registered and will be skipped.\n`+
    `Tasks run sequentially; closing the task drawer stops auto-continue after the current durable step.`
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
    namespaceImportState.creating=false;libraryBrowserState.importing=false;libraryBrowserState.open=false;
    importTaskState.open=true;importTaskState.selected=taskIds[0]||'';renderLibraryBrowser();renderImportTasks();
    if(taskIds.length)void runNamespaceTaskQueue(taskIds);
  }catch(error){
    namespaceImportState.creating=false;libraryBrowserState.importing=false;namespaceImportState.error=error.message||String(error);libraryBrowserState.error=namespaceImportState.error;renderLibraryBrowser();
  }
}

mountNamespaceImportControl();
