/* Mathematical navigation layer over the bounded Canvas graph.
   Search is server-backed. Claim edges remain Proof-derived; Proof witnesses are
   fetched lazily only when a reader expands one relation in the inspector. */
const graphNavigatorState={
  query:'',results:[],searching:false,serial:0,timer:null,
  proofArtifacts:new Map(),proofLoading:new Set(),
};

function graphNodeMap(){return new Map((argumentState.graph?.nodes||[]).map(node=>[node.id,node]));}
function graphArea(node){return String(node?.area||'').trim()||'Unclassified';}
function graphEdgeKey(edge){return `${edge.from}::${edge.to}`;}

/* Recent slices are grouped by mathematical field. Rooted neighborhoods still use
   dependency distance, but Claims in the same field occupy contiguous sectors. */
const graphNavigationBaseLayout=buildCanvasLayout;
buildCanvasLayout=function mathematicalClusterLayout(graph){
  const nodes=graph?.nodes||[];
  graphCanvasState.clusters=[];
  if(!nodes.length)return new Map();
  const rootId=(graph?.root&&nodes.some(node=>node.id===graph.root))?graph.root:null;
  if(!rootId){
    const groups=new Map();
    for(const node of nodes){const area=graphArea(node);if(!groups.has(area))groups.set(area,[]);groups.get(area).push(node);}
    const entries=[...groups.entries()].sort((a,b)=>b[1].length-a[1].length||a[0].localeCompare(b[0]));
    const cols=Math.max(1,Math.ceil(Math.sqrt(entries.length))),rows=Math.ceil(entries.length/cols),positions=new Map();
    const xGap=330,yGap=270;
    entries.forEach(([area,items],groupIndex)=>{
      const col=groupIndex%cols,row=Math.floor(groupIndex/cols),cx=(col-(cols-1)/2)*xGap,cy=(row-(rows-1)/2)*yGap;
      items.sort((a,b)=>String(a.title||a.id).localeCompare(String(b.title||b.id)));
      items.forEach((node,index)=>{
        const angle=index*Math.PI*(3-Math.sqrt(5))+stableAngle(node.id)*.05,radius=index?28*Math.sqrt(index):0;
        positions.set(node.id,{x:cx+Math.cos(angle)*radius,y:cy+Math.sin(angle)*radius});
      });
      const radius=Math.max(72,35*Math.sqrt(Math.max(1,items.length))+36);
      graphCanvasState.clusters.push({label:area,count:items.length,x:cx,y:cy,radius});
    });
    return positions;
  }

  const adjacency=graphAdjacency(graph),level=new Map([[rootId,0]]),queue=[rootId];
  for(let i=0;i<queue.length;i++){
    const current=queue[i],next=(level.get(current)||0)+1;
    for(const neighbor of adjacency.get(current)||[]){if(!level.has(neighbor)){level.set(neighbor,next);queue.push(neighbor);}}
  }
  const positions=new Map([[rootId,{x:0,y:0}]]),rings=new Map();
  for(const node of nodes){if(node.id===rootId)continue;const ring=level.get(node.id)||Math.max(1,Number(graph.depth)||1);if(!rings.has(ring))rings.set(ring,[]);rings.get(ring).push(node);}
  for(const [ring,items] of [...rings.entries()].sort((a,b)=>a[0]-b[0])){
    items.sort((a,b)=>graphArea(a).localeCompare(graphArea(b))||String(a.title||a.id).localeCompare(String(b.title||b.id)));
    const radius=155*ring,offset=stableAngle(rootId)*.28+ring*.37;
    items.forEach((node,index)=>{const angle=offset+Math.PI*2*index/Math.max(1,items.length);positions.set(node.id,{x:Math.cos(angle)*radius,y:Math.sin(angle)*radius});});
  }
  return positions;
};

const graphNavigationBaseDraw=drawGraphCanvas;
drawGraphCanvas=function drawMathematicalClusters(canvas){
  graphNavigationBaseDraw(canvas);
  if(!canvas||!graphCanvasState.clusters?.length)return;
  const ctx=canvas.getContext('2d'),palette=graphCanvasPalette(canvas),scale=graphCanvasState.view.scale;
  ctx.save();ctx.textAlign='center';ctx.textBaseline='middle';
  for(const cluster of graphCanvasState.clusters){
    const point=graphScreenPoint(cluster),r=Math.max(38,cluster.radius*scale);
    ctx.beginPath();ctx.arc(point.x,point.y,r,0,Math.PI*2);ctx.strokeStyle='rgba(90,105,100,.12)';ctx.lineWidth=1;ctx.setLineDash([5,7]);ctx.stroke();ctx.setLineDash([]);
    if(scale>.36){ctx.font='600 10px ui-sans-serif, system-ui, sans-serif';ctx.fillStyle=palette.muted;ctx.fillText(`${cluster.label} · ${cluster.count}`,point.x,point.y-r-10);}
  }
  ctx.restore();
};

