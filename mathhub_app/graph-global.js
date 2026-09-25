/* Obsidian-style persistent global graph.
   The registry stays represented at all times. Curated mathematical areas and
   deterministic Lean-namespace clusters can both unfold progressively into the
   same Canvas without replacing nodes already on screen. */
const graphGlobalState={
  loaded:false,loading:false,nodes:new Map(),edges:new Map(),
  expandedAreas:new Set(),expandedClaims:new Set(),claimCount:0,clusterCount:0,starterStatus:null,
};
let graphStarterPollTimer=null;

function graphGlobalEdgeKey(edge){return `${edge.evidence_kind||'uses'}:${edge.from}:${edge.to}`;}
function graphGlobalGraph(){return {
  mode:'world',persistent:true,session_id:'mathhub-global-world-v2',root:null,depth:argumentState.depth||1,
  nodes:[...graphGlobalState.nodes.values()],edges:[...graphGlobalState.edges.values()],
  claim_count:graphGlobalState.claimCount,cluster_count:graphGlobalState.clusterCount,
};}
function graphIsCluster(nodeOrId){const id=typeof nodeOrId==='string'?nodeOrId:nodeOrId?.id;return String(id||'').startsWith('AREA-')||nodeOrId?.kind==='cluster';}
function graphGlobalNode(id){return graphGlobalState.nodes.get(id)||null;}
function graphClusterKey(node){return node?.cluster_key||((node?.cluster_source==='curated-area')?`area:${node.title||''}`:`ns:${node?.namespace_path||node?.title||''}`);}

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

async function loadStarterStatus(){
  try{graphGlobalState.starterStatus=await apiJson('/api/starter-status');}
  catch(error){graphGlobalState.starterStatus={error:String(error?.message||error)};}
  return graphGlobalState.starterStatus;
}

function scheduleStarterGraphPoll(){
  if(graphStarterPollTimer)clearTimeout(graphStarterPollTimer);
  graphStarterPollTimer=setTimeout(async()=>{
    graphStarterPollTimer=null;
    if(state.view!=='graph'||graphGlobalState.claimCount>0)return;
    await loadGlobalWorld({reset:true});
  },2500);
}

async function loadGlobalWorld({reset=false}={}){
  if(graphGlobalState.loading)return;
  if(graphGlobalState.loaded&&!reset){argumentState.graph=graphGlobalGraph();render();return;}
  graphGlobalState.loading=true;
  try{
    const world=await apiJson('/api/graph/world');
    graphGlobalState.nodes.clear();graphGlobalState.edges.clear();graphGlobalState.expandedAreas.clear();graphGlobalState.expandedClaims.clear();
    for(const node of world.nodes||[])graphGlobalState.nodes.set(node.id,node);mergeWorldEdges(world.edges||[]);
    graphGlobalState.claimCount=Number(world.claim_count||0);graphGlobalState.clusterCount=Number(world.cluster_count||0);graphGlobalState.loaded=true;
    if(graphGlobalState.claimCount===0)await loadStarterStatus();else graphGlobalState.starterStatus=null;
    argumentState.graph=graphGlobalGraph();graphCanvasState.key='';graphCanvasState.positions=new Map();state.graphSelected=(world.nodes||[])[0]?.id||'';
  }finally{graphGlobalState.loading=false;render();}
}

async function expandGlobalArea(clusterId){
  const cluster=graphGlobalNode(clusterId);if(!cluster||!graphIsCluster(cluster)||graphGlobalState.loading)return;
  const key=graphClusterKey(cluster);if(!key)return;
  graphGlobalState.loading=true;state.graphSelected=clusterId;render();
  try{
    const chunk=await apiJson(`/api/graph/cluster?key=${encodeURIComponent(key)}&limit=100`);
    mergeWorldChunk(chunk,{anchorId:clusterId,radius:105});graphGlobalState.expandedAreas.add(key);
  }finally{graphGlobalState.loading=false;argumentState.graph=graphGlobalGraph();render();}
}

