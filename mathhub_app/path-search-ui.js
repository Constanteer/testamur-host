/* Exact bounded ProofDependency path-search affordance and lazy witness read. */
function pathSearchWitnessHtml(filter,witness){
  const via=filter.via?` via:${filter.via}`:'';
  const routeThrough=(filter.routeThrough||[]).map(value=>` route-through:${value}`).join('');
  const query=`path:${filter.depth}:${filter.target}${via}${routeThrough}`;
  const description=filter.routeThrough?.length
    ?`exact ${esc(filter.depth)}-hop path with source-Proof constraints`
    :(filter.via
      ?`exact ${esc(filter.depth)}-hop current ProofDependency path through ${esc(filter.via)}`
      :`exact ${esc(filter.depth)}-hop current ProofDependency path`);
  if(!witness?.available){
    return `<div class="structural-match-row unavailable"><div><code class="structural-match-query">${esc(query)}</code><span>${description}</span></div><div><span class="structural-match-unavailable">path index unavailable; no inference made</span></div></div>`;
  }
  if(!witness?.matched){
    return `<div class="structural-match-row unavailable"><div><code class="structural-match-query">${esc(query)}</code><span>${description}</span></div><div><span class="structural-match-unavailable">recorded path witness unavailable; no alternate Proof, alternate-middle, transitive or text fallback used</span></div></div>`;
  }
  const steps=(witness.steps||[]).map((step,index)=>{
    const route=[step.proof_id&&`Proof ${step.proof_id}`,step.build_id&&`Build ${step.build_id}`]
      .filter(Boolean).map(value=>`<code>${esc(value)}</code>`).join(' ');
    const target=[step.to_claim_id,step.to_title||step.to_lean_name].filter(Boolean).join(' · ');
    const evidence=step.evidence_constant?`<small>evidence <code>${esc(step.evidence_constant)}</code></small>`:'';
    return `<div class="structural-path-step"><span>${index+1}</span><div>${route}<span aria-hidden="true">→</span><code>${esc(target)}</code>${evidence}</div></div>`;
  }).join('');
  const viaNote=filter.via&&witness.via_claim_id
    ?`<small>middle constraint resolved exactly to <code>${esc(witness.via_claim_id)}</code>; the first dependency edge supplies the Lean environment for a named <code>via:</code> selector.</small>`
    :'';
  const routeRows=(witness.source_route_constraints||[]).map(row=>{
    const target=[row.target_claim_id,row.target_title||row.target_lean_name].filter(Boolean).join(' · ');
    const evidence=row.evidence_constant?` · evidence ${row.evidence_constant}`:'';
    return `<div class="structural-quantifier-clause positive"><code>route-through:${esc(row.selector||'')}</code><span>mapped current ProofDependency → ${esc(target)}${esc(evidence)}</span></div>`;
  }).join('');
  const sourceStep=(witness.steps||[])[0]||{};
  const routeHeader=(filter.routeThrough||[]).length
    ?`<div class="structural-path-route-constraints"><div><strong>Source Proof constraints</strong><span>same witness as hop 1</span></div><div>${[sourceStep.proof_id&&`Proof ${sourceStep.proof_id}`,sourceStep.build_id&&`Build ${sourceStep.build_id}`].filter(Boolean).map(value=>`<code>${esc(value)}</code>`).join(' ')}</div>${routeRows}</div>`
    :'';
  return `<div class="structural-match-row"><div><code class="structural-match-query">${esc(query)}</code><span>${description}</span>${viaNote}</div>${routeHeader}<div class="structural-path-steps">${steps}</div></div>`;
}