function graphRelations(selectedId){
  const graph=argumentState.graph,nodes=graphNodeMap(),outgoing=[],incoming=[];
  if(!graph||!selectedId)return {outgoing,incoming};
  for(const edge of graph.edges||[]){
    if(edge.from===selectedId&&nodes.has(edge.to))outgoing.push({edge,node:nodes.get(edge.to)});
    if(edge.to===selectedId&&nodes.has(edge.from))incoming.push({edge,node:nodes.get(edge.from)});
  }
  const sort=(a,b)=>String(a.node.title||'').localeCompare(String(b.node.title||''));outgoing.sort(sort);incoming.sort(sort);
  return {outgoing,incoming};
}

function graphRelationList(title,items,empty){
  return `<section class="graph-relation-section"><h3>${esc(title)}</h3>${items.length?items.slice(0,12).map(({edge,node})=>{
    const count=Number(edge.proof_count||edge.proof_ids?.length||1),key=graphEdgeKey(edge);
    return `<div class="graph-relation-row"><button class="graph-relation-target" data-graph-select="${esc(node.id)}"><strong>${esc(node.title||node.lean_name||'Claim')}</strong><code>${esc(node.lean_name||'')}</code></button><button class="graph-proof-count" data-graph-edge-proofs="${esc(key)}">${count} proof${count===1?'':'s'}</button><div class="graph-edge-proof-slot" data-graph-edge-slot="${esc(key)}"></div></div>`;
  }).join(''):`<div class="graph-relation-empty">${esc(empty)}</div>`}${items.length>12?`<div class="graph-relation-more">${items.length-12} more in this loaded window</div>`:''}</section>`;
}

function graphSearchHtml(){
  return `<section class="graph-search-box"><div class="graph-search-field"><input id="graph-claim-search" value="${esc(graphNavigatorState.query)}" placeholder="Find a Claim and center the graph…" autocomplete="off"/><span>${graphNavigatorState.searching?'searching…':'server search'}</span></div><div class="graph-search-results" id="graph-search-results"></div></section>`;
}

function renderGraphSearchResults(){
  const host=document.querySelector('#graph-search-results');if(!host)return;
  const q=graphNavigatorState.query.trim();
  if(!q){host.innerHTML='';return;}
  if(graphNavigatorState.searching){host.innerHTML='<div class="graph-search-note">Searching the Claim registry…</div>';return;}
  if(!graphNavigatorState.results.length){host.innerHTML='<div class="graph-search-note">No matching Claims.</div>';return;}
  host.innerHTML=graphNavigatorState.results.map(item=>`<button class="graph-search-result" data-graph-search-center="${esc(item.claim_id)}"><span><strong>${esc(item.title||item.lean_name||'Claim')}</strong><code>${esc(item.lean_name||'')}</code></span><small>${esc(item.area||'')}${item.status?` · ${esc(item.status)}`:''}</small></button>`).join('');
}

async function searchGraphClaims(query){
  const q=String(query||'').trim();graphNavigatorState.query=q;const serial=++graphNavigatorState.serial;
  if(q.length<2){graphNavigatorState.results=[];graphNavigatorState.searching=false;renderGraphSearchResults();return;}
  graphNavigatorState.searching=true;renderGraphSearchResults();
  try{
    const payload=await apiJson(`/api/claims?limit=8&q=${encodeURIComponent(q)}`);
    if(serial!==graphNavigatorState.serial)return;graphNavigatorState.results=payload.claims||[];
  }catch(_){if(serial===graphNavigatorState.serial)graphNavigatorState.results=[];}
  finally{if(serial===graphNavigatorState.serial){graphNavigatorState.searching=false;renderGraphSearchResults();}}
}

function scheduleGraphSearch(value){clearTimeout(graphNavigatorState.timer);graphNavigatorState.query=value;graphNavigatorState.timer=setTimeout(()=>searchGraphClaims(value),220);}

async function loadGraphEdgeProofs(edge,host){
  if(!edge||!host)return;const ids=(edge.proof_ids||[]).slice(0,4),key=graphEdgeKey(edge);
  if(!ids.length){host.innerHTML='<div class="graph-proof-empty">No Proof witness IDs in this projection.</div>';return;}
  host.innerHTML='<div class="graph-proof-loading">Loading Proof routes…</div>';
  try{
    const artifacts=[];
    for(const id of ids){
      if(graphNavigatorState.proofArtifacts.has(id)){artifacts.push(graphNavigatorState.proofArtifacts.get(id));continue;}
      const artifact=await apiJson(`/api/proofs/${id}`);graphNavigatorState.proofArtifacts.set(id,artifact);artifacts.push(artifact);
    }
    host.innerHTML=`<div class="graph-proof-routes">${artifacts.map((proof,index)=>{const source=String(proof.proof_source||'').trim(),trimmed=source.length>760?source.slice(0,759)+'…':source;return `<article><div><span>Proof ${index+1}</span><strong>${esc(proof.proof_author||'Formal proof')}</strong></div><pre>${esc(trimmed||'Source unavailable.')}</pre></article>`;}).join('')}${(edge.proof_ids||[]).length>ids.length?`<div class="graph-proof-more">Showing ${ids.length} of ${(edge.proof_ids||[]).length} Proof witnesses.</div>`:''}</div>`;
  }catch(error){host.innerHTML=`<div class="graph-proof-error">${esc(error.message)}</div>`;}
}

