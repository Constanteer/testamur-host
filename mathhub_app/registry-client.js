/* Persistent registry layer for the local MathHub workstation.
   Loaded after app.js so the static prototype remains usable without server.py. */

const registryState = {
  ready: false,
  claims: new Map(),
  details: new Map(),
  loading: new Set(),
};

function registryToUi(item) {
  return {
    id: item.claim_id,
    title: item.title,
    status: item.status,
    area: item.area || 'Unclassified',
    author: item.claim_author || '—',
    formalizer: 'local registry',
    statement: item.statement,
    latex: item.latex || '\\text{formal statement registered in Lean}',
    leanName: item.lean_name,
    imports: ['Mathlib'],
    declaration: item.formal_declaration,
    proof: 'by\n  -- select or create a proof version below\n  sorry',
    recordedAxioms: [],
    library: 'pinned local environment',
    tags: ['registry', `v${item.version}`],
    dependencies: [],
    registry: true,
    version: item.version,
    contentHash: item.content_hash,
    latestBuildId: item.latest_build_id,
  };
}

async function apiJson(url, options = {}) {
  const response = await fetch(url, options);
  let body = {};
  try { body = await response.json(); } catch (_) {}
  if (!response.ok) throw new Error(body.error || `${response.status} ${response.statusText}`);
  return body;
}

async function loadRegistryClaims() {
  try {
    const payload = await apiJson('/api/claims');
    registryState.ready = true;
    for (const item of payload.claims || []) {
      registryState.claims.set(item.claim_id, item);
      const ui = registryToUi(item);
      const at = claims.findIndex(c => c.id === ui.id);
      if (at >= 0) claims[at] = ui; else claims.unshift(ui);
    }
    render();
  } catch (_) {
    registryState.ready = false;
  }
}

async function ensureClaimDetail(id) {
  if (!id?.startsWith('CLM-') || !registryState.claims.has(id)) return null;
  if (registryState.details.has(id)) return registryState.details.get(id);
  if (registryState.loading.has(id)) return null;
  registryState.loading.add(id);
  try {
    const detail = await apiJson(`/api/claims/${id}`);
    registryState.details.set(id, detail);
    const c = claims.find(x => x.id === id);
    if (c && detail.proofs?.length) {
      const proof = detail.proofs[0];
      c.imports = proof.imports;
      c.proof = proof.proof_source;
      c.formalizer = proof.proof_author || 'local proof';
      state.work[id] = {
        imports: proof.imports.join('\n'),
        declaration: detail.current.formal_declaration,
        proof: proof.proof_source,
        theoremName: detail.current.lean_name,
      };
    }
    return detail;
  } finally {
    registryState.loading.delete(id);
  }
}

function shortHash(value = '') {
  return value ? `${value.slice(0, 10)}…${value.slice(-8)}` : '—';
}

function markPresentationStale(claimId) {
  if (!claimId) return;
  window.dispatchEvent(new CustomEvent('mathhub:presentation-stale', {detail:{claimId}}));
}

