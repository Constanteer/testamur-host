/* Explicit Claim-level and same-Proof-route dependency quantifiers. */
function quantifierSchemaGapHtml(witness){
  const gaps=witness?.query_unavailable_requirements||[];
  if(!gaps.length)return '';
  const rows=gaps.map(gap=>{
    const selector=gap?.value?`${gap.operator}:${gap.value}`:String(gap?.operator||'query');
    const missing=(gap?.missing||[]).join(', ');
    return `<span class="structural-evidence-state-gap"><code>${esc(selector)}</code>${missing?` <span>missing ${esc(missing)}</span>`:''}</span>`;
  }).join('<span aria-hidden="true"> · </span>');
  return `<small class="structural-evidence-state-detail">${rows}</small>`;
}

function universalEvidenceFailureDetail(witness,selector){
  const universal=witness?.evidence_state?.universal;
  if(!universal)return '';
  if(universal.state==='no-current-routes')return 'No current Proof routes exist; universal negative is deliberately non-vacuous.';
  for(const route of universal.routes||[]){
    if(route.reason==='missing-active-build')return `${route.proof_id||'A current Proof'} has no active audited argument Build.`;
    const state=(route.selectors||[]).find(row=>String(row.selector)===String(selector));
    if(!state)continue;
    if(state.reason==='selector-unresolved')return `${route.proof_id||'A current Proof'} cannot resolve this selector in environment ${route.environment_hash||'unknown'}.`;
    if(state.reason==='raw-proof-ref-present'){
      const targets=(state.contradicting_claim_ids||[]).join(', ');
      return `${route.proof_id||'A current Proof'} has a raw proof Expr reference mapped to ${targets||'the selected Claim'}.`;
    }
  }
  if(universal.state==='incomplete')return 'At least one current Proof route lacks complete negative evidence.';
  if(universal.state==='contradicted')return 'At least one current Proof route directly contradicts this exclusion.';
  return '';
}

function routeGroupFailureDetail(witness){
  const group=witness?.evidence_state?.route_group;
  if(!group)return '';
  if(group.state==='no-current-routes')return 'No current Proof routes exist.';
  if(group.state==='no-positive-route')return 'No current complete Proof route satisfies all mapped route-uses clauses.';
  if(group.state==='incomplete'){
    const route=(group.candidate_routes||[]).find(row=>row.state==='incomplete');
    if(route?.reason==='missing-active-build')return `${route.proof_id||'A candidate Proof'} has no active audited argument Build.`;
    const unresolved=(route?.selectors||[]).find(row=>row.reason==='selector-unresolved');
    if(unresolved)return `${route.proof_id||'A candidate Proof'} cannot resolve ${unresolved.selector} in its Build environment.`;
    return 'At least one candidate route remains evidence-incomplete.';
  }
  if(group.state==='contradicted'){
    const route=(group.candidate_routes||[]).find(row=>row.state==='contradicted');
    const contradicted=(route?.selectors||[]).find(row=>row.reason==='raw-proof-ref-present');
    const targets=(contradicted?.contradicting_claim_ids||[]).join(', ');
    return `${route?.proof_id||'A candidate Proof'} directly contradicts ${contradicted?.selector||'a route-not-uses clause'}${targets?` via ${targets}`:''}.`;
  }
  return '';
}

