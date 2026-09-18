/* Scalable browser renderer for bounded argument-graph neighborhoods.
   The server remains responsible for graph size: the browser never downloads the
   whole registry. Canvas avoids one DOM/SVG element per node or edge. */
const graphCanvasState={key:'',positions:new Map(),view:{scale:1,panX:0,panY:0},hoverId:'',pointer:null,resizeObserver:null};

function graphCanvasKey(graph){
  return [graph?.root||'recent',graph?.depth||0,(graph?.nodes||[]).map(n=>n.id).join('|')].join('::');
}

function graphAdjacency(graph){
  const map=new Map((graph?.nodes||[]).map(n=>[n.id,[]]));
  for(const edge of graph?.edges||[]){
    if(map.has(edge.from)&&map.has(edge.to)){map.get(edge.from).push(edge.to);map.get(edge.to).push(edge.from);}
  }
  return map;
}

function stableAngle(id){
  let hash=2166136261;
  for(const char of String(id)){hash^=char.charCodeAt(0);hash=Math.imul(hash,16777619);}
  return ((hash>>>0)%6283)/1000;
}

function buildCanvasLayout(graph){
  const nodes=graph?.nodes||[];
  const positions=new Map();
  if(!nodes.length)return positions;
  const rootId=(graph?.root&&nodes.some(n=>n.id===graph.root))?graph.root:null;
  if(!rootId){
    const golden=Math.PI*(3-Math.sqrt(5));
    nodes.forEach((node,index)=>{
      const radius=34*Math.sqrt(index);
      const angle=index*golden+stableAngle(node.id)*.08;
      positions.set(node.id,{x:Math.cos(angle)*radius,y:Math.sin(angle)*radius});
    });
    return positions;
  }

  const adjacency=graphAdjacency(graph),level=new Map([[rootId,0]]),queue=[rootId];
  for(let i=0;i<queue.length;i++){
    const current=queue[i],nextLevel=(level.get(current)||0)+1;
    for(const neighbor of adjacency.get(current)||[]){
      if(level.has(neighbor))continue;
      level.set(neighbor,nextLevel);queue.push(neighbor);
    }
  }
  positions.set(rootId,{x:0,y:0});
  const groups=new Map();let maxLevel=0;
  for(const node of nodes){
    if(node.id===rootId)continue;
    const l=level.has(node.id)?level.get(node.id):Math.max(2,maxLevel+1);
    maxLevel=Math.max(maxLevel,l);if(!groups.has(l))groups.set(l,[]);groups.get(l).push(node);
  }
  for(const [l,items] of [...groups.entries()].sort((a,b)=>a[0]-b[0])){
    const radius=150*l;
    items.sort((a,b)=>String(a.id).localeCompare(String(b.id)));
    const offset=stableAngle(rootId)*.35+l*.41;
    items.forEach((node,index)=>{
      const angle=offset+(Math.PI*2*index/Math.max(1,items.length));
      positions.set(node.id,{x:Math.cos(angle)*radius,y:Math.sin(angle)*radius});
    });
  }
  return positions;
}

function canvasBounds(positions){
  const points=[...positions.values()];
  if(!points.length)return {minX:-1,maxX:1,minY:-1,maxY:1};
  return points.reduce((b,p)=>({minX:Math.min(b.minX,p.x),maxX:Math.max(b.maxX,p.x),minY:Math.min(b.minY,p.y),maxY:Math.max(b.maxY,p.y)}),{minX:Infinity,maxX:-Infinity,minY:Infinity,maxY:-Infinity});
}

function graphCanvasPalette(canvas){
  const s=getComputedStyle(canvas);
  const pick=(name,fallback)=>s.getPropertyValue(name).trim()||fallback;
  return {paper:pick('--panel','#fbfaf6'),ink:pick('--ink','#202522'),muted:pick('--muted','#68716d'),line:pick('--line','#d9d8d1'),accent:pick('--accent','#4f7564'),blue:pick('--blue','#486b7a'),red:pick('--red','#8a4e49')};
}