function buildHistoryHtml(detail) {
  const builds = (detail.proofs || []).flatMap(p => (p.builds || []).map(b => ({...b, proofAuthor: p.proof_author})))
    .sort((a,b) => b.build_id.localeCompare(a.build_id));
  const proofs = detail.proofs || [];
  return `<section class="artifact-section registry-section">
    <div class="section-head"><div><h2 class="artifact-heading">Immutable registry history</h2><p class="artifact-intro">${detail.claim_id} is a durable identity. Claim versions, Proof records, and Build records are append-only.</p></div><span class="version-chip">Claim v${detail.current.version}</span></div>
    <div class="registry-object-strip">
      <div><span class="object-kind">Claim</span><strong>${detail.claim_id}</strong><small>${shortHash(detail.current.content_hash)}</small></div>
      <div><span class="object-kind">Versions</span><strong>${detail.versions.length}</strong><small>${detail.versions.map(v=>`v${v.version}`).join(' · ')}</small></div>
      <div><span class="object-kind">Proofs</span><strong>${proofs.length}</strong><small>each proof is immutable</small></div>
      <div><span class="object-kind">Builds</span><strong>${builds.length}</strong><small>environment-pinned records</small></div>
    </div>
    <div class="section-head" style="margin-top:38px"><h3 class="artifact-heading">Proof records</h3><span class="section-note">new edits create a new PRF id</span></div>
    <div class="proof-history">${proofs.length ? proofs.map(p=>`<div class="proof-history-row"><div><strong>${p.proof_id}</strong><small>${esc(p.proof_author || 'anonymous formalizer')} · ${esc(p.created_at)}</small></div><div class="meta-mono">${shortHash(p.content_hash)}</div><button class="btn" data-build-proof="${p.proof_id}">Build + audit</button></div>`).join('') : '<div class="empty">No proof has been registered for this Claim yet.</div>'}</div>
    <div class="section-head" style="margin-top:38px"><h3 class="artifact-heading">Build records</h3><span class="section-note">never overwritten</span></div>
    <div class="build-history">${builds.length ? builds.map(b=>{
      const semantic=b.semantic||{}; const binding=semantic.formal_binding;
      return `<details class="build-row"><summary><span>${badge(b.status)}</span><strong>${b.build_id}</strong><span>${b.proof_id}</span><span>${b.duration_ms} ms</span><span>${esc(b.created_at)}</span></summary><div class="build-detail"><div><span>environment</span><code>${shortHash(b.environment_hash)}</code></div><div><span>source</span><code>${shortHash(b.source_hash)}</code></div><div><span>mathlib</span><code>${esc(b.mathlib_revision || '—')}</code></div><div><span>axioms</span><code>${esc((b.axioms || []).join(', ') || 'none reported')}</code></div><div><span>imports</span><code>${esc((b.minimal_imports || []).join(', ') || 'none reported')}</code></div>${binding?`<div><span>formal fingerprint</span><code>${shortHash(binding.formal_fingerprint)}</code></div><div><span>direct / transitive</span><code>${(semantic.direct_dependencies||[]).length} / ${(semantic.transitive_dependencies||[]).length}</code></div>`:''}<pre class="log">${esc(b.output || '')}</pre></div></details>`;
    }).join('') : '<div class="empty">No immutable build record yet.</div>'}</div>
    <div class="section-head" style="margin-top:38px"><h3 class="artifact-heading">Legacy mapped dependencies</h3><span class="section-note">superseded by the semantic index below</span></div>
    <ul class="audit-list">${detail.dependencies?.length ? detail.dependencies.map(d=>`<li>${esc(d.title)} · ${d.claim_id}<span style="float:right;color:var(--muted)">${esc(d.relation)}</span></li>`).join('') : '<li>No legacy Claim dependency mapping.</li>'}</ul>
  </section>`;
}

function decorateRegistryClaim() {
  if (state.view !== 'claim' || !registryState.claims.has(state.selected)) return;
  const article = document.querySelector('.claim-grid article');
  if (!article) return;
  const detail = registryState.details.get(state.selected);
  if (!detail) {
    const loading = document.createElement('section');
    loading.className = 'artifact-section registry-section';
    loading.innerHTML = '<div class="callout">Loading local registry history…</div>';
    article.appendChild(loading);
    ensureClaimDetail(state.selected).then(() => render()).catch(err => {
      loading.innerHTML = `<div class="callout bad">${esc(err.message)}</div>`;
    });
    return;
  }
  const wrapper = document.createElement('div');
  wrapper.innerHTML = buildHistoryHtml(detail);
  article.appendChild(wrapper.firstElementChild);

  const actions = document.querySelector('.workbench-actions');
  if (actions && !actions.querySelector('[data-save-proof]')) {
    const save = document.createElement('button');
    save.className = 'btn';
    save.dataset.saveProof = state.selected;
    save.textContent = 'Save as new Proof';
    actions.insertBefore(save, actions.querySelector('.section-note'));
  }
}

async function saveCurrentProof(claimId) {
  const c = claims.find(x => x.id === claimId);
  const w = workFor(c);
  const author = prompt('Proof / formalization author', c.formalizer === 'local registry' ? '' : c.formalizer) ?? '';
  const proof = await apiJson(`/api/claims/${claimId}/proofs`, {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      proof_author: author,
      imports: w.imports.split(/\n+/).map(x=>x.trim()).filter(Boolean),
      proof_source: w.proof,
      build: true,
    }),
  });
  const build = proof.build;
  registryState.details.delete(claimId);
  await loadRegistryClaims();
  await ensureClaimDetail(claimId);
  if (build) markPresentationStale(claimId);
  alert(`${proof.proof_id} saved${build ? `. ${build.build_id}: ${build.status}.` : '.'}`);
  render();
}

async function buildProof(proofId) {
  const claimId = state.selected;
  const build = await apiJson(`/api/proofs/${proofId}/build`, {
    method:'POST', headers:{'Content-Type':'application/json'}, body:'{}',
  });
  registryState.details.delete(claimId);
  await loadRegistryClaims();
  await ensureClaimDetail(claimId);
  markPresentationStale(claimId);
  alert(`${build.build_id}: ${build.status}`);
  render();
}

