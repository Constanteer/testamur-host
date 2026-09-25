/* Obsidian-style persistent global graph.
   The registry stays represented at all times. Curated mathematical areas and
   deterministic Lean-namespace clusters can both unfold progressively into the
   same Canvas without replacing nodes already on screen. */
const graphGlobalState={
  loaded:false,loading:false,nodes:new Map(),edges:new Map(),
  expandedAreas:new Set(),expandedClaims:new Set(),claimCount:0,clusterCount:0,
  libraryMode:false,libraryIndexed:false,libraryStatus:null,libraryTheoremCount:0,
  libraryModulePath:'',libraryModuleHiddenCount:0,libraryModuleError:'',openingModulePath:'',
  needsInitialFit:true,pendingFocusIds:[],
  theoremDetails:new Map(),theoremLoading:new Set(),theoremErrors:new Map(),theoremImporting:new Set(),
};
let graphLibraryPollTimer=null;

function graphGlobalEdgeKey(edge){return `${edge.evidence_kind||'uses'}:${edge.from}:${edge.to}`;}
function graphGlobalGraph(){return {
  mode:'world',persistent:true,session_id:'mathhub-global-world-v3',root:null,depth:argumentState.depth||1,
  nodes:[...graphGlobalState.nodes.values()],edges:[...graphGlobalState.edges.values()],
  claim_count:graphGlobalState.claimCount,cluster_count:graphGlobalState.clusterCount,
};}
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
  const ids=[clusterId,...new Set([...(preferredIds||[]),...children])];
  fitGlobalGraphNodes(ids,{maxScale:1.18,padding:84});
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
    const cached=graphGlobalState.theoremDetails.get(name);mergeLibrarySourceRelations(name,cached);return cached;
  }
  if(graphGlobalState.theoremLoading.has(name))return null;
  graphGlobalState.theoremLoading.add(name);graphGlobalState.theoremErrors.delete(name);render();
  try{
    const suffix=refresh?'&refresh=1':'';
    const detail=await apiJson(`/api/library/theorem?name=${encodeURIComponent(name)}${suffix}`);
    if(!detail?.ok)throw new Error(detail?.error||'Theorem inspection failed');
    graphGlobalState.theoremDetails.set(name,detail);
    mergeLibrarySourceRelations(name,detail);
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
    graphGlobalState.nodes.clear();graphGlobalState.edges.clear();graphGlobalState.expandedAreas.clear();graphGlobalState.expandedClaims.clear();graphGlobalState.pendingFocusIds=[];graphGlobalState.needsInitialFit=true;graphGlobalState.libraryModulePath='';graphGlobalState.libraryModuleHiddenCount=0;graphGlobalState.libraryModuleError='';
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
  if(!graphGlobalState.libraryMode)return '';
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
  const key=graphClusterKey(cluster);if(!key)return;
  state.graphSelected=clusterId;
  if(graphGlobalState.expandedAreas.has(key)){
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
  if(graphIsCluster(node||root))await expandGlobalArea(root);else await expandGlobalClaim(root);
};

graphCanvasKey=function globalCanvasKey(graph){return graph?.persistent?'mathhub-global-world-v3':`local:${graph?.root||''}`;};
const graphGlobalBaseLayout=buildCanvasLayout;
buildCanvasLayout=function globalInitialLayout(graph){
  if(!graph?.persistent)return graphGlobalBaseLayout(graph);
  graphCanvasState.clusters=[];
  const positions=new Map(),clusters=(graph.nodes||[]).filter(graphIsCluster),claims=(graph.nodes||[]).filter(node=>!graphIsCluster(node));
  const golden=Math.PI*(3-Math.sqrt(5));
  clusters.sort((a,b)=>Number(b.claim_count||0)-Number(a.claim_count||0)||String(a.title).localeCompare(String(b.title)));
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
  const root=node?.id==='LIBMOD:(root)';
  const label=root?'Mathlib':String(node?.title||'Module').split('.').slice(-1)[0];
  const fontSize=root?12.5:(selected||hover?11.5:10.5);
  ctx.font=`${root||selected?'650 ':'600 '}${fontSize}px ui-sans-serif,system-ui,sans-serif`;
  const labelWidth=Math.min(150,ctx.measureText(label).width);
  return {root,label,fontSize,labelWidth,width:root?Math.max(82,labelWidth+42):labelWidth+24,height:root?34:24};
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
    edges:(graph.edges||[]).filter(edge=>edge.evidence_kind!=='module-contains'),
    // Persistent Mathlib nodes are drawn below with their own glyph language.
    // Let the base renderer draw only relation arrows; otherwise its generic
    // hollow-circle + selected-halo nodes leak through and create bullseyes.
    nodes:[],
  };
  graphGlobalBaseDraw(canvas);argumentState.graph=original;

  const ctx=canvas.getContext('2d'),palette=graphCanvasPalette(canvas),selected=state.graphSelected,hover=graphCanvasState.hoverId;
  ctx.save();

  // Module containment: thin, non-directional, lower visual priority.
  ctx.strokeStyle='rgba(91,100,108,.20)';
  ctx.lineWidth=1;
  for(const edge of graph.edges||[]){
    if(edge.evidence_kind!=='module-contains')continue;
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
    const size=isSelected?6.5:(isHover?5.5:4.2);
    ctx.save();ctx.translate(p.x,p.y);ctx.rotate(Math.PI/4);
    ctx.beginPath();ctx.rect(-size/2,-size/2,size,size);
    if(node.status==='verified'){ctx.fillStyle=palette.accent;ctx.fill();}
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
    ctx.fillStyle=m.root?'rgba(29,34,39,.94)':(libraryCluster?'rgba(255,255,255,.94)':'rgba(255,255,255,.91)');
    ctx.fill();
    ctx.strokeStyle=isSelected?palette.ink:(isHover?'rgba(70,78,86,.62)':'rgba(92,101,109,.30)');
    ctx.lineWidth=isSelected?1.7:1;
    ctx.stroke();

    // Small hierarchy glyph — square for modules, solid center for Mathlib.
    const gx=x+(m.root?13:10),gy=p.y;
    ctx.beginPath();ctx.rect(gx-3.5,gy-3.5,7,7);
    ctx.fillStyle=m.root?'#fff':(isSelected?palette.ink:'rgba(78,88,97,.70)');
    ctx.fill();

    ctx.textAlign='left';ctx.textBaseline='middle';
    ctx.fillStyle=m.root?'#fff':palette.ink;
    ctx.font=`${m.root||isSelected?'650 ':'600 '}${m.fontSize}px ui-sans-serif,system-ui,sans-serif`;
    const label=m.label.length>24?m.label.slice(0,23)+'…':m.label;
    ctx.fillText(label,gx+(m.root?10:9),p.y+.2);

    // Counts stay off the node until hover/selection; topology is primary.
    if((isSelected||isHover)&&!m.root&&graphCanvasState.view.scale>.52){
      const count=Math.max(0,Number(node.claim_count||0));
      ctx.font='8.5px ui-sans-serif,system-ui,sans-serif';ctx.fillStyle=palette.muted;ctx.textAlign='center';ctx.textBaseline='top';
      ctx.fillText(`${count.toLocaleString()} ${graphGlobalState.libraryMode?'theorems':'claims'}`,p.x,y+m.height+5);
    }
  }
  ctx.restore();
};