(function(){
  if(typeof structuralFiltersFromQuery!=='function'||typeof loadStructuralEvidence!=='function')return;

  const baseHint=structuralSearchHint;
  const baseFilters=structuralFiltersFromQuery;
  const baseMatched=structuralMatchedEvidence;
  const baseDecorate=decorateStructuralSearch;
  const baseLoad=loadStructuralEvidence;

  structuralSearchHint=function pathStructuralSearchHint(){
    const html=baseHint();
    const routeExample='<code>path:2:Nat.add_comm via:Nat.succ route-through:Nat.zero</code>';
    if(html.includes('route-through:Nat.zero'))return html;
    if(html.includes('<code>path:2:Nat.add_comm via:Nat.succ</code>')){
      return html.replace(
        '<code>path:2:Nat.add_comm via:Nat.succ</code>',
        `<code>path:2:Nat.add_comm via:Nat.succ</code><span aria-hidden="true">·</span>${routeExample}`
      );
    }
    if(html.includes('path:2:Nat.add_comm')){
      return html.replace(
        '<code>path:2:Nat.add_comm</code>',
        `<code>path:2:Nat.add_comm</code><span aria-hidden="true">·</span><code>path:2:Nat.add_comm via:Nat.succ</code><span aria-hidden="true">·</span>${routeExample}`
      );
    }
    return html+routeExample;
  };

  structuralFiltersFromQuery=function pathStructuralFiltersFromQuery(raw){
    const filters=baseFilters(raw).filter(filter=>filter.operator!=='path');
    const seen=new Set(filters.map(row=>`${row.operator}\u0000${row.value}`));
    const re=/(?:^|\s)path:(\d+):([^\s]+)(?:\s+via:([^\s]+))?((?:\s+route-through:[^\s]+)*)/gi;
    let match;
    while((match=re.exec(String(raw||'')))!==null){
      const depth=Number(match[1]);
      const target=match[2];
      const via=match[3]||'';
      const routeThrough=[];
      const routeRe=/route-through:([^\s]+)/gi;
      let routeMatch;
      while((routeMatch=routeRe.exec(match[4]||''))!==null){
        if(!routeThrough.includes(routeMatch[1]))routeThrough.push(routeMatch[1]);
      }
      const suffix=[via&&`via:${via}`,...routeThrough.map(value=>`route-through:${value}`)].filter(Boolean).join(' ');
      const value=`${depth}:${target}${suffix?` ${suffix}`:''}`;
      const key=`path\u0000${value}`;
      if(seen.has(key))continue;
      seen.add(key);
      filters.push({operator:'path',value,depth,target,...(via?{via}:{}),...(routeThrough.length?{routeThrough}:{})});
    }
    return filters;
  };

  structuralMatchedEvidence=function pathStructuralMatchedEvidence(argument,filters,preferredLeanName=''){
    return baseMatched(argument,filters.filter(filter=>filter.operator!=='path'),preferredLeanName);
  };

  async function readPathWitness(claimId,filter){
    if(typeof apiJson!=='function')return {available:false,matched:false,steps:[]};
    const params=new URLSearchParams({depth:String(filter.depth),target:String(filter.target)});
    if(filter.via)params.set('via',String(filter.via));
    for(const selector of filter.routeThrough||[])params.append('route_through',String(selector));
    return apiJson(`/api/claims/${claimId}/path-witness?${params.toString()}`);
  }

  loadStructuralEvidence=async function pathLoadStructuralEvidence(details,claimId,filters){
    const pathFilters=filters.filter(filter=>filter.operator==='path');
    if(!pathFilters.length)return baseLoad(details,claimId,filters);
    if(details.dataset.structuralEvidenceState)return;
    const ordinary=filters.filter(filter=>filter.operator!=='path');
    const body=details.querySelector('[data-structural-evidence-body]');

    if(ordinary.length){
      await baseLoad(details,claimId,ordinary);
    }else{
      details.dataset.structuralEvidenceState='loading';
      if(body)body.innerHTML='<span class="structural-match-loading">Loading exact graph path witness…</span>';
    }

    const witnesses=await Promise.all(pathFilters.map(async filter=>{
      try{return {filter,witness:await readPathWitness(claimId,filter)}}
      catch(_){return {filter,witness:{available:false,matched:false,steps:[]}}}
    }));
    const html=witnesses.map(({filter,witness})=>pathSearchWitnessHtml(filter,witness)).join('');
    if(body){
      if(ordinary.length)body.insertAdjacentHTML('beforeend',html);
      else body.innerHTML=html;
      body.insertAdjacentHTML('beforeend','<div class="structural-match-note">Path witnesses are exact current ProofDependency chains. <code>via:</code> binds the actual middle Claim. Every <code>route-through:</code> clause binds to the same source Proof / active Build that supplies hop 1; another Proof route cannot satisfy it. No alternate-Proof, alternate-middle, transitive-closure or rendered-Lean inference is used.</div>');
    }
    details.dataset.structuralEvidenceState='loaded';
  };

  function decoratePathSearch(){
    if(typeof state!=='object'||state.view!=='registry')return;
    const hint=document.querySelector('.structural-search-hint');
    if(hint&&!String(hint.textContent||'').includes('route-through:Nat.zero')){
      hint.outerHTML=structuralSearchHint();
    }
    const input=document.querySelector('#search');
    if(input)input.placeholder='Search mathematics · path:2:Nat.add_comm via:Nat.succ route-through:Nat.zero · route-uses:Nat.add_comm';
  }

  decorateStructuralSearch=function pathDecorateStructuralSearch(){
    baseDecorate();
    decoratePathSearch();
  };

  decoratePathSearch();
})();
