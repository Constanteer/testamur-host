/* Namespace registry coverage is distinct from Proof dependency coverage. */
function namespaceCoverageLabel(c){
  if(!c||c.status==='unavailable')return 'Registry coverage unknown';
  return `${Number(c.registered_theorems||0).toLocaleString()} / ${Number(c.total_theorems||0).toLocaleString()} theorems registered`;
}

const graphCoverageBaseDraw=drawGraphCanvas;
drawGraphCanvas=function drawGraphWithNamespaceCoverage(canvas){
  graphCoverageBaseDraw(canvas);
  const graph=argumentState.graph;if(!canvas||!graph?.persistent)return;
  if(typeof graphGlobalState!=='undefined'&&graphGlobalState.libraryMode)return;
  const ctx=canvas.getContext('2d'),palette=graphCanvasPalette(canvas);
  ctx.save();ctx.lineCap='round';
  for(const node of graph.nodes||[]){
    if(!graphIsCluster(node))continue;
    const coverage=node.registry_coverage,pct=coverage?.percent;
    if(pct===null||pct===undefined||!Number.isFinite(Number(pct)))continue;
    const world=graphCanvasState.positions.get(node.id);if(!world)continue;
    const p=graphScreenPoint(world),scale=Math.max(.72,Math.min(1.25,graphCanvasState.view.scale));
    const r=(Math.max(18,Math.min(46,18+Math.sqrt(Number(node.claim_count||1))*2.1))+4)*scale;
    ctx.beginPath();ctx.arc(p.x,p.y,r,-Math.PI/2,-Math.PI/2+Math.PI*2*Math.max(0,Math.min(100,Number(pct)))/100);
    ctx.strokeStyle=palette.ink;ctx.globalAlpha=.42;ctx.lineWidth=Math.max(1.5,2.2*scale);ctx.stroke();
  }
  ctx.restore();
};

const graphCoverageBaseInspector=globalInspector;
globalInspector=function globalInspectorWithCoverage(node){
  const html=graphCoverageBaseInspector(node);
  if(!node||!graphIsCluster(node)||node.cluster_source!=='lean-namespace')return html;
  const c=node.registry_coverage;
  const panel=c&&c.status!=='unavailable'
    ?`<div class="graph-registry-coverage"><div><strong>${Number(c.percent||0).toFixed(1)}%</strong><span>namespace registered</span></div><div class="graph-registry-meter"><span style="width:${Math.max(0,Math.min(100,Number(c.percent||0)))}%"></span></div><p>${esc(namespaceCoverageLabel(c))} · ${Number(c.remaining_theorems||0).toLocaleString()} remaining</p></div>`
    :`<div class="graph-registry-coverage unavailable"><div><strong>—</strong><span>registry coverage</span></div><p>Build a complete theorem index for this namespace before MathHub shows a percentage.</p></div>`;
  return html.replace('<button class="btn btn-primary"',`${panel}<button class="btn btn-primary"`);
};
