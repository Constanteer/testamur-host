/* Mathematical graph search extension: mapped current ProofDependency evidence. */
(function(){
  if(typeof structuralFiltersFromQuery!=='function'||typeof structuralMatchedEvidence!=='function')return;

  const baseHint=structuralSearchHint;
  const baseFilters=structuralFiltersFromQuery;
  const baseMatched=structuralMatchedEvidence;
  const baseDecorate=decorateStructuralSearch;

  function canonicalClaimRef(value){
    const match=/^CLM-(\d+)$/i.exec(String(value||''));
    if(!match)return null;
    return `CLM-${String(Number(match[1])).padStart(6,'0')}`;
  }

  structuralSearchHint=function graphStructuralSearchHint(){
    const html=baseHint();
    if(html.includes('used-by:CLM-000123'))return html;
    return html.replace(
      '<code>proof-ref:Nat.add_comm</code>',
      '<code>uses:Nat.add_comm</code><span aria-hidden="true">·</span><code>used-by:CLM-000123</code><span aria-hidden="true">·</span><code>proof-ref:Nat.add_comm</code>'
    );
  };

  structuralFiltersFromQuery=function graphStructuralFiltersFromQuery(raw){
    const filters=baseFilters(raw);
    const seen=new Set(filters.map(row=>`${row.operator}\u0000${row.value}`));
    const re=/(?:^|\s)(uses|used-by):([^\s]+)/gi;
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

  function usesEvidence(argument,filter){
    const selector=canonicalClaimRef(filter.value);
    for(const proof of argument?.proofs||[]){
      if(!proof?.argument_index_complete)continue;
      for(const dependency of proof.depends_on||[]){
        if(!dependency?.active)continue;
        const evidence=(dependency.evidence_constants||[]).map(String);
        const target=canonicalClaimRef(dependency.claim_id)||dependency.claim_id||null;
        const matched=selector ? target===selector : evidence.includes(filter.value);
        if(!matched)continue;
        const evidenceConstant=selector ? (evidence[0]||null) : filter.value;
        return {
          operator:'uses',value:filter.value,matched:true,
          basis:selector
            ? `mapped current ProofDependency to ${target}${evidenceConstant?` · evidence ${evidenceConstant}`:''}`
            : `mapped current ProofDependency via exact FormalBinding ${filter.value}${target?` → ${target}`:''}`,
          proof_id:proof.proof_id||null,
          build_id:proof.argument_build_id||null,
          schema:null,
          target_claim_id:target,
          evidence_constant:evidenceConstant,
        };
      }
    }
    return {
      operator:'uses',value:filter.value,matched:false,
      basis:selector
        ? `mapped current ProofDependency to ${selector}`
        : `mapped current ProofDependency via exact environment-scoped FormalBinding ${filter.value}`,
      proof_id:null,build_id:null,schema:null,target_claim_id:selector,evidence_constant:null,
    };
  }

  function usedByEvidence(argument,filter){
    const selector=canonicalClaimRef(filter.value);
    for(const source of argument?.used_by||[]){
      const sourceClaim=canonicalClaimRef(source?.claim_id)||source?.claim_id||null;
      const exactStoredName=String(source?.lean_name||'');
      const matched=selector ? sourceClaim===selector : exactStoredName===filter.value;
      if(!matched)continue;
      const proofIds=(source?.proof_ids||[]).map(String);
      const evidence=(source?.evidence_constants||[]).map(String);
      return {
        operator:'used-by',value:filter.value,matched:true,
        basis:selector
          ? `current reverse ProofDependency from source ${sourceClaim}`
          : `current reverse ProofDependency from exact recorded source name ${filter.value}${sourceClaim?` · ${sourceClaim}`:''}`,
        proof_id:proofIds[0]||null,
        build_id:null,
        schema:null,
        source_claim_id:sourceClaim,
        evidence_constant:evidence[0]||null,
      };
    }
    return {
      operator:'used-by',value:filter.value,matched:false,
      basis:selector
        ? `current reverse ProofDependency from source ${selector}`
        : `server search resolves source ${filter.value} through an exact environment-scoped FormalBinding; the compact reverse argument read does not repeat external source-binding names`,
      proof_id:null,build_id:null,schema:null,source_claim_id:selector,evidence_constant:null,
    };
  }

  structuralMatchedEvidence=function graphStructuralMatchedEvidence(argument,filters,preferredLeanName=''){
    const ordinary=filters.filter(filter=>filter.operator!=='uses'&&filter.operator!=='used-by');
    const baseRows=baseMatched(argument,ordinary,preferredLeanName);
    const byKey=new Map(baseRows.map(row=>[`${row.operator}\u0000${row.value}`,row]));
    for(const filter of filters){
      if(filter.operator==='uses')byKey.set(`uses\u0000${filter.value}`,usesEvidence(argument,filter));
      if(filter.operator==='used-by')byKey.set(`used-by\u0000${filter.value}`,usedByEvidence(argument,filter));
    }
    return filters.map(filter=>byKey.get(`${filter.operator}\u0000${filter.value}`)).filter(Boolean);
  };

  function decorateGraphSearch(){
    if(typeof state!=='object'||state.view!=='registry')return;
    const hint=document.querySelector('.structural-search-hint');
    if(hint&&!String(hint.textContent||'').includes('used-by:CLM-000123')){
      hint.outerHTML=structuralSearchHint();
    }
    const input=document.querySelector('#search');
    if(input)input.placeholder='Search mathematics · uses:Nat.add_comm · used-by:CLM-000123 · head:Eq';
  }

  decorateStructuralSearch=function graphDecorateStructuralSearch(){
    baseDecorate();
    decorateGraphSearch();
  };

  decorateGraphSearch();
})();
