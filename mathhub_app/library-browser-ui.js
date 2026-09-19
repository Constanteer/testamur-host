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

const LIBRARY_TOPICS=[
  {id:'numbers',label:'Numbers & arithmetic',namespace:'Nat',description:'Primes, divisibility, arithmetic, combinatorics'},
  {id:'algebra',label:'Algebra',namespace:'Algebra',description:'Groups, rings, fields, polynomials'},
  {id:'linear',label:'Linear algebra',namespace:'LinearAlgebra',description:'Vectors, matrices, linear maps'},
  {id:'topology',label:'Topology',namespace:'Topology',description:'Continuity, compactness, metric spaces'},
  {id:'measure',label:'Measure & integration',namespace:'MeasureTheory',description:'Measures, integrals, measurable spaces'},
  {id:'probability',label:'Probability',namespace:'Probability',description:'Random variables, distributions, expectation'},
];
function libraryTopicForNamespace(namespace){return LIBRARY_TOPICS.find(topic=>topic.namespace===namespace)||null;}
function humanizeLeanName(name=''){
  const short=String(name).split('.').pop()||String(name);
  const spaced=short.replace(/_/g,' ').replace(/([a-z0-9])([A-Z])/g,'$1 $2');
  return spaced.replace(/\b\w/g,ch=>ch.toUpperCase());
}

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
  const friendly=humanizeLeanName(item.short_name||name);
  return `<div class="library-row ${registered?'registered':''}"><label class="library-select"><input type="checkbox" data-library-select="${esc(name)}" ${selected?'checked':''} ${registered||libraryBrowserState.importing?'disabled':''}/></label><div class="library-row-main"><div class="library-friendly-name"><strong>${esc(friendly)}</strong>${registered?'<span class="library-registered">in MathHub</span>':''}${status?.error?'<span class="library-import-failed">needs attention</span>':''}</div><code class="library-lean-name">${esc(name)}</code>${status?.error?`<div class="library-row-error">${esc(status.error)}</div>`:''}${libraryInspectionHtml(name)}</div><div class="library-row-actions"><button class="btn" data-library-inspect="${esc(name)}" ${libraryBrowserState.importing?'disabled':''}>Formal details</button>${registered&&claimId?`<button class="btn" data-library-open-claim="${esc(claimId)}">Open</button>`:''}</div></div>`;
}

function libraryIndexSummary(result){
  const complete=result.index_complete?'complete index':`first ${Number(result.indexed_theorems||0).toLocaleString()} indexed`;
  const source=result.cache_hit?'cached SQLite index':'fresh Lean environment scan';
  return `${Number(result.indexed_theorems||0).toLocaleString()} indexed · ${complete} · ${source}`;
}

