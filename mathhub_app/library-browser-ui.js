/* Deterministic Lean-library browser. No AI classification: namespace discovery
   comes from the pinned Lean Environment and only kernel theorem constants are
   offered as Claim import candidates. Namespace theorem names are cached by
   environment/import set, so later searches do not relaunch Lean. */
const libraryBrowserState={
  open:false,loading:false,searching:false,importing:false,namespace:'Nat',imports:'Mathlib',search:'',
  result:null,error:'',selected:new Set(),inspections:new Map(),importStatus:new Map(),
  importedCount:0,importTotal:0,lastLeanInvocations:0,lastAutoDependencies:0,lastClosureTruncated:false,
};
const LIBRARY_IMPORT_BATCH_SIZE=40;
const LIBRARY_DEPENDENCY_DEPTH=2;
const LIBRARY_DEPENDENCY_BUDGET=160;

function libraryImports(){return String(libraryBrowserState.imports||'Mathlib').split(/\n+/).map(x=>x.trim()).filter(Boolean);}
function libraryItem(name){return libraryBrowserState.result?.items?.find(item=>item.declaration_name===name)||null;}
function librarySelectedAvailable(){return [...libraryBrowserState.selected].filter(name=>{const item=libraryItem(name);return item&&!item.registered;});}

function libraryInspectionHtml(name){
  const value=libraryBrowserState.inspections.get(name);if(!value)return '';
  if(value.loading)return '<div class="library-inspection quiet-empty">Inspecting with Lean…</div>';
  if(value.error)return `<div class="library-inspection library-error">${esc(value.error)}</div>`;
  return `<div class="library-inspection"><div class="library-inspection-type"><span>Lean type</span><code>${esc(value.pretty_type||'—')}</code></div><div class="library-inspection-meta"><span>${(value.axioms||[]).length} axioms</span><span>${(value.statement_dependencies||[]).length} statement refs</span><span>${(value.proof_dependencies||[]).length} proof refs</span></div></div>`;
}

function libraryRow(item){
  const name=item.declaration_name,status=libraryBrowserState.importStatus.get(name),selected=libraryBrowserState.selected.has(name),registered=item.registered||status?.ok,claimId=status?.claim_id||item.claim_id||'';
  return `<div class="library-row ${registered?'registered':''}"><label class="library-select"><input type="checkbox" data-library-select="${esc(name)}" ${selected?'checked':''} ${registered||libraryBrowserState.importing?'disabled':''}/></label><div class="library-row-main"><div class="library-name-line"><code>${esc(name)}</code>${registered?'<span class="library-registered">registered</span>':''}${status?.error?'<span class="library-import-failed">failed</span>':''}</div>${status?.error?`<div class="library-row-error">${esc(status.error)}</div>`:''}${libraryInspectionHtml(name)}</div><div class="library-row-actions"><button class="btn" data-library-inspect="${esc(name)}" ${libraryBrowserState.importing?'disabled':''}>Inspect</button>${registered&&claimId?`<button class="btn" data-library-open-claim="${esc(claimId)}">Open Claim</button>`:''}</div></div>`;
}

function libraryIndexSummary(result){
  const complete=result.index_complete?'complete index':`first ${Number(result.indexed_theorems||0).toLocaleString()} indexed`;
  const source=result.cache_hit?'cached SQLite index':'fresh Lean environment scan';
  return `${Number(result.indexed_theorems||0).toLocaleString()} indexed · ${complete} · ${source}`;
}

