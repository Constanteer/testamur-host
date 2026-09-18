/* Current-Claim-Version workbench invariant.
   A Proof belongs to exactly one Claim Version. Never preload an old Version's
   proof source into the current Version's declaration editor. */

const versionBaseEnsureClaimDetail = ensureClaimDetail;
ensureClaimDetail = async function versionAwareEnsureClaimDetail(id) {
  const detail = await versionBaseEnsureClaimDetail(id);
  if (!detail) return detail;
  const c = claims.find(x => x.id === id);
  if (!c) return detail;

  const currentProof = detail.current_proof_id
    ? (detail.proofs || []).find(p => p.proof_id === detail.current_proof_id)
    : (detail.proofs || []).find(p =>
        p.is_current_version && Object.prototype.hasOwnProperty.call(p, 'proof_source'));

  const imports = currentProof?.imports?.length ? currentProof.imports : ['Mathlib'];
  const proofSource = currentProof?.proof_source || 'by\n  sorry';
  c.imports = imports;
  c.proof = proofSource;
  c.formalizer = currentProof?.proof_author || '—';
  c.status = detail.status || c.status;
  state.work[id] = {
    imports: imports.join('\n'),
    declaration: detail.current.formal_declaration,
    proof: proofSource,
    theoremName: detail.current.lean_name,
  };
  return detail;
};
