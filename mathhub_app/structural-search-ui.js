/* Deterministic formal-search affordances and lazy, evidence-only hit explanations. */
function structuralSearchHint(){
  return `<details class="structural-search-hint" aria-label="Advanced formal search"><summary>Advanced formal search</summary><div class="structural-search-options"><span class="structural-search-label">Exact Lean filters</span><code>namespace:Nat</code><span aria-hidden="true">·</span><code>statement-ref:Eq</code><span aria-hidden="true">·</span><code>proof-ref:Nat.add_comm</code><span aria-hidden="true">·</span><code>head:Eq</code><span aria-hidden="true">·</span><code>binders:2</code><span aria-hidden="true">·</span><code>binder-domain:Nat</code><span aria-hidden="true">·</span><code>result-ref:Eq</code><span class="structural-search-note">Optional expert syntax. Ordinary search works without any of these filters.</span></div></details>`;
}

function structuralFiltersFromQuery(raw){
  const filters=[];
  const seen=new Set();
  const re=/(?:^|\s)(namespace|statement-ref|proof-ref|head|binders|binder-domain|result-ref):([^\s]+)/gi;
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
}

function structuralProofWitnessMap(argument){
  const rows=argument?.formal_source?.proof_witnesses||[];
  return new Map(rows.filter(row=>row?.proof_id).map(row=>[row.proof_id,row]));
}

function structuralMatchedEvidence(argument,filters,preferredLeanName=''){
  const proofs=argument?.proofs||[];
  const sourceByProof=structuralProofWitnessMap(argument);
  const output=[];
  const unavailable=(filter,basis)=>({
    operator:filter.operator,value:filter.value,matched:false,basis,
    proof_id:null,build_id:null,schema:null,
  });
  const routeEvidence=(filter,basis,predicate,shape='partition')=>{
    for(const proof of proofs){
      if(!proof?.argument_index_complete)continue;
      const source=sourceByProof.get(proof.proof_id)||{};
      const build=source.argument_build||null;
      if(!build)continue;
      const evidence=shape==='shape'?build.type_shape:build.type_partition;
      if(evidence&&predicate(evidence)){
        return {
          operator:filter.operator,value:filter.value,matched:true,basis,
          proof_id:proof.proof_id||source.proof_id||null,
          build_id:proof.argument_build_id||source.argument_build_id||build.build_id||null,
          schema:shape==='shape'?(evidence.shape_version||null):(evidence.partition_version||null),
        };
      }
    }
    return unavailable(filter,basis);
  };

  for(const filter of filters){
    const value=filter.value;
    if(filter.operator==='namespace'){
      const name=String(preferredLeanName||'');
      const matched=name===value||name.startsWith(`${value}.`);
      output.push({
        operator:'namespace',value,matched,
        basis:'preferred current Lean declaration name',
        declaration:name||null,proof_id:null,build_id:null,schema:null,
      });
      continue;
    }
    if(filter.operator==='statement-ref'){
      const proof=proofs.find(row=>row?.argument_index_complete&&(row.raw_refs?.statement||[]).includes(value));
      if(proof){
        output.push({
          operator:'statement-ref',value,matched:true,
          basis:'constant observed anywhere in ConstantInfo.type',
          proof_id:proof.proof_id||null,build_id:proof.argument_build_id||null,schema:null,
        });
      }else output.push(unavailable(filter,'constant observed anywhere in ConstantInfo.type'));
      continue;
    }
    if(filter.operator==='proof-ref'){
      const proof=proofs.find(row=>row?.argument_index_complete&&(row.raw_refs?.proof||[]).includes(value));
      if(proof){
        output.push({
          operator:'proof-ref',value,matched:true,
          basis:'direct constant observed in the elaborated proof / value Expr',
          proof_id:proof.proof_id||null,build_id:proof.argument_build_id||null,schema:null,
        });
      }else output.push(unavailable(filter,'direct constant observed in the elaborated proof / value Expr'));
      continue;
    }
    if(filter.operator==='head'){
      output.push(routeEvidence(filter,'final result application head',shape=>shape.result_head===value,'shape'));
      continue;
    }
    if(filter.operator==='binders'){
      const expected=Number(value);
      output.push(routeEvidence(filter,'leading Pi / forall binder count',shape=>Number(shape.result_binder_count)===expected,'shape'));
      continue;
    }
    if(filter.operator==='binder-domain'){
      output.push(routeEvidence(filter,'leading binder-domain application head',partition=>(partition.binder_domain_heads||[]).includes(value)));
      continue;
    }
    if(filter.operator==='result-ref'){
      output.push(routeEvidence(filter,'constant used by the final result Expr',partition=>(partition.result_refs||[]).includes(value)));
    }
  }
  return output;
}

