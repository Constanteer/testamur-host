/* Readability budgets for the persistent Obsidian-style graph.
   These are interaction budgets, not renderer capability limits. The global
   skeleton remains present while real Claim detail grows in controlled batches. */
const GRAPH_CLUSTER_UNFOLD_LIMIT=100;
const GRAPH_CLAIM_NEIGHBOR_LIMIT={1:80,2:120};
const graphClusterPaging={cursor:new Map(),remaining:new Map(),loaded:new Map()};

const graphBudgetBaseWorld=loadGlobalWorld;
loadGlobalWorld=async function budgetedGlobalWorld(options={}){
  if(options.reset){graphClusterPaging.cursor.clear();graphClusterPaging.remaining.clear();graphClusterPaging.loaded.clear();}
  return graphBudgetBaseWorld(options);
};

expandGlobalArea=async function budgetedExpandGlobalCluster(clusterId){
  const cluster=graphGlobalNode(clusterId);if(!cluster||!graphIsCluster(cluster)||graphGlobalState.loading)return;
  const key=graphClusterKey(cluster);if(!key)return;
  const cursor=graphClusterPaging.cursor.get(key)||'';
  graphGlobalState.loading=true;state.graphSelected=clusterId;render();
  try{
    const params=new URLSearchParams({key,limit:String(GRAPH_CLUSTER_UNFOLD_LIMIT)});if(cursor)params.set('cursor',cursor);
    const chunk=await apiJson(`/api/graph/cluster?${params}`);
    mergeWorldChunk(chunk,{anchorId:clusterId,radius:105});graphGlobalState.expandedAreas.add(key);
    const loaded=Number(graphClusterPaging.loaded.get(key)||0)+Number(chunk.page_claim_count||0);graphClusterPaging.loaded.set(key,loaded);
    graphClusterPaging.remaining.set(key,Number(chunk.remaining_claim_count||0));
    if(chunk.next_cursor)graphClusterPaging.cursor.set(key,chunk.next_cursor);else graphClusterPaging.cursor.delete(key);
  }finally{graphGlobalState.loading=false;argumentState.graph=graphGlobalGraph();render();}
};

expandGlobalClaim=async function budgetedExpandGlobalClaim(claimId){
  if(!claimId||graphIsCluster(claimId)||graphGlobalState.loading)return;
  graphGlobalState.loading=true;state.graphSelected=claimId;render();
  try{
    const depth=argumentState.depth===2?2:1,limit=GRAPH_CLAIM_NEIGHBOR_LIMIT[depth];
    const params=new URLSearchParams({claim:claimId,depth:String(depth),limit:String(limit),relations:'uses'});
    const chunk=await apiJson(`/api/graph?${params}`);mergeWorldChunk(chunk,{anchorId:claimId,radius:150});graphGlobalState.expandedClaims.add(claimId);
    if(typeof graphTrailRecord==='function')graphTrailRecord(claimId);if(typeof syncGraphUrl==='function')syncGraphUrl(claimId,{push:true});
  }finally{graphGlobalState.loading=false;argumentState.graph=graphGlobalGraph();render();}
};

const graphBudgetBaseInspector=globalInspector;
globalInspector=function pagedClusterInspector(node){
  if(!graphIsCluster(node))return graphBudgetBaseInspector(node);
  const key=graphClusterKey(node),expanded=graphGlobalState.expandedAreas.has(key),cursor=graphClusterPaging.cursor.get(key)||'',remaining=Number(graphClusterPaging.remaining.get(key)||0),loaded=Number(graphClusterPaging.loaded.get(key)||0),namespace=node.cluster_source==='lean-namespace',kind=namespace?'Lean namespace':'Curated field',path=namespace?(node.namespace_path||node.title):node.title;
  let action=namespace?'Expand namespace':'Expand field',disabled='';
  if(expanded&&cursor)action=`Load next ${Math.min(GRAPH_CLUSTER_UNFOLD_LIMIT,remaining||GRAPH_CLUSTER_UNFOLD_LIMIT)}`;
  else if(expanded&&!cursor){action='Direct Claims loaded';disabled=' disabled';}
  const progress=expanded?`<p class="global-cluster-progress">${loaded.toLocaleString()} direct Claims unfolded${remaining?` · ${remaining.toLocaleString()} remaining`:''}</p>`:'';
  return `<div class="global-inspector-kind">${esc(kind)}</div><h2>${esc(path)}</h2><strong class="global-claim-count">${Number(node.claim_count||0).toLocaleString()} Claims</strong><p>${namespace?'This deterministic fallback comes from the Lean declaration namespace. Child namespaces stay stable while direct theorem nodes are revealed in cursor-paginated batches.':'This explicit mathematical area overrides namespace grouping. Direct Claim nodes are revealed in cursor-paginated batches while the field remains as an anchor.'}</p>${progress}<button class="btn btn-primary" data-expand-global-area="${esc(node.id)}"${disabled}>${esc(action)}</button>`;
};