function graphScreenPoint(world){const v=graphCanvasState.view;return {x:world.x*v.scale+v.panX,y:world.y*v.scale+v.panY};}

function fitGraphCanvas(canvas){
  const rect=canvas.getBoundingClientRect(),bounds=canvasBounds(graphCanvasState.positions),margin=84;
  const width=Math.max(1,bounds.maxX-bounds.minX),height=Math.max(1,bounds.maxY-bounds.minY);
  const scale=Math.max(.18,Math.min(1.35,(rect.width-margin*2)/width,(rect.height-margin*2)/height));
  graphCanvasState.view.scale=Number.isFinite(scale)?scale:1;
  graphCanvasState.view.panX=rect.width/2-((bounds.minX+bounds.maxX)/2)*graphCanvasState.view.scale;
  graphCanvasState.view.panY=rect.height/2-((bounds.minY+bounds.maxY)/2)*graphCanvasState.view.scale;
  drawGraphCanvas(canvas);
}

function drawArrow(ctx,from,to,radius,palette,width){
  const dx=to.x-from.x,dy=to.y-from.y,length=Math.hypot(dx,dy);if(length<1)return;
  const ux=dx/length,uy=dy/length,end={x:to.x-ux*radius,y:to.y-uy*radius};
  ctx.strokeStyle=palette.line;ctx.lineWidth=width;ctx.beginPath();ctx.moveTo(from.x,from.y);ctx.lineTo(end.x,end.y);ctx.stroke();
  const size=5+Math.min(3,width),angle=Math.atan2(dy,dx);
  ctx.fillStyle=palette.line;ctx.beginPath();ctx.moveTo(end.x,end.y);ctx.lineTo(end.x-Math.cos(angle-.55)*size,end.y-Math.sin(angle-.55)*size);ctx.lineTo(end.x-Math.cos(angle+.55)*size,end.y-Math.sin(angle+.55)*size);ctx.closePath();ctx.fill();
}

function drawGraphCanvas(canvas){
  const graph=argumentState.graph;if(!canvas||!graph)return;
  const rect=canvas.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2);
  const width=Math.max(1,Math.floor(rect.width)),height=Math.max(1,Math.floor(rect.height));
  if(canvas.width!==Math.floor(width*dpr)||canvas.height!==Math.floor(height*dpr)){canvas.width=Math.floor(width*dpr);canvas.height=Math.floor(height*dpr);}
  const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,width,height);
  const palette=graphCanvasPalette(canvas),nodes=graph.nodes||[],byId=new Map(nodes.map(n=>[n.id,n])),selected=state.graphSelected||graph.root||nodes[0]?.id;

  for(const edge of graph.edges||[]){
    const a=graphCanvasState.positions.get(edge.from),b=graphCanvasState.positions.get(edge.to);if(!a||!b)continue;
    const from=graphScreenPoint(a),to=graphScreenPoint(b),proofs=Number(edge.proof_count||edge.proof_ids?.length||1);
    drawArrow(ctx,from,to,10,palette,Math.min(2.4,1+Math.log2(Math.max(1,proofs))*.28));
  }

  const dense=nodes.length>72,showOrdinaryLabels=!dense&&graphCanvasState.view.scale>.68;
  for(const node of nodes){
    const world=graphCanvasState.positions.get(node.id);if(!world)continue;
    const point=graphScreenPoint(world),isSelected=node.id===selected,isHover=node.id===graphCanvasState.hoverId,radius=isSelected?10:isHover?8:6;
    if(point.x<-80||point.y<-80||point.x>width+80||point.y>height+80)continue;
    ctx.beginPath();ctx.arc(point.x,point.y,radius,0,Math.PI*2);
    if(node.status==='verified'){ctx.fillStyle=palette.accent;ctx.fill();}
    else if(node.status==='partial'){ctx.fillStyle=palette.paper;ctx.fill();ctx.strokeStyle=palette.blue;ctx.lineWidth=2;ctx.stroke();}
    else{ctx.fillStyle=palette.paper;ctx.fill();ctx.strokeStyle=palette.muted;ctx.lineWidth=1.2;ctx.stroke();}
    if(isSelected){ctx.beginPath();ctx.arc(point.x,point.y,radius+5,0,Math.PI*2);ctx.strokeStyle=palette.ink;ctx.lineWidth=1;ctx.stroke();}
    if(showOrdinaryLabels||isSelected||isHover){
      const title=String(node.title||node.lean_name||'Claim'),max=isSelected||isHover?38:24,label=title.length>max?title.slice(0,max-1)+'…':title;
      ctx.font=`${isSelected?'600 ':''}12px ui-serif, Georgia, serif`;ctx.textAlign='center';ctx.textBaseline='top';ctx.fillStyle=palette.ink;ctx.fillText(label,point.x,point.y+radius+8);
      if((isSelected||isHover)&&node.lean_name){ctx.font='10px ui-monospace, SFMono-Regular, monospace';ctx.fillStyle=palette.muted;ctx.fillText(String(node.lean_name).slice(0,46),point.x,point.y+radius+25);}
    }
  }
}

