/* Keep the current persistent graph session alive while import tasks add Claims.
   Refresh the root skeleton plus coverage metadata for already-visible namespace
   clusters. Never re-fetch cluster projection pages merely to update percentages. */
async function refreshVisibleNamespaceCoverage(){
  if(typeof graphGlobalState==='undefined'||!graphGlobalState.loaded)return;
  const keys=[...new Set([...graphGlobalState.nodes.values()]
    .filter(node=>node?.kind==='cluster'&&node.cluster_source==='lean-namespace')
    .map(node=>String(node.cluster_key||''))
    .filter(key=>key.startsWith('ns:')))].slice(0,100);
  if(!keys.length)return;
  const params=new URLSearchParams();for(const key of keys)params.append('key',key);
  const result=await apiJson(`/api/graph/coverage?${params.toString()}`),coverages=result.coverages||{};
  for(const node of graphGlobalState.nodes.values()){
    const key=String(node?.cluster_key||''),coverage=coverages[key];
    if(coverage)graphGlobalState.nodes.set(node.id,{...node,registry_coverage:coverage});
  }
}

window.addEventListener('mathhub:import-task-progress',async event=>{
  const action=event.detail?.action;if(action!=='step')return;
  if(state?.view!=='graph'||typeof graphGlobalState==='undefined'||!graphGlobalState.loaded||graphGlobalState.loading)return;
  try{
    const world=await apiJson('/api/graph/world');
    if(typeof mergeWorldChunk==='function')mergeWorldChunk(world);
    graphGlobalState.claimCount=Number(world.claim_count||graphGlobalState.claimCount||0);
    graphGlobalState.clusterCount=Number(world.cluster_count||graphGlobalState.clusterCount||0);
    await refreshVisibleNamespaceCoverage();
    argumentState.graph=graphGlobalGraph();render();
  }catch(_){}
});
