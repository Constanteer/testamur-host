/* Obsidian-style persistent global graph.
   The registry stays represented at all times. Curated mathematical areas and
   deterministic Lean-namespace clusters can both unfold progressively into the
   same Canvas without replacing nodes already on screen. */
const graphGlobalState={
  loaded:false,loading:false,nodes:new Map(),edges:new Map(),
  expandedAreas:new Set(),expandedClaims:new Set(),claimCount:0,clusterCount:0,
  libraryMode:false,libraryIndexed:false,libraryStatus:null,
  theoremDetails:new Map(),theoremLoading:new Set(),theoremErrors:new Map(),theoremImporting:new Set(),
};
let graphLibraryPollTimer=null;

function graphGlobalEdgeKey(edge){return `${edge.evidence_kind||'uses'}:${edge.from}:${edge.to}`;}
function graphGlobalGraph(){return {
  mode:'world',persistent:true,session_id:'mathhub-global-world-v2',root:null,depth:argumentState.depth||1,
  nodes:[...graphGlobalState.nodes.values()],edges:[...graphGlobalState.edges.values()],
  claim_count:graphGlobalState.claimCount,cluster_count:graphGlobalState.clusterCount,
};}
function graphIsCluster(nodeOrId){const id=typeof nodeOrId==='string'?nodeOrId:nodeOrId?.id;return String(id||'').startsWith('AREA-')||nodeOrId?.kind==='cluster';}
function graphGlobalNode(id){return graphGlobalState.nodes.get(id)||null;}
function graphClusterKey(node){
  if(node?.cluster_source==='mathlib-library')return `libns:${node?.namespace_path||node?.title||''}`;
  return node?.cluster_key||((node?.cluster_source==='curated-area')?`area:${node.title||''}`:`ns:${node?.namespace_path||node?.title||''}`);
}
function graphIsLibraryTheorem(node){return node?.kind==='library-theorem'||String(node?.id||'').startsWith('LIB:');}

function mergeWorldEdges(edges){
  for(const edge of edges||[]){const key=graphGlobalEdgeKey(edge),old=graphGlobalState.edges.get(key);if(!old)graphGlobalState.edges.set(key,{...edge});else graphGlobalState.edges.set(key,{...old,...edge,proof_count:Math.max(Number(old.proof_count||0),Number(edge.proof_count||0)),proof_ids:[...new Set([...(old.proof_ids||[]),...(edge.proof_ids||[])])]});}
}
function placeAround(anchorId,newNodes,radius=125){
  const anchor=graphCanvasState.positions.get(anchorId)||{x:0,y:0},golden=Math.PI*(3-Math.sqrt(5));
  newNodes.forEach((node,index)=>{if(graphCanvasState.positions.has(node.id))return;const r=radius+24*Math.sqrt(index),a=index*golden+stableAngle(node.id)*.17;graphCanvasState.positions.set(node.id,{x:anchor.x+Math.cos(a)*r,y:anchor.y+Math.sin(a)*r});});
}
function mergeWorldChunk(chunk,{anchorId='',radius=125}={}){
  const newcomers=[];
  for(const node of chunk?.nodes||[]){if(!graphGlobalState.nodes.has(node.id))newcomers.push(node);graphGlobalState.nodes.set(node.id,{...graphGlobalState.nodes.get(node.id),...node});}
  mergeWorldEdges(chunk?.edges||[]);
  if(anchorId&&newcomers.length)placeAround(anchorId,newcomers,radius);
  argumentState.graph=graphGlobalGraph();
  return newcomers;
}

async function loadLibraryStatus(){
  try{graphGlobalState.libraryStatus=await apiJson('/api/library/status');}
  catch(error){graphGlobalState.libraryStatus={error:String(error?.message||error)};}
  return graphGlobalState.libraryStatus;
}

