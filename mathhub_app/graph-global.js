/* Obsidian-style persistent global graph.
   The registry stays represented at all times. Curated mathematical areas and
   deterministic Lean-namespace clusters can both unfold progressively into the
   same Canvas without replacing nodes already on screen. */
const graphGlobalState={
  loaded:false,loading:false,nodes:new Map(),edges:new Map(),
  expandedAreas:new Set(),expandedClaims:new Set(),claimCount:0,clusterCount:0,
  libraryMode:false,libraryIndexed:false,libraryStatus:null,libraryTheoremCount:0,
  libraryModulePath:'',libraryModuleHiddenCount:0,libraryModuleError:'',openingModulePath:'',
  libraryContextPrunedCount:0,
  dependencyMode:false,dependencyRoot:'',dependencyDetails:new Map(),dependencyLoading:new Set(),dependencyExpanded:new Set(),dependencyBulkLoading:false,dependencyPrefetching:false,dependencyPrefetchedCount:0,dependencyEdgeFilter:'all',
  needsInitialFit:true,pendingFocusIds:[],
  theoremDetails:new Map(),theoremLoading:new Set(),theoremErrors:new Map(),theoremImporting:new Set(),
};
let graphLibraryPollTimer=null;

function graphGlobalEdgeKey(edge){return `${edge.evidence_kind||'uses'}:${edge.from}:${edge.to}`;}
function graphGlobalGraph(){
  const nodes=[...graphGlobalState.nodes.values()].filter(node=>!node.context_hidden);
  const visibleIds=new Set(nodes.map(node=>node.id));
  const edges=[...graphGlobalState.edges.values()].filter(edge=>visibleIds.has(edge.from)&&visibleIds.has(edge.to));
  return {
    mode:'world',persistent:true,session_id:'mathhub-global-world-v3',root:null,depth:argumentState.depth||1,
    nodes,edges,
    claim_count:graphGlobalState.claimCount,cluster_count:nodes.filter(graphIsCluster).length,
  };
}
function graphIsCluster(nodeOrId){const id=typeof nodeOrId==='string'?nodeOrId:nodeOrId?.id;return String(id||'').startsWith('AREA-')||nodeOrId?.kind==='cluster';}
function graphGlobalNode(id){return graphGlobalState.nodes.get(id)||null;}
function graphClusterKey(node){
  if(node?.cluster_source==='mathlib-module')return `libmod:${node?.module_path||node?.title||''}`;
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
  graphGlobalState.clusterCount=[...graphGlobalState.nodes.values()].filter(graphIsCluster).length;
  argumentState.graph=graphGlobalGraph();
  return newcomers;
}

function dependencyNodeName(nodeOrId){
  const node=typeof nodeOrId==='string'?graphGlobalNode(nodeOrId):nodeOrId;
  return String(node?.lean_name||node?.title||'').trim();
}

function dependencyChildren(nodeId){
  return [...graphGlobalState.edges.values()]
    .filter(edge=>edge.evidence_kind==='lean-dependency'&&edge.from===nodeId)
    .map(edge=>edge.to);
}

function dependencyEdgeVisible(edge){
  if(edge?.evidence_kind!=='lean-dependency')return false;
  const filter=graphGlobalState.dependencyEdgeFilter||'all';
  if(filter==='all')return true;
  return (edge.dependency_roles||[]).includes(filter);
}

function placeDependencyChildren(anchorId,childIds){
  const anchor=graphCanvasState.positions.get(anchorId)||{x:0,y:0};
  const fresh=childIds.filter(id=>!graphCanvasState.positions.has(id));
  const spacing=Math.max(34,Math.min(58,520/Math.max(1,fresh.length)));
  fresh.forEach((id,index)=>{
    const offset=(index-(fresh.length-1)/2)*spacing;
    graphCanvasState.positions.set(id,{x:anchor.x+190,y:anchor.y+offset});
  });
}

function mergeLibraryDependencyResult(result,{anchorName=''}={}){
  const anchorId=`LIB:${anchorName||result?.declaration_name||''}`;
  const anchor=graphGlobalNode(anchorId);
  const parentDepth=Number(anchor?.dependency_depth||0);
  const newcomers=[];
  for(const raw of result?.nodes||[]){
    const isAnchor=raw.id===anchorId;
    const old=graphGlobalState.nodes.get(raw.id);
    const node={
      ...old,...raw,
      kind:'library-declaration',
      dependency_root:raw.id===`LIB:${graphGlobalState.dependencyRoot}`,
      dependency_depth:isAnchor?parentDepth:Math.min(Number(old?.dependency_depth??Infinity),parentDepth+1),
    };
    if(!Number.isFinite(node.dependency_depth))node.dependency_depth=parentDepth+1;
    if(!old)newcomers.push(node);
    graphGlobalState.nodes.set(node.id,node);
  }
  mergeWorldEdges(result?.edges||[]);
  graphGlobalState.dependencyDetails.set(String(result?.declaration_name||anchorName),result);
  graphGlobalState.dependencyExpanded.add(String(result?.declaration_name||anchorName));
  const children=dependencyChildren(anchorId);
  placeDependencyChildren(anchorId,children);
  argumentState.graph=graphGlobalGraph();
  return newcomers;
}

function dependencyContextModule(){
  const root=graphGlobalNode(`LIB:${graphGlobalState.dependencyRoot}`);
  return String(root?.source_module||'').trim();
}

async function prefetchDependencyLookahead(names,{limit=24}={}){
  if(!graphGlobalState.dependencyMode||graphGlobalState.dependencyPrefetching)return 0;
  const wanted=[...new Set((names||[]).map(String).filter(Boolean))]
    .filter(name=>!graphGlobalState.dependencyDetails.has(name)&&!graphGlobalState.dependencyLoading.has(name))
    .slice(0,Math.max(1,Math.min(24,limit)));
  if(!wanted.length)return 0;
  graphGlobalState.dependencyPrefetching=true;render();
  try{
    const batch=await apiJson('/api/library/dependencies/batch',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        declaration_names:wanted,
        context_module:dependencyContextModule(),
      }),
    });
    if(!batch?.ok)throw new Error(batch?.error||'Dependency look-ahead failed');
    let stored=0;
    for(const result of batch.items||[]){
      const name=String(result?.declaration_name||'').trim();
      if(!name||!result?.ok)continue;
      graphGlobalState.dependencyDetails.set(name,result);
      const existing=graphGlobalNode(`LIB:${name}`);
      const rootMeta=(result.nodes||[]).find(node=>node.id===`LIB:${name}`);
      if(existing&&rootMeta)graphGlobalState.nodes.set(existing.id,{...existing,...rootMeta,dependency_root:existing.dependency_root});
      stored++;
    }
    graphGlobalState.dependencyPrefetchedCount+=stored;
    return stored;
  }catch(error){
    // Look-ahead is an optimization only. A node can still fall back to an
    // on-demand inspection if this batch fails.
    return 0;
  }finally{
    graphGlobalState.dependencyPrefetching=false;render();
  }
}

function scheduleDependencyLookahead(name){
  const id=`LIB:${name}`;
  const children=dependencyChildren(id)
    .map(childId=>dependencyNodeName(graphGlobalNode(childId)))
    .filter(Boolean);
  if(!children.length)return;
  const run=()=>void prefetchDependencyLookahead(children,{limit:24});
  if('requestIdleCallback' in window)window.requestIdleCallback(run,{timeout:450});
  else setTimeout(run,40);
}

async function expandLibraryDependencyNode(name,{refresh=false}={}){
  name=String(name||'').trim();if(!name||graphGlobalState.dependencyLoading.has(name))return null;
  if(graphGlobalState.dependencyExpanded.has(name)&&!refresh){
    const id=`LIB:${name}`;render();requestAnimationFrame(()=>fitGlobalGraphNodes([id,...dependencyChildren(id)],{maxScale:1.1,padding:88}));return graphGlobalState.dependencyDetails.get(name)||null;
  }
  if(!refresh&&graphGlobalState.dependencyDetails.has(name)){
    const cached=graphGlobalState.dependencyDetails.get(name);
    state.graphSelected=`LIB:${name}`;
    mergeLibraryDependencyResult(cached,{anchorName:name});
    render();
    scheduleDependencyLookahead(name);
    requestAnimationFrame(()=>{
      const id=`LIB:${name}`;
      fitGlobalGraphNodes([id,...dependencyChildren(id)],{maxScale:1.1,padding:88});
    });
    return cached;
  }
  graphGlobalState.dependencyLoading.add(name);state.graphSelected=`LIB:${name}`;render();
  try{
    const suffix=refresh?'&refresh=1':'';
    const result=await apiJson(`/api/library/dependencies?name=${encodeURIComponent(name)}${suffix}`);
    if(!result?.ok)throw new Error(result?.error||'Dependency inspection failed');
    mergeLibraryDependencyResult(result,{anchorName:name});
    scheduleDependencyLookahead(name);
    return result;
  }catch(error){
    graphGlobalState.theoremErrors.set(name,error?.message||String(error));
    return null;
  }finally{
    graphGlobalState.dependencyLoading.delete(name);render();
    requestAnimationFrame(()=>{
      const id=`LIB:${name}`;
      fitGlobalGraphNodes([id,...dependencyChildren(id)],{maxScale:1.1,padding:88});
    });
  }
}