const baseRenderSubmit = renderSubmit;
renderSubmit = function persistentSubmit() {
  if (!registryState.ready) return baseRenderSubmit();
  return `<main class="shell page"><div class="page-narrow"><div class="eyebrow">Register</div><h1 class="display-title">Create a durable Claim identity.</h1><p class="display-deck">Registration creates CLM-xxxxxx and Claim version v1. A supplied proof becomes a separate immutable PRF record; MathHub compiles it once and stores the resulting BLD record.</p>
    <div class="submit-layout"><form id="claim-form">
      <div class="field"><label>Title</label><input id="draft-title" required placeholder="Claim title"/></div>
      <div class="registry-two-col"><div class="field"><label>Claim author</label><input id="draft-author" placeholder="name / handle"/></div><div class="field"><label>Field</label><input id="draft-area" placeholder="e.g. Number theory"/></div></div>
      <div class="field"><label>Natural-language statement</label><textarea id="draft-human" style="font-family:var(--serif)" required></textarea></div>
      <div class="field"><label>LaTeX statement</label><textarea id="draft-latex" style="font-family:var(--serif)" placeholder="\\forall n ..."></textarea></div>
      <div class="field"><label>Lean imports</label><textarea id="draft-imports">Mathlib</textarea></div>
      <div class="field"><label>Formal Lean declaration</label><textarea id="draft-decl" required placeholder="theorem my_claim : ..."></textarea></div>
      <div class="field"><label>Proof / formalization author</label><input id="draft-proof-author" placeholder="optional"/></div>
      <div class="field"><label>Lean proof</label><textarea id="draft-proof" style="min-height:230px" placeholder="by\n  ..."></textarea></div>
      <div class="workbench-actions"><button class="btn" type="button" id="draft-test">Compile draft</button><button class="btn btn-primary" type="submit">Register + build</button></div><div id="draft-result" style="margin-top:24px"></div>
    </form><aside><div class="callout"><strong>Append-only provenance.</strong><br>Editing a formal Claim later creates Claim v2; editing a proof creates a new PRF record. Previous builds remain addressable.</div><div class="schema registry-schema">CLM-000123\n ├─ VER-000123  Claim v1\n ├─ VER-000130  Claim v2\n └─ PRF-000201\n     ├─ BLD-000401\n     └─ BLD-000419</div></aside></div></div></main>`;
};

const baseBind = bind;
bind = function persistentBind() {
  baseBind();
  document.querySelectorAll('[data-save-proof]').forEach(el => el.addEventListener('click', async () => {
    try { await saveCurrentProof(el.dataset.saveProof); } catch (err) { alert(`Could not save proof: ${err.message}`); }
  }));
  document.querySelectorAll('[data-build-proof]').forEach(el => el.addEventListener('click', async () => {
    try { await buildProof(el.dataset.buildProof); } catch (err) { alert(`Build failed to start: ${err.message}`); }
  }));

  const form = document.querySelector('#claim-form');
  if (form && registryState.ready) {
    form.addEventListener('submit', async event => {
      event.preventDefault();
      event.stopImmediatePropagation();
      const decl = document.querySelector('#draft-decl')?.value.trim() || '';
      const match = decl.match(/\b(?:theorem|lemma)\s+([^\s:(]+)/u);
      if (!match) {
        alert('MathHub Claim nodes must be Lean theorem or lemma declarations. Definitions stay in the formal substrate.');
        return;
      }
      const button = form.querySelector('button[type="submit"]');
      if (button) { button.disabled = true; button.textContent = 'Registering…'; }
      try {
        const created = await apiJson('/api/claims', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({
            title: document.querySelector('#draft-title')?.value || '',
            statement: document.querySelector('#draft-human')?.value || '',
            latex: document.querySelector('#draft-latex')?.value || '',
            area: document.querySelector('#draft-area')?.value || '',
            claim_author: document.querySelector('#draft-author')?.value || '',
            formal_declaration: decl,
            lean_name: match[1],
            proof_author: document.querySelector('#draft-proof-author')?.value || '',
            imports: (document.querySelector('#draft-imports')?.value || 'Mathlib').split(/\n+/).map(x=>x.trim()).filter(Boolean),
            proof_source: document.querySelector('#draft-proof')?.value || '',
            build: true,
          }),
        });
        const build = created.created_build || null;
        await loadRegistryClaims();
        registryState.details.set(created.claim_id, await apiJson(`/api/claims/${created.claim_id}`));
        state.selected = created.claim_id; state.graphSelected = created.claim_id; state.view = 'claim';
        if (build) markPresentationStale(created.claim_id);
        alert(`${created.claim_id} registered${build ? `; ${build.build_id}: ${build.status}` : created.build_error ? `; build pending: ${created.build_error}` : ''}.`);
        render();
      } catch (err) {
        alert(`Registration failed: ${err.message}`);
      } finally {
        if (button) { button.disabled = false; button.textContent = 'Register + build'; }
      }
    }, true);
  }
};

const baseRender = render;
render = function registryRender() {
  baseRender();
  decorateRegistryClaim();
};

loadRegistryClaims();