async function loadLibraryTheoremDetail(name,{refresh=false}={}){
  name=String(name||'').trim();if(!name)return null;
  if(!refresh&&graphGlobalState.theoremDetails.has(name))return graphGlobalState.theoremDetails.get(name);
  if(graphGlobalState.theoremLoading.has(name))return null;
  graphGlobalState.theoremLoading.add(name);graphGlobalState.theoremErrors.delete(name);render();
  try{
    const suffix=refresh?'&refresh=1':'';
    const detail=await apiJson(`/api/library/theorem?name=${encodeURIComponent(name)}${suffix}`);
    if(!detail?.ok)throw new Error(detail?.error||'Theorem inspection failed');
    graphGlobalState.theoremDetails.set(name,detail);
    return detail;
  }catch(error){
    graphGlobalState.theoremErrors.set(name,error?.message||String(error));
    return null;
  }finally{
    graphGlobalState.theoremLoading.delete(name);render();
  }
}

function selectLibraryTheorem(name,{anchorId=''}={}){
  name=String(name||'').trim();if(!name)return;
  const id=`LIB:${name}`;
  let node=graphGlobalState.nodes.get(id);
  if(!node){
    node={
      id,kind:'library-theorem',lean_name:name,title:name.split('.').pop()||name,
      namespace_path:name.includes('.')?name.split('.').slice(0,-1).join('.'):'',
      status:'library',registered:false,claim_id:null,
    };
    graphGlobalState.nodes.set(id,node);
    if(anchorId)placeAround(anchorId,[node],118);
    argumentState.graph=graphGlobalGraph();
  }
  state.graphSelected=id;render();
  void loadLibraryTheoremDetail(name);
}

async function randomLibraryTheorem(){
  try{
    const result=await apiJson('/api/library/random');
    if(!result?.ok)throw new Error(result?.error||'No theorem available');
    selectLibraryTheorem(result.lean_name);
  }catch(error){
    graphGlobalState.libraryStatus={...(graphGlobalState.libraryStatus||{}),error:error?.message||String(error)};
    render();
  }
}

async function importLibraryTheorem(name){
  name=String(name||'').trim();if(!name||graphGlobalState.theoremImporting.has(name))return;
  graphGlobalState.theoremImporting.add(name);render();
  try{
    const result=await apiJson('/api/claims/import',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({declaration_name:name,imports:['Mathlib']}),
    });
    const id=`LIB:${name}`,node=graphGlobalState.nodes.get(id);
    if(node){
      node.registered=true;
      node.claim_id=result.claim_id||result.public_id||node.claim_id||null;
      graphGlobalState.nodes.set(id,node);
    }
    const detail=graphGlobalState.theoremDetails.get(name);
    if(detail)graphGlobalState.theoremDetails.set(name,{...detail,registered:true,claim_id:result.claim_id||result.public_id||null});
  }catch(error){
    graphGlobalState.theoremErrors.set(name,error?.message||String(error));
  }finally{
    graphGlobalState.theoremImporting.delete(name);render();
  }
}

function libraryDependencyList(title,names,anchorId){
  const items=[...new Set((names||[]).map(String))].slice(0,18);
  if(!items.length)return '';
  return `<section class="library-theorem-deps"><h3>${esc(title)}</h3><div class="library-theorem-dep-list">${items.map(name=>`<button type="button" data-library-theorem-name="${esc(name)}" data-library-anchor="${esc(anchorId||'')}"><code>${esc(name)}</code></button>`).join('')}</div></section>`;
}

function scheduleLibraryGraphPoll(){
  if(graphLibraryPollTimer)clearTimeout(graphLibraryPollTimer);
  graphLibraryPollTimer=setTimeout(async()=>{
    graphLibraryPollTimer=null;
    if(state.view!=='graph'||graphGlobalState.claimCount>0||graphGlobalState.libraryIndexed)return;
    await loadGlobalWorld({reset:true});
  },2500);
}