async function expandDependencyFrontier(){
  if(!graphGlobalState.dependencyMode||graphGlobalState.dependencyBulkLoading)return;
  const visible=graphGlobalGraph().nodes.filter(node=>graphIsLibraryTheorem(node));
  const frontier=visible
    .map(node=>dependencyNodeName(node))
    .filter(name=>name&&name!==graphGlobalState.dependencyRoot&&!graphGlobalState.dependencyExpanded.has(name))
    .slice(0,24);
  if(!frontier.length)return;
  graphGlobalState.dependencyBulkLoading=true;render();
  try{
    await prefetchDependencyLookahead(frontier,{limit:24});
    for(const name of frontier){
      const cached=graphGlobalState.dependencyDetails.get(name);
      if(cached&&!graphGlobalState.dependencyExpanded.has(name))mergeLibraryDependencyResult(cached,{anchorName:name});
    }
    // Prime the next frontier too, but do not draw it yet.
    const next=[...new Set(frontier.flatMap(name=>dependencyChildren(`LIB:${name}`).map(id=>dependencyNodeName(graphGlobalNode(id))).filter(Boolean)))];
    if(next.length)setTimeout(()=>void prefetchDependencyLookahead(next,{limit:24}),30);
  }finally{
    graphGlobalState.dependencyBulkLoading=false;render();
    requestAnimationFrame(()=>fitGlobalGraphNodes(graphGlobalGraph().nodes.map(node=>node.id),{maxScale:.95,padding:92}));
  }
}

async function openLibraryDependencyGraph(name){
  name=String(name||'').trim();if(!name)return;
  if(typeof libraryBrowserState!=='undefined')libraryBrowserState.open=false;
  state.view='graph';
  graphGlobalState.loaded=true;graphGlobalState.loading=false;
  graphGlobalState.libraryMode=true;graphGlobalState.libraryIndexed=true;
  graphGlobalState.dependencyMode=true;graphGlobalState.dependencyRoot=name;
  graphGlobalState.dependencyDetails.clear();graphGlobalState.dependencyLoading.clear();graphGlobalState.dependencyExpanded.clear();graphGlobalState.dependencyBulkLoading=false;graphGlobalState.dependencyPrefetching=false;graphGlobalState.dependencyPrefetchedCount=0;graphGlobalState.dependencyEdgeFilter='all';
  graphGlobalState.nodes.clear();graphGlobalState.edges.clear();graphGlobalState.expandedAreas.clear();graphGlobalState.expandedClaims.clear();
  graphGlobalState.libraryModulePath='';graphGlobalState.libraryModuleHiddenCount=0;graphGlobalState.libraryContextPrunedCount=0;
  const rootId=`LIB:${name}`;
  graphGlobalState.nodes.set(rootId,{id:rootId,kind:'library-declaration',lean_name:name,title:name.split('.').pop()||name,namespace_path:name.includes('.')?name.split('.').slice(0,-1).join('.'):'',dependency_root:true,dependency_depth:0});
  state.graphSelected=rootId;argumentState.graph=graphGlobalGraph();
  graphCanvasState.key='';graphCanvasState.positions=new Map([[rootId,{x:0,y:0}]]);
  graphGlobalState.needsInitialFit=false;
  render();
  requestAnimationFrame(()=>fitGlobalGraphNodes([rootId],{maxScale:1.12,padding:110}));
  // Presentation comes from the SQLite source index and should appear before
  // Lean dependency inspection finishes. Dependency extraction runs in parallel.
  const presentationPromise=loadLibraryTheoremDetail(name).catch(()=>null);
  const result=await expandLibraryDependencyNode(name);
  await presentationPromise;
  if(result)requestAnimationFrame(()=>fitGlobalGraphNodes([...graphGlobalGraph().nodes.map(node=>node.id)],{maxScale:1.0,padding:96}));
}

function fitGlobalGraphNodes(nodeIds,{maxScale=1.15,padding=72}={}){
  const canvas=document.querySelector('#argument-graph-canvas-element');if(!canvas)return;
  const points=(nodeIds||[]).map(id=>graphCanvasState.positions.get(id)).filter(Boolean);
  if(!points.length)return;
  const rect=canvas.getBoundingClientRect(),inspector=document.querySelector('.global-graph-inspector');
  const reserveRight=inspector?Math.min(rect.width*.38,inspector.getBoundingClientRect().width+34):0;
  const usableWidth=Math.max(220,rect.width-reserveRight),usableHeight=Math.max(220,rect.height);
  const bounds=points.reduce((b,p)=>({
    minX:Math.min(b.minX,p.x),maxX:Math.max(b.maxX,p.x),
    minY:Math.min(b.minY,p.y),maxY:Math.max(b.maxY,p.y),
  }),{minX:Infinity,maxX:-Infinity,minY:Infinity,maxY:-Infinity});
  const width=Math.max(120,bounds.maxX-bounds.minX),height=Math.max(120,bounds.maxY-bounds.minY);
  const sx=(usableWidth-padding*2)/width,sy=(usableHeight-padding*2)/height;
  const scale=Math.max(.24,Math.min(maxScale,sx,sy));
  const centerX=(bounds.minX+bounds.maxX)/2,centerY=(bounds.minY+bounds.maxY)/2;
  graphCanvasState.view.scale=Number.isFinite(scale)?scale:1;
  graphCanvasState.view.panX=usableWidth/2-centerX*graphCanvasState.view.scale;
  graphCanvasState.view.panY=usableHeight/2-centerY*graphCanvasState.view.scale;
  drawGraphCanvas(canvas);
}

function focusGlobalBranch(clusterId,preferredIds=[]){
  const children=[...graphGlobalState.nodes.values()]
    .filter(node=>node.parent_cluster_id===clusterId)
    .map(node=>node.id);
  const ancestorContext=graphGlobalState.libraryMode
    ? libraryAncestorChain(clusterId).slice(1,3)
    : [];
  const ids=[clusterId,...ancestorContext,...new Set([...(preferredIds||[]),...children])];
  fitGlobalGraphNodes(ids,{maxScale:1.18,padding:84});
}

function libraryAncestorChain(clusterId){
  const chain=[];let current=graphGlobalNode(clusterId),guard=0;
  while(current&&guard++<32){
    chain.push(current.id);
    const parentId=String(current.parent_cluster_id||'');
    if(!parentId)break;
    current=graphGlobalNode(parentId);
  }
  return chain;
}

function clearLibraryContextCompaction(){
  for(const [id,node] of [...graphGlobalState.nodes.entries()]){
    if(node.context_summary){
      graphGlobalState.nodes.delete(id);
      graphCanvasState.positions.delete(id);
      continue;
    }
    if(node.context_hidden)graphGlobalState.nodes.set(id,{...node,context_hidden:false});
  }
  for(const [key,edge] of [...graphGlobalState.edges.entries()]){
    if(edge.evidence_kind==='module-context-summary')graphGlobalState.edges.delete(key);
  }
  graphGlobalState.libraryContextPrunedCount=0;
}

function revealLibraryContextSummary(summaryId){
  const summary=graphGlobalNode(summaryId);
  if(!summary?.context_summary)return;
  for(const id of summary.context_hidden_ids||[]){
    const node=graphGlobalNode(id);
    if(node)graphGlobalState.nodes.set(id,{...node,context_hidden:false});
  }
  graphGlobalState.nodes.delete(summaryId);
  graphCanvasState.positions.delete(summaryId);
  for(const [key,edge] of [...graphGlobalState.edges.entries()]){
    if(edge.from===summaryId||edge.to===summaryId)graphGlobalState.edges.delete(key);
  }
  graphGlobalState.libraryContextPrunedCount=[...graphGlobalState.nodes.values()].filter(node=>node.context_hidden).length;
  graphGlobalState.clusterCount=graphGlobalGraph().nodes.filter(graphIsCluster).length;
  argumentState.graph=graphGlobalGraph();
  state.graphSelected=summary.summary_parent_id||state.graphSelected;
  render();
  requestAnimationFrame(()=>focusGlobalBranch(summary.summary_parent_id||state.graphSelected));
}

