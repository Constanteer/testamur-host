/* Lightweight layout relaxation for the persistent global graph.
   This is presentation only: ProofDependency remains the source of every edge. */
const graphForceState={frame:0,raf:0,velocity:new Map()};

function graphForceVelocity(id){if(!graphForceState.velocity.has(id))graphForceState.velocity.set(id,{x:0,y:0});return graphForceState.velocity.get(id);}
function graphForceAdd(id,fx,fy,weight=1){const v=graphForceVelocity(id);v.x+=fx/weight;v.y+=fy/weight;}

function graphForceTick(){
  const graph=argumentState.graph;if(!graph?.persistent||!graphGlobalState.loaded)return false;
  const nodes=(graph.nodes||[]).filter(node=>graphCanvasState.positions.has(node.id)),count=nodes.length;
  if(!count)return false;
  const byId=new Map(nodes.map(node=>[node.id,node]));

  for(const edge of graph.edges||[]){
    const a=graphCanvasState.positions.get(edge.from),b=graphCanvasState.positions.get(edge.to);if(!a||!b)continue;
    const dx=b.x-a.x,dy=b.y-a.y,d=Math.max(1,Math.hypot(dx,dy)),clusterEdge=graphIsCluster(byId.get(edge.from))||graphIsCluster(byId.get(edge.to));
    const target=clusterEdge?330:145,k=clusterEdge?0.0016:0.0032,delta=(d-target)*k,fx=dx/d*delta,fy=dy/d*delta;
    graphForceAdd(edge.from,fx,fy,graphIsCluster(byId.get(edge.from))?5:1);graphForceAdd(edge.to,-fx,-fy,graphIsCluster(byId.get(edge.to))?5:1);
  }

  // Expanded Claim nodes and child namespace clusters stay loosely associated
  // with the exact parent cluster that produced them. This works for both
  // curated areas and namespace hierarchy; no semantic grouping is invented here.
  for(const node of nodes){
    const parentId=node.parent_cluster_id;if(!parentId)continue;
    const p=graphCanvasState.positions.get(node.id),h=graphCanvasState.positions.get(parentId);if(!h||!p)continue;
    const dx=h.x-p.x,dy=h.y-p.y,d=Math.max(1,Math.hypot(dx,dy)),preferred=graphIsCluster(node)?205:150,pull=Math.max(0,d-preferred)*.0009;
    graphForceAdd(node.id,dx/d*pull,dy/d*pull,graphIsCluster(node)?2.5:1);
  }

  const pairLimit=count<=360?count:Math.min(count,46);
  for(let i=0;i<count;i++){
    const aNode=nodes[i],a=graphCanvasState.positions.get(aNode.id);if(!a)continue;
    const iterations=count<=360?count-i-1:pairLimit;
    for(let step=0;step<iterations;step++){
      const j=count<=360?i+1+step:(i+1+((step*37+i*13)%Math.max(1,count-1)))%count;if(j===i||j>=count)continue;
      const bNode=nodes[j],b=graphCanvasState.positions.get(bNode.id);if(!b)continue;let dx=b.x-a.x,dy=b.y-a.y,d2=dx*dx+dy*dy;if(d2<1){dx=.5;dy=.5;d2=.5;}
      const d=Math.sqrt(d2),clusterPair=graphIsCluster(aNode)||graphIsCluster(bNode),strength=(clusterPair?1500:520)/Math.max(90,d2),fx=dx/d*strength,fy=dy/d*strength;
      graphForceAdd(aNode.id,-fx,-fy,graphIsCluster(aNode)?7:1);graphForceAdd(bNode.id,fx,fy,graphIsCluster(bNode)?7:1);
    }
  }

  let cx=0,cy=0;for(const node of nodes){const p=graphCanvasState.positions.get(node.id);cx+=p.x;cy+=p.y;}cx/=count;cy/=count;
  for(const node of nodes){const v=graphForceVelocity(node.id),p=graphCanvasState.positions.get(node.id),mass=graphIsCluster(node)?4:1;v.x+=(-cx*.0008)/mass;v.y+=(-cy*.0008)/mass;v.x*=.84;v.y*=.84;const speed=Math.hypot(v.x,v.y),cap=graphIsCluster(node)?3.2:7;if(speed>cap){v.x=v.x/speed*cap;v.y=v.y/speed*cap;}p.x+=v.x;p.y+=v.y;}
  return true;
}

function runGraphForce(frames=90){
  graphForceState.frame=Math.max(graphForceState.frame,frames);if(graphForceState.raf)return;
  const step=()=>{graphForceState.raf=0;if(graphForceState.frame<=0)return;graphForceState.frame--;if(graphForceTick()){const canvas=document.querySelector('#argument-graph-canvas-element');if(canvas)drawGraphCanvas(canvas);}graphForceState.raf=requestAnimationFrame(step);};
  graphForceState.raf=requestAnimationFrame(step);
}

const graphForceBaseMerge=mergeWorldChunk;
mergeWorldChunk=function forceAwareMerge(chunk,options={}){
  const result=graphForceBaseMerge(chunk,options);
  // Mathlib source-module browsing has a meaningful hierarchy. Force simulation
  // destroys that structure and turns parent/child branches into a random cloud.
  if(!graphGlobalState.libraryMode)runGraphForce(100);
  return result;
};
const graphForceBaseWorld=loadGlobalWorld;
loadGlobalWorld=async function forceAwareWorld(options={}){
  const result=await graphForceBaseWorld(options);
  if(!graphGlobalState.libraryMode)runGraphForce(130);
  return result;
};