async function loadGlobalWorld({reset=false}={}){
  if(graphGlobalState.loading)return;
  if(graphGlobalState.loaded&&!reset){argumentState.graph=graphGlobalGraph();render();return;}
  graphGlobalState.loading=true;
  try{
    let world=await apiJson('/api/graph/world');
    graphGlobalState.claimCount=Number(world.claim_count||0);
    graphGlobalState.libraryMode=false;graphGlobalState.libraryIndexed=false;graphGlobalState.libraryStatus=null;
    if(graphGlobalState.claimCount===0){
      const library=await apiJson('/api/library/world');
      if(library?.indexed){
        world=library;
        graphGlobalState.libraryMode=true;
        graphGlobalState.libraryIndexed=true;
      }else{
        await loadLibraryStatus();
      }
    }
    graphGlobalState.nodes.clear();graphGlobalState.edges.clear();graphGlobalState.expandedAreas.clear();graphGlobalState.expandedClaims.clear();
    for(const node of world.nodes||[])graphGlobalState.nodes.set(node.id,node);mergeWorldEdges(world.edges||[]);
    graphGlobalState.clusterCount=Number(world.cluster_count||0);graphGlobalState.loaded=true;
    argumentState.graph=graphGlobalGraph();graphCanvasState.key='';graphCanvasState.positions=new Map();state.graphSelected=(world.nodes||[])[0]?.id||'';
  }finally{graphGlobalState.loading=false;render();}
}

async function expandGlobalArea(clusterId){
  const cluster=graphGlobalNode(clusterId);if(!cluster||!graphIsCluster(cluster)||graphGlobalState.loading)return;
  const key=graphClusterKey(cluster);if(!key)return;
  graphGlobalState.loading=true;state.graphSelected=clusterId;render();
  try{
    const chunk=cluster.cluster_source==='mathlib-library'
      ? await apiJson(`/api/library/cluster?namespace=${encodeURIComponent(cluster.namespace_path||cluster.title||'')}&limit=100`)
      : await apiJson(`/api/graph/cluster?key=${encodeURIComponent(key)}&limit=100`);
    mergeWorldChunk(chunk,{anchorId:clusterId,radius:105});graphGlobalState.expandedAreas.add(key);
  }finally{graphGlobalState.loading=false;argumentState.graph=graphGlobalGraph();render();}
}

async function expandGlobalClaim(claimId){
  if(!claimId||graphIsCluster(claimId)||graphGlobalState.loading)return;
  if(graphIsLibraryTheorem(graphGlobalNode(claimId)))return;
  graphGlobalState.loading=true;state.graphSelected=claimId;render();
  try{
    const params=new URLSearchParams({claim:claimId,depth:String(argumentState.depth||1),limit:String(argumentState.depth===2?120:80),relations:'uses'});
    const chunk=await apiJson(`/api/graph?${params}`);mergeWorldChunk(chunk,{anchorId:claimId,radius:150});graphGlobalState.expandedClaims.add(claimId);
    if(typeof graphTrailRecord==='function')graphTrailRecord(claimId);if(typeof syncGraphUrl==='function')syncGraphUrl(claimId,{push:true});
  }finally{graphGlobalState.loading=false;argumentState.graph=graphGlobalGraph();render();}
}

loadGraph=async function persistentGlobalLoad(root=''){
  if(!graphGlobalState.loaded)await loadGlobalWorld();
  if(!root)return;
  const node=graphGlobalNode(root);
  if(graphIsCluster(node||root))await expandGlobalArea(root);else await expandGlobalClaim(root);
};

