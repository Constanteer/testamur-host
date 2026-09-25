/* Project one concrete Proof route back onto the Claim graph without changing
   the graph ontology. The base graph stays Claim→Claim; this is a temporary
   reading overlay over Proof-specific dependency evidence. */
const graphProofFocusState={proofId:''};

function focusedGraphProof(){return graphNavigatorState.proofArtifacts.get(graphProofFocusState.proofId)||null;}
function focusedGraphRoute(){
  const proof=focusedGraphProof(),route=proof?.argument;if(!route)return null;
  const source=route.claim_id,targets=(route.depends_on||[]).filter(dep=>dep.active!==false).map(dep=>dep.claim_id);
  return {proof,source,targets:[...new Set(targets)]};
}

const graphProofFocusBaseDraw=drawGraphCanvas;
drawGraphCanvas=function drawFocusedProofRoute(canvas){
  graphProofFocusBaseDraw(canvas);
  const route=focusedGraphRoute(),graph=argumentState.graph;if(!canvas||!route||!graph)return;
  if(typeof graphGlobalState!=='undefined'&&graphGlobalState.libraryMode)return;
  const positions=graphCanvasState.positions,palette=graphCanvasPalette(canvas),ctx=canvas.getContext('2d');
  const sourceWorld=positions.get(route.source);if(!sourceWorld)return;
  const source=graphScreenPoint(sourceWorld),visibleTargets=route.targets.filter(id=>positions.has(id));
  ctx.save();
  for(const id of visibleTargets){const target=graphScreenPoint(positions.get(id));drawArrow(ctx,source,target,11,{...palette,line:palette.accent},3.1);}
  for(const id of [route.source,...visibleTargets]){const world=positions.get(id);if(!world)continue;const point=graphScreenPoint(world);ctx.beginPath();ctx.arc(point.x,point.y,id===route.source?17:14,0,Math.PI*2);ctx.strokeStyle=palette.accent;ctx.lineWidth=id===route.source?2.6:2;ctx.stroke();}
  ctx.restore();
};

const graphProofFocusBaseLoadProofs=loadGraphEdgeProofs;
loadGraphEdgeProofs=async function proofFocusAwareLoad(edge,host){
  await graphProofFocusBaseLoadProofs(edge,host);
  const ids=(edge?.proof_ids||[]).slice(0,4),articles=[...host.querySelectorAll('.graph-proof-routes article')];
  articles.forEach((article,index)=>{
    const proofId=ids[index];if(!proofId)return;
    const button=document.createElement('button');button.type='button';button.className='graph-proof-focus-button';button.dataset.focusGraphProof=proofId;button.textContent=graphProofFocusState.proofId===proofId?'Route highlighted':'Highlight route';article.append(button);
  });
};

function graphProofFocusHtml(){
  const route=focusedGraphRoute();if(!route)return '';
  const author=route.proof.proof_author||'Formal proof',visible=route.targets.filter(id=>graphCanvasState.positions.has(id)).length,total=route.targets.length;
  return `<div class="graph-proof-focus-bar"><span>Proof route</span><strong>${esc(author)}</strong><code>${esc(route.proof.proof_id||graphProofFocusState.proofId)}</code><small>${visible}/${total} dependencies visible in this window</small><button type="button" data-clear-graph-proof>Clear</button></div>`;
}

const graphProofFocusBaseRender=renderGraph;
renderGraph=function proofFocusRenderGraph(){
  const html=graphProofFocusBaseRender(),focus=graphProofFocusHtml();
  if(!focus)return html;
  if(html.includes('<div class="graph-window-meta">'))return html.replace('<div class="graph-window-meta">',`${focus}<div class="graph-window-meta">`);
  return html;
};

app.addEventListener('click',event=>{
  const focus=event.target.closest?.('[data-focus-graph-proof]');
  if(focus){graphProofFocusState.proofId=focus.dataset.focusGraphProof||'';render();return;}
  if(event.target.closest?.('[data-clear-graph-proof]')){graphProofFocusState.proofId='';render();}
});