renderGraph=function mathematicalNavigatorRender(){
  const graph=argumentState.graph;
  if(!graph)return `<main class="shell page argument-graph-page canvas-graph-page"><div class="eyebrow">Argument graph</div><h1 class="display-title">Navigate mathematics by argument.</h1><p class="display-deck">Search for a Claim, or start from a recent bounded slice. The browser never downloads the whole registry.</p>${graphSearchHtml()}<button class="btn" data-recent-graph>Load a recent slice</button></main>`;
  const nodes=graph.nodes||[],selected=nodes.find(node=>node.id===state.graphSelected)||nodes.find(node=>node.id===graph.root)||nodes[0],truncated=!!graph.truncated,relations=graphRelations(selected?.id);
  return `<main class="shell page argument-graph-page canvas-graph-page"><div class="graph-title-row"><div><div class="eyebrow">Argument graph</div><h1 class="display-title">A local window into the argument graph.</h1><p class="display-deck">A → B means A depends on B. Search, re-center, and expand Proof witnesses without loading all of mathematics.</p></div><div class="graph-depth-controls"><button class="filter ${argumentState.depth===1?'active':''}" data-depth="1">1 hop</button><button class="filter ${argumentState.depth===2?'active':''}" data-depth="2">2 hops</button></div></div>${graphSearchHtml()}<div class="graph-window-meta"><span>${nodes.length} node${nodes.length===1?'':'s'} loaded</span><span>${graph.mode==='neighborhood'?'local neighborhood':'field-clustered recent slice'}</span>${truncated?'<strong>window capped — re-center to continue</strong>':''}<button type="button" data-graph-fit>Fit view</button><button type="button" data-recent-graph>Recent slice</button></div><div class="argument-graph-shell canvas-graph-shell"><div class="canvas-graph-stage"><canvas id="argument-graph-canvas-element" aria-label="Interactive argument graph. Drag to pan, scroll to zoom, click to select, double-click to re-center."></canvas><div class="canvas-graph-hint">drag to pan · wheel to zoom · double-click to re-center</div></div><aside class="argument-graph-inspector canvas-graph-inspector">${selected?`<code>${esc(selected.lean_name||'')}</code><h2>${esc(selected.title||'Claim')}</h2>${badge(selected.status||'open')}<p>${esc(selected.area||'')}</p><div class="graph-inspector-actions"><button class="btn btn-primary" data-claim="${selected.id}">Open Claim</button><button class="btn" data-center="${selected.id}">Fetch neighborhood</button></div>${graphRelationList('Depends on',relations.outgoing,'No outgoing dependency edge in this loaded window.')}${graphRelationList('Used by',relations.incoming,'No incoming use edge in this loaded window.')}<div class="canvas-scale-note">The inspector describes only the loaded neighborhood. Re-center a Claim to ask the server for its next local window.</div>`:'No Claim selected.'}</aside></div></main>`;
};

const graphNavigatorBaseBind=bind;
bind=function mathematicalNavigatorBind(){
  graphNavigatorBaseBind();if(state.view!=='graph')return;
  renderGraphSearchResults();
  const input=document.querySelector('#graph-claim-search');if(input)input.addEventListener('input',event=>scheduleGraphSearch(event.target.value));
  document.querySelector('#graph-search-results')?.addEventListener('click',event=>{const button=event.target.closest('[data-graph-search-center]');if(!button)return;const id=button.dataset.graphSearchCenter;graphNavigatorState.results=[];graphNavigatorState.query='';state.graphSelected=id;loadGraph(id);});
  document.querySelectorAll('[data-graph-select]').forEach(button=>button.addEventListener('click',()=>{state.graphSelected=button.dataset.graphSelect;render();}));
  document.querySelectorAll('[data-graph-edge-proofs]').forEach(button=>button.addEventListener('click',async()=>{
    const key=button.dataset.graphEdgeProofs,edge=(argumentState.graph?.edges||[]).find(item=>graphEdgeKey(item)===key),slot=document.querySelector(`[data-graph-edge-slot="${CSS.escape(key)}"]`);if(!edge||!slot)return;
    const open=slot.dataset.loaded==='1';if(open){slot.innerHTML='';slot.dataset.loaded='0';button.classList.remove('active');return;}
    button.classList.add('active');await loadGraphEdgeProofs(edge,slot);slot.dataset.loaded='1';
  }));
};