function globalInspector(node){
  if(!node)return '<div class="global-inspector-empty">Select a module or theorem.</div>';
  if(graphIsCluster(node)){
    const key=graphClusterKey(node),expanded=graphGlobalState.expandedAreas.has(key),library=graphGlobalState.libraryMode&&(node.cluster_source==='mathlib-module'||node.cluster_source==='mathlib-library'),moduleCluster=node.cluster_source==='mathlib-module',namespace=node.cluster_source==='lean-namespace',kind=library?(moduleCluster?'Mathlib module':'Mathlib namespace'):(namespace?'Lean namespace':'Curated field'),path=node.module_path||node.namespace_path||node.title;
    const action=expanded?'Focus branch':(moduleCluster?'Expand one level':(library||namespace?'Expand namespace':'Expand field'));
    const countLabel=library?'theorems':'Claims';
    const hidden=Number(node.expansion_hidden_count||0);
    const copy=moduleCluster
      ? 'Double-click expands this module in place and keeps the surrounding graph visible. Enter module is an explicit focused navigation mode.'
      : library
        ? 'Read-only theorems from the installed Mathlib source index. Expand freely; nothing becomes a Claim until you explicitly import it.'
        : (namespace?'This deterministic fallback comes from the Lean declaration namespace.':'This curated mathematical field groups durable MathHub Claims.');
    const enter=moduleCluster&&path!=='(root)'?`<button class="btn" data-open-library-module="${esc(path)}" ${graphGlobalState.openingModulePath===path?'disabled':''}>${graphGlobalState.openingModulePath===path?'Entering…':'Enter module'}</button>`:'';
    return `<div class="global-inspector-kind">${esc(kind)}</div><h2>${esc(path==='(root)'?'Mathlib':path)}</h2><strong class="global-claim-count">${Number(node.claim_count||0).toLocaleString()} ${countLabel}</strong><p>${esc(copy)}</p>${hidden?`<div class="global-module-limit">Showing a bounded branch · ${hidden.toLocaleString()} more available through deeper modules or search.</div>`:''}<button class="btn btn-primary" data-expand-global-area="${esc(node.id)}">${esc(action)}</button>${enter}`;
  }
  if(graphIsLibraryTheorem(node)){
    const name=String(node.lean_name||node.title||''),detail=graphGlobalState.theoremDetails.get(name),loading=graphGlobalState.theoremLoading.has(name),error=graphGlobalState.theoremErrors.get(name),importing=graphGlobalState.theoremImporting.has(name),registered=Boolean(node.registered||detail?.registered),claimId=node.claim_id||detail?.claim_id||'';
    if(loading&&!detail)return `<div class="global-inspector-kind">Mathlib theorem · read-only</div><code>${esc(name)}</code><h2>${esc(node.title||'Theorem')}</h2><div class="library-theorem-loading"><span></span>Loading theorem source…</div>`;
    if(error&&!detail)return `<div class="global-inspector-kind">Mathlib theorem · read-only</div><code>${esc(name)}</code><h2>${esc(node.title||'Theorem')}</h2><div class="library-theorem-error">${esc(error)}</div><button class="btn" data-library-inspect-refresh="${esc(name)}">Retry inspection</button>`;
    if(!detail)return `<div class="global-inspector-kind">Mathlib theorem · read-only</div><code>${esc(name)}</code><h2>${esc(node.title||'Theorem')}</h2><p>Selecting a theorem reads its declaration and source-reference neighborhood directly from the installed Mathlib index.</p>`;
    const q=encodeURIComponent(name),nodeId=`LIB:${name}`;
    const action=registered&&claimId
      ? `<button class="btn btn-primary" data-claim="${esc(claimId)}">Open Claim</button>`
      : `<button class="btn btn-primary" data-library-import-theorem="${esc(name)}" ${importing?'disabled':''}>${importing?'Importing…':'Import as Claim'}</button>`;
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
      <p>Solid theorem links here are conservative source-statement references. They are browsing evidence, not verified proof dependencies; importing the theorem runs Lean and records the verified evidence separately.</p>
      ${error?`<div class="library-theorem-error">${esc(error)}</div>`:''}
      <div class="graph-inspector-actions">${action}<a class="btn" href="./?view=library&q=${q}">Open in Math library</a></div>`;
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
  const title=graphGlobalState.libraryMode?'Explore Mathlib by module.':'Mathematics, all in one field of view.';
  const copy=graphGlobalState.libraryMode
    ? 'Double-click a module to expand one level in place. Module lines show containment; theorem lines show conservative source-statement references. Enter module remains available when you want a focused next-level view.'
    : 'Curated areas take priority; unclassified formal mathematics organizes itself deterministically by Lean namespace. Double-click clusters to unfold the hierarchy, then Claims to grow Proof neighborhoods.';
  const represented=graphGlobalState.libraryMode
    ? `${Number(graphGlobalState.libraryTheoremCount||0).toLocaleString()} Mathlib theorems indexed`
    : `${Number(graphGlobalState.claimCount).toLocaleString()} Claims represented`;
  return `<main class="global-graph-page">${libraryBreadcrumbHtml()}<div class="global-graph-top"><div><div class="eyebrow">${graphGlobalState.libraryMode?'Read-only library graph':'Global argument graph'}</div><h1>${esc(title)}</h1><p>${esc(copy)}</p></div><div class="global-graph-actions">${graphGlobalState.libraryMode?`${graphGlobalState.libraryModulePath?'<button class="btn" data-library-module-back>Back</button>':''}<a class="btn" href="./?view=library">Search Mathlib</a><button class="btn" data-random-library-theorem>Random theorem</button>`:`<button class="filter ${argumentState.depth===1?'active':''}" data-depth="1">1 hop</button><button class="filter ${argumentState.depth===2?'active':''}" data-depth="2">2 hops</button>`}<button class="btn" data-reset-global-world>Reset world</button></div></div>${!graphGlobalState.libraryMode&&typeof graphSearchHtml==='function'?graphSearchHtml():''}${trail}${focus}<div class="global-world-meta"><span>${represented}</span><span>${graphGlobalState.clusterCount} ${graphGlobalState.libraryMode?(graphGlobalState.libraryModulePath?'child modules':'top-level modules'):'root namespaces / clusters'}</span><span>${nonClusters} ${graphGlobalState.libraryMode?'theorems':'Claims'} visible</span>${graphGlobalState.libraryMode&&graphGlobalState.libraryModuleHiddenCount? `<span>${graphGlobalState.libraryModuleHiddenCount.toLocaleString()} more in this branch</span>` : `<span>${graphGlobalState.expandedAreas.size} clusters expanded</span>`}${graphGlobalState.loading?'<strong>loading…</strong>':''}</div>${graphGlobalState.libraryModuleError?`<div class="global-module-error"><strong>Module open failed</strong><code>${esc(graphGlobalState.libraryModuleError)}</code></div>`:''}<div class="global-graph-stage"><canvas id="argument-graph-canvas-element" aria-label="Global mathematical graph"></canvas><div class="global-graph-hint">drag · zoom · click to inspect · double-click to expand · module → child = hierarchy · theorem → theorem = source reference</div><aside class="global-graph-inspector">${globalInspector(selected)}</aside></div></main>`;
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
  document.querySelector('[data-reset-global-world]')?.addEventListener('click',()=>loadGlobalWorld({reset:true}));
  document.querySelector('[data-library-module-root]')?.addEventListener('click',()=>loadGlobalWorld({reset:true}));
  document.querySelector('[data-library-module-back]')?.addEventListener('click',()=>openLibraryModuleParent());
  document.querySelectorAll('[data-library-module-path]').forEach(button=>button.addEventListener('click',()=>openLibraryModule(button.dataset.libraryModulePath)));
  document.querySelector('[data-random-library-theorem]')?.addEventListener('click',()=>randomLibraryTheorem());
  document.querySelector('[data-expand-global-area]')?.addEventListener('click',event=>expandGlobalArea(event.currentTarget.dataset.expandGlobalArea));
  document.querySelector('[data-open-library-module]')?.addEventListener('click',event=>openLibraryModule(event.currentTarget.dataset.openLibraryModule));
  document.querySelector('[data-expand-global-claim]')?.addEventListener('click',event=>expandGlobalClaim(event.currentTarget.dataset.expandGlobalClaim));
  document.querySelectorAll('[data-library-theorem-name]').forEach(button=>button.addEventListener('click',()=>selectLibraryTheorem(button.dataset.libraryTheoremName,{anchorId:button.dataset.libraryAnchor||state.graphSelected})));
  document.querySelector('[data-library-import-theorem]')?.addEventListener('click',event=>importLibraryTheorem(event.currentTarget.dataset.libraryImportTheorem));
  document.querySelector('[data-library-inspect-refresh]')?.addEventListener('click',event=>loadLibraryTheoremDetail(event.currentTarget.dataset.libraryInspectRefresh,{refresh:true}));
  const selected=graphGlobalNode(state.graphSelected);
  if(graphIsLibraryTheorem(selected)){
    const name=String(selected.lean_name||selected.title||'');
    if(name&&!graphGlobalState.theoremDetails.has(name)&&!graphGlobalState.theoremLoading.has(name)&&!graphGlobalState.theoremErrors.has(name))void loadLibraryTheoremDetail(name);
  }
  const inspector=document.querySelector('.global-graph-inspector');
  if(inspector&&window.MathJax?.typesetPromise)window.MathJax.typesetPromise([inspector]).catch(()=>{});
};

restoreGraphLocation=async function restoreGlobalGraphLocation(){
  const target=typeof graphStateFromLocation==='function'?graphStateFromLocation():null;if(!target)return;
  if(typeof graphWebState!=='undefined')graphWebState.restoring=true;
  try{state.view='graph';argumentState.depth=target.depth||1;await loadGlobalWorld();if(target.claim){state.graphSelected=target.claim;await expandGlobalClaim(target.claim);}}
  finally{if(typeof graphWebState!=='undefined')graphWebState.restoring=false;render();}
};
