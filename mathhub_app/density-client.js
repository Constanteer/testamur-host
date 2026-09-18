/* Presentation-density layer.
   Precise object references and raw Lean constants remain available, but the
   primary research UI favors theorem titles, formal identifiers, hashes and
   grouped mathematical structure. */

function readableSlug(value = '', fallback = 'claim') {
  const ascii = String(value).normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  const slug = ascii.replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  return slug || String(fallback).replace(/[^A-Za-z0-9_.]+/g, '-').toLowerCase();
}

function namespaceOf(name = '') {
  const clean = String(name).replace(/^_root_\./, '');
  const parts = clean.split('.');
  if (!parts[0]) return 'anonymous';
  if (parts[0] === 'Mathlib' && parts[1]) return `Mathlib.${parts[1]}`;
  return parts[0];
}

function dependencyGroups(items = []) {
  const groups = new Map();
  for (const item of items) {
    const ns = namespaceOf(item);
    if (!groups.has(ns)) groups.set(ns, []);
    groups.get(ns).push(item);
  }
  return [...groups.entries()].sort((a,b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
}

// semantic-client calls this function at render time, so replacing it here changes
// both workbench audit and Lean Index without touching the exact stored data.
dependencyList = function groupedDependencyList(items, limit = 80) {
  if (!items?.length) return '<li>none reported</li>';
  const groups = dependencyGroups(items);
  const preview = groups.slice(0, 14);
  return `<li class="dependency-group-summary"><div class="namespace-chips">${preview.map(([ns, values]) => `<span><code>${esc(ns)}</code><strong>${values.length}</strong></span>`).join('')}${groups.length > preview.length ? `<span class="namespace-more">+${groups.length-preview.length} namespaces</span>` : ''}</div></li>
    <li class="raw-dependency-disclosure"><details><summary>Raw declarations · ${items.length}</summary><div class="raw-constant-list">${items.slice(0,limit).map(x=>`<code>${esc(x)}</code>`).join('')}${items.length>limit?`<span>+ ${items.length-limit} more in stored closure</span>`:''}</div></details></li>`;
};

function objectHandle(kind, object) {
  if (kind === 'claim') return object?.lean_name || object?.leanName || readableSlug(object?.title || '', 'claim');
  if (kind === 'proof') return `proof · ${shortHash(object?.content_hash || '')}`;
  if (kind === 'build') return `build · ${shortHash(object?.source_hash || '')}`;
  if (kind === 'version') return `v${object?.version ?? object?.version_no ?? '?'}`;
  return 'object';
}

function buildLogPreview(build) {
  if (Object.prototype.hasOwnProperty.call(build, 'output')) {
    return esc(build.output || 'No compiler output recorded.');
  }
  return build.output_available
    ? 'Compiler log not loaded. Expand this Build to fetch the full artifact.'
    : 'No compiler output recorded.';
}

// Replace the registry history renderer with a less ticket-system-like presentation.
// Canonical refs survive in provenance/copy surfaces, not in the reading hierarchy.
buildHistoryHtml = function quietBuildHistoryHtml(detail) {
  const builds = (detail.proofs || []).flatMap(p => (p.builds || []).map(b => ({...b, proofAuthor:p.proof_author, proofContentHash:p.content_hash})))
    .sort((a,b) => b.created_at.localeCompare(a.created_at));
  const proofs = detail.proofs || [];
  const current = detail.current || {};
  return `<section class="artifact-section registry-section quiet-registry-history">
    <div class="section-head"><div><h2 class="artifact-heading">Registry history</h2><p class="artifact-intro">The visible identity is the mathematical/formal object. Database keys and canonical refs are provenance, not the page title.</p></div><span class="version-chip">v${current.version}</span></div>
    <div class="registry-object-strip quiet-object-strip">
      <div><span class="object-kind">Claim</span><strong class="formal-object-name">${esc(current.lean_name || current.title || 'claim')}</strong><small>${esc(readableSlug(current.title || '', current.lean_name || 'claim'))}</small></div>
      <div><span class="object-kind">Versions loaded</span><strong>${detail.versions.length}</strong><small>append-only formal history</small></div>
      <div><span class="object-kind">Proofs loaded</span><strong>${proofs.length}</strong><small>immutable proof artifacts</small></div>
      <div><span class="object-kind">Builds loaded</span><strong>${builds.length}</strong><small>environment-pinned window</small></div>
    </div>

    <div class="section-head history-subhead"><h3 class="artifact-heading">Proofs</h3><span class="section-note">content-addressed presentation</span></div>
    <div class="proof-history">${proofs.length ? proofs.map(p=>`<div class="proof-history-row quiet-proof-row"><div><strong>${esc(objectHandle('proof',p))}</strong><small>${esc(p.proof_author || 'anonymous formalizer')} · ${esc(p.created_at)}</small></div><div class="meta-mono">${shortHash(p.content_hash)}</div><button class="btn" data-build-proof="${p.proof_id}">Build + audit</button></div>`).join('') : '<div class="empty">No proof has been registered for this Claim yet.</div>'}</div>

    <div class="section-head history-subhead"><h3 class="artifact-heading">Builds</h3><span class="section-note">summary first · full compiler artifact on demand</span></div>
    <div class="build-history">${builds.length ? builds.map(b=>{
      const semantic=b.semantic||{}; const binding=semantic.formal_binding;
      return `<details class="build-row quiet-build-row" data-build-record="${esc(b.build_id)}"><summary><span>${badge(b.status)}</span><strong>${esc(objectHandle('build',b))}</strong><span>${b.duration_ms} ms</span><span>${esc(b.created_at)}</span></summary><div class="build-detail"><div><span>environment</span><code>${shortHash(b.environment_hash)}</code></div><div><span>source</span><code>${shortHash(b.source_hash)}</code></div><div><span>mathlib</span><code>${esc(b.mathlib_revision || '—')}</code></div><div><span>axioms</span><code>${esc((b.axioms || []).join(', ') || 'none reported')}</code></div>${binding?`<div><span>formal fingerprint</span><code>${shortHash(binding.formal_fingerprint)}</code></div><div><span>direct / transitive</span><code>${(semantic.direct_dependencies||[]).length} / ${(semantic.transitive_dependencies||[]).length}</code></div>`:''}<details class="internal-record"><summary>Canonical provenance</summary><code>${esc(b.build_id)} · ${esc(b.proof_id)}</code></details><pre class="log" data-build-log="${esc(b.build_id)}">${buildLogPreview(b)}</pre></div></details>`;
    }).join('') : '<div class="empty">No immutable build record yet.</div>'}</div>
  </section>`;
};

function decorateQuietIdentifiers() {
  // Every Claim card uses the formal name as its compact technical label. Static
  // demo IDs and live canonical refs both stay out of the primary list surface.
  document.querySelectorAll('[data-claim]').forEach(el => {
    const id = el.dataset.claim;
    const c = claims.find(x => x.id === id);
    if (!c) return;
    const label = el.querySelector('.claim-id');
    if (label) label.textContent = c.leanName || readableSlug(c.title, 'claim');
  });

  if (state.view === 'claim') {
    const c = claims.find(x => x.id === state.selected);
    if (c) {
      const header = document.querySelector('.claim-header .claim-id');
      if (header) header.textContent = c.leanName || readableSlug(c.title, 'claim');
    }
  }

  // Graph nodes are identified visually by title; Lean names remain in inspector.
  document.querySelectorAll('[data-semantic-node]').forEach(node => {
    const label = node.querySelector('.graph-id');
    if (label) label.textContent = '';
  });
}

const densityBaseRender = render;
render = function densityRender() {
  densityBaseRender();
  decorateQuietIdentifiers();
};