graphCanvasKey=function globalCanvasKey(graph){return graph?.persistent?'mathhub-global-world-v2':`local:${graph?.root||''}`;};
const graphGlobalBaseLayout=buildCanvasLayout;
buildCanvasLayout=function globalInitialLayout(graph){
  if(!graph?.persistent)return graphGlobalBaseLayout(graph);
  const positions=new Map(),clusters=(graph.nodes||[]).filter(graphIsCluster),claims=(graph.nodes||[]).filter(node=>!graphIsCluster(node));
  const golden=Math.PI*(3-Math.sqrt(5));
  clusters.sort((a,b)=>Number(b.claim_count||0)-Number(a.claim_count||0)||String(a.title).localeCompare(String(b.title)));
  clusters.forEach((node,index)=>{const radius=index?190*Math.sqrt(index):0,angle=index*golden;positions.set(node.id,{x:Math.cos(angle)*radius,y:Math.sin(angle)*radius});});
  for(const node of claims){const parent=positions.get(node.parent_cluster_id),i=claims.indexOf(node),angle=i*golden+stableAngle(node.id),anchor=parent||{x:0,y:0};positions.set(node.id,{x:anchor.x+Math.cos(angle)*(95+16*Math.sqrt(i)),y:anchor.y+Math.sin(angle)*(95+16*Math.sqrt(i))});}
  return positions;
};

const graphGlobalBaseDraw=drawGraphCanvas;
drawGraphCanvas=function drawGlobalGraph(canvas){
  const graph=argumentState.graph;if(!canvas||!graph?.persistent){graphGlobalBaseDraw(canvas);return;}
  const original=argumentState.graph;
  argumentState.graph={...graph,nodes:(graph.nodes||[]).map(node=>graphIsCluster(node)?{...node,title:'',lean_name:''}:node)};
  graphGlobalBaseDraw(canvas);argumentState.graph=original;
  const ctx=canvas.getContext('2d'),palette=graphCanvasPalette(canvas),selected=state.graphSelected;
  ctx.save();
  for(const node of graph.nodes||[]){if(!graphIsCluster(node))continue;const world=graphCanvasState.positions.get(node.id);if(!world)continue;const p=graphScreenPoint(world),r=Math.max(18,Math.min(46,18+Math.sqrt(Number(node.claim_count||1))*2.1))*Math.max(.72,Math.min(1.25,graphCanvasState.view.scale));
    ctx.beginPath();ctx.arc(p.x,p.y,r,0,Math.PI*2);ctx.fillStyle=node.cluster_source==='curated-area'?'rgba(61,91,121,.055)':'rgba(75,105,95,.055)';ctx.fill();ctx.strokeStyle=node.id===selected?palette.ink:(node.cluster_source==='curated-area'?'rgba(61,91,121,.30)':'rgba(75,105,95,.28)');ctx.lineWidth=node.id===selected?2:1.2;ctx.stroke();
    ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillStyle=palette.ink;ctx.font=`600 ${node.id===selected?13:11}px ui-serif, Georgia, serif`;ctx.fillText(String(node.title||'Cluster').split('.').slice(-1)[0].slice(0,34),p.x,p.y-2);ctx.font='9px ui-sans-serif,system-ui,sans-serif';ctx.fillStyle=palette.muted;ctx.fillText(`${node.claim_count||0} ${graphGlobalState.libraryMode?'theorems':'claims'}`,p.x,p.y+13);
  }
  ctx.restore();
};