function compressLibraryContext(clusterId){
  if(!graphGlobalState.libraryMode)return 0;
  const current=graphGlobalNode(clusterId);
  if(!current||current.cluster_source!=='mathlib-module')return 0;

  // Shrink old levels, do not delete them. Each ancestor keeps the active path
  // plus one reversible '+N siblings' summary node. This preserves orientation
  // without leaving arbitrary old modules scattered around the canvas.
  clearLibraryContextCompaction();
  const chain=libraryAncestorChain(clusterId);
  if(chain.length<2)return 0;

  let hiddenTotal=0;
  for(let distance=1;distance<chain.length;distance++){
    const ancestorId=chain[distance],activeChildId=chain[distance-1];
    const siblings=[...graphGlobalState.nodes.values()].filter(node=>
      !node.context_summary&&node.parent_cluster_id===ancestorId&&node.id!==activeChildId
    );
    if(!siblings.length)continue;

    for(const node of siblings){
      graphGlobalState.nodes.set(node.id,{...node,context_hidden:true});
    }
    hiddenTotal+=siblings.length;

    const summaryId=`LIBSUM:${ancestorId}:${activeChildId}`;
    const summary={
      id:summaryId,kind:'cluster',cluster_source:'mathlib-context-summary',context_summary:true,
      summary_parent_id:ancestorId,summary_active_child_id:activeChildId,
      context_hidden_ids:siblings.map(node=>node.id),
      parent_cluster_id:ancestorId,module_path:'',namespace_path:'',
      title:`+${siblings.length} sibling${siblings.length===1?'':'s'}`,
      claim_count:siblings.reduce((sum,node)=>sum+Number(node.claim_count||0),0),
      library_count:siblings.length,
    };
    graphGlobalState.nodes.set(summaryId,summary);
    mergeWorldEdges([{
      from:ancestorId,to:summaryId,evidence_kind:'module-context-summary',proof_count:0,proof_ids:[]
    }]);
    placeAround(ancestorId,[summary],88+distance*12);
  }

  graphGlobalState.libraryContextPrunedCount=hiddenTotal;
  graphGlobalState.clusterCount=graphGlobalGraph().nodes.filter(graphIsCluster).length;
  argumentState.graph=graphGlobalGraph();
  return hiddenTotal;
}
async function loadLibraryStatus(){
  try{graphGlobalState.libraryStatus=await apiJson('/api/library/status');}
  catch(error){graphGlobalState.libraryStatus={error:String(error?.message||error)};}
  return graphGlobalState.libraryStatus;
}

function mergeLibrarySourceRelations(name,detail){
  const anchorId=`LIB:${name}`,outgoing=[...new Set((detail?.source_dependencies||[]).map(String))].slice(0,14),incoming=[...new Set((detail?.source_used_by||[]).map(String))].slice(0,10);
  const additions=[],ensureNode=(other)=>{
    const id=`LIB:${other}`;
    if(!graphGlobalState.nodes.has(id)){
      const node={id,kind:'library-theorem',lean_name:other,title:other.split('.').pop()||other,namespace_path:other.includes('.')?other.split('.').slice(0,-1).join('.'):'',status:'library',registered:false,claim_id:null};
      graphGlobalState.nodes.set(id,node);additions.push(node);
    }
  };
  outgoing.forEach(ensureNode);incoming.forEach(ensureNode);
  if(additions.length)placeAround(anchorId,additions,132);
  mergeWorldEdges([
    ...outgoing.map(target=>({from:anchorId,to:`LIB:${target}`,evidence_kind:'mathlib-source-ref',proof_count:0,proof_ids:[]})),
    ...incoming.map(source=>({from:`LIB:${source}`,to:anchorId,evidence_kind:'mathlib-source-ref',proof_count:0,proof_ids:[]})),
  ]);
  argumentState.graph=graphGlobalGraph();
}