async function expandGlobalClaim(claimId){
  if(!claimId||graphIsCluster(claimId)||graphGlobalState.loading)return;
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
    ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillStyle=palette.ink;ctx.font=`600 ${node.id===selected?13:11}px ui-serif, Georgia, serif`;ctx.fillText(String(node.title||'Cluster').split('.').slice(-1)[0].slice(0,34),p.x,p.y-2);ctx.font='9px ui-sans-serif,system-ui,sans-serif';ctx.fillStyle=palette.muted;ctx.fillText(`${node.claim_count||0} claims`,p.x,p.y+13);
  }
  ctx.restore();
};

function globalInspector(node){
  if(!node)return '<div class="global-inspector-empty">Select a cluster or Claim.</div>';
  if(graphIsCluster(node)){
    const key=graphClusterKey(node),expanded=graphGlobalState.expandedAreas.has(key),namespace=node.cluster_source==='lean-namespace',kind=namespace?'Lean namespace':'Curated field',path=namespace?(node.namespace_path||node.title):node.title,action=expanded?'Expand again':(namespace?'Expand namespace':'Expand field');
    return `<div class="global-inspector-kind">${esc(kind)}</div><h2>${esc(path)}</h2><strong class="global-claim-count">${Number(node.claim_count||0).toLocaleString()} Claims</strong><p>${namespace?'This deterministic fallback comes from the Lean declaration namespace. Expanding it reveals the next namespace level before theorem detail when available.':'This explicit mathematical area overrides namespace grouping. Expanding it reveals real Claims while the field remains as an anchor.'}</p><button class="btn btn-primary" data-expand-global-area="${esc(node.id)}">${esc(action)}</button>`;
  }
  const relations=typeof graphRelations==='function'?graphRelations(node.id):{outgoing:[],incoming:[]};
  return `<div class="global-inspector-kind">Claim</div><code>${esc(node.lean_name||'')}</code><h2>${esc(node.title||'Claim')}</h2>${badge(node.status||'open')}<p>${esc(node.area||'')}</p><div class="graph-inspector-actions"><button class="btn btn-primary" data-claim="${esc(node.id)}">Open Claim</button><button class="btn" data-expand-global-claim="${esc(node.id)}">Expand neighbors</button></div>${typeof graphRelationList==='function'?graphRelationList('Depends on',relations.outgoing,'No loaded dependencies yet.')+graphRelationList('Used by',relations.incoming,'No loaded uses yet.'):''}`;
}