function quantifierWitnessHtml(filters,witness){
  const universalFilters=filters.filter(filter=>filter.operator==='not-uses');
  const routeFilters=filters.filter(filter=>filter.operator==='route-uses'||filter.operator==='route-not-uses');
  if(!witness?.available){
    const gapHtml=quantifierSchemaGapHtml(witness);
    return `<div class="structural-match-row unavailable"><div><code class="structural-match-query">dependency quantifiers</code><span>exact negative / same-route evidence</span></div><div><span class="structural-match-unavailable">quantifier evidence unavailable; no absence inference made</span></div>${gapHtml}</div>`;
  }

  const universalBySelector=new Map((witness.universal||[]).map(row=>[String(row.selector),row]));
  const universalHtml=universalFilters.map(filter=>{
    const row=universalBySelector.get(String(filter.value));
    if(!row?.matched){
      const detail=universalEvidenceFailureDetail(witness,filter.value);
      return `<div class="structural-match-row unavailable"><div><code class="structural-match-query">not-uses:${esc(filter.value)}</code><span>all current Proof routes must exclude this Claim in complete raw proof evidence</span></div><div><span class="structural-match-unavailable">universal exclusion witness is no longer current; no missing-edge fallback used</span></div>${detail?`<small class="structural-evidence-state-detail">${esc(detail)}</small>`:''}</div>`;
    }
    const checked=Number(row.complete_routes_checked||0);
    return `<div class="structural-match-row"><div><code class="structural-match-query">not-uses:${esc(filter.value)}</code><span>universal current-route exclusion</span></div><div><code>${esc(String(checked))} complete route${checked===1?'':'s'} checked</code></div><small>Each route has an active complete argument Build; the selector resolves by exact environment-scoped FormalBinding, and no raw proof Expr constant maps to that Claim.</small>${witness?.evidence_state?.universal?.state==='established'?'<small class="structural-evidence-state-detail">Evidence state: established.</small>':''}</div>`;
  }).join('');

  let routeHtml='';
  if(routeFilters.length){
    const route=witness.route_group;
    if(!route?.matched){
      const detail=routeGroupFailureDetail(witness);
      routeHtml=`<div class="structural-match-row unavailable"><div><code class="structural-match-query">same Proof route</code><span>${routeFilters.map(filter=>`${filter.operator}:${filter.value}`).map(esc).join(' · ')}</span></div><div><span class="structural-match-unavailable">same-route witness is no longer current; clauses were not split across Proofs</span></div>${detail?`<small class="structural-evidence-state-detail">${esc(detail)}</small>`:''}</div>`;
    }else{
      const header=[route.proof_id&&`Proof ${route.proof_id}`,route.build_id&&`Build ${route.build_id}`]
        .filter(Boolean).map(value=>`<code>${esc(value)}</code>`).join(' ');
      const positiveBySelector=new Map((route.positive||[]).map(row=>[String(row.selector),row]));
      const negativeBySelector=new Map((route.negative||[]).map(row=>[String(row.selector),row]));
      const clauses=routeFilters.map(filter=>{
        if(filter.operator==='route-uses'){
          const row=positiveBySelector.get(String(filter.value));
          const target=row?.target_claim_id?` → ${row.target_claim_id}`:'';
          const evidence=row?.evidence_constant?` · evidence ${row.evidence_constant}`:'';
          return `<div class="structural-quantifier-clause positive"><code>route-uses:${esc(filter.value)}</code><span>mapped current ProofDependency${esc(target)}${esc(evidence)}</span></div>`;
        }
        const row=negativeBySelector.get(String(filter.value));
        const targets=(row?.resolved_claim_ids||[]).join(', ');
        return `<div class="structural-quantifier-clause negative"><code>route-not-uses:${esc(filter.value)}</code><span>raw proof Expr exclusion${targets?` · resolved ${esc(targets)}`:''}</span></div>`;
      }).join('');
      routeHtml=`<div class="structural-match-row structural-quantifier-route"><div><code class="structural-match-query">same Proof route</code><span>all route-scoped clauses share one existential witness</span></div><div>${header}</div><div class="structural-quantifier-clauses">${clauses}</div><small>Positive clauses are mapped ProofDependency edges. Negative clauses are established from the same active Build's complete raw proof refs plus exact FormalBindings, not from missing edges.</small>${witness?.evidence_state?.route_group?.state==='established'?'<small class="structural-evidence-state-detail">Evidence state: established.</small>':''}</div>`;
    }
  }
  return universalHtml+routeHtml;
}