async function loadLibraryTheoremDetail(name,{refresh=false}={}){
  name=String(name||'').trim();if(!name)return null;
  if(!refresh&&graphGlobalState.theoremDetails.has(name)){
    const cached=graphGlobalState.theoremDetails.get(name);
    if(!graphGlobalState.dependencyMode)mergeLibrarySourceRelations(name,cached);
    return cached;
  }
  if(graphGlobalState.theoremLoading.has(name))return null;
  graphGlobalState.theoremLoading.add(name);graphGlobalState.theoremErrors.delete(name);render();
  try{
    const suffix=refresh?'&refresh=1':'';
    const detail=await apiJson(`/api/library/theorem?name=${encodeURIComponent(name)}${suffix}`);
    if(!detail?.ok)throw new Error(detail?.error||'Theorem inspection failed');
    graphGlobalState.theoremDetails.set(name,detail);
    if(!graphGlobalState.dependencyMode)mergeLibrarySourceRelations(name,detail);
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

function editLibraryTheoremFromGraph(name){
  name=String(name||'').trim();if(!name)return;
  const detail=graphGlobalState.theoremDetails.get(name);
  if(!detail){void loadLibraryTheoremDetail(name).then(()=>editLibraryTheoremFromGraph(name));return;}
  state.sourceDraft={
    title:String(detail.short_name||name).split('.').pop().replace(/_/g,' '),
    statement:'',
    imports:String(detail.source_module||'Mathlib'),
    declaration:String(detail.source_declaration||detail.pretty_type||''),
    proof:'',
  };
  state.sourceIngest={loading:false,error:'',result:null,selected:-1};
  state.sourceRegister={loading:false,error:'',success:null};
  state.sourceCheck={running:false,result:null,error:''};
  state.view='submit';
  render();
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
  graphGlobalState.dependencyMode=false;graphGlobalState.dependencyRoot='';graphGlobalState.dependencyBulkLoading=false;
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
    graphGlobalState.nodes.clear();graphGlobalState.edges.clear();graphGlobalState.expandedAreas.clear();graphGlobalState.expandedClaims.clear();graphGlobalState.pendingFocusIds=[];graphGlobalState.needsInitialFit=true;graphGlobalState.libraryModulePath='';graphGlobalState.libraryModuleHiddenCount=0;graphGlobalState.libraryModuleError='';graphGlobalState.libraryContextPrunedCount=0;
    for(const node of world.nodes||[])graphGlobalState.nodes.set(node.id,node);mergeWorldEdges(world.edges||[]);
    graphGlobalState.clusterCount=Number(world.cluster_count||0);graphGlobalState.libraryTheoremCount=Number(world.theorem_count||0);graphGlobalState.loaded=true;
    argumentState.graph=graphGlobalGraph();graphCanvasState.key='';graphCanvasState.positions=new Map();state.graphSelected=(world.nodes||[])[0]?.id||'';
  }finally{graphGlobalState.loading=false;render();}
}

function parentLibraryModulePath(path){
  const parts=String(path||'').split('.').filter(Boolean);
  parts.pop();
  return parts.join('.');
}

async function openLibraryModule(modulePath){
  modulePath=String(modulePath||'').trim();
  if(!modulePath||graphGlobalState.loading)return;
  graphGlobalState.dependencyMode=false;graphGlobalState.dependencyRoot='';graphGlobalState.dependencyBulkLoading=false;
  graphGlobalState.loading=true;
  graphGlobalState.openingModulePath=modulePath;
  graphGlobalState.libraryModuleError='';
  render();
  try{
    const chunk=await apiJson(`/api/library/module?path=${encodeURIComponent(modulePath)}&limit=36`);
    if(!chunk?.ok)throw new Error(chunk?.error||`Could not open Mathlib module ${modulePath}`);
    const nodes=(chunk.nodes||[]).map(node=>({...node,parent_cluster_id:null}));
    if(!nodes.length){
      throw new Error(`No child modules or direct theorems were returned for ${modulePath}.`);
    }
    graphGlobalState.nodes.clear();
    graphGlobalState.edges.clear();
    graphGlobalState.expandedAreas.clear();
    graphGlobalState.expandedClaims.clear();
    for(const node of nodes)graphGlobalState.nodes.set(node.id,node);
    mergeWorldEdges(chunk.edges||[]);
    graphGlobalState.libraryModulePath=modulePath;
    graphGlobalState.libraryModuleHiddenCount=Number(chunk.hidden_count||0);
    graphGlobalState.libraryContextPrunedCount=0;
    graphGlobalState.clusterCount=nodes.filter(graphIsCluster).length;
    argumentState.graph=graphGlobalGraph();
    graphCanvasState.key='';
    graphCanvasState.positions=new Map();
    graphGlobalState.needsInitialFit=true;
    state.graphSelected=nodes[0]?.id||'';
  }catch(error){
    graphGlobalState.libraryModuleError=error?.message||String(error);
  }finally{
    graphGlobalState.loading=false;
    graphGlobalState.openingModulePath='';
    render();
  }
}

async function openLibraryModuleParent(){
  const current=graphGlobalState.libraryModulePath;
  if(!current){await loadGlobalWorld({reset:true});return;}
  const parent=parentLibraryModulePath(current);
  if(parent)await openLibraryModule(parent);
  else await loadGlobalWorld({reset:true});
}

function libraryBreadcrumbHtml(){
  if(!graphGlobalState.libraryMode||graphGlobalState.dependencyMode)return '';
  const path=String(graphGlobalState.libraryModulePath||'');
  const parts=path.split('.').filter(Boolean);
  const crumbs=[`<button type="button" data-library-module-root>Mathlib</button>`];
  let acc='';
  for(let i=0;i<parts.length;i++){
    acc=acc?`${acc}.${parts[i]}`:parts[i];
    const last=i===parts.length-1;
    crumbs.push(last
      ? `<span>${esc(parts[i])}</span>`
      : `<button type="button" data-library-module-path="${esc(acc)}">${esc(parts[i])}</button>`);
  }
  return `<nav class="global-library-breadcrumb" aria-label="Mathlib module path">${crumbs.join('<i>/</i>')}</nav>`;
}

async function expandGlobalArea(clusterId){
  const cluster=graphGlobalNode(clusterId);if(!cluster||!graphIsCluster(cluster)||graphGlobalState.loading)return;
  if(cluster.context_summary){revealLibraryContextSummary(clusterId);return;}
  const key=graphClusterKey(cluster);if(!key)return;
  state.graphSelected=clusterId;
  if(graphGlobalState.expandedAreas.has(key)&&!(graphGlobalState.libraryMode&&cluster.cluster_source==='mathlib-module')){
    render();
    requestAnimationFrame(()=>focusGlobalBranch(clusterId));
    return;
  }
  graphGlobalState.loading=true;render();
  let focusIds=[];
  try{
    const chunk=cluster.cluster_source==='mathlib-module'
      ? await apiJson(`/api/library/module?path=${encodeURIComponent(cluster.module_path||cluster.title||'')}&limit=36`)
      : cluster.cluster_source==='mathlib-library'
        ? await apiJson(`/api/library/cluster?namespace=${encodeURIComponent(cluster.namespace_path||cluster.title||'')}&limit=36`)
        : await apiJson(`/api/graph/cluster?key=${encodeURIComponent(key)}&limit=100`);
    if(Number.isFinite(Number(chunk?.hidden_count))){
      graphGlobalState.nodes.set(clusterId,{...cluster,expansion_hidden_count:Number(chunk.hidden_count||0),expansion_shown_count:Number(chunk.shown_count||0)});
    }
    const newcomers=mergeWorldChunk(chunk,{anchorId:clusterId,radius:145});
    focusIds=newcomers.map(node=>node.id);
    graphGlobalState.expandedAreas.add(key);
    if(graphGlobalState.libraryMode&&cluster.cluster_source==='mathlib-module'){
      compressLibraryContext(clusterId);
      focusIds=[...graphGlobalState.nodes.values()].filter(node=>node.parent_cluster_id===clusterId).map(node=>node.id);
    }
  }finally{
    graphGlobalState.loading=false;argumentState.graph=graphGlobalGraph();render();
    requestAnimationFrame(()=>focusGlobalBranch(clusterId,focusIds));
  }
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
  if(graphIsLibraryTheorem(node)){
    if(graphGlobalState.dependencyMode)await expandLibraryDependencyNode(dependencyNodeName(node));
    else await openLibraryDependencyGraph(dependencyNodeName(node));
    return;
  }
  if(graphIsCluster(node||root))await expandGlobalArea(root);else await expandGlobalClaim(root);
};

graphCanvasKey=function globalCanvasKey(graph){
  if(!graph?.persistent)return `local:${graph?.root||''}`;
  return graphGlobalState.dependencyMode
    ? `mathhub-dependency:${graphGlobalState.dependencyRoot}`
    : 'mathhub-global-world-v3';
};
const graphGlobalBaseLayout=buildCanvasLayout;
buildCanvasLayout=function globalInitialLayout(graph){
  if(!graph?.persistent)return graphGlobalBaseLayout(graph);
  graphCanvasState.clusters=[];
  const positions=new Map(),clusters=(graph.nodes||[]).filter(graphIsCluster),claims=(graph.nodes||[]).filter(node=>!graphIsCluster(node));
  const golden=Math.PI*(3-Math.sqrt(5));
  clusters.sort((a,b)=>Number(b.claim_count||0)-Number(a.claim_count||0)||String(a.title).localeCompare(String(b.title)));
  if(graphGlobalState.dependencyMode){
    const levels=new Map();
    for(const node of claims){
      const depth=Math.max(0,Number(node.dependency_depth||0));
      if(!levels.has(depth))levels.set(depth,[]);
      levels.get(depth).push(node);
    }
    for(const [depth,nodes] of [...levels.entries()].sort((a,b)=>a[0]-b[0])){
      nodes.sort((a,b)=>String(a.lean_name||a.title).localeCompare(String(b.lean_name||b.title)));
      const spacing=Math.max(38,Math.min(62,560/Math.max(1,nodes.length)));
      nodes.forEach((node,index)=>positions.set(node.id,{x:depth*205,y:(index-(nodes.length-1)/2)*spacing}));
    }
    return positions;
  }
  if(graphGlobalState.libraryMode){
    const libraryRoot=clusters.find(node=>node.id==='LIBMOD:(root)');
    if(libraryRoot){
      positions.set(libraryRoot.id,{x:0,y:0});
      const top=clusters.filter(node=>node.parent_cluster_id===libraryRoot.id);
      const rings=[{count:10,radius:205},{count:18,radius:365},{count:30,radius:530}];
      let cursor=0;
      for(const ring of rings){
        const slice=top.slice(cursor,cursor+ring.count);if(!slice.length)break;
        slice.forEach((node,index)=>{
          const angle=(-Math.PI/2)+(index/Math.max(1,slice.length))*Math.PI*2+(cursor?0.09:0);
          positions.set(node.id,{x:Math.cos(angle)*ring.radius,y:Math.sin(angle)*ring.radius});
        });
        cursor+=slice.length;
      }
      top.slice(cursor).forEach((node,index)=>{
        const radius=690+80*Math.floor(index/36),angle=(index%36)/36*Math.PI*2;
        positions.set(node.id,{x:Math.cos(angle)*radius,y:Math.sin(angle)*radius});
      });
      const deeper=clusters.filter(node=>node.parent_cluster_id&&node.parent_cluster_id!==libraryRoot.id);
      const siblingIndex=new Map();
      for(const node of deeper){
        const parent=positions.get(node.parent_cluster_id)||{x:0,y:0};
        const i=siblingIndex.get(node.parent_cluster_id)||0;siblingIndex.set(node.parent_cluster_id,i+1);
        const angle=i*golden+stableAngle(node.id)*.12,radius=125+26*Math.sqrt(i);
        positions.set(node.id,{x:parent.x+Math.cos(angle)*radius,y:parent.y+Math.sin(angle)*radius});
      }
    }else{
      const roots=clusters.filter(node=>!node.parent_cluster_id);
      roots.forEach((node,index)=>{const radius=index?190*Math.sqrt(index):0,angle=index*golden;positions.set(node.id,{x:Math.cos(angle)*radius,y:Math.sin(angle)*radius});});
    }
  }else{
    clusters.forEach((node,index)=>{const radius=index?190*Math.sqrt(index):0,angle=index*golden;positions.set(node.id,{x:Math.cos(angle)*radius,y:Math.sin(angle)*radius});});
  }
  const claimSiblingIndex=new Map();
  for(const node of claims){
    const parent=positions.get(node.parent_cluster_id)||{x:0,y:0};
    const i=claimSiblingIndex.get(node.parent_cluster_id)||0;claimSiblingIndex.set(node.parent_cluster_id,i+1);
    const angle=i*golden+stableAngle(node.id),radius=92+18*Math.sqrt(i);
    positions.set(node.id,{x:parent.x+Math.cos(angle)*radius,y:parent.y+Math.sin(angle)*radius});
  }
  return positions;
};

const graphGlobalBaseDraw=drawGraphCanvas;
const graphGlobalBaseHitTest=graphHitTest;

function graphGlobalModuleMetrics(ctx,node,{selected=false,hover=false}={}){
  const root=node?.id==='LIBMOD:(root)',summary=Boolean(node?.context_summary);
  const label=root?'Mathlib':String(node?.title||'Module').split('.').slice(-1)[0];
  const fontSize=root?12.5:(summary?9.5:(selected||hover?11.5:10.5));
  ctx.font=`${root||selected?'650 ':'600 '}${fontSize}px ui-sans-serif,system-ui,sans-serif`;
  const labelWidth=Math.min(summary?110:150,ctx.measureText(label).width);
  return {root,summary,label,fontSize,labelWidth,width:root?Math.max(82,labelWidth+42):(summary?labelWidth+18:labelWidth+24),height:root?34:(summary?20:24)};
}

function graphGlobalRoundRect(ctx,x,y,w,h,r){
  const radius=Math.min(r,w/2,h/2);
  if(typeof ctx.roundRect==='function'){
    ctx.beginPath();ctx.roundRect(x,y,w,h,radius);return;
  }
  ctx.beginPath();ctx.moveTo(x+radius,y);ctx.arcTo(x+w,y,x+w,y+h,radius);ctx.arcTo(x+w,y+h,x,y+h,radius);ctx.arcTo(x,y+h,x,y,radius);ctx.arcTo(x,y,x+w,y,radius);ctx.closePath();
}

graphHitTest=function globalGraphHitTest(canvas,x,y){
  const graph=argumentState.graph;
  if(!graph?.persistent)return graphGlobalBaseHitTest(canvas,x,y);
  const ctx=canvas?.getContext?.('2d');
  if(!ctx)return graphGlobalBaseHitTest(canvas,x,y);
  // Test module labels as real click targets; theorem/Claim nodes keep the base
  // circular hit target. This makes the compact module glyphs easy to use.
  for(const node of [...(graph.nodes||[])].reverse()){
    if(!graphIsCluster(node))continue;
    const world=graphCanvasState.positions.get(node.id);if(!world)continue;
    const p=graphScreenPoint(world),m=graphGlobalModuleMetrics(ctx,node,{selected:node.id===state.graphSelected,hover:node.id===graphCanvasState.hoverId});
    const left=p.x-m.width/2,top=p.y-m.height/2;
    if(x>=left-4&&x<=left+m.width+4&&y>=top-4&&y<=top+m.height+4)return node;
  }
  return graphGlobalBaseHitTest(canvas,x,y);
};

drawGraphCanvas=function drawGlobalGraph(canvas){
  const graph=argumentState.graph;if(!canvas||!graph?.persistent){graphGlobalBaseDraw(canvas);return;}

  // The generic canvas renderer still owns theorem/Claim nodes and directional
  // theorem-reference arrows. Module containment is drawn separately below so
  // hierarchy reads as a quiet tree, not as a second kind of proof arrow.
  const original=argumentState.graph;
  argumentState.graph={
    ...graph,
    edges:(graph.edges||[]).filter(edge=>edge.evidence_kind!=='module-contains'&&(!graphGlobalState.dependencyMode||edge.evidence_kind!=='lean-dependency')),
    // Persistent Mathlib nodes are drawn below with their own glyph language.
    // Let the base renderer draw only relation arrows; otherwise its generic
    // hollow-circle + selected-halo nodes leak through and create bullseyes.
    nodes:[],
  };
  graphGlobalBaseDraw(canvas);argumentState.graph=original;

  const ctx=canvas.getContext('2d'),palette=graphCanvasPalette(canvas),selected=state.graphSelected,hover=graphCanvasState.hoverId;
  ctx.save();

  if(graphGlobalState.dependencyMode){
    const dependencyPalette={...palette,line:'rgba(58,68,77,.58)'};
    for(const edge of graph.edges||[]){
      if(!dependencyEdgeVisible(edge))continue;
      const a=graphCanvasState.positions.get(edge.from),b=graphCanvasState.positions.get(edge.to);if(!a||!b)continue;
      drawArrow(ctx,graphScreenPoint(a),graphScreenPoint(b),9,dependencyPalette,1.35);
    }
  }

  // Module containment: thin, non-directional, lower visual priority.
  ctx.strokeStyle='rgba(91,100,108,.20)';
  ctx.lineWidth=1;
  for(const edge of graph.edges||[]){
    if(edge.evidence_kind!=='module-contains'&&edge.evidence_kind!=='module-context-summary')continue;
    const a=graphCanvasState.positions.get(edge.from),b=graphCanvasState.positions.get(edge.to);if(!a||!b)continue;
    const from=graphScreenPoint(a),to=graphScreenPoint(b);
    ctx.beginPath();ctx.moveTo(from.x,from.y);ctx.lineTo(to.x,to.y);ctx.stroke();
  }

  // Theorems/Claims use compact diamonds instead of generic circles. A selected
  // theorem is slightly larger and darker, never wrapped in a target-like halo.
  const ordinaryNodes=(graph.nodes||[]).filter(node=>!graphIsCluster(node));
  const dense=ordinaryNodes.length>72,showLabels=!dense&&graphCanvasState.view.scale>.72;
  for(const node of ordinaryNodes){
    const world=graphCanvasState.positions.get(node.id);if(!world)continue;
    const p=graphScreenPoint(world),isSelected=node.id===selected,isHover=node.id===hover;
    const size=node.dependency_root?9:(node.is_axiom?7:(isSelected?6.5:(isHover?5.5:4.2)));
    ctx.save();ctx.translate(p.x,p.y);ctx.rotate(node.is_axiom?0:Math.PI/4);
    ctx.beginPath();ctx.rect(-size/2,-size/2,size,size);
    if(node.dependency_root){ctx.fillStyle=palette.ink;ctx.fill();}
    else if(node.is_axiom){ctx.fillStyle='rgba(255,255,255,.96)';ctx.fill();ctx.strokeStyle=palette.ink;ctx.lineWidth=1.4;ctx.stroke();}
    else if(node.status==='verified'){ctx.fillStyle=palette.accent;ctx.fill();}
    else{
      ctx.fillStyle=isSelected?palette.ink:(isHover?'rgba(69,78,86,.86)':'rgba(111,121,129,.62)');
      ctx.fill();
    }
    ctx.restore();
    if(showLabels||isSelected||isHover){
      const title=String(node.title||node.lean_name||'Theorem'),max=isSelected||isHover?42:26,label=title.length>max?title.slice(0,max-1)+'…':title;
      ctx.font=`${isSelected?'600 ':'400 '}11px ui-serif,Georgia,serif`;ctx.textAlign='center';ctx.textBaseline='top';ctx.fillStyle=palette.ink;
      ctx.fillText(label,p.x,p.y+8);
    }
  }

  for(const node of graph.nodes||[]){
    if(!graphIsCluster(node))continue;
    const world=graphCanvasState.positions.get(node.id);if(!world)continue;
    const p=graphScreenPoint(world),isSelected=node.id===selected,isHover=node.id===hover;
    const m=graphGlobalModuleMetrics(ctx,node,{selected:isSelected,hover:isHover}),x=p.x-m.width/2,y=p.y-m.height/2;
    const libraryCluster=graphGlobalState.libraryMode&&(node.cluster_source==='mathlib-module'||node.cluster_source==='mathlib-library');

    // Compact label-chip nodes replace the previous giant hollow circles.
    graphGlobalRoundRect(ctx,x,y,m.width,m.height,m.root?8:6);
    ctx.fillStyle=m.root?'rgba(29,34,39,.94)':(m.summary?'rgba(248,249,250,.82)':(libraryCluster?'rgba(255,255,255,.94)':'rgba(255,255,255,.91)'));
    ctx.fill();
    if(m.summary)ctx.setLineDash([3,3]);
    ctx.strokeStyle=isSelected?palette.ink:(isHover?'rgba(70,78,86,.62)':(m.summary?'rgba(92,101,109,.24)':'rgba(92,101,109,.30)'));
    ctx.lineWidth=isSelected?1.7:1;
    ctx.stroke();
    ctx.setLineDash([]);

    // Small hierarchy glyph for real modules; summaries are already explicit.
    const gx=x+(m.root?13:(m.summary?9:10)),gy=p.y;
    if(!m.summary){
      ctx.beginPath();ctx.rect(gx-3.5,gy-3.5,7,7);
      ctx.fillStyle=m.root?'#fff':(isSelected?palette.ink:'rgba(78,88,97,.70)');
      ctx.fill();
    }

    ctx.textAlign='left';ctx.textBaseline='middle';
    ctx.fillStyle=m.root?'#fff':(m.summary?palette.muted:palette.ink);
    ctx.font=`${m.root||isSelected?'650 ':'600 '}${m.fontSize}px ui-sans-serif,system-ui,sans-serif`;
    const label=m.label.length>24?m.label.slice(0,23)+'…':m.label;
    ctx.fillText(label,gx+(m.root?10:(m.summary?0:9)),p.y+.2);

    // Counts stay off the node until hover/selection; topology is primary.
    if((isSelected||isHover)&&!m.root&&graphCanvasState.view.scale>.52){
      const count=Math.max(0,Number(node.claim_count||0));
      ctx.font='8.5px ui-sans-serif,system-ui,sans-serif';ctx.fillStyle=palette.muted;ctx.textAlign='center';ctx.textBaseline='top';
      ctx.fillText(`${count.toLocaleString()} ${graphGlobalState.libraryMode?'theorems':'claims'}`,p.x,y+m.height+5);
    }
  }
  ctx.restore();
};

function dependencyRelationRoles(nodeId){
  const roles=new Set();
  for(const edge of graphGlobalState.edges.values()){
    if(edge.evidence_kind==='lean-dependency'&&edge.to===nodeId){
      for(const role of edge.dependency_roles||[])roles.add(role);
    }
  }
  return [...roles];
}

function dependencyFocusCard(){
  if(!graphGlobalState.dependencyMode)return '';
  const name=graphGlobalState.dependencyRoot,source=graphGlobalState.theoremDetails.get(name),meta=graphGlobalState.dependencyDetails.get(name),loading=graphGlobalState.dependencyLoading.has(name);
  const short=source?.short_name||name.split('.').pop()||name;
  const formula=source?.presentation_latex
    ? `<div class="dependency-focus-formula" aria-label="${esc(source.presentation_text||name)}">\\[${esc(source.presentation_latex)}\\]</div>`
    : source?.presentation_text
      ? `<div class="dependency-focus-text">${esc(source.presentation_text)}</div>`
      : meta?.pretty_type
        ? `<pre class="dependency-focus-lean">${esc(meta.pretty_type)}</pre>`
        : `<div class="dependency-focus-loading"><span></span>${loading?'Loading dependencies…':'Loading mathematical presentation…'}</div>`;
  return `<section class="dependency-focus-card">
    <div class="dependency-focus-head"><div><div class="eyebrow">Theorem</div><h1>${esc(short)}</h1><code>${esc(name)}</code></div><div class="dependency-focus-badges"><span>${Number(meta?.direct_dependency_count||0).toLocaleString()} direct dependencies</span><span>${meta?.fast_path===false?'full audit':'interactive Lean graph'}</span></div></div>
    ${formula}
    <div class="dependency-focus-foot"><span>Displayed as mathematics when a deterministic LaTeX presentation is available.</span><button class="btn" data-library-edit-copy="${esc(name)}" ${source?'':'disabled'}>Edit a copy</button></div>
  </section>`;
}

function dependencyInspector(node){
  const name=dependencyNodeName(node),meta=graphGlobalState.dependencyDetails.get(name),loading=graphGlobalState.dependencyLoading.has(name),expanded=graphGlobalState.dependencyExpanded.has(name),roles=dependencyRelationRoles(node.id);
  const source=graphGlobalState.theoremDetails.get(name);
  const error=graphGlobalState.theoremErrors.get(name);
  const direct=Number(meta?.direct_dependency_count||dependencyChildren(node.id).length);
  const expandedChildren=dependencyChildren(node.id).filter(id=>graphGlobalState.dependencyExpanded.has(dependencyNodeName(graphGlobalNode(id)))).length;
  const axioms=(meta?.axioms||[]).map(String);
  const closure=(meta?.transitive_dependencies||[]).map(String);
  const formula=source?.presentation_latex
    ? `<div class="library-theorem-formula" aria-label="${esc(source.presentation_text||name)}">\\[${esc(source.presentation_latex)}\\]</div>`
    : meta?.pretty_type
      ? `<pre class="library-theorem-type">${esc(meta.pretty_type)}</pre>`
      : '';
  const roleText=roles.length?`<span class="dependency-role">${esc(roles.join(' + '))} dependency</span>`:'';
  const axiomHtml=axioms.length?`<section class="library-theorem-deps"><h3>Axioms reached</h3><div class="library-theorem-dep-list">${axioms.map(item=>`<button type="button" data-library-dependency-root="${esc(item)}"><code>${esc(item)}</code></button>`).join('')}</div></section>`:'';
  const closurePreview=closure.length?`<details class="library-theorem-source"><summary>Transitive closure · ${closure.length.toLocaleString()} declarations</summary><div class="library-theorem-dep-list dependency-closure-list">${closure.slice(0,120).map(item=>`<button type="button" data-library-dependency-root="${esc(item)}" title="Open this declaration as a dependency root"><code>${esc(item)}</code></button>`).join('')}${closure.length>120?`<span class="quiet-empty">+${(closure.length-120).toLocaleString()} more</span>`:''}</div></details>`:'';
  return `<div class="global-inspector-kind">${node.dependency_root?'Dependency root':'Lean dependency'} ${roleText}</div>
    <code class="library-theorem-name">${esc(name)}</code>
    <h2>${esc(node.title||name.split('.').pop()||name)}</h2>
    ${formula}
    <div class="dependency-summary-grid"><div><strong>${direct.toLocaleString()}</strong><span>direct</span></div><div><strong>${expandedChildren.toLocaleString()}</strong><span>children expanded</span></div><div><strong>${meta?.duration_ms?Math.max(1,Math.round(Number(meta.duration_ms)/100)/10)+'s':'—'}</strong><span>Lean inspect</span></div></div>
    <p>Arrows are direct Lean declaration/proof dependencies. Deeper closure is discovered by expanding nodes; module ownership is never mixed into this view.</p>
    ${axiomHtml}${closurePreview}
    ${error?`<div class="library-theorem-error">${esc(error)}</div>`:''}
    <div class="graph-inspector-actions">
      <button class="btn btn-primary" data-library-dependency-expand="${esc(name)}" ${loading?'disabled':''}>${loading?'Inspecting…':expanded?'Refocus dependencies':'Expand dependencies'}</button>
      ${source?`<button class="btn" data-library-edit-copy="${esc(name)}">Edit a copy</button>`:''}
      ${node.source_module?`<a class="btn" href="./?view=library&q=${encodeURIComponent(name)}">Open in Math library</a>`:''}
    </div>`;
}

function globalInspector(node){
  if(!node)return '<div class="global-inspector-empty">Select a module or theorem.</div>';
  if(graphGlobalState.dependencyMode&&graphIsLibraryTheorem(node))return dependencyInspector(node);
  if(node.context_summary){
    const count=(node.context_hidden_ids||[]).length;
    return `<div class="global-inspector-kind">Compacted context</div><h2>${esc(node.title||'Older siblings')}</h2><p>${count.toLocaleString()} sibling item${count===1?'':'s'} from this older level are folded into one context node. They are still in memory; nothing was discarded.</p><button class="btn btn-primary" data-expand-global-area="${esc(node.id)}">Show this level</button>`;
  }
  if(graphIsCluster(node)){
    const key=graphClusterKey(node),expanded=graphGlobalState.expandedAreas.has(key),library=graphGlobalState.libraryMode&&(node.cluster_source==='mathlib-module'||node.cluster_source==='mathlib-library'),moduleCluster=node.cluster_source==='mathlib-module',namespace=node.cluster_source==='lean-namespace',kind=library?(moduleCluster?'Mathlib module':'Mathlib namespace'):(namespace?'Lean namespace':'Curated field'),path=node.module_path||node.namespace_path||node.title;
    const action=expanded?'Focus branch':(moduleCluster?'Expand one level':(library||namespace?'Expand namespace':'Expand field'));
    const countLabel=library?'theorems':'Claims';
    const hidden=Number(node.expansion_hidden_count||0);
    const copy=moduleCluster
      ? 'Double-click expands this module in place. Older ancestor levels are progressively compacted so this branch can grow without leaving the whole path open.'
      : library
        ? 'Read-only theorems from the installed Mathlib source index. Expand freely; nothing becomes a Claim until you explicitly import it.'
        : (namespace?'This deterministic fallback comes from the Lean declaration namespace.':'This curated mathematical field groups durable MathHub Claims.');
    const enter=moduleCluster&&path!=='(root)'?`<button class="btn" data-open-library-module="${esc(path)}" ${graphGlobalState.openingModulePath===path?'disabled':''}>${graphGlobalState.openingModulePath===path?'Entering…':'Enter module'}</button>`:'';
    return `<div class="global-inspector-kind">${esc(kind)}</div><h2>${esc(path==='(root)'?'Mathlib':path)}</h2><strong class="global-claim-count">${Number(node.claim_count||0).toLocaleString()} ${countLabel}</strong><p>${esc(copy)}</p>${hidden?`<div class="global-module-limit">Showing a bounded branch · ${hidden.toLocaleString()} more available through deeper modules or search.</div>`:''}<button class="btn btn-primary" data-expand-global-area="${esc(node.id)}">${esc(action)}</button>${enter}`;
  }
  if(graphIsLibraryTheorem(node)){
    const name=String(node.lean_name||node.title||''),detail=graphGlobalState.theoremDetails.get(name),loading=graphGlobalState.theoremLoading.has(name),error=graphGlobalState.theoremErrors.get(name),registered=Boolean(node.registered||detail?.registered),claimId=node.claim_id||detail?.claim_id||'';
    if(loading&&!detail)return `<div class="global-inspector-kind">Mathlib theorem · read-only</div><code>${esc(name)}</code><h2>${esc(node.title||'Theorem')}</h2><div class="library-theorem-loading"><span></span>Loading theorem source…</div>`;
    if(error&&!detail)return `<div class="global-inspector-kind">Mathlib theorem · read-only</div><code>${esc(name)}</code><h2>${esc(node.title||'Theorem')}</h2><div class="library-theorem-error">${esc(error)}</div><button class="btn" data-library-inspect-refresh="${esc(name)}">Retry inspection</button>`;
    if(!detail)return `<div class="global-inspector-kind">Mathlib theorem · read-only</div><code>${esc(name)}</code><h2>${esc(node.title||'Theorem')}</h2><p>Selecting a theorem reads its declaration and source-reference neighborhood directly from the installed Mathlib index.</p>`;
    const q=encodeURIComponent(name),nodeId=`LIB:${name}`;
    const action=`<button class="btn btn-primary" data-library-open-dependency-graph="${esc(name)}">Open dependencies</button><button class="btn" data-library-edit-copy="${esc(name)}">Edit a copy</button>${registered&&claimId?`<button class="btn" data-claim="${esc(claimId)}">Open workspace record</button>`:''}`;
    const formula=detail.presentation_latex
      ? `<div class="library-theorem-formula" aria-label="${esc(detail.presentation_text||name)}">\\[${esc(detail.presentation_latex)}\\]</div>`
      : `<div class="library-theorem-formula-fallback">${esc(detail.presentation_text||'No conservative LaTeX rendering for this Lean syntax.')}</div>`;
    const outgoing=libraryDependencyList('Referenced in statement',detail.source_dependencies,nodeId);
    const incoming=libraryDependencyList('Referenced by statements',detail.source_used_by,nodeId);
    return `<div class="global-inspector-kind">Mathlib theorem · source index</div>
      <code class="library-theorem-name">${esc(name)}</code>
      <h2>${esc(detail.short_name||node.title||'Theorem')}</h2>
      ${formula}
      ${outgoing}${incoming}
      <details class="library-theorem-source"><summary>Lean source</summary><pre class="library-theorem-type">${esc(detail.source_declaration||detail.pretty_type||'Declaration unavailable')}</pre></details>
      <div class="library-theorem-meta"><span>${esc(detail.source_module||'Mathlib')}</span><span>${esc(detail.source_path||'source unavailable')}:${Number(detail.source_line||0)}</span><span>0 Lean calls</span></div>
      <p>This Mathlib theorem is already part of the read-only library. Edit a copy to experiment or derive new Lean source; only user-added source becomes a new workspace record.</p>
      ${error?`<div class="library-theorem-error">${esc(error)}</div>`:''}
      <div class="graph-inspector-actions">${action}<a class="btn" href="./?view=library&q=${q}">Open in Math library</a></div>`;
  }
  const relations=typeof graphRelations==='function'?graphRelations(node.id):{outgoing:[],incoming:[]};
  return `<div class="global-inspector-kind">Claim</div><code>${esc(node.lean_name||'')}</code><h2>${esc(node.title||'Claim')}</h2>${badge(node.status||'open')}<p>${esc(node.area||'')}</p><div class="graph-inspector-actions"><button class="btn btn-primary" data-claim="${esc(node.id)}">Open Claim</button><button class="btn" data-expand-global-claim="${esc(node.id)}">Expand neighbors</button></div>${typeof graphRelationList==='function'?graphRelationList('Depends on',relations.outgoing,'No loaded dependencies yet.')+graphRelationList('Used by',relations.incoming,'No loaded uses yet.'):''}`;
}

// Initial unexpanded view is the top-level modules view; expanded views report modules visible.
renderGraph=function renderPersistentGlobalGraph(){
  const graph=argumentState.graph;
  if(!graphGlobalState.loaded||!graph)return `<main class="global-graph-page"><div class="global-graph-loading"><div class="eyebrow">Global argument graph</div><h1>Opening the mathematical universe…</h1><p>MathHub shows durable Claims when they exist and otherwise reads the pinned Mathlib library directly.</p><button class="btn btn-primary" data-load-global-world>Open global graph</button></div></main>`;
  if(graphGlobalState.claimCount===0&&!graphGlobalState.libraryIndexed){
    const status=graphGlobalState.libraryStatus||{},scanState=String(status.state||'idle'),attempts=Number(status.attempts||0),error=String(status.last_error||status.error||'');
    const failed=scanState==='error'||scanState==='retry_wait';
    const heading=failed?'Mathlib index retrying…':scanState==='scanning'?'Indexing Mathlib once…':'Preparing Mathlib index…';
    const copy=failed
      ? 'The installed Mathlib source index could not be built yet. MathHub is retrying with backoff; no Claims, Proofs or Builds are being created.'
      : 'MathHub is indexing theorem and lemma declarations directly from the installed Mathlib source files. No Lean process is started.';
    return `<main class="global-graph-page"><section class="global-starter-empty">
      <div class="eyebrow">Mathlib library substrate</div><div class="global-starter-spinner" aria-hidden="true"></div>
      <h1>${esc(heading)}</h1>
      <p>${esc(copy)}</p>
      <div class="global-starter-worker-state"><strong>State: ${esc(scanState)}</strong><small>${attempts? `attempt ${attempts}` : 'waiting to start'}</small></div>
      ${error?`<details class="global-starter-detail" open><summary>Technical detail</summary><code>${esc(error)}</code></details>`:''}
      <div class="global-starter-actions"><button class="btn btn-primary" data-refresh-library-world>Refresh now</button><a class="btn" href="./?view=library">Open Math library</a></div>
      <small>After this source scan, Connections and Math library read SQLite. Lean runs only when you explicitly import or verify a theorem.</small>
    </section></main>`;
  }
  const selected=graphGlobalNode(state.graphSelected)||graph.nodes?.[0],nonClusters=(graph.nodes||[]).filter(node=>!graphIsCluster(node)).length,focus=!graphGlobalState.libraryMode&&typeof graphProofFocusHtml==='function'?graphProofFocusHtml():'',trail=!graphGlobalState.libraryMode&&typeof graphTrailHtml==='function'?graphTrailHtml():'';
  const dependency=graphGlobalState.dependencyMode,rootMeta=dependency?graphGlobalState.dependencyDetails.get(graphGlobalState.dependencyRoot):null;
  const title=dependency?`${graphGlobalState.dependencyRoot.split('.').pop()} dependencies.`:(graphGlobalState.libraryMode?'Explore Mathlib by module.':'Mathematics, all in one field of view.');
  const copy=dependency
    ? 'This is a dependency graph, not a module tree. Every arrow means “depends on”. Double-click any declaration—Choice included—to reveal what it depends on next.'
    : graphGlobalState.libraryMode
      ? 'Double-click a module to expand one level in place. As you go deeper, older levels are compacted automatically so the active branch stays readable. Module lines show containment; theorem lines show conservative source-statement references.'
      : 'Curated areas take priority; unclassified formal mathematics organizes itself deterministically by Lean namespace. Double-click clusters to unfold the hierarchy, then Claims to grow Proof neighborhoods.';
  const represented=dependency
    ? `${nonClusters.toLocaleString()} declarations visible · ${(graph.edges||[]).filter(edge=>edge.evidence_kind==='lean-dependency').length.toLocaleString()} dependency edges`
    : graphGlobalState.libraryMode
      ? `${Number(graphGlobalState.libraryTheoremCount||0).toLocaleString()} Mathlib theorems indexed`
      : `${Number(graphGlobalState.claimCount).toLocaleString()} Claims represented`;
  const actions=dependency
    ? `<button class="btn btn-primary" data-expand-dependency-frontier ${graphGlobalState.dependencyBulkLoading?'disabled':''}>${graphGlobalState.dependencyBulkLoading?'Expanding…':'Expand next level'}</button><button class="filter ${graphGlobalState.dependencyEdgeFilter==='all'?'active':''}" data-dependency-filter="all">All deps</button><button class="filter ${graphGlobalState.dependencyEdgeFilter==='proof'?'active':''}" data-dependency-filter="proof">Proof</button><button class="filter ${graphGlobalState.dependencyEdgeFilter==='statement'?'active':''}" data-dependency-filter="statement">Statement</button><a class="btn" href="./?view=library">Math library</a><button class="btn" data-back-library-modules>Module browser</button><button class="btn" data-reset-global-world>Reset dependency graph</button>`
    : graphGlobalState.libraryMode
      ? `${graphGlobalState.libraryModulePath?'<button class="btn" data-library-module-back>Back</button>':''}<a class="btn" href="./?view=library">Search Mathlib</a><button class="btn" data-random-library-theorem>Random theorem</button><button class="btn" data-reset-global-world>Reset world</button>`
      : `<button class="filter ${argumentState.depth===1?'active':''}" data-depth="1">1 hop</button><button class="filter ${argumentState.depth===2?'active':''}" data-depth="2">2 hops</button><button class="btn" data-reset-global-world>Reset world</button>`;
  const meta=dependency
    ? `<div class="global-world-meta"><span>${represented}</span><span>${Number(rootMeta?.direct_dependency_count||0).toLocaleString()} direct from root</span><span>${graphGlobalState.dependencyExpanded.size.toLocaleString()} opened</span>${graphGlobalState.dependencyPrefetchedCount?`<span>${graphGlobalState.dependencyPrefetchedCount.toLocaleString()} look-ahead cached</span>`:''}${graphGlobalState.dependencyLoading.size?'<strong>reading current dependencies…</strong>':graphGlobalState.dependencyPrefetching?'<strong>caching the next step…</strong>':''}</div>`
    : `<div class="global-world-meta"><span>${represented}</span><span>${graphGlobalState.clusterCount} ${graphGlobalState.libraryMode?(graphGlobalState.libraryModulePath?'child modules':'modules visible'):'root namespaces / clusters'}</span><span>${nonClusters} ${graphGlobalState.libraryMode?'theorems':'Claims'} visible</span>${graphGlobalState.libraryMode&&graphGlobalState.libraryContextPrunedCount? `<span>${graphGlobalState.libraryContextPrunedCount.toLocaleString()} older-context nodes compacted</span>` : graphGlobalState.libraryMode&&graphGlobalState.libraryModuleHiddenCount? `<span>${graphGlobalState.libraryModuleHiddenCount.toLocaleString()} more in this branch</span>` : `<span>${graphGlobalState.expandedAreas.size} clusters expanded</span>`}${graphGlobalState.loading?'<strong>loading…</strong>':''}</div>`;
  const hint=dependency?'drag · zoom · click to inspect · double-click to expand dependencies · arrow = depends on':'drag · zoom · click to inspect · double-click to expand · module → child = hierarchy · theorem → theorem = source reference';
  return `<main class="global-graph-page ${dependency?'dependency-graph-page':''}">${libraryBreadcrumbHtml()}${dependency?dependencyFocusCard():''}<div class="global-graph-top"><div><div class="eyebrow">${dependency?'Lean dependency graph':graphGlobalState.libraryMode?'Read-only library graph':'Global argument graph'}</div><h1>${esc(title)}</h1><p>${esc(copy)}</p></div><div class="global-graph-actions">${actions}</div></div>${!graphGlobalState.libraryMode&&typeof graphSearchHtml==='function'?graphSearchHtml():''}${trail}${focus}${meta}${graphGlobalState.libraryModuleError?`<div class="global-module-error"><strong>Module open failed</strong><code>${esc(graphGlobalState.libraryModuleError)}</code></div>`:''}<div class="global-graph-stage"><canvas id="argument-graph-canvas-element" aria-label="${dependency?'Lean dependency graph':'Global mathematical graph'}"></canvas><div class="global-graph-hint">${esc(hint)}</div><aside class="global-graph-inspector">${globalInspector(selected)}</aside></div></main>`;
};

const graphGlobalBaseBind=bind;
bind=function bindPersistentGlobalGraph(){
  graphGlobalBaseBind();if(state.view!=='graph')return;
  if(!graphGlobalState.loaded&&!graphGlobalState.loading)loadGlobalWorld();
  document.querySelector('[data-load-global-world]')?.addEventListener('click',()=>loadGlobalWorld());
  document.querySelector('[data-refresh-library-world]')?.addEventListener('click',()=>loadGlobalWorld({reset:true}));
  if(graphGlobalState.loaded&&graphGlobalState.claimCount===0&&!graphGlobalState.libraryIndexed)scheduleLibraryGraphPoll();
  else if(graphLibraryPollTimer){clearTimeout(graphLibraryPollTimer);graphLibraryPollTimer=null;}
  if(graphGlobalState.loaded&&graphGlobalState.needsInitialFit){
    graphGlobalState.needsInitialFit=false;
    requestAnimationFrame(()=>{
      const all=[...graphGlobalState.nodes.values()],roots=all.filter(node=>!node.parent_cluster_id).map(node=>node.id);
      const fitIds=graphGlobalState.libraryMode
        ? all.filter(node=>node.id==='LIBMOD:(root)'||node.parent_cluster_id==='LIBMOD:(root)').map(node=>node.id)
        : roots;
      fitGlobalGraphNodes(fitIds,{maxScale:1.0,padding:76});
    });
  }
  document.querySelector('[data-reset-global-world]')?.addEventListener('click',()=>graphGlobalState.dependencyMode?openLibraryDependencyGraph(graphGlobalState.dependencyRoot):loadGlobalWorld({reset:true}));
  document.querySelector('[data-back-library-modules]')?.addEventListener('click',()=>{graphGlobalState.dependencyMode=false;graphGlobalState.dependencyRoot='';loadGlobalWorld({reset:true});});
  document.querySelector('[data-expand-dependency-frontier]')?.addEventListener('click',()=>void expandDependencyFrontier());
  document.querySelectorAll('[data-dependency-filter]').forEach(button=>button.addEventListener('click',()=>{
    graphGlobalState.dependencyEdgeFilter=button.dataset.dependencyFilter||'all';
    render();
  }));
  document.querySelector('[data-library-module-root]')?.addEventListener('click',()=>loadGlobalWorld({reset:true}));
  document.querySelector('[data-library-module-back]')?.addEventListener('click',()=>openLibraryModuleParent());
  document.querySelectorAll('[data-library-module-path]').forEach(button=>button.addEventListener('click',()=>openLibraryModule(button.dataset.libraryModulePath)));
  document.querySelector('[data-random-library-theorem]')?.addEventListener('click',()=>randomLibraryTheorem());
  document.querySelector('[data-expand-global-area]')?.addEventListener('click',event=>expandGlobalArea(event.currentTarget.dataset.expandGlobalArea));
  document.querySelector('[data-open-library-module]')?.addEventListener('click',event=>openLibraryModule(event.currentTarget.dataset.openLibraryModule));
  document.querySelector('[data-expand-global-claim]')?.addEventListener('click',event=>expandGlobalClaim(event.currentTarget.dataset.expandGlobalClaim));
  document.querySelectorAll('[data-library-theorem-name]').forEach(button=>button.addEventListener('click',()=>selectLibraryTheorem(button.dataset.libraryTheoremName,{anchorId:button.dataset.libraryAnchor||state.graphSelected})));
  document.querySelectorAll('[data-library-dependency-name]').forEach(button=>button.addEventListener('click',()=>void expandLibraryDependencyNode(button.dataset.libraryDependencyName)));
  document.querySelectorAll('[data-library-open-dependency-graph]').forEach(button=>button.addEventListener('click',()=>void openLibraryDependencyGraph(button.dataset.libraryOpenDependencyGraph)));
  document.querySelectorAll('[data-library-dependency-root]').forEach(button=>button.addEventListener('click',()=>void openLibraryDependencyGraph(button.dataset.libraryDependencyRoot)));
  document.querySelector('[data-library-dependency-expand]')?.addEventListener('click',event=>void expandLibraryDependencyNode(event.currentTarget.dataset.libraryDependencyExpand));
  document.querySelector('[data-library-edit-copy]')?.addEventListener('click',event=>editLibraryTheoremFromGraph(event.currentTarget.dataset.libraryEditCopy));
  document.querySelector('[data-library-inspect-refresh]')?.addEventListener('click',event=>loadLibraryTheoremDetail(event.currentTarget.dataset.libraryInspectRefresh,{refresh:true}));
  const selected=graphGlobalNode(state.graphSelected);
  if(graphIsLibraryTheorem(selected)){
    const name=String(selected.lean_name||selected.title||'');
    const shouldLoadSource=!graphGlobalState.dependencyMode||Boolean(selected.source_module);
    if(shouldLoadSource&&name&&!graphGlobalState.theoremDetails.has(name)&&!graphGlobalState.theoremLoading.has(name)&&!graphGlobalState.theoremErrors.has(name))void loadLibraryTheoremDetail(name);
  }
  const inspector=document.querySelector('.global-graph-inspector'),focusCard=document.querySelector('.dependency-focus-card');
  if(window.MathJax?.typesetPromise){
    const targets=[inspector,focusCard].filter(Boolean);
    if(targets.length)window.MathJax.typesetPromise(targets).catch(()=>{});
  }
};

restoreGraphLocation=async function restoreGlobalGraphLocation(){
  const target=typeof graphStateFromLocation==='function'?graphStateFromLocation():null;if(!target)return;
  if(typeof graphWebState!=='undefined')graphWebState.restoring=true;
  try{state.view='graph';argumentState.depth=target.depth||1;await loadGlobalWorld();if(target.claim){state.graphSelected=target.claim;await expandGlobalClaim(target.claim);}}
  finally{if(typeof graphWebState!=='undefined')graphWebState.restoring=false;render();}
};