function graphHitTest(canvas,x,y){
  const graph=argumentState.graph;if(!graph)return null;let best=null,bestDistance=18;
  for(const node of graph.nodes||[]){const world=graphCanvasState.positions.get(node.id);if(!world)continue;const p=graphScreenPoint(world),d=Math.hypot(p.x-x,p.y-y);if(d<bestDistance){best=node;bestDistance=d;}}
  return best;
}

function mountGraphCanvas(){
  const canvas=document.querySelector('#argument-graph-canvas-element'),graph=argumentState.graph;if(!canvas||!graph)return;
  const key=graphCanvasKey(graph),fresh=key!==graphCanvasState.key;
  if(fresh){graphCanvasState.key=key;graphCanvasState.positions=buildCanvasLayout(graph);graphCanvasState.hoverId='';}
  const local=(event)=>{const r=canvas.getBoundingClientRect();return {x:event.clientX-r.left,y:event.clientY-r.top};};
  canvas.addEventListener('pointerdown',event=>{const p=local(event);canvas.setPointerCapture?.(event.pointerId);graphCanvasState.pointer={id:event.pointerId,startX:p.x,startY:p.y,panX:graphCanvasState.view.panX,panY:graphCanvasState.view.panY,moved:false};});
  canvas.addEventListener('pointermove',event=>{const p=local(event),drag=graphCanvasState.pointer;if(drag&&drag.id===event.pointerId){const dx=p.x-drag.startX,dy=p.y-drag.startY;if(Math.hypot(dx,dy)>3)drag.moved=true;graphCanvasState.view.panX=drag.panX+dx;graphCanvasState.view.panY=drag.panY+dy;drawGraphCanvas(canvas);return;}const hit=graphHitTest(canvas,p.x,p.y),next=hit?.id||'';if(next!==graphCanvasState.hoverId){graphCanvasState.hoverId=next;canvas.style.cursor=next?'pointer':'grab';drawGraphCanvas(canvas);}});
  canvas.addEventListener('pointerup',event=>{const p=local(event),drag=graphCanvasState.pointer;graphCanvasState.pointer=null;if(drag&&!drag.moved){const hit=graphHitTest(canvas,p.x,p.y);if(hit){state.graphSelected=hit.id;render();}}});
  canvas.addEventListener('pointerleave',()=>{if(!graphCanvasState.pointer&&graphCanvasState.hoverId){graphCanvasState.hoverId='';drawGraphCanvas(canvas);}});
  canvas.addEventListener('wheel',event=>{event.preventDefault();const p=local(event),old=graphCanvasState.view.scale,factor=Math.exp(-event.deltaY*.0012),next=Math.max(.18,Math.min(4,old*factor));graphCanvasState.view.panX=p.x-(p.x-graphCanvasState.view.panX)*(next/old);graphCanvasState.view.panY=p.y-(p.y-graphCanvasState.view.panY)*(next/old);graphCanvasState.view.scale=next;drawGraphCanvas(canvas);},{passive:false});
  canvas.addEventListener('dblclick',event=>{const p=local(event),hit=graphHitTest(canvas,p.x,p.y);if(hit){state.graphSelected=hit.id;loadGraph(hit.id);}});
  document.querySelector('[data-graph-fit]')?.addEventListener('click',()=>fitGraphCanvas(canvas));
  graphCanvasState.resizeObserver?.disconnect();graphCanvasState.resizeObserver=new ResizeObserver(()=>drawGraphCanvas(canvas));graphCanvasState.resizeObserver.observe(canvas);
  if(fresh)requestAnimationFrame(()=>fitGraphCanvas(canvas));else requestAnimationFrame(()=>drawGraphCanvas(canvas));
}