function libraryBrowserHtml(){
  const result=libraryBrowserState.result,available=librarySelectedAvailable(),count=result?.total_theorems||0;
  const progress=libraryBrowserState.importing?`<div class="library-progress"><strong>${libraryBrowserState.importedCount}/${libraryBrowserState.importTotal}</strong><span>root theorems processed · dependencies auto-imported to depth ${LIBRARY_DEPENDENCY_DEPTH}, bounded at ${LIBRARY_DEPENDENCY_BUDGET} Claims per batch</span></div>`:((libraryBrowserState.lastLeanInvocations||libraryBrowserState.lastAutoDependencies)?`<div class="library-progress"><strong>${libraryBrowserState.lastAutoDependencies}</strong><span>dependency Claims followed automatically · ${libraryBrowserState.lastLeanInvocations} Lean process invocations${libraryBrowserState.lastClosureTruncated?' · closure budget reached':''}</span></div>`:'');
  const resultCount=result?.query?`${Number(result.matched_theorems||0).toLocaleString()} matches`:`${Number(count).toLocaleString()} theorem constants`;
  const search=result?`<form class="library-search" id="library-search-form"><label><span>Search within ${esc(result.namespace)}</span><input id="library-search" value="${esc(libraryBrowserState.search)}" placeholder="add_comm, prime, tendsto…" autocomplete="off"/></label><button class="btn" type="submit" ${libraryBrowserState.searching||libraryBrowserState.importing?'disabled':''}>${libraryBrowserState.searching?'Searching index…':'Search'}</button>${libraryBrowserState.search?'<button class="btn" type="button" data-library-search-clear>Clear search</button>':''}</form>`:'';
  return `<div class="library-backdrop" data-library-close-backdrop><section class="library-browser" role="dialog" aria-modal="true" aria-label="Import Lean library theorems"><header class="library-browser-head"><div><div class="eyebrow">Pinned Lean library</div><h2>Browse theorem declarations</h2><p>Enumerate kernel theorem constants by namespace. Importing a theorem also follows its unregistered theorem dependencies automatically within a bounded closure; definitions, instances, constructors and recursors stay formal substrate.</p></div><button class="library-close" data-library-close aria-label="Close">×</button></header><form class="library-query" id="library-query-form"><label><span>Namespace</span><input id="library-namespace" value="${esc(libraryBrowserState.namespace)}" placeholder="Topology.Algebra" autocomplete="off"/></label><label><span>Imports</span><textarea id="library-imports" rows="2">${esc(libraryBrowserState.imports)}</textarea></label><button class="btn btn-primary" type="submit" ${libraryBrowserState.loading||libraryBrowserState.importing?'disabled':''}>${libraryBrowserState.loading?'Building theorem index…':'Open namespace'}</button></form>${libraryBrowserState.error?`<div class="library-error">${esc(libraryBrowserState.error)}</div>`:''}${result?`<div class="library-result-head"><div><strong>${resultCount}</strong><span> under <code>${esc(result.namespace)}</code></span></div><small>${esc(libraryIndexSummary(result))}${result.duration_ms?` · ${result.duration_ms} ms Lean scan`:''}</small></div>${search}<div class="library-selection-bar"><div><strong>${available.length}</strong><span> selected roots · theorem dependencies are included automatically</span></div><div class="library-selection-actions"><button class="btn" data-library-select-visible ${libraryBrowserState.importing?'disabled':''}>Select unregistered shown</button><button class="btn" data-library-clear ${libraryBrowserState.importing?'disabled':''}>Clear</button><button class="btn btn-primary" data-library-import ${!available.length||libraryBrowserState.importing?'disabled':''}>${libraryBrowserState.importing?'Importing closure…':`Import selected (${available.length})`}</button></div></div>${progress}<div class="library-list">${(result.items||[]).map(libraryRow).join('')||'<div class="quiet-empty">No theorem constants match this view.</div>'}</div>${!result.index_complete?'<div class="library-footnote">This namespace exceeds the local theorem-index cap. Search covers the indexed prefix only; use a narrower namespace for a complete index.</div>':result.truncated?'<div class="library-footnote">The result set is larger than this page. Search for a theorem name or use a narrower namespace.</div>':''}`:'<div class="library-empty"><strong>Start with a Lean namespace.</strong><span>Examples: Nat, Topology.Algebra, MeasureTheory.</span></div>'}</section></div>`;
}

function renderLibraryBrowser(){
  document.querySelector('#mathhub-library-browser')?.remove();
  if(!libraryBrowserState.open)return;
  const host=document.createElement('div');host.id='mathhub-library-browser';host.innerHTML=libraryBrowserHtml();document.body.appendChild(host);
  bindLibraryBrowserOverlay(host);
}