function globalInspector(node){
  if(!node)return '<div class="global-inspector-empty">Select a namespace, theorem or Claim.</div>';
  if(graphIsCluster(node)){
    const key=graphClusterKey(node),expanded=graphGlobalState.expandedAreas.has(key),library=node.cluster_source==='mathlib-library',namespace=node.cluster_source==='lean-namespace',kind=library?'Mathlib namespace':(namespace?'Lean namespace':'Curated field'),path=node.namespace_path||node.title,action=expanded?'Expand again':(library||namespace?'Expand namespace':'Expand field');
    const countLabel=library?'theorems':'Claims';
    const copy=library
      ? 'Read-only theorems from the pinned Mathlib environment. Expand freely; nothing becomes a Claim until you explicitly import it.'
      : (namespace?'This deterministic fallback comes from the Lean declaration namespace. Expanding it reveals the next namespace level before theorem detail when available.':'This explicit mathematical area overrides namespace grouping. Expanding it reveals real Claims while the field remains as an anchor.');
    return `<div class="global-inspector-kind">${esc(kind)}</div><h2>${esc(path)}</h2><strong class="global-claim-count">${Number(node.claim_count||0).toLocaleString()} ${countLabel}</strong><p>${esc(copy)}</p><button class="btn btn-primary" data-expand-global-area="${esc(node.id)}">${esc(action)}</button>`;
  }
  if(graphIsLibraryTheorem(node)){
    const name=String(node.lean_name||node.title||''),detail=graphGlobalState.theoremDetails.get(name),loading=graphGlobalState.theoremLoading.has(name),error=graphGlobalState.theoremErrors.get(name),importing=graphGlobalState.theoremImporting.has(name),registered=Boolean(node.registered||detail?.registered),claimId=node.claim_id||detail?.claim_id||'';
    if(loading&&!detail)return `<div class="global-inspector-kind">Mathlib theorem · read-only</div><code>${esc(name)}</code><h2>${esc(node.title||'Theorem')}</h2><div class="library-theorem-loading"><span></span>Inspecting theorem with Lean…</div>`;
    if(error&&!detail)return `<div class="global-inspector-kind">Mathlib theorem · read-only</div><code>${esc(name)}</code><h2>${esc(node.title||'Theorem')}</h2><div class="library-theorem-error">${esc(error)}</div><button class="btn" data-library-inspect-refresh="${esc(name)}">Retry inspection</button>`;
    if(!detail)return `<div class="global-inspector-kind">Mathlib theorem · read-only</div><code>${esc(name)}</code><h2>${esc(node.title||'Theorem')}</h2><p>Selecting a theorem inspects its statement on demand. This does not create a Claim.</p>`;
    const axioms=(detail.axioms||[]).map(item=>`<code>${esc(item)}</code>`).join(' ');
    const statementDeps=libraryDependencyList('Statement references',detail.statement_dependencies,node.id);
    const proofDeps=libraryDependencyList('Proof references',detail.proof_dependencies,node.id);
    const q=encodeURIComponent(name);
    const action=registered&&claimId
      ? `<button class="btn btn-primary" data-claim="${esc(claimId)}">Open Claim</button>`
      : `<button class="btn btn-primary" data-library-import-theorem="${esc(name)}" ${importing?'disabled':''}>${importing?'Importing…':'Import as Claim'}</button>`;
    return `<div class="global-inspector-kind">Mathlib theorem · read-only${detail.cache_hit?' · cached':''}</div>
      <code class="library-theorem-name">${esc(name)}</code>
      <h2>${esc(detail.short_name||node.title||'Theorem')}</h2>
      <pre class="library-theorem-type">${esc(detail.pretty_type||'Type unavailable')}</pre>
      ${axioms?`<div class="library-theorem-axioms"><span>Axioms</span>${axioms}</div>`:''}
      ${statementDeps}${proofDeps}
      <div class="library-theorem-meta"><span>${Number(detail.statement_dependency_count||0)} statement refs</span><span>${Number(detail.proof_dependency_count||0)} proof refs</span><span>${Number(detail.duration_ms||0)} ms first inspection</span></div>
      ${error?`<div class="library-theorem-error">${esc(error)}</div>`:''}
      <div class="graph-inspector-actions">${action}<button class="btn" data-library-inspect-refresh="${esc(name)}">Re-inspect</button><a class="btn" href="./?view=library&q=${q}">Open in Math library</a></div>`;
  }
  const relations=typeof graphRelations==='function'?graphRelations(node.id):{outgoing:[],incoming:[]};
  return `<div class="global-inspector-kind">Claim</div><code>${esc(node.lean_name||'')}</code><h2>${esc(node.title||'Claim')}</h2>${badge(node.status||'open')}<p>${esc(node.area||'')}</p><div class="graph-inspector-actions"><button class="btn btn-primary" data-claim="${esc(node.id)}">Open Claim</button><button class="btn" data-expand-global-claim="${esc(node.id)}">Expand neighbors</button></div>${typeof graphRelationList==='function'?graphRelationList('Depends on',relations.outgoing,'No loaded dependencies yet.')+graphRelationList('Used by',relations.incoming,'No loaded uses yet.'):''}`;
}

