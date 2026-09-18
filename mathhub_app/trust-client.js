/* Trusted-base presentation.
   Proof completeness (verified / partial / failed) and trusted base are separate
   dimensions. The database stores raw axioms; this UI labels their implication. */

const STANDARD_TRUST_AXIOMS = new Set(['propext', 'Classical.choice', 'Quot.sound']);
const EXACT_COMPILER_AXIOMS = new Set(['Lean.trustCompiler', 'Lean.ofReduceBool', 'Lean.ofReduceNat']);
const GENERATED_COMPILER_AXIOM_RE = /\._native\.(?:native_decide|bv_decide)\.ax_[0-9]+$/;

function classifyTrustClient(axioms = []) {
  const ordered = [...new Set((axioms || []).filter(Boolean))];
  const compiler = ordered.filter(x => EXACT_COMPILER_AXIOMS.has(x) || GENERATED_COMPILER_AXIOM_RE.test(x));
  const nonstandard = ordered.filter(x =>
    !STANDARD_TRUST_AXIOMS.has(x) && x !== 'sorryAx' &&
    !EXACT_COMPILER_AXIOMS.has(x) && !GENERATED_COMPILER_AXIOM_RE.test(x)
  );
  const trustClass = compiler.length && nonstandard.length ? 'extended'
    : compiler.length ? 'compiler-assisted'
    : nonstandard.length ? 'axiomatized'
    : 'kernel';
  return {trust_class: trustClass, compiler_trust_axioms: compiler, nonstandard_axioms: nonstandard};
}

function trustLabel(value) {
  return ({
    kernel: 'kernel trust',
    axiomatized: 'extra axioms',
    'compiler-assisted': 'compiler-assisted',
    extended: 'extended trust',
  })[value] || value || 'trust unknown';
}

function trustExplanation(value) {
  return ({
    kernel: 'No nonstandard or compiler-trust axioms were reported beyond Lean’s ordinary mathematical axioms.',
    axiomatized: 'The proof is complete, but its theorem depends on one or more additional axioms.',
    'compiler-assisted': 'The proof is complete, but native/compiler evaluation is part of the trusted base.',
    extended: 'The proof uses both additional axioms and compiler/native trust.',
  })[value] || 'Trusted-base classification unavailable.';
}

function trustChip(value, extraClass = '') {
  if (!value) return '';
  return `<span class="trust-chip trust-${esc(value)} ${extraClass}" title="${esc(trustExplanation(value))}">${esc(trustLabel(value))}</span>`;
}

function latestDetailBuild(detail) {
  const all = (detail?.proofs || []).flatMap(p => p.builds || []);
  return all.sort((a,b) => String(b.created_at || '').localeCompare(String(a.created_at || '')))[0] || null;
}

function trustForBuild(build) {
  if (!build) return null;
  if (build.trust_class) return build;
  return {...build, ...classifyTrustClient(build.axioms || [])};
}

function decorateCatalogTrust() {
  document.querySelectorAll('[data-claim]').forEach(card => {
    if (card.querySelector('[data-trust-chip]')) return;
    const item = registryState.claims.get(card.dataset.claim);
    if (!item?.trust_class) return;
    const status = card.querySelector('.status');
    if (!status) return;
    const holder = document.createElement('span');
    holder.dataset.trustChip = 'catalog';
    holder.innerHTML = trustChip(item.trust_class);
    status.after(holder.firstElementChild);
  });
}

function decorateClaimTrust() {
  if (state.view !== 'claim' || !registryState.claims.has(state.selected)) return;
  const detail = registryState.details.get(state.selected);
  const build = trustForBuild(latestDetailBuild(detail));
  if (!build?.trust_class) return;

  const header = document.querySelector('.claim-header');
  if (header && !header.querySelector('[data-trust-header]')) {
    const chip = document.createElement('span');
    chip.dataset.trustHeader = '1';
    chip.innerHTML = trustChip(build.trust_class, 'trust-header-chip');
    header.querySelector('.status')?.after(chip.firstElementChild);
  }

  const rail = document.querySelector('.meta-rail');
  if (rail && !rail.querySelector('[data-trust-rail]')) {
    const row = document.createElement('div');
    row.className = 'meta-row trust-meta-row';
    row.dataset.trustRail = '1';
    const extras = [
      ...(build.compiler_trust_axioms || []),
      ...(build.nonstandard_axioms || []),
    ];
    row.innerHTML = `<div class="meta-label">Trusted base</div><div class="meta-value">${trustChip(build.trust_class)}${extras.length ? `<div class="trust-axiom-note">${extras.slice(0,4).map(esc).join('<br>')}${extras.length > 4 ? `<br>+ ${extras.length - 4} more` : ''}</div>` : ''}${build.audit_ok === false ? '<div class="trust-index-note">dependency index incomplete</div>' : ''}</div>`;
    rail.appendChild(row);
  }
}

function decorateBuildTrust() {
  if (state.view !== 'claim') return;
  const detail = registryState.details.get(state.selected);
  for (const row of document.querySelectorAll('[data-build-record]')) {
    if (row.querySelector('[data-build-trust]')) continue;
    const buildId = row.dataset.buildRecord;
    const build = trustForBuild((detail?.proofs || []).flatMap(p=>p.builds||[]).find(b=>b.build_id===buildId));
    if (!build?.trust_class) continue;
    const holder = document.createElement('span');
    holder.dataset.buildTrust = buildId;
    holder.innerHTML = trustChip(build.trust_class);
    const summary = row.querySelector('summary');
    if (summary) summary.insertBefore(holder.firstElementChild, summary.children[1] || null);
  }
}

function decorateCurrentAuditTrust() {
  if (state.view !== 'claim') return;
  const result = state.audits[state.selected];
  const slot = document.querySelector('#audit-slot');
  if (!result || !slot || slot.querySelector('[data-audit-trust]') || result.error || !result.ok) return;
  const trust = classifyTrustClient(result.axioms || []);
  const block = document.createElement('div');
  block.className = 'audit-trust-line';
  block.dataset.auditTrust = '1';
  const extras = [...trust.compiler_trust_axioms, ...trust.nonstandard_axioms];
  block.innerHTML = `<strong>Trusted base</strong>${trustChip(trust.trust_class)}<span>${esc(trustExplanation(trust.trust_class))}</span>${extras.length ? `<details><summary>Additional trust axioms</summary><code>${extras.map(esc).join('<br>')}</code></details>` : ''}`;
  const summary = slot.querySelector('.audit-summary, .semantic-summary');
  if (summary) summary.after(block); else slot.prepend(block);
}

function decorateTrustSurfaces() {
  decorateCatalogTrust();
  decorateClaimTrust();
  decorateBuildTrust();
  decorateCurrentAuditTrust();
}

const trustBaseRender = render;
render = function trustRender() {
  trustBaseRender();
  decorateTrustSurfaces();
};