async function fetchLibraryNamespace({query='',refresh=false}={}){
  return apiJson('/api/library/namespace',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({namespace:libraryBrowserState.namespace,imports:libraryImports(),query,limit:120,refresh})});
}

async function previewLibraryNamespace(){
  const namespace=document.querySelector('#library-namespace')?.value?.trim()||'';
  const imports=document.querySelector('#library-imports')?.value||'Mathlib';
  libraryBrowserState.namespace=namespace;libraryBrowserState.imports=imports;libraryBrowserState.search='';libraryBrowserState.loading=true;libraryBrowserState.error='';libraryBrowserState.result=null;libraryBrowserState.selected.clear();libraryBrowserState.inspections.clear();libraryBrowserState.importStatus.clear();libraryBrowserState.lastLeanInvocations=0;libraryBrowserState.lastAutoDependencies=0;libraryBrowserState.lastClosureTruncated=false;renderLibraryBrowser();
  try{libraryBrowserState.result=await fetchLibraryNamespace();}
  catch(error){libraryBrowserState.error=error.message||String(error);}
  finally{libraryBrowserState.loading=false;renderLibraryBrowser();}
}

async function searchLibraryNamespace(query=''){
  if(!libraryBrowserState.result||libraryBrowserState.searching)return;
  libraryBrowserState.search=String(query||'').trim();libraryBrowserState.searching=true;libraryBrowserState.error='';libraryBrowserState.selected.clear();renderLibraryBrowser();
  try{libraryBrowserState.result=await fetchLibraryNamespace({query:libraryBrowserState.search});}
  catch(error){libraryBrowserState.error=error.message||String(error);}
  finally{libraryBrowserState.searching=false;renderLibraryBrowser();}
}

async function inspectLibraryDeclaration(name){
  libraryBrowserState.inspections.set(name,{loading:true});renderLibraryBrowser();
  try{
    const result=await apiJson('/api/lean/inspect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({declaration_name:name,imports:libraryImports()})});
    if(!result.ok)throw new Error(result.error||result.engine_error||'Lean inspection failed');
    libraryBrowserState.inspections.set(name,result);
  }catch(error){libraryBrowserState.inspections.set(name,{error:error.message||String(error)});}
  renderLibraryBrowser();
}

function invalidateMathHubReadsAfterImport(){
  argumentState?.cache?.clear?.();
  registryState?.details?.clear?.();
  if(typeof graphGlobalState!=='undefined')graphGlobalState.loaded=false;
}

async function importLibrarySelection(){
  const names=librarySelectedAvailable();if(!names.length||libraryBrowserState.importing)return;
  libraryBrowserState.importing=true;libraryBrowserState.importedCount=0;libraryBrowserState.importTotal=names.length;libraryBrowserState.lastLeanInvocations=0;libraryBrowserState.lastAutoDependencies=0;libraryBrowserState.lastClosureTruncated=false;libraryBrowserState.error='';renderLibraryBrowser();
  for(let offset=0;offset<names.length;offset+=LIBRARY_IMPORT_BATCH_SIZE){
    const chunk=names.slice(offset,offset+LIBRARY_IMPORT_BATCH_SIZE);
    try{
      const batch=await apiJson('/api/claims/import-closure',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({declaration_names:chunk,imports:libraryImports(),max_depth:LIBRARY_DEPENDENCY_DEPTH,max_claims:LIBRARY_DEPENDENCY_BUDGET})});
      libraryBrowserState.lastLeanInvocations+=Number(batch.lean_invocations||0);
      libraryBrowserState.lastAutoDependencies+=Number(batch.auto_dependency_claims||0);
      libraryBrowserState.lastClosureTruncated=libraryBrowserState.lastClosureTruncated||Boolean(batch.truncated);
      const returned=new Map((batch.root_items||[]).map(item=>[item.declaration_name,item]));
      for(const name of chunk){
        const result=returned.get(name);
        if(result&&(result.status==='imported'||result.status==='already_registered')){
          libraryBrowserState.importStatus.set(name,{ok:true,claim_id:result.claim_id,status:result.status});
          const item=libraryItem(name);if(item){item.registered=true;item.claim_id=result.claim_id;}
        }else{libraryBrowserState.importStatus.set(name,{error:result?.error||'Dependency-closure import did not return a root result for this theorem'});}
        libraryBrowserState.importedCount+=1;libraryBrowserState.selected.delete(name);
      }
    }catch(error){for(const name of chunk){libraryBrowserState.importStatus.set(name,{error:error.message||String(error)});libraryBrowserState.importedCount+=1;libraryBrowserState.selected.delete(name);}}
    renderLibraryBrowser();
  }
  libraryBrowserState.importing=false;invalidateMathHubReadsAfterImport();
  try{if(typeof loadCatalogPage==='function')await loadCatalogPage({reset:true});}catch(_){}
  renderLibraryBrowser();
}

