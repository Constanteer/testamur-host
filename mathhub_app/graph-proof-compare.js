/* Temporary reading overlay for two compared Proof routes. Claim graph ontology is unchanged. */
const graphProofCompareState={comparison:null};
function graphProofCompareTargets(route){return [...new Set((route?.depends_on||[]).filter(d=>d?.active!==false&&d?.claim_id).map(d=>d.claim_id))];}
function graphProofCompareRouteData(){
  const cmp=graphProofCompareState.comparison;if(!cmp)return null;
  return {source:cmp.claim_id,left:graphProofCompareTargets(cmp.left_route),right:graphProofCompareTargets(cmp.right_route),comparison:cmp};
}
const graphProofCompareBaseDraw=drawGraphCanvas;
drawGraphCanvas=function drawComparedProofRoutes(canvas){
  graphProofCompareBaseDraw(canvas);const route=graphProofCompareRouteData(),graph=argumentState.graph;if(!canvas||!route||!graph)return;
  if(typeof graphGlobalState!=='undefined'&&graphGlobalState.libraryMode)return;
  const positions=graphCanvasState.positions,sourceWorld=positions.get(route.source);if(!sourceWorld)return;
  const ctx=canvas.getContext('2d'),palette=graphCanvasPalette(canvas),source=graphScreenPoint(sourceWorld),left=new Set(route.left),right=new Set(route.right),all=[...new Set([...left,...right])];
  ctx.save();
  for(const id of all){const world=positions.get(id);if(!world)continue;const target=graphScreenPoint(world),inLeft=left.has(id),inRight=right.has(id);
    if(inLeft){ctx.setLineDash([]);drawArrow(ctx,source,target,11,{...palette,line:palette.accent},3.1);}
    if(inRight){ctx.setLineDash([7,5]);drawArrow(ctx,source,target,11,{...palette,line:palette.ink},2.2);ctx.setLineDash([]);}
    ctx.beginPath();ctx.arc(target.x,target.y,inLeft&&inRight?16:14,0,Math.PI*2);ctx.strokeStyle=inLeft&&inRight?palette.accent:(inLeft?palette.accent:palette.ink);ctx.lineWidth=inLeft&&inRight?2.5:1.8;ctx.stroke();
  }
  ctx.beginPath();ctx.arc(source.x,source.y,18,0,Math.PI*2);ctx.strokeStyle=palette.accent;ctx.lineWidth=2.8;ctx.stroke();ctx.restore();
};
function graphProofCompareBar(){
  const route=graphProofCompareRouteData();if(!route)return '';
  const cmp=route.comparison,m=cmp.mathematical||{},left=cmp.left||{},right=cmp.right||{},positions=graphCanvasState.positions||new Map();
  const leftVisible=route.left.filter(id=>positions.has(id)).length,rightVisible=route.right.filter(id=>positions.has(id)).length,scope=cmp.comparison_scope==='complete-current-dependencies'?'dependency-complete routes':'currently mapped footprint';
  return `<div class="graph-proof-compare-bar"><div><span>Proof comparison · ${esc(scope)}</span><strong>A · ${esc(left.proof_author||left.proof_id||'Proof')}</strong><strong>B · ${esc(right.proof_author||right.proof_id||'Proof')}</strong></div><div><span class="graph-route-key"><i class="solid"></i>A solid</span><span class="graph-route-key"><i class="dashed"></i>B dashed</span><small>A ${leftVisible}/${route.left.length} visible · B ${rightVisible}/${route.right.length} visible · ${Number(m.shared_count||0)} shared</small><button type="button" data-clear-graph-proof-comparison>Clear</button></div></div>`;
}
const graphProofCompareBaseRender=renderGraph;
renderGraph=function renderComparedProofGraph(){const html=graphProofCompareBaseRender(),bar=graphProofCompareBar();if(!bar)return html;if(html.includes('<div class="global-world-meta">'))return html.replace('<div class="global-world-meta">',`${bar}<div class="global-world-meta">`);if(html.includes('<div class="graph-window-meta">'))return html.replace('<div class="graph-window-meta">',`${bar}<div class="graph-window-meta">`);return html;};
window.addEventListener('mathhub:proof-comparison-overlay',async event=>{
  const cmp=event.detail;if(!cmp?.claim_id||!cmp?.argument_witnesses_available)return;graphProofCompareState.comparison=cmp;graphProofFocusState.proofId='';state.view='graph';
  try{if(typeof loadGlobalWorld==='function')await loadGlobalWorld();state.graphSelected=cmp.claim_id;if(typeof expandGlobalClaim==='function')await expandGlobalClaim(cmp.claim_id);else if(typeof loadGraph==='function')await loadGraph(cmp.claim_id);}finally{render();}
});
app.addEventListener('click',event=>{if(event.target.closest?.('[data-clear-graph-proof-comparison]')){graphProofCompareState.comparison=null;render();}});
