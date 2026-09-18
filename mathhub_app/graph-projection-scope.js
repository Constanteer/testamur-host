/* Cluster pages progressively refine one visual projection. Nodes persist, but
   aggregate edges from an older page must not survive after more direct Claims
   have been unfolded. projection_scope makes that replacement explicit. */
const graphProjectionBaseMerge=mergeWorldChunk;
mergeWorldChunk=function projectionScopedMerge(chunk,options={}){
  const scope=String(chunk?.projection_scope||'');
  if(scope){
    for(const [key,edge] of graphGlobalState.edges.entries()){
      if(edge?.projection_scope===scope)graphGlobalState.edges.delete(key);
    }
    chunk={...chunk,edges:(chunk.edges||[]).map(edge=>({...edge,projection_scope:scope}))};
  }
  return graphProjectionBaseMerge(chunk,options);
};
