/* Compare two Proof routes as a deterministic read transform over ArgumentIndex data. */
const proofCompareState={claimId:'',selected:[],comparison:null};

function proofCompareActiveDeps(route){
  const map=new Map();
  for(const dep of route?.depends_on||[]){if(dep?.active===false||!dep?.claim_id)continue;map.set(dep.claim_id,dep);}
  return map;
}
function proofCompareSet(values){return new Set((values||[]).map(String).filter(Boolean));}
function proofCompareRouteSummary(route){
  const raw=route?.raw_refs||{},deps=proofCompareActiveDeps(route),axioms=proofCompareSet(route?.verification?.axioms||[]);
  return {proof_id:route?.proof_id,proof_author:route?.proof_author||'Formal proof',status:route?.status||'unbuilt',coverage:route?.coverage||{},verification:route?.verification||{},argument_build_id:route?.argument_build_id||'',argument_index_complete:!!route?.argument_index_complete,direct_claim_dependencies:deps.size,raw_proof_constants:proofCompareSet(raw.proof).size,raw_transitive_constants:proofCompareSet(raw.transitive).size,axiom_count:axioms.size};
}
function compareProofRoutesClient(left,right){
  if(!left||!right)return null;
  if(left.claim_id!==right.claim_id||left.claim_version_id!==right.claim_version_id)return {error:'Proofs must establish the same Claim revision.'};
  const witnesses=!!left.argument_index_complete&&!!right.argument_index_complete;
  const ld=proofCompareActiveDeps(left),rd=proofCompareActiveDeps(right),li=new Set(ld.keys()),ri=new Set(rd.keys()),shared=[...li].filter(x=>ri.has(x)),union=new Set([...li,...ri]);
  const lraw=proofCompareSet(left.raw_refs?.proof),rraw=proofCompareSet(right.raw_refs?.proof),ltr=proofCompareSet(left.raw_refs?.transitive),rtr=proofCompareSet(right.raw_refs?.transitive),lax=proofCompareSet(left.verification?.axioms),rax=proofCompareSet(right.verification?.axioms);
  const depOverlap=witnesses?(union.size?Math.round(1000*shared.length/union.size)/10:100):null;
  const rawUnion=new Set([...lraw,...rraw]),rawShared=[...lraw].filter(x=>rraw.has(x));
  const formalOverlap=witnesses?(rawUnion.size?Math.round(1000*rawShared.length/rawUnion.size)/10:100):null;
  const depSort=(a,b)=>String(a.title||a.claim_id).localeCompare(String(b.title||b.claim_id));
  const leftCoverage=String(left.coverage?.status||'unavailable'),rightCoverage=String(right.coverage?.status||'unavailable'),fullyCovered=witnesses&&leftCoverage==='complete'&&rightCoverage==='complete';
  return {
    claim_id:left.claim_id,claim_version_id:left.claim_version_id,argument_witnesses_available:witnesses,comparison_scope:fullyCovered?'complete-current-dependencies':(witnesses?'registered-current-dependencies':'verification-only'),left:proofCompareRouteSummary(left),right:proofCompareRouteSummary(right),left_route:left,right_route:right,
    mathematical:{shared_dependencies:shared.map(id=>ld.get(id)).sort(depSort),only_left:[...li].filter(x=>!ri.has(x)).map(id=>ld.get(id)).sort(depSort),only_right:[...ri].filter(x=>!li.has(x)).map(id=>rd.get(id)).sort(depSort),shared_count:shared.length,union_count:union.size,dependency_overlap_percent:depOverlap},
    formal:{direct_constant_overlap_percent:formalOverlap,left_direct_count:lraw.size,right_direct_count:rraw.size,left_transitive_count:ltr.size,right_transitive_count:rtr.size,shared_transitive_count:[...ltr].filter(x=>rtr.has(x)).length},
    trust:{shared_axioms:[...lax].filter(x=>rax.has(x)).sort(),only_left_axioms:[...lax].filter(x=>!rax.has(x)).sort(),only_right_axioms:[...rax].filter(x=>!lax.has(x)).sort()}
  };
}
function proofCompareResetForClaim(claimId){if(proofCompareState.claimId!==claimId){proofCompareState.claimId=claimId;proofCompareState.selected=[];proofCompareState.comparison=null;}}
function proofCompareRefresh(c){
  if(!c){proofCompareState.selected=[];proofCompareState.comparison=null;return;}
  proofCompareResetForClaim(c.id);
  const routes=argFor(c)?.proofs||[],available=new Set(routes.map(p=>p.proof_id));
  proofCompareState.selected=proofCompareState.selected.filter(id=>available.has(id)).slice(0,2);
  const left=routes.find(p=>p.proof_id===proofCompareState.selected[0]),right=routes.find(p=>p.proof_id===proofCompareState.selected[1]);
  proofCompareState.comparison=left&&right?compareProofRoutesClient(left,right):null;
}
function proofCompareSelectionLabel(proofId){const i=proofCompareState.selected.indexOf(proofId);return i===0?'A':i===1?'B':'';}
function toggleProofCompare(proofId){
  const c=claims.find(x=>x.id===state.selected)||claims[0];if(!c||!proofId)return;proofCompareRefresh(c);
  const existing=proofCompareState.selected.indexOf(proofId);
  if(existing>=0)proofCompareState.selected.splice(existing,1);else if(proofCompareState.selected.length<2)proofCompareState.selected.push(proofId);else proofCompareState.selected[1]=proofId;
  proofCompareRefresh(c);render();
}
function proofCompareCoverage(c){if(!c||c.status==='unavailable')return 'unavailable';return c.percent===null||c.percent===undefined?c.status:`${c.percent}%`;}
function proofCompareDepList(title,items,sideClass=''){
  return `<div class="proof-compare-dep-group ${sideClass}"><strong>${esc(title)}</strong>${items?.length?`<div class="proof-compare-pills">${items.map(d=>`<button data-open-claim="${esc(d.claim_id)}"><span>${esc(d.title||d.lean_name||d.claim_id)}</span><code>${esc(d.lean_name||'')}</code></button>`).join('')}</div>`:'<span class="proof-compare-none">none</span>'}</div>`;
}
function proofCompareMetric(label,a,b){return `<div class="proof-compare-metric"><span>${esc(label)}</span><strong>${esc(String(a))}</strong><strong>${esc(String(b))}</strong></div>`;}
function proofComparisonScopeText(cmp){
  if(cmp.comparison_scope==='verification-only')return 'At least one Proof has no complete argument witness, so dependency-route overlap is unavailable.';
  if(cmp.comparison_scope==='registered-current-dependencies')return 'At least one route has incomplete registry coverage. Overlap describes only currently mapped Claim dependencies, not the complete proof route.';
  return 'Both routes have dependency-complete current Claim mappings.';
}
function proofComparisonHtml(cmp){
  if(!cmp)return proofCompareState.selected.length===1?'<div class="proof-compare-prompt">Proof A selected. Choose a second route to compare.</div>':'';
  if(cmp.error)return `<div class="callout warn">${esc(cmp.error)}</div>`;
  const a=cmp.left,b=cmp.right,m=cmp.mathematical,f=cmp.formal,t=cmp.trust,overlap=m.dependency_overlap_percent,formalOverlap=f.direct_constant_overlap_percent;
  const heading=overlap===null||overlap===undefined?'Dependency overlap unavailable':`${Number(overlap)}% registered direct dependency overlap`;
  const overlay=cmp.argument_witnesses_available?'<button class="btn btn-primary" data-overlay-proof-comparison>Overlay both routes on Graph</button>':'';
  return `<section class="proof-comparison"><header><div><div class="eyebrow">Proof comparison</div><h3>${esc(heading)}</h3><p>${esc(proofComparisonScopeText(cmp))} Differences are descriptive; MathHub does not rank one Proof as better.</p></div><button class="btn" data-clear-proof-comparison>Clear</button></header><div class="proof-compare-columns"><div class="proof-compare-route-label"><span>A</span><strong>${esc(a.proof_author)}</strong><code>${esc(a.proof_id||'')}</code></div><div class="proof-compare-route-label"><span>B</span><strong>${esc(b.proof_author)}</strong><code>${esc(b.proof_id||'')}</code></div></div><div class="proof-compare-metrics"><div class="proof-compare-metric proof-compare-head"><span></span><strong>A</strong><strong>B</strong></div>${proofCompareMetric('Verification',a.status,b.status)}${proofCompareMetric('Dependency coverage',proofCompareCoverage(a.coverage),proofCompareCoverage(b.coverage))}${proofCompareMetric('Direct Claim dependencies',a.direct_claim_dependencies,b.direct_claim_dependencies)}${proofCompareMetric('Raw proof constants',a.raw_proof_constants,b.raw_proof_constants)}${proofCompareMetric('Raw transitive constants',a.raw_transitive_constants,b.raw_transitive_constants)}${proofCompareMetric('Recorded axioms',a.axiom_count,b.axiom_count)}</div><div class="proof-compare-overlap"><strong>${m.shared_count} shared / ${m.union_count} currently mapped direct Claim dependencies</strong><span>Formal direct-constant overlap ${formalOverlap===null||formalOverlap===undefined?'unavailable':`${Number(formalOverlap)}%`} · transitive intersection ${Number(f.shared_transitive_count)}</span></div><div class="proof-compare-deps">${proofCompareDepList('Shared dependencies',m.shared_dependencies,'shared')}${proofCompareDepList('Only A',m.only_left,'left')}${proofCompareDepList('Only B',m.only_right,'right')}</div>${(t.only_left_axioms.length||t.only_right_axioms.length)?`<div class="proof-compare-trust"><strong>Axiom differences</strong><span>A only: ${t.only_left_axioms.map(esc).join(', ')||'none'}</span><span>B only: ${t.only_right_axioms.map(esc).join(', ')||'none'}</span></div>`:''}<div class="proof-compare-actions">${overlay}</div></section>`;
}

