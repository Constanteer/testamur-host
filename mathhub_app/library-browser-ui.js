/* Deterministic Lean-library browser. Mathlib is a read-only substrate that is
   already present in MathHub; browsing it is not an import operation. "Add Lean"
   is reserved for user-provided source. Namespace theorem names are cached by
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
  if(value.loading)return '<div class="library-inspection quiet-empty">Loading Mathlib source…</div>';
  if(value.error)return `<div class="library-inspection library-error">${esc(value.error)}</div>`;
  return `<div class="library-inspection"><div class="library-inspection-type"><span>Source declaration</span><code>${esc(value.source_declaration||value.pretty_type||'—')}</code></div><details class="library-machine-details"><summary>Source location</summary><div><span>Lean name</span><code>${esc(name)}</code></div><div><span>Module</span><code>${esc(value.source_module||'Mathlib')}</code></div><div><span>File</span><code>${esc(value.source_path||'—')}:${Number(value.source_line||0)}</code></div><div class="library-inspection-meta"><span>read-only source index</span><span>0 Lean calls</span></div></details></div>`;
}

function libraryRow(item){
  const name=item.declaration_name,status=libraryBrowserState.importStatus.get(name),registered=item.registered||status?.ok,claimId=status?.claim_id||item.claim_id||'';
  const friendly=humanizeLeanName(item.short_name||name);
  return `<div class="library-row ${registered?'registered':''}"><div class="library-row-main"><div class="library-friendly-name"><strong>${esc(friendly)}</strong>${registered?'<span class="library-registered">workspace record</span>':''}</div><span class="library-row-kind">Mathlib theorem · already available</span>${libraryInspectionHtml(name)}</div><div class="library-row-actions"><button class="btn" data-library-inspect="${esc(name)}">Details</button><button class="btn btn-primary" data-library-edit-copy="${esc(name)}">Edit a copy</button>${registered&&claimId?`<button class="btn" data-library-open-claim="${esc(claimId)}">Open record</button>`:''}</div></div>`;
}

async function editLibraryTheoremCopy(name){
  name=String(name||'').trim();if(!name)return;
  let detail=libraryBrowserState.inspections.get(name);
  if(!detail||detail.loading||detail.error){
    try{
      detail=await apiJson(`/api/library/theorem?name=${encodeURIComponent(name)}`);
      if(!detail?.ok)throw new Error(detail?.error||'Mathlib source detail unavailable');
      libraryBrowserState.inspections.set(name,detail);
    }catch(error){
      libraryBrowserState.inspections.set(name,{error:error.message||String(error)});
      renderLibraryBrowser();
      return;
    }
  }
  state.sourceDraft=state.sourceDraft||{title:'',statement:'',imports:'Mathlib',declaration:'',proof:''};
  state.sourceDraft={
    title:humanizeLeanName(detail.short_name||name),
    statement:'',
    imports:String(detail.source_module||'Mathlib'),
    declaration:String(detail.source_declaration||detail.pretty_type||''),
    proof:'',
  };
  state.sourceIngest=state.sourceIngest||{loading:false,error:'',result:null,selected:-1};
  state.sourceIngest={loading:false,error:'',result:null,selected:-1};
  state.sourceRegister={loading:false,error:'',success:null};
  state.sourceCheck={running:false,result:null,error:''};
  libraryBrowserState.open=false;
  state.view='submit';
  render();
}


function libraryIndexSummary(result){
  const complete=result.index_complete?'complete index':`first ${Number(result.indexed_theorems||0).toLocaleString()} indexed`;
  const source=result.global_cache||result.cache_hit?'cached Mathlib source index':'Mathlib source index';
  return `${Number(result.indexed_theorems||0).toLocaleString()} indexed · ${complete} · ${source}`;
}

function libraryBrowserHtml(){
  const result=libraryBrowserState.result,count=result?.total_theorems||0;
  const topic=libraryTopicForNamespace(libraryBrowserState.namespace);
  const runtimeReady=Boolean(state?.runtime?.ready);
  const setupStep=Number(state?.runtime?.setupStep||0),setupSteps=Number(state?.runtime?.setupSteps||0);
  const setupProgress=setupSteps>0?Math.max(0,Math.min(100,Math.round((setupStep/setupSteps)*100))):0;
  const setup=!runtimeReady?`<div class="library-setup-card"><span class="runtime-dot ${state?.runtime?.setupRunning?'working':state?.runtime?.checked?'bad':''}"></span><div class="library-setup-content"><div class="library-setup-title"><strong>${esc(state?.runtime?.label||'Preparing the math library…')}</strong>${state?.runtime?.setupRunning&&setupSteps?`<small>Step ${setupStep} of ${setupSteps}</small>`:''}</div><span>${state?.runtime?.setupError?'The pinned Lean/Mathlib environment did not finish preparing. Retry is safe; existing Claims are not changed.':state?.runtime?.setupRunning?(state?.runtime?.setupStage||'MathHub is preparing its pinned Mathlib environment automatically.'):'The library is preparing in the background.'}</span>${state?.runtime?.setupRunning&&setupSteps?`<div class="library-setup-progress" role="progressbar" aria-label="Math library preparation" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${setupProgress}"><i style="width:${setupProgress}%"></i></div>`:''}${state?.runtime?.setupError?`<div class="library-setup-actions"><button class="btn" type="button" data-library-retry-setup>Retry setup</button><details class="library-setup-detail"><summary>Technical detail</summary><code>${esc(state.runtime.setupError)}</code></details></div>`:''}</div></div>`:'';
  const progress='';
  const resultCount=result?.query?`${Number(result.matched_theorems||0).toLocaleString()} matches`:`${Number(count).toLocaleString()} theorems`;
  const search=result?`<form class="library-search" id="library-search-form"><label><span>Search ${esc(topic?.label||'this area')}</span><input id="library-search" value="${esc(libraryBrowserState.search)}" placeholder="prime numbers, continuity, matrices…" autocomplete="off"/></label><button class="btn btn-primary" type="submit" ${libraryBrowserState.searching||libraryBrowserState.importing?'disabled':''}>${libraryBrowserState.searching?'Searching…':'Search'}</button>${libraryBrowserState.search?'<button class="btn" type="button" data-library-search-clear>Clear</button>':''}</form>`:'';
  return `<main class="shell page library-page"><section class="library-hero"><div><div class="eyebrow">Math library</div><h1>Browse Mathlib as mathematics.</h1><p>Mathlib is already here: browse it, inspect exact Lean, or edit a copy. “Add Lean” is only for source you bring yourself.</p></div><div class="library-hero-stat"><strong>${Number(result?.indexed_theorems||count||0).toLocaleString()}</strong><span>Mathlib theorems in this index</span></div></section>
  <div class="library-workspace"><aside class="library-topic-rail"><div class="library-section-label">Fields</div>${LIBRARY_TOPICS.map(item=>`<button class="library-topic ${item.namespace===libraryBrowserState.namespace?'selected':''}" data-library-topic="${esc(item.namespace)}"><strong>${esc(item.label)}</strong><span>${esc(item.description)}</span></button>`).join('')}<div class="library-rail-note">These are deterministic views over the pinned Lean environment—not AI-generated categories.</div></aside>
  <section class="library-main">
  ${setup}
  <header class="library-current-head"><div><span>Current field</span><h2>${esc(topic?.label||'Custom Lean area')}</h2><p>${esc(topic?.description||'Advanced namespace selection')}</p></div><form class="library-query friendly" id="library-query-form"><button class="btn btn-primary" type="submit" ${libraryBrowserState.loading||libraryBrowserState.importing||!runtimeReady?'disabled':''}>${libraryBrowserState.loading?'Loading…':'Load theorems'}</button><details class="library-advanced"><summary>Lean settings</summary><div class="library-advanced-grid"><label><span>Lean namespace</span><input id="library-namespace" value="${esc(libraryBrowserState.namespace)}" placeholder="Topology.Algebra" autocomplete="off"/></label><label><span>Imports</span><textarea id="library-imports" rows="2">${esc(libraryBrowserState.imports)}</textarea></label></div><div class="library-advanced-actions"><button class="btn" type="button" data-library-import-activity>Background imports</button></div></details></form></header>
  ${libraryBrowserState.error?`<div class="library-error">${esc(libraryBrowserState.error)}</div>`:''}
  ${result?`<div class="library-result-head"><div><strong>${resultCount}</strong><span> · ${esc(topic?.label||result.namespace)}</span></div><small>${result.cache_hit?'local index':'pinned Lean scan'}${result.duration_ms?` · ${result.duration_ms} ms`:''}</small></div>${search}<div class="library-readonly-note"><strong>Already in the library.</strong><span>Opening or editing a Mathlib theorem does not “add” it. Edit a copy to experiment; use Add Lean for your own source.</span></div>${progress}<div class="library-list">${(result.items||[]).map(libraryRow).join('')||'<div class="quiet-empty">No theorems match this search.</div>'}</div>${!result.index_complete?'<div class="library-footnote">This namespace is large, so MathHub indexed a bounded slice. Search or choose a narrower namespace for exhaustive results.</div>':result.truncated?'<div class="library-footnote">More results exist. Narrow the search to load the theorem you want.</div>':''}`:`<div class="library-empty"><strong>${runtimeReady?'Choose a field to load its theorems.':'The library will become browsable when Lean setup finishes.'}</strong><span>${runtimeReady?'Mathlib is read-only here. Open a theorem or edit a copy; Add Lean is for your own source.':'Your registry stays available while the pinned environment is repaired.'}</span></div>`}
  </section></div></main>`;
}

const libraryBrowserBaseCurrent=current;
current=function currentWithMathLibrary(){
  if(state.view==='library')return libraryBrowserHtml();
  return libraryBrowserBaseCurrent();
};

function renderLibraryBrowser(){
  if(!libraryBrowserState.open||state.view!=='library')return;
  render();
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
    const result=await apiJson(`/api/library/theorem?name=${encodeURIComponent(name)}`);
    if(!result.ok)throw new Error(result.error||'Mathlib source detail unavailable');
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
  if(!claimId)return;libraryBrowserState.open=false;state.selected=claimId;state.view='claim';if(typeof loadArgument==='function')loadArgument(claimId,{force:true});render();
}

function bindLibraryBrowserOverlay(host){
  host.querySelector('#library-query-form')?.addEventListener('submit',event=>{event.preventDefault();previewLibraryNamespace();});
  host.querySelectorAll('[data-library-topic]').forEach(button=>button.addEventListener('click',()=>{libraryBrowserState.namespace=button.dataset.libraryTopic||'Nat';libraryBrowserState.search='';libraryBrowserState.result=null;libraryBrowserState.error='';renderLibraryBrowser();if(state.runtime.ready)void previewLibraryNamespace();}));
  host.querySelector('#library-search-form')?.addEventListener('submit',event=>{event.preventDefault();searchLibraryNamespace(host.querySelector('#library-search')?.value||'');});
  host.querySelector('[data-library-search-clear]')?.addEventListener('click',()=>searchLibraryNamespace(''));
  host.querySelector('[data-library-retry-setup]')?.addEventListener('click',async()=>{const button=host.querySelector('[data-library-retry-setup]');if(button){button.disabled=true;button.textContent='Retrying…';}try{await apiJson('/api/runtime/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});}catch(error){libraryBrowserState.error=error.message||String(error);}finally{if(typeof checkRuntime==='function')await checkRuntime();renderLibraryBrowser();}});
  host.querySelector('[data-library-import-activity]')?.addEventListener('click',()=>{if(typeof openImportTasks==='function')openImportTasks();});
  host.querySelectorAll('[data-library-inspect]').forEach(button=>button.addEventListener('click',()=>inspectLibraryDeclaration(button.dataset.libraryInspect)));
  host.querySelectorAll('[data-library-edit-copy]').forEach(button=>button.addEventListener('click',()=>void editLibraryTheoremCopy(button.dataset.libraryEditCopy)));
  host.querySelectorAll('[data-library-open-claim]').forEach(button=>button.addEventListener('click',()=>openLibraryClaim(button.dataset.libraryOpenClaim)));
}

function openLibraryBrowser(){
  libraryBrowserState.open=true;state.view='library';render();
  if(state.runtime.ready&&!libraryBrowserState.result)void previewLibraryNamespace();
  else if(!state.runtime.ready&&typeof checkRuntime==='function')void checkRuntime();
}

function mountLibraryBrowserButton(){
  const top=document.querySelector('.topbar');if(!top)return;
  const nav=top.querySelector('.topnav');if(!nav)return;
  const home=nav.querySelector('[data-view="registry"]');if(home)home.textContent='Your MathHub';
  const graph=nav.querySelector('[data-view="graph"]');if(graph)graph.textContent='Connections';
  if(nav.querySelector('[data-library-browser]'))return;
  const button=document.createElement('button');button.dataset.libraryBrowser='';button.className=state.view==='library'?'active':'';button.textContent='Math library';button.title='Browse real theorems from the pinned Mathlib environment';
  if(graph)nav.insertBefore(button,graph);else nav.appendChild(button);
  button.addEventListener('click',openLibraryBrowser);
}

function mountLibraryEmptyStart(){
  if(state.view!=='registry'||claims.length)return;
  document.querySelector('.overview-line')?.remove();
  const layout=document.querySelector('.registry-layout');if(!layout)return;
  const section=document.createElement('section');section.className='mathhub-empty-start';
  const starterNote=state.runtime?.ready?'The installed Mathlib sources are available to browse immediately.':'The Mathlib source index will appear as soon as the pinned environment finishes preparing.';
  section.innerHTML=`<div class="mathhub-empty-copy"><div class="eyebrow">Starting your library</div><h2>Mathlib is already available.</h2><p>${esc(starterNote)} Browse existing mathematics directly. Use Add Lean only when you want to upload, fetch, paste, or write new Lean source of your own.</p><div class="mathhub-empty-actions"><button class="btn btn-primary mathhub-empty-primary" data-empty-browse>Open Math library</button><button class="btn" data-empty-add>Add Lean source</button></div></div><div class="mathhub-empty-example"><span>Browse a field</span><button data-empty-topic="Nat">Numbers & arithmetic</button><button data-empty-topic="LinearAlgebra">Linear algebra</button><button data-empty-topic="Topology">Topology</button><button data-empty-topic="Probability">Probability</button></div>`;
  layout.replaceWith(section);
  section.querySelector('[data-empty-browse]')?.addEventListener('click',openLibraryBrowser);
  section.querySelector('[data-empty-add]')?.addEventListener('click',()=>{state.view='submit';render();});
  section.querySelectorAll('[data-empty-topic]').forEach(button=>button.addEventListener('click',()=>{libraryBrowserState.namespace=button.dataset.emptyTopic||'Nat';libraryBrowserState.result=null;libraryBrowserState.search='';openLibraryBrowser();}));
}

const libraryBrowserBaseBind=bind;
bind=function bindLibraryBrowser(){
  libraryBrowserBaseBind();
  mountLibraryBrowserButton();
  mountLibraryEmptyStart();
  if(state.view==='library'){
    const page=document.querySelector('.library-page');
    if(page)bindLibraryBrowserOverlay(page);
  }
};
window.addEventListener('keydown',event=>{if(event.key==='Escape'&&state.view==='library'&&!libraryBrowserState.importing){libraryBrowserState.open=false;state.view='registry';render();}});
mountLibraryBrowserButton();