function openLibraryClaim(claimId){
  if(!claimId)return;libraryBrowserState.open=false;renderLibraryBrowser();state.selected=claimId;state.view='claim';if(typeof loadArgument==='function')loadArgument(claimId,{force:true});render();
}

function bindLibraryBrowserOverlay(host){
  host.querySelector('[data-library-close]')?.addEventListener('click',()=>{if(!libraryBrowserState.importing){libraryBrowserState.open=false;renderLibraryBrowser();}});
  host.querySelector('[data-library-close-backdrop]')?.addEventListener('click',event=>{if(event.target===event.currentTarget&&!libraryBrowserState.importing){libraryBrowserState.open=false;renderLibraryBrowser();}});
  host.querySelector('#library-query-form')?.addEventListener('submit',event=>{event.preventDefault();previewLibraryNamespace();});
  host.querySelector('#library-search-form')?.addEventListener('submit',event=>{event.preventDefault();searchLibraryNamespace(host.querySelector('#library-search')?.value||'');});
  host.querySelector('[data-library-search-clear]')?.addEventListener('click',()=>searchLibraryNamespace(''));
  host.querySelectorAll('[data-library-select]').forEach(input=>input.addEventListener('change',event=>{const name=event.currentTarget.dataset.librarySelect;if(event.currentTarget.checked)libraryBrowserState.selected.add(name);else libraryBrowserState.selected.delete(name);renderLibraryBrowser();}));
  host.querySelectorAll('[data-library-inspect]').forEach(button=>button.addEventListener('click',()=>inspectLibraryDeclaration(button.dataset.libraryInspect)));
  host.querySelectorAll('[data-library-open-claim]').forEach(button=>button.addEventListener('click',()=>openLibraryClaim(button.dataset.libraryOpenClaim)));
  host.querySelector('[data-library-select-visible]')?.addEventListener('click',()=>{for(const item of libraryBrowserState.result?.items||[])if(!item.registered)libraryBrowserState.selected.add(item.declaration_name);renderLibraryBrowser();});
  host.querySelector('[data-library-clear]')?.addEventListener('click',()=>{libraryBrowserState.selected.clear();renderLibraryBrowser();});
  host.querySelector('[data-library-import]')?.addEventListener('click',()=>importLibrarySelection());
}

function mountLibraryBrowserButton(){
  const top=document.querySelector('.topbar');if(!top||top.querySelector('[data-library-browser]'))return;
  const newClaim=top.querySelector('[data-view="submit"]');if(!newClaim)return;
  const button=document.createElement('button');button.className='btn';button.dataset.libraryBrowser='';button.textContent='Import library';button.disabled=state.runtime.checked&&!state.runtime.ready;button.title=button.disabled?'Pinned Lean environment is unavailable':'Browse theorem declarations in the pinned Lean environment';
  newClaim.before(button);button.addEventListener('click',()=>{libraryBrowserState.open=true;renderLibraryBrowser();});
}

const libraryBrowserBaseBind=bind;
bind=function bindLibraryBrowser(){libraryBrowserBaseBind();mountLibraryBrowserButton();};
window.addEventListener('keydown',event=>{if(event.key==='Escape'&&libraryBrowserState.open&&!libraryBrowserState.importing){libraryBrowserState.open=false;renderLibraryBrowser();}});
mountLibraryBrowserButton();