renderGraph=function renderPersistentGlobalGraph(){
  const graph=argumentState.graph;
  if(!graphGlobalState.loaded||!graph)return `<main class="global-graph-page"><div class="global-graph-loading"><div class="eyebrow">Global argument graph</div><h1>Opening the mathematical universe…</h1><p>MathHub shows durable Claims when they exist and otherwise reads the pinned Mathlib library directly.</p><button class="btn btn-primary" data-load-global-world>Open global graph</button></div></main>`;
  if(graphGlobalState.claimCount===0&&!graphGlobalState.libraryIndexed){
    const status=graphGlobalState.libraryStatus||{},scanState=String(status.state||'idle'),attempts=Number(status.attempts||0),error=String(status.last_error||status.error||'');
    const failed=scanState==='error'||scanState==='retry_wait';
    const heading=failed?'Mathlib index retrying…':scanState==='scanning'?'Indexing Mathlib once…':'Preparing Mathlib index…';
    const copy=failed
      ? 'The last one-time Lean scan failed. MathHub is retrying with backoff; no Claims, Proofs or Builds are being created.'
      : 'Lean is scanning theorem names from the pinned environment into a read-only navigation cache. This happens once per Mathlib environment.';
    return `<main class="global-graph-page"><section class="global-starter-empty">
      <div class="eyebrow">Mathlib library substrate</div><div class="global-starter-spinner" aria-hidden="true"></div>
      <h1>${esc(heading)}</h1>
      <p>${esc(copy)}</p>
      <div class="global-starter-worker-state"><strong>State: ${esc(scanState)}</strong><small>${attempts? `attempt ${attempts}` : 'waiting to start'}</small></div>
      ${error?`<details class="global-starter-detail" open><summary>Technical detail</summary><code>${esc(error)}</code></details>`:''}
      <div class="global-starter-actions"><button class="btn btn-primary" data-refresh-library-world>Refresh now</button><a class="btn" href="./?view=library">Open Math library</a></div>
      <small>After this environment scan, Connections and Math library read SQLite and do not rerun Lean.</small>
    </section></main>`;
  }
  const selected=graphGlobalNode(state.graphSelected)||graph.nodes?.[0],nonClusters=(graph.nodes||[]).filter(node=>!graphIsCluster(node)).length,focus=!graphGlobalState.libraryMode&&typeof graphProofFocusHtml==='function'?graphProofFocusHtml():'',trail=!graphGlobalState.libraryMode&&typeof graphTrailHtml==='function'?graphTrailHtml():'';
  const title=graphGlobalState.libraryMode?'Mathlib, directly from the pinned environment.':'Mathematics, all in one field of view.';
  const copy=graphGlobalState.libraryMode
    ? 'This is read-only library substrate, not a set of MathHub Claims. Expand namespaces to browse theorems; import only what you actually want to track.'
    : 'Curated areas take priority; unclassified formal mathematics organizes itself deterministically by Lean namespace. Double-click clusters to unfold the hierarchy, then Claims to grow Proof neighborhoods.';
  const represented=graphGlobalState.libraryMode
    ? `${[...graphGlobalState.nodes.values()].filter(node=>graphIsCluster(node)).reduce((sum,node)=>sum+Number(node.library_count||0),0).toLocaleString()} Mathlib theorems indexed`
    : `${Number(graphGlobalState.claimCount).toLocaleString()} Claims represented`;
  return `<main class="global-graph-page"><div class="global-graph-top"><div><div class="eyebrow">${graphGlobalState.libraryMode?'Read-only library graph':'Global argument graph'}</div><h1>${esc(title)}</h1><p>${esc(copy)}</p></div><div class="global-graph-actions">${graphGlobalState.libraryMode?'<button class="btn" data-random-library-theorem>Random theorem</button>':`<button class="filter ${argumentState.depth===1?'active':''}" data-depth="1">1 hop</button><button class="filter ${argumentState.depth===2?'active':''}" data-depth="2">2 hops</button>`}<button class="btn" data-reset-global-world>Reset world</button></div></div>${!graphGlobalState.libraryMode&&typeof graphSearchHtml==='function'?graphSearchHtml():''}${trail}${focus}<div class="global-world-meta"><span>${represented}</span><span>${graphGlobalState.clusterCount} root namespaces${graphGlobalState.libraryMode?'':' / clusters'}</span><span>${nonClusters} ${graphGlobalState.libraryMode?'theorems':'Claims'} unfolded</span><span>${graphGlobalState.expandedAreas.size} clusters expanded</span>${graphGlobalState.loading?'<strong>growing graph…</strong>':''}</div><div class="global-graph-stage"><canvas id="argument-graph-canvas-element" aria-label="Global mathematical graph"></canvas><div class="global-graph-hint">drag · zoom · click to inspect · double-click to unfold</div><aside class="global-graph-inspector">${globalInspector(selected)}</aside></div></main>`;
};