const proofCompareBaseProofHtml=proofHtml;
proofHtml=function proofHtmlWithCompare(p,i){
  const html=proofCompareBaseProofHtml(p,i),label=proofCompareSelectionLabel(p.proof_id),button=`<button type="button" class="proof-compare-select ${label?'selected':''}" data-compare-proof="${esc(p.proof_id||'')}">${label?`Route ${label}`:'Compare'}</button>`;
  return html.replace('</header>',`${button}</header>`);
};
const proofCompareBaseRenderClaim=renderClaim;
renderClaim=function renderClaimWithProofComparison(){
  const c=claims.find(x=>x.id===state.selected)||claims[0];proofCompareRefresh(c);
  const html=proofCompareBaseRenderClaim(),block=proofComparisonHtml(proofCompareState.comparison);
  if(!block)return html;
  return html.replace('<div class="proof-routes">',`${block}<div class="proof-routes">`);
};
const proofCompareBaseBind=bind;
bind=function bindProofComparison(){
  proofCompareBaseBind();
  document.querySelectorAll('[data-compare-proof]').forEach(button=>button.addEventListener('click',()=>toggleProofCompare(button.dataset.compareProof||'')));
  document.querySelector('[data-clear-proof-comparison]')?.addEventListener('click',()=>{proofCompareState.selected=[];proofCompareState.comparison=null;render();});
  document.querySelector('[data-overlay-proof-comparison]')?.addEventListener('click',()=>{if(!proofCompareState.comparison)return;window.dispatchEvent(new CustomEvent('mathhub:proof-comparison-overlay',{detail:proofCompareState.comparison}));});
};