function structuralEvidenceRowsHtml(rows){
  if(!rows.length)return '<div class="structural-match-empty">No structural filter is active.</div>';
  return rows.map(row=>{
    const query=`${row.operator}:${row.value}`;
    const witness=row.matched
      ? [row.proof_id&&`Proof ${row.proof_id}`,row.build_id&&`Build ${row.build_id}`,row.schema].filter(Boolean).map(x=>`<code>${esc(x)}</code>`).join(' ')
      : '<span class="structural-match-unavailable">recorded witness unavailable in the current argument read; no inference made</span>';
    const declaration=row.declaration?`<small>matched declaration <code>${esc(row.declaration)}</code></small>`:'';
    return `<div class="structural-match-row ${row.matched?'':'unavailable'}"><div><code class="structural-match-query">${esc(query)}</code><span>${esc(row.basis||'recorded Lean evidence')}</span></div><div>${witness}</div>${declaration}</div>`;
  }).join('');
}

function structuralEvidenceDetails(claimId){
  return `<details class="structural-match-evidence" data-structural-evidence="${esc(claimId)}"><summary><span>Matched formal evidence</span><small>exact recorded Lean witnesses · loaded on demand</small></summary><div class="structural-match-body" data-structural-evidence-body>Open to read the Build-scoped evidence for this search hit.</div></details>`;
}

function structuralPreferredLeanName(claimId){
  const raw=(typeof registryState==='object'&&registryState?.claims?.get)?registryState.claims.get(claimId):null;
  if(raw?.lean_name)return raw.lean_name;
  const ui=(typeof claims!=='undefined'&&Array.isArray(claims))?claims.find(claim=>claim.id===claimId):null;
  return ui?.leanName||'';
}

async function loadStructuralEvidence(details,claimId,filters){
  if(details.dataset.structuralEvidenceState)return;
  details.dataset.structuralEvidenceState='loading';
  const body=details.querySelector('[data-structural-evidence-body]');
  if(body)body.innerHTML='<span class="structural-match-loading">Loading recorded argument witnesses…</span>';
  try{
    const preferred=structuralPreferredLeanName(claimId);
    if(filters.every(filter=>filter.operator==='namespace')){
      if(body)body.innerHTML=structuralEvidenceRowsHtml(structuralMatchedEvidence({},filters,preferred));
      details.dataset.structuralEvidenceState='loaded';
      return;
    }
    const argument=(typeof loadArgument==='function')?await loadArgument(claimId):null;
    if(!argument){
      if(body)body.innerHTML='<span class="structural-match-unavailable">Argument evidence is unavailable. Nothing was inferred from rendered Lean text.</span>';
      details.dataset.structuralEvidenceState='unavailable';
      return;
    }
    const rows=structuralMatchedEvidence(argument,filters,preferred);
    if(body)body.innerHTML=structuralEvidenceRowsHtml(rows)+`<div class="structural-match-note">Each Build-scoped filter is witnessed independently on an active complete argument Build. Combined filters may therefore be satisfied by different Proof routes; the exact Proof and Build are shown above.</div>`;
    details.dataset.structuralEvidenceState='loaded';
  }catch(_){
    if(body)body.innerHTML='<span class="structural-match-unavailable">Could not read the recorded argument witness. No fallback inference was used.</span>';
    details.dataset.structuralEvidenceState='unavailable';
  }
}

function decorateStructuralEvidence(){
  if(typeof state!=='object'||state.view!=='registry')return;
  const filters=structuralFiltersFromQuery(state.search);
  if(!filters.length)return;
  document.querySelectorAll('.argument-claim-row[data-claim], .claim-card[data-claim]').forEach(card=>{
    if(card.nextElementSibling?.classList?.contains('structural-match-evidence'))return;
    const claimId=card.dataset.claim;
    card.insertAdjacentHTML('afterend',structuralEvidenceDetails(claimId));
    const details=card.nextElementSibling;
    if(!details)return;
    details.addEventListener('toggle',()=>{
      if(details.open)loadStructuralEvidence(details,claimId,filters);
    });
  });
}

function decorateStructuralSearch(){
  if(typeof state!=='object' || state.view!=='registry') return;
  const hero=document.querySelector('.argument-search')||document.querySelector('.hero-search');
  if(hero&&!document.querySelector('.structural-search-hint')){
    hero.insertAdjacentHTML('afterend',structuralSearchHint());
  }
  const input=document.querySelector('#search');
  if(input) input.placeholder='Search by theorem, topic, author, or keyword…';
  decorateStructuralEvidence();
}

/* Live catalog pages are already filtered by Catalog.page(). Do not interpret
   structural operators a second time as literal browser-side text. Static mode
   keeps the prototype's original local filtering behavior. */
if(typeof renderRegistry==='function'){
  const structuralSearchBaseRegistryRender=renderRegistry;
  renderRegistry=function structuralSearchRegistryRender(){
    const live=typeof catalogState==='object'&&!!catalogState.live;
    if(!live)return structuralSearchBaseRegistryRender();
    const rawSearch=state.search;
    state.search='';
    try{
      const html=structuralSearchBaseRegistryRender();
      const emptySearch='<input id="search" class="search-input" value=""';
      const restored=`<input id="search" class="search-input" value="${esc(rawSearch)}"`;
      return html.replace(emptySearch,restored);
    }finally{
      state.search=rawSearch;
    }
  };
}

const structuralSearchBaseRender=render;
render=function structuralSearchRender(){
  structuralSearchBaseRender();
  decorateStructuralSearch();
};

decorateStructuralSearch();
