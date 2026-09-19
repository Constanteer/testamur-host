/* Route existing import entry points through the durable task state machine while
   keeping the old synchronous closure API available for compatibility clients. */

importLibrarySelection=async function taskBackedLibrarySelection(){
  const names=librarySelectedAvailable();if(!names.length||libraryBrowserState.importing)return;
  libraryBrowserState.importing=true;libraryBrowserState.error='';renderLibraryBrowser();
  try{
    const task=await createImportTask({
      declaration_names:names,
      imports:libraryImports(),
      max_depth:LIBRARY_DEPENDENCY_DEPTH,
      max_claims:Math.max(LIBRARY_DEPENDENCY_BUDGET,names.length),
    },{open:true,run:false});
    for(const name of names)libraryBrowserState.selected.delete(name);
    libraryBrowserState.importing=false;libraryBrowserState.open=false;renderLibraryBrowser();
    importTaskState.selected=task.task_id;importTaskState.open=true;renderImportTasks();
  }catch(error){
    libraryBrowserState.importing=false;libraryBrowserState.error=error.message||String(error);renderLibraryBrowser();
  }
};

importProofConstant=async function taskBackedProofConstant(name,proofId,button){
  const key=`${proofId}:${name}`;if(dependencyResolutionState.importing.has(key))return;
  dependencyResolutionState.importing.add(key);const oldLabel=button.textContent;button.disabled=true;button.textContent='Creating task…';
  try{
    const task=await createImportTask({
      declaration_names:[name],
      imports:proofImports(proofId),
      max_depth:2,
      max_claims:160,
    },{open:true,run:false});
    button.textContent='Import task started';
    importTaskState.selected=task.task_id;importTaskState.open=true;renderImportTasks();
  }catch(error){
    button.disabled=false;button.textContent=oldLabel;
    const host=button.closest('.constant-inspection');
    if(host){let errorNode=host.querySelector('.constant-import-error');if(!errorNode){errorNode=document.createElement('div');errorNode.className='constant-import-error';host.appendChild(errorNode);}errorNode.textContent=error.message||String(error);}
  }finally{dependencyResolutionState.importing.delete(key);}
};

window.addEventListener('mathhub:import-task-progress',event=>{
  const task=event.detail?.task;if(!task)return;
  for(const item of task.root_items||[]){
    const name=item.declaration_name||'',row=libraryItem(name);
    if(item.status==='imported'||item.status==='already_registered'){
      libraryBrowserState.importStatus.set(name,{ok:true,claim_id:item.claim_id,status:item.status});
      if(row){row.registered=true;row.claim_id=item.claim_id;}
    }else if(item.status==='failed')libraryBrowserState.importStatus.set(name,{error:item.error||'Import failed'});
  }
  if(libraryBrowserState.open)renderLibraryBrowser();
});
