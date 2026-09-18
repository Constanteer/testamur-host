/* Read-only bridge between raw Lean proof refs and mapped mathematical Claims. */
const dependencyResolutionState={inspections:new Map(),loading:new Set(),importing:new Set()};
const DEPENDENCY_IMPORT_DEPTH=2;
const DEPENDENCY_IMPORT_BUDGET=160;

function unmappedProofConstants(route){
  const raw=route?.raw_refs?.proof||[];
  const mapped=new Set((route?.depends_on||[]).flatMap(dep=>dep.evidence_constants||[]));
  return raw.filter(name=>!mapped.has(name));
}

function proofImports(proofId){
  const argument=argumentState.cache.get(state.selected);
  const route=argument?.proofs?.find(proof=>proof.proof_id===proofId);
  return route?.imports?.length?route.imports:['Mathlib'];
}

function inspectionHtml(name,result,proofId){
  if(!result)return '<div class="constant-inspection quiet">Not inspected.</div>';
  if(result.error||result.engine_error)return `<div class="constant-inspection bad">${esc(result.error||result.engine_error)}</div>`;
  const type=result.pretty_type||'Lean returned no pretty type.';
  const axioms=result.axioms||[];
  return `<div class="constant-inspection"><div class="constant-type"><span>Lean type</span><code>${esc(type)}</code></div><div class="constant-inspection-meta"><span>${axioms.length?`${axioms.length} axiom${axioms.length===1?'':'s'}`:'no axioms reported'}</span><span>${(result.statement_dependencies||[]).length} statement refs</span><span>${(result.proof_dependencies||[]).length} proof refs</span></div><div class="constant-actions"><button class="btn constant-import" type="button" data-import-constant="${esc(name)}" data-proof-id="${esc(proofId)}">Import theorem + dependencies</button><span>Lean will classify and add unregistered theorem dependencies automatically. Definitions and other substrate remain raw refs.</span></div></div>`;
}

async function inspectProofConstant(name,proofId,host){
  const key=`${proofId}:${name}`;
  if(dependencyResolutionState.inspections.has(key)){
    host.innerHTML=inspectionHtml(name,dependencyResolutionState.inspections.get(key),proofId);
    bindDependencyImportButtons(host);
    return;
  }
  if(dependencyResolutionState.loading.has(key))return;
  dependencyResolutionState.loading.add(key);
  host.innerHTML='<div class="constant-inspection quiet">Inspecting in the pinned Lean environment…</div>';
  try{
    const result=await apiJson('/api/lean/inspect',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({imports:proofImports(proofId),declaration_name:name}),
    });
    dependencyResolutionState.inspections.set(key,result);
    host.innerHTML=inspectionHtml(name,result,proofId);
    bindDependencyImportButtons(host);
  }catch(error){
    host.innerHTML=`<div class="constant-inspection bad">${esc(error.message)}</div>`;
  }finally{
    dependencyResolutionState.loading.delete(key);
  }
}

async function importProofConstant(name,proofId,button){
  const key=`${proofId}:${name}`;
  if(dependencyResolutionState.importing.has(key))return;
  dependencyResolutionState.importing.add(key);
  const oldLabel=button.textContent;
  button.disabled=true;
  button.textContent='Importing closure…';
  try{
    const closure=await apiJson('/api/claims/import-closure',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({imports:proofImports(proofId),declaration_names:[name],max_depth:DEPENDENCY_IMPORT_DEPTH,max_claims:DEPENDENCY_IMPORT_BUDGET}),
    });
    const imported=(closure.root_items||[])[0]||null;
    if(!imported||!['imported','already_registered'].includes(imported.status))throw new Error(imported?.error||'The theorem root was not imported');
    const sourceClaim=state.selected;
    if(sourceClaim){
      argumentState.cache.delete(sourceClaim);
      registryState.details.delete(sourceClaim);
    }
    await loadRegistryClaims();
    if(sourceClaim)await loadArgument(sourceClaim,{force:true});
    const importedId=imported.claim_id||imported.public_id||'';
    if(importedId&&registryState.claims.has(importedId))state.graphSelected=importedId;
    render();
  }catch(error){
    button.disabled=false;
    button.textContent=oldLabel;
    const host=button.closest('.constant-inspection');
    if(host){
      let errorNode=host.querySelector('.constant-import-error');
      if(!errorNode){errorNode=document.createElement('div');errorNode.className='constant-import-error';host.appendChild(errorNode);}
      errorNode.textContent=error.message;
    }
  }finally{
    dependencyResolutionState.importing.delete(key);
  }
}

function bindDependencyImportButtons(root=document){
  root.querySelectorAll?.('[data-import-constant]').forEach(button=>{
    if(button.dataset.importBound==='1')return;
    button.dataset.importBound='1';
    button.addEventListener('click',()=>importProofConstant(button.dataset.importConstant||'',button.dataset.proofId||'',button));
  });
}

const dependencyResolutionBaseProofHtml=proofHtml;
proofHtml=function resolvedProofHtml(route,index){
  const html=dependencyResolutionBaseProofHtml(route,index);
  const constants=unmappedProofConstants(route);
  if(!route.argument_index_complete||!constants.length)return html;
  const panel=`<details class="unmapped-constants"><summary><span>Unmapped proof constants</span><strong>${constants.length}</strong></summary><p>Lean says this Proof expression references these constants, but they are not currently projected to registered mathematical Claim dependencies. They may be theorems, definitions, recursors, instances, or other formal substrate. Inspect first; theorem imports now follow missing theorem dependencies automatically within a bounded closure.</p><div class="unmapped-constant-list">${constants.slice(0,80).map(name=>`<div class="unmapped-constant"><div><code>${esc(name)}</code><button type="button" data-inspect-constant="${esc(name)}" data-proof-id="${esc(route.proof_id)}">Inspect</button></div><div class="constant-inspection-slot" data-inspection-slot="${esc(route.proof_id)}:${esc(name)}"></div></div>`).join('')}</div>${constants.length>80?`<div class="unmapped-truncated">Showing 80 of ${constants.length} constants.</div>`:''}</details>`;
  return html.replace('</article>',`${panel}</article>`);
};

const dependencyResolutionBaseBind=bind;
bind=function dependencyResolutionBind(){
  dependencyResolutionBaseBind();
  document.querySelectorAll('[data-inspect-constant]').forEach(button=>button.addEventListener('click',async()=>{
    const name=button.dataset.inspectConstant||'';
    const proofId=button.dataset.proofId||'';
    const key=`${proofId}:${name}`;
    const slot=[...document.querySelectorAll('[data-inspection-slot]')].find(node=>node.dataset.inspectionSlot===key);
    if(!name||!proofId||!slot)return;
    button.disabled=true;
    try{await inspectProofConstant(name,proofId,slot);}finally{button.disabled=false;button.textContent='Inspect again';}
  }));
  bindDependencyImportButtons(document);
};