renderGraph=function renderPersistentGlobalGraph(){
  const graph=argumentState.graph;
  if(!graphGlobalState.loaded||!graph)return `<main class="global-graph-page"><div class="global-graph-loading"><div class="eyebrow">Global argument graph</div><h1>Opening the mathematical universe…</h1><p>Small sparse registries open as literal Claim graphs. Larger registries keep every Claim represented through curated fields or Lean-namespace clusters.</p><button class="btn btn-primary" data-load-global-world>Open global graph</button></div></main>`;
  if(graphGlobalState.claimCount===0){
    const starter=graphGlobalState.starterStatus||{},task=starter.task||{},status=String(task.status||''),lastError=String(task.last_error||starter.error||''),imported=Number(task.imported||0),failed=Number(task.failed||0),processed=Number(task.processed_claims||0),scheduled=Number(task.scheduled_claims||0);
    const heading=status==='paused'?'Starter import paused':status==='completed'?'Starter import finished without Claims':'Preparing starter mathematics…';
    const copy=status==='paused'
      ? 'Mathlib is ready, but the starter import paused. MathHub will retry recoverable work automatically.'
      : status==='completed'
        ? 'The starter task completed without populating the registry. Its task detail is shown below instead of an empty graph.'
        : 'Mathlib is ready. MathHub is importing a small deterministic starter set so Connections has real Claims to display.';
    return `<main class="global-graph-page"><section class="global-starter-empty">
      <div class="eyebrow">Global argument graph</div><div class="global-starter-spinner" aria-hidden="true"></div>
      <h1>${esc(heading)}</h1><p>${esc(copy)}</p>
      <div class="global-starter-stats">
        <span><strong>${processed}</strong> processed</span><span><strong>${scheduled}</strong> scheduled</span><span><strong>${imported}</strong> imported</span><span><strong>${failed}</strong> failed</span>
      </div>
      ${lastError?`<details class="global-starter-detail"><summary>Technical detail</summary><code>${esc(lastError)}</code></details>`:''}
      <div class="global-starter-actions"><button class="btn btn-primary" data-refresh-starter-world>Refresh now</button><a class="btn" href="./?view=library">Open Math library</a></div>
      <small>Connections will appear automatically as soon as the first starter Claims are recorded.</small>
    </section></main>`;
  }
  const selected=graphGlobalNode(state.graphSelected)||graph.nodes?.[0],realClaims=(graph.nodes||[]).filter(node=>!graphIsCluster(node)).length,focus=typeof graphProofFocusHtml==='function'?graphProofFocusHtml():'',trail=typeof graphTrailHtml==='function'?graphTrailHtml():'';
  return `<main class="global-graph-page"><div class="global-graph-top"><div><div class="eyebrow">Global argument graph</div><h1>Mathematics, all in one field of view.</h1><p>Curated areas take priority; unclassified formal mathematics organizes itself deterministically by Lean namespace. Double-click clusters to unfold the hierarchy, then Claims to grow Proof neighborhoods.</p></div><div class="global-graph-actions"><button class="filter ${argumentState.depth===1?'active':''}" data-depth="1">1 hop</button><button class="filter ${argumentState.depth===2?'active':''}" data-depth="2">2 hops</button><button class="btn" data-reset-global-world>Reset world</button></div></div>${typeof graphSearchHtml==='function'?graphSearchHtml():''}${trail}${focus}<div class="global-world-meta"><span>${Number(graphGlobalState.claimCount).toLocaleString()} Claims represented</span><span>${graphGlobalState.clusterCount} root clusters</span><span>${realClaims} Claims unfolded</span><span>${graphGlobalState.expandedAreas.size} clusters expanded</span>${graphGlobalState.loading?'<strong>growing graph…</strong>':''}</div><div class="global-graph-stage"><canvas id="argument-graph-canvas-element" aria-label="Global mathematical argument graph"></canvas><div class="global-graph-hint">drag · zoom · click to inspect · double-click to unfold</div><aside class="global-graph-inspector">${globalInspector(selected)}</aside></div></main>`;
};

const graphGlobalBaseBind=bind;
bind=function bindPersistentGlobalGraph(){
  graphGlobalBaseBind();if(state.view!=='graph')return;
  if(!graphGlobalState.loaded&&!graphGlobalState.loading)loadGlobalWorld();
  document.querySelector('[data-load-global-world]')?.addEventListener('click',()=>loadGlobalWorld());
  document.querySelector('[data-refresh-starter-world]')?.addEventListener('click',()=>loadGlobalWorld({reset:true}));
  if(graphGlobalState.loaded&&graphGlobalState.claimCount===0)scheduleStarterGraphPoll();
  else if(graphStarterPollTimer){clearTimeout(graphStarterPollTimer);graphStarterPollTimer=null;}
  document.querySelector('[data-reset-global-world]')?.addEventListener('click',()=>loadGlobalWorld({reset:true}));
  document.querySelector('[data-expand-global-area]')?.addEventListener('click',event=>expandGlobalArea(event.currentTarget.dataset.expandGlobalArea));
  document.querySelector('[data-expand-global-claim]')?.addEventListener('click',event=>expandGlobalClaim(event.currentTarget.dataset.expandGlobalClaim));
};

restoreGraphLocation=async function restoreGlobalGraphLocation(){
  const target=typeof graphStateFromLocation==='function'?graphStateFromLocation():null;if(!target)return;
  if(typeof graphWebState!=='undefined')graphWebState.restoring=true;
  try{state.view='graph';argumentState.depth=target.depth||1;await loadGlobalWorld();if(target.claim){state.graphSelected=target.claim;await expandGlobalClaim(target.claim);}}
  finally{if(typeof graphWebState!=='undefined')graphWebState.restoring=false;render();}
};