(function(){
  if(typeof structuralFiltersFromQuery!=='function'||typeof loadStructuralEvidence!=='function')return;

  const baseHint=structuralSearchHint;
  const baseFilters=structuralFiltersFromQuery;
  const baseMatched=structuralMatchedEvidence;
  const baseDecorate=decorateStructuralSearch;
  const baseLoad=loadStructuralEvidence;

  structuralSearchHint=function quantifierStructuralSearchHint(){
    const html=baseHint();
    if(html.includes('route-not-uses:Nat.mul_comm'))return html;
    const anchor='<code>path:2:Nat.add_comm</code>';
    const addition='<code>path:2:Nat.add_comm</code><span aria-hidden="true">·</span><code>not-uses:Nat.mul_comm</code><span aria-hidden="true">·</span><code>route-uses:Nat.add_comm</code><span aria-hidden="true">·</span><code>route-not-uses:Nat.mul_comm</code>';
    return html.includes(anchor)?html.replace(anchor,addition):html+addition;
  };

  structuralFiltersFromQuery=function quantifierStructuralFiltersFromQuery(raw){
    const filters=baseFilters(raw);
    const seen=new Set(filters.map(row=>`${row.operator}\u0000${row.value}`));
    const re=/(?:^|\s)(route-not-uses|route-uses|not-uses):([^\s]+)/gi;
    let match;
    while((match=re.exec(String(raw||'')))!==null){
      const operator=match[1].toLowerCase();
      const value=match[2];
      const key=`${operator}\u0000${value}`;
      if(seen.has(key))continue;
      seen.add(key);
      filters.push({operator,value});
    }
    return filters;
  };

  structuralMatchedEvidence=function quantifierStructuralMatchedEvidence(argument,filters,preferredLeanName=''){
    const ordinary=filters.filter(filter=>!['not-uses','route-uses','route-not-uses'].includes(filter.operator));
    return baseMatched(argument,ordinary,preferredLeanName);
  };

  async function readQuantifierWitness(claimId,filters){
    if(typeof apiJson!=='function')return {available:false,universal:[],route_group:null};
    const params=new URLSearchParams();
    for(const filter of filters){
      if(filter.operator==='not-uses')params.append('not_uses',filter.value);
      if(filter.operator==='route-uses')params.append('route_uses',filter.value);
      if(filter.operator==='route-not-uses')params.append('route_not_uses',filter.value);
    }
    return apiJson(`/api/claims/${claimId}/quantifier-witness?${params.toString()}`);
  }

  loadStructuralEvidence=async function quantifierLoadStructuralEvidence(details,claimId,filters){
    const quantified=filters.filter(filter=>['not-uses','route-uses','route-not-uses'].includes(filter.operator));
    if(!quantified.length)return baseLoad(details,claimId,filters);
    if(details.dataset.structuralEvidenceState)return;
    const ordinary=filters.filter(filter=>!['not-uses','route-uses','route-not-uses'].includes(filter.operator));
    const body=details.querySelector('[data-structural-evidence-body]');

    if(ordinary.length){
      await baseLoad(details,claimId,ordinary);
    }else{
      details.dataset.structuralEvidenceState='loading';
      if(body)body.innerHTML='<span class="structural-match-loading">Loading exact dependency quantifier witness…</span>';
    }

    let witness;
    try{witness=await readQuantifierWitness(claimId,quantified)}
    catch(_){witness={available:false,universal:[],route_group:null}}
    const html=quantifierWitnessHtml(quantified,witness);
    if(body){
      if(ordinary.length)body.insertAdjacentHTML('beforeend',html);
      else body.innerHTML=html;
      body.insertAdjacentHTML('beforeend','<div class="structural-match-note">Quantifiers are explicit: <code>not-uses:</code> is universal across all current complete Proof routes; <code>route-*</code> clauses share one current Proof / active Build witness. Negative clauses are never inferred from a missing graph edge.</div>');
    }
    details.dataset.structuralEvidenceState='loaded';
  };

  function decorateQuantifierSearch(){
    if(typeof state!=='object'||state.view!=='registry')return;
    const hint=document.querySelector('.structural-search-hint');
    if(hint&&!String(hint.textContent||'').includes('route-not-uses:Nat.mul_comm')){
      hint.outerHTML=structuralSearchHint();
    }
    const input=document.querySelector('#search');
    if(input)input.placeholder='Search by theorem, topic, author, or keyword…';
  }

  decorateStructuralSearch=function quantifierDecorateStructuralSearch(){
    baseDecorate();
    decorateQuantifierSearch();
  };

  decorateQuantifierSearch();
})();