const graphGlobalBaseBind=bind;
bind=function bindPersistentGlobalGraph(){
  graphGlobalBaseBind();if(state.view!=='graph')return;
  if(!graphGlobalState.loaded&&!graphGlobalState.loading)loadGlobalWorld();
  document.querySelector('[data-load-global-world]')?.addEventListener('click',()=>loadGlobalWorld());
  document.querySelector('[data-refresh-library-world]')?.addEventListener('click',()=>loadGlobalWorld({reset:true}));
  if(graphGlobalState.loaded&&graphGlobalState.claimCount===0&&!graphGlobalState.libraryIndexed)scheduleLibraryGraphPoll();
  else if(graphLibraryPollTimer){clearTimeout(graphLibraryPollTimer);graphLibraryPollTimer=null;}
  document.querySelector('[data-reset-global-world]')?.addEventListener('click',()=>loadGlobalWorld({reset:true}));
  document.querySelector('[data-random-library-theorem]')?.addEventListener('click',()=>randomLibraryTheorem());
  document.querySelector('[data-expand-global-area]')?.addEventListener('click',event=>expandGlobalArea(event.currentTarget.dataset.expandGlobalArea));
  document.querySelector('[data-expand-global-claim]')?.addEventListener('click',event=>expandGlobalClaim(event.currentTarget.dataset.expandGlobalClaim));
  document.querySelectorAll('[data-library-theorem-name]').forEach(button=>button.addEventListener('click',()=>selectLibraryTheorem(button.dataset.libraryTheoremName,{anchorId:button.dataset.libraryAnchor||state.graphSelected})));
  document.querySelector('[data-library-import-theorem]')?.addEventListener('click',event=>importLibraryTheorem(event.currentTarget.dataset.libraryImportTheorem));
  document.querySelector('[data-library-inspect-refresh]')?.addEventListener('click',event=>loadLibraryTheoremDetail(event.currentTarget.dataset.libraryInspectRefresh,{refresh:true}));
  const selected=graphGlobalNode(state.graphSelected);
  if(graphIsLibraryTheorem(selected)){
    const name=String(selected.lean_name||selected.title||'');
    if(name&&!graphGlobalState.theoremDetails.has(name)&&!graphGlobalState.theoremLoading.has(name)&&!graphGlobalState.theoremErrors.has(name))void loadLibraryTheoremDetail(name);
  }
};

restoreGraphLocation=async function restoreGlobalGraphLocation(){
  const target=typeof graphStateFromLocation==='function'?graphStateFromLocation():null;if(!target)return;
  if(typeof graphWebState!=='undefined')graphWebState.restoring=true;
  try{state.view='graph';argumentState.depth=target.depth||1;await loadGlobalWorld();if(target.claim){state.graphSelected=target.claim;await expandGlobalClaim(target.claim);}}
  finally{if(typeof graphWebState!=='undefined')graphWebState.restoring=false;render();}
};
