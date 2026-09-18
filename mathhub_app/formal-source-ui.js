/* Formal source provenance is evidence/read-model data, never inferred module ownership. */
function formalSourceShort(value,n=12){const text=String(value||'');return text.length>n?`${text.slice(0,n)}…`:text;}
function formalSourceBindingRows(bindings){
  return (bindings||[]).map(binding=>`<div class="formal-source-binding"><div><code>${esc(binding.lean_name||'')}</code><span>${esc(binding.origin||'binding')}</span></div><div><span>Build</span><code>${esc(binding.source_build_id||'—')}</code><span>environment</span><code title="${esc(binding.environment_hash||'')}">${esc(formalSourceShort(binding.environment_hash))}</code></div><div><span>type</span><code title="${esc(binding.type_hash||'')}">${esc(formalSourceShort(binding.type_hash))}</code><span>fingerprint</span><code title="${esc(binding.formal_fingerprint||'')}">${esc(formalSourceShort(binding.formal_fingerprint))}</code></div></div>`).join('');
}
function formalSourceRefs(values){
  const refs=(values||[]).filter(Boolean);
  return refs.length?refs.map(value=>`<code>${esc(value)}</code>`).join(' '):'<code>—</code>';
}
function formalSourceStructure(build){
  const shape=build?.type_shape||null,partition=build?.type_partition||null;
  if(!shape&&!partition)return '<code>—</code><small>no recorded type-structure witness</small>';
  const head=shape?(shape.result_head||'non-constant'):'unavailable';
  const rawBinders=partition?.result_binder_count??shape?.result_binder_count;
  const binders=Number.isFinite(Number(rawBinders))?Number(rawBinders):'—';
  const schemas=[shape?.shape_version,partition?.partition_version].filter(Boolean);
  const partitionMissing=!partition;
  return `<div><small>result head ${esc(head)} · ${esc(binders)} leading binder${binders===1?'':'s'}</small><small>binder-domain heads ${partitionMissing?'<code>—</code>':formalSourceRefs(partition.binder_domain_heads)}</small><small>result constants ${partitionMissing?'<code>—</code>':formalSourceRefs(partition.result_refs)}</small><small>evidence <code>${esc(build?.build_id||'—')}</code>${schemas.length?` · ${schemas.map(x=>`<code>${esc(x)}</code>`).join(' ')}`:''}</small><small>${partitionMissing?'no recorded positional type-partition witness':'formal type evidence only; not ProofDependency edges'}</small></div>`;
}
function formalSourceWitnessRows(witnesses){
  return (witnesses||[]).map(w=>{
    const vb=w.verification_build||{},ab=w.argument_build||{},structure=Object.keys(ab).length?ab:vb;
    const evidence=({
      'external-declaration-proof-expr':'original external theorem proof Expr',
      'local-proof-expr':'local Proof Expr',
      unavailable:'unavailable',
    })[w.argument_evidence_origin]||w.argument_evidence_origin||'unavailable';
    return `<div class="formal-source-witness"><header><strong>${esc(w.proof_author||'Formal proof')}</strong><code>${esc(w.proof_id||'')}</code></header><div class="formal-source-witness-grid"><span>Verification</span><div><code>${esc(w.verification_build_id||'—')}</code><small>${esc(w.verification_role==='import-bridge'?'import bridge':'local proof')}</small></div><span>Argument</span><div><code>${esc(w.argument_build_id||'—')}</code><small>${esc(evidence)}</small></div><span>Environment</span><div><code title="${esc(ab.environment_hash||vb.environment_hash||'')}">${esc(formalSourceShort(ab.environment_hash||vb.environment_hash))}</code>${(ab.mathlib_revision||vb.mathlib_revision)?`<small>mathlib ${esc(formalSourceShort(ab.mathlib_revision||vb.mathlib_revision,14))}</small>`:''}</div><span>Type structure</span><div>${formalSourceStructure(structure)}</div><span>Proof imports</span><div><code>${esc((w.imports||[]).join(', ')||'—')}</code><small>visibility set, not source-module ownership</small></div></div></div>`;
  }).join('');
}
function formalSourceHtml(source){
  if(!source)return '';
  const imported=source.kind==='imported-library',external=source.external_declarations||[],local=source.local_declarations||[];
  const primary=imported?external[0]:local[0]||source.claim_lean_name||'';
  const pair=(source.import_pairs||[])[0]||{},sourceBuild=(source.bindings||[]).find(b=>b.origin==='imported-declaration')?.source_build||null;
  const meta=[];
  if(imported)meta.push('<span>Imported Lean theorem</span>');else if(source.kind==='local')meta.push('<span>Local Lean declaration</span>');else meta.push('<span>No current FormalBinding</span>');
  if(sourceBuild?.mathlib_revision)meta.push(`<span>mathlib <code>${esc(formalSourceShort(sourceBuild.mathlib_revision,14))}</code></span>`);
  if(sourceBuild?.environment_hash)meta.push(`<span>environment <code title="${esc(sourceBuild.environment_hash)}">${esc(formalSourceShort(sourceBuild.environment_hash))}</code></span>`);
  const alias=pair.verification_aliases||local;
  return `<section class="formal-source-block"><div class="formal-source-kicker">Formal source</div><div class="formal-source-primary"><code>${esc(primary||'unbound')}</code>${meta.join('')}</div>${imported&&alias.length?`<div class="formal-source-bridge"><span>verification alias</span>${alias.map(x=>`<code>${esc(x)}</code>`).join('')}</div>`:''}<details class="formal-source-details"><summary>Binding & witness provenance</summary><div class="formal-source-note">Current Claim revision only. Lean namespace and Proof imports are not treated as source-module evidence.</div><h4>FormalBindings</h4><div class="formal-source-bindings">${formalSourceBindingRows(source.bindings)||'<span class="quiet-empty">No current bindings.</span>'}</div><h4>Proof witnesses</h4><div class="formal-source-witnesses">${formalSourceWitnessRows(source.proof_witnesses)||'<span class="quiet-empty">No current Proof witness.</span>'}</div><div class="formal-source-module-note"><strong>Source module</strong><span>not recorded</span><small>${esc(source.source_module_note||'')}</small></div></details></section>`;
}

const formalSourceBaseRenderClaim=renderClaim;
renderClaim=function renderClaimWithFormalSource(){
  const html=formalSourceBaseRenderClaim(),c=claims.find(x=>x.id===state.selected)||claims[0];if(!c)return html;
  const block=formalSourceHtml((argFor(c)||{}).formal_source);if(!block)return html;
  return html.replace('<section class="proof-routes-section">',`${block}<section class="proof-routes-section">`);
};