// Keep the public graph bounded even when the registry itself is enormous. Depth
// increases the window modestly; the backend enforces a hard 200-node cap too.
loadGraph=async function scalableLoadGraph(root=''){
  if(argumentState.graphLoading)return;argumentState.graphLoading=true;if(state.view==='graph')render();
  try{
    const limit=argumentState.depth<=1?120:180,p=new URLSearchParams({depth:String(argumentState.depth),limit:String(limit),relations:'uses'});if(root)p.set('claim',root);
    argumentState.graph=await apiJson(`/api/graph?${p}`);if(root)state.graphSelected=root;
  }finally{argumentState.graphLoading=false;if(state.view==='graph')render();}
};

renderGraph=function scalableRenderGraph(){
  const graph=argumentState.graph;
  if(!graph)return `<main class="shell page argument-graph-page"><div class="eyebrow">Argument graph</div><h1 class="display-title">Explore mathematics locally, not all at once.</h1><p class="display-deck">The registry can contain millions of Claims; the browser requests only a bounded neighborhood around what you are reading.</p><button class="btn" data-recent-graph>Load a recent slice</button></main>`;
  const nodes=graph.nodes||[],selected=nodes.find(n=>n.id===state.graphSelected)||nodes.find(n=>n.id===graph.root)||nodes[0],truncated=!!graph.truncated;
  return `<main class="shell page argument-graph-page canvas-graph-page"><div class="graph-title-row"><div><div class="eyebrow">Argument graph</div><h1 class="display-title">A local window into the argument graph.</h1><p class="display-deck">A → B means A depends on B. Double-click a node to re-center and fetch its neighborhood; the whole registry never enters the page.</p></div><div class="graph-depth-controls"><button class="filter ${argumentState.depth===1?'active':''}" data-depth="1">1 hop</button><button class="filter ${argumentState.depth===2?'active':''}" data-depth="2">2 hops</button></div></div><div class="graph-window-meta"><span>${nodes.length} node${nodes.length===1?'':'s'} loaded</span><span>${graph.mode==='neighborhood'?'local neighborhood':'recent slice'}</span>${truncated?'<strong>window capped — re-center to continue</strong>':''}<button type="button" data-graph-fit>Fit view</button><button type="button" data-recent-graph>Recent slice</button></div><div class="argument-graph-shell canvas-graph-shell"><div class="canvas-graph-stage"><canvas id="argument-graph-canvas-element" aria-label="Interactive argument graph. Drag to pan, scroll to zoom, click to select, double-click to re-center."></canvas><div class="canvas-graph-hint">drag to pan · wheel to zoom · double-click to expand locally</div></div><aside class="argument-graph-inspector canvas-graph-inspector">${selected?`<code>${esc(selected.lean_name||'')}</code><h2>${esc(selected.title)}</h2>${badge(selected.status||'open')}<p>${esc(selected.area||'')}</p><button class="btn btn-primary" data-claim="${selected.id}">Open Claim</button><button class="btn" data-center="${selected.id}">Fetch neighborhood</button>`:'No Claim selected.'}<div class="canvas-scale-note">Canvas handles the visible window. Scale comes from bounded server queries, not from drawing every mathematical object at once.</div></aside></div></main>`;
};

const graphCanvasBaseBind=bind;
bind=function graphCanvasBind(){graphCanvasBaseBind();if(state.view==='graph')mountGraphCanvas();};