function libraryBrowserHtml(){
  const result=libraryBrowserState.result,available=librarySelectedAvailable(),count=result?.total_theorems||0;
  const topic=libraryTopicForNamespace(libraryBrowserState.namespace);
  const runtimeReady=Boolean(state?.runtime?.ready);
  const setup=!runtimeReady?`<div class="library-setup-card"><span class="runtime-dot ${state?.runtime?.setupRunning?'working':state?.runtime?.checked?'bad':''}"></span><div><strong>${esc(state?.runtime?.label||'Preparing the math library…')}</strong><span>${state?.runtime?.setupError?'The automatic setup stopped before the library was ready. You can retry here; no terminal commands are required.':'MathHub downloads and prepares its pinned Mathlib environment automatically. You can leave this window open; no terminal commands are required.'}</span>${state?.runtime?.setupError?'<button class="btn" type="button" data-library-retry-setup>Retry setup</button>':''}</div></div>`:'';
  const progress=libraryBrowserState.importing?`<div class="library-progress"><strong>${libraryBrowserState.importedCount}/${libraryBrowserState.importTotal}</strong><span>selected statements queued; dependencies are followed automatically</span></div>`:((libraryBrowserState.lastLeanInvocations||libraryBrowserState.lastAutoDependencies)?`<div class="library-progress"><strong>${libraryBrowserState.lastAutoDependencies}</strong><span>related theorem dependencies added automatically${libraryBrowserState.lastClosureTruncated?' · current safety limit reached':''}</span></div>`:'');
  const resultCount=result?.query?`${Number(result.matched_theorems||0).toLocaleString()} matches`:`${Number(count).toLocaleString()} theorems`;
  const search=result?`<form class="library-search" id="library-search-form"><label><span>Search ${esc(topic?.label||'this area')}</span><input id="library-search" value="${esc(libraryBrowserState.search)}" placeholder="prime numbers, continuity, matrices…" autocomplete="off"/></label><button class="btn btn-primary" type="submit" ${libraryBrowserState.searching||libraryBrowserState.importing?'disabled':''}>${libraryBrowserState.searching?'Searching…':'Search'}</button>${libraryBrowserState.search?'<button class="btn" type="button" data-library-search-clear>Clear</button>':''}</form>`:'';
  return `<div class="library-backdrop" data-library-close-backdrop><section class="library-browser" role="dialog" aria-modal="true" aria-label="Browse the Math Library"><header class="library-browser-head"><div><div class="eyebrow">Math library</div><h2>Find something mathematical.</h2><p>Choose an area, search for a theorem, and add the statements you want to explore. MathHub keeps the Lean identifiers and dependency evidence underneath without making you start there.</p></div><button class="library-close" data-library-close aria-label="Close">×</button></header>
  ${setup}
  <section class="library-topic-section"><div class="library-section-label">Browse by area</div><div class="library-topic-grid">${LIBRARY_TOPICS.map(item=>`<button class="library-topic ${item.namespace===libraryBrowserState.namespace?'selected':''}" data-library-topic="${esc(item.namespace)}"><strong>${esc(item.label)}</strong><span>${esc(item.description)}</span></button>`).join('')}</div></section>
  <form class="library-query friendly" id="library-query-form"><div class="library-current-topic"><span>Current area</span><strong>${esc(topic?.label||'Custom Lean area')}</strong><small>${esc(topic?.description||'Advanced selection')}</small></div><button class="btn btn-primary" type="submit" ${libraryBrowserState.loading||libraryBrowserState.importing||!runtimeReady?'disabled':''}>${libraryBrowserState.loading?'Loading…':'Browse this area'}</button><details class="library-advanced"><summary>Advanced Lean settings</summary><div class="library-advanced-grid"><label><span>Lean namespace</span><input id="library-namespace" value="${esc(libraryBrowserState.namespace)}" placeholder="Topology.Algebra" autocomplete="off"/></label><label><span>Imports</span><textarea id="library-imports" rows="2">${esc(libraryBrowserState.imports)}</textarea></label></div><div class="library-advanced-actions"><button class="btn" type="button" data-library-import-activity>Background import activity</button></div></details></form>
  ${libraryBrowserState.error?`<div class="library-error">${esc(libraryBrowserState.error)}</div>`:''}
  ${result?`<div class="library-result-head"><div><strong>${resultCount}</strong><span> in ${esc(topic?.label||result.namespace)}</span></div><small>${result.cache_hit?'ready from local index':'indexed from the pinned library'}${result.duration_ms?` · ${result.duration_ms} ms`:''}</small></div>${search}<div class="library-selection-bar"><div><strong>${available.length}</strong><span> selected</span></div><div class="library-selection-actions"><button class="btn" data-library-select-visible ${libraryBrowserState.importing?'disabled':''}>Select shown</button><button class="btn" data-library-clear ${libraryBrowserState.importing?'disabled':''}>Clear</button><button class="btn btn-primary" data-library-import ${!available.length||libraryBrowserState.importing?'disabled':''}>${libraryBrowserState.importing?'Adding…':`Add selected (${available.length})`}</button></div></div>${progress}<div class="library-list">${(result.items||[]).map(libraryRow).join('')||'<div class="quiet-empty">No theorems match this search.</div>'}</div>${!result.index_complete?'<div class="library-footnote">This area is large, so MathHub indexed a bounded portion. Choose a more specific area in Advanced settings if you need exhaustive results.</div>':result.truncated?'<div class="library-footnote">There are more results than this page. Search by ordinary keywords to narrow the list.</div>':''}`:'<div class="library-empty"><strong>Choose an area to begin.</strong><span>The exact Lean namespace is available under Advanced settings, but it is not required for normal browsing.</span></div>'}</section></div>`;
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
  host.querySelectorAll('[data-library-topic]').forEach(button=>button.addEventListener('click',()=>{libraryBrowserState.namespace=button.dataset.libraryTopic||'Nat';libraryBrowserState.search='';libraryBrowserState.result=null;libraryBrowserState.error='';renderLibraryBrowser();if(state.runtime.ready)void previewLibraryNamespace();}));
  host.querySelector('#library-search-form')?.addEventListener('submit',event=>{event.preventDefault();searchLibraryNamespace(host.querySelector('#library-search')?.value||'');});
  host.querySelector('[data-library-search-clear]')?.addEventListener('click',()=>searchLibraryNamespace(''));
  host.querySelector('[data-library-retry-setup]')?.addEventListener('click',async()=>{const button=host.querySelector('[data-library-retry-setup]');if(button){button.disabled=true;button.textContent='Retrying…';}try{await apiJson('/api/runtime/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});}catch(error){libraryBrowserState.error=error.message||String(error);}finally{if(typeof checkRuntime==='function')await checkRuntime();renderLibraryBrowser();}});
  host.querySelector('[data-library-import-activity]')?.addEventListener('click',()=>{libraryBrowserState.open=false;renderLibraryBrowser();if(typeof openImportTasks==='function')openImportTasks();});
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
  const button=document.createElement('button');button.className='btn btn-primary';button.dataset.libraryBrowser='';button.textContent='Browse library';button.title='Browse Mathlib; MathHub prepares the pinned library automatically';
  newClaim.before(button);button.addEventListener('click',()=>{libraryBrowserState.open=true;renderLibraryBrowser();if(state.runtime.ready&&!libraryBrowserState.result)void previewLibraryNamespace();else if(!state.runtime.ready&&typeof checkRuntime==='function')void checkRuntime();});
}

const libraryBrowserBaseBind=bind;
bind=function bindLibraryBrowser(){libraryBrowserBaseBind();mountLibraryBrowserButton();};
window.addEventListener('keydown',event=>{if(event.key==='Escape'&&libraryBrowserState.open&&!libraryBrowserState.importing){libraryBrowserState.open=false;renderLibraryBrowser();}});
mountLibraryBrowserButton();
