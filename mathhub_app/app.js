const claims = [
  {
    id: 'CLM-0001',
    slug: 'infinitely-many-primes',
    title: 'Infinitude of prime numbers',
    status: 'verified',
    area: 'Number theory',
    author: 'Euclid',
    formalizer: '@mathlib',
    statement: 'There are infinitely many prime numbers.',
    latex: '\\forall n \\in \\mathbb{N},\\; \\exists p > n,\\; p \\text{ prime}.',
    leanName: 'infinitely_many_primes',
    imports: ['Mathlib.Data.Nat.Prime.Infinite'],
    declaration: 'theorem infinitely_many_primes :\n    ∀ n : ℕ, ∃ p : ℕ, n < p ∧ Nat.Prime p',
    proof: 'by\n  intro n\n  simpa [gt_iff_lt] using Nat.exists_infinite_primes n',
    recordedAxioms: ['propext', 'Classical.choice', 'Quot.sound'],
    library: 'mathlib4',
    tags: ['prime', 'arithmetic', 'classical'],
    dependencies: [
      { id: 'CLM-0101', label: 'Prime divisor existence', kind: 'uses' },
      { id: 'CLM-0102', label: 'Finite product divisibility lemma', kind: 'uses' }
    ]
  },
  {
    id: 'CLM-0002',
    slug: 'bolzano-weierstrass-real',
    title: 'Bolzano–Weierstrass theorem over ℝ',
    status: 'verified',
    area: 'Real analysis',
    author: 'B. Bolzano / K. Weierstrass',
    formalizer: '@analysis-formalization',
    statement: 'Every bounded sequence of real numbers admits a convergent subsequence.',
    latex: '\\text{bounded}(x_n) \\Longrightarrow \\exists (n_k),\\; x_{n_k} \\to L.',
    leanName: 'bolzano_weierstrass_registry',
    imports: ['Mathlib.Topology.Instances.Real', 'Mathlib.Topology.Sequences'],
    declaration: 'theorem bolzano_weierstrass_registry\n    (x : ℕ → ℝ) (h : Bornology.IsBounded (Set.range x)) :\n    ∃ a : ℝ, ∃ n : ℕ → ℕ, StrictMono n ∧ Tendsto (x ∘ n) atTop (𝓝 a)',
    proof: 'by\n  -- Registry demonstration: complete formal proof to be supplied.\n  sorry',
    recordedAxioms: ['Classical.choice'],
    library: 'mathlib4',
    tags: ['sequence', 'compactness', 'analysis'],
    dependencies: [
      { id: 'CLM-0103', label: 'Compactness of closed bounded intervals', kind: 'uses' }
    ]
  },
  {
    id: 'CLM-0003',
    slug: 'spectral-real-symmetric',
    title: 'Spectral theorem for real symmetric matrices',
    status: 'partial',
    area: 'Linear algebra',
    author: 'Classical result',
    formalizer: '@hank',
    statement: 'Every real symmetric matrix is orthogonally diagonalizable.',
    latex: 'A^T=A \\Longrightarrow \\exists Q,D,\\; Q^TQ=I,\\; A=QDQ^T.',
    leanName: 'spectral_real_symmetric',
    imports: ['Mathlib.LinearAlgebra.Matrix.Spectrum'],
    declaration: 'theorem spectral_real_symmetric : True',
    proof: 'by\n  trivial',
    recordedAxioms: [],
    library: 'mathlib4',
    tags: ['matrix', 'eigenvalue', 'linear-algebra'],
    dependencies: [
      { id: 'CLM-0105', label: 'Existence of real eigenvalue', kind: 'requires' },
      { id: 'CLM-0106', label: 'Orthogonal complement invariance', kind: 'requires' }
    ]
  },
  {
    id: 'CLM-0004',
    slug: 'goldbach',
    title: 'Strong Goldbach conjecture',
    status: 'open',
    area: 'Number theory',
    author: 'Christian Goldbach',
    formalizer: '—',
    statement: 'Every even integer greater than 2 is the sum of two prime numbers.',
    latex: '\\forall n>2,\\;2\\mid n \\Longrightarrow \\exists p,q\\text{ prime},\\;n=p+q.',
    leanName: 'strong_goldbach',
    imports: ['Mathlib.Data.Nat.Prime.Basic'],
    declaration: 'theorem strong_goldbach :\n    ∀ n : ℕ, 2 < n → Even n → ∃ p q : ℕ, Nat.Prime p ∧ Nat.Prime q ∧ n = p + q',
    proof: 'by\n  -- Open claim: no proof exists in the registry.\n  sorry',
    recordedAxioms: [],
    library: 'mathlib4',
    tags: ['prime', 'additive-number-theory', 'open'],
    dependencies: [{ id: 'CLM-0001', label: 'Infinitude of prime numbers', kind: 'related' }]
  },
  {
    id: 'CLM-0005',
    slug: 'riemann-hypothesis',
    title: 'Riemann hypothesis',
    status: 'open',
    area: 'Analytic number theory',
    author: 'Bernhard Riemann',
    formalizer: '—',
    statement: 'Every non-trivial zero of the Riemann zeta function has real part 1/2.',
    latex: '\\zeta(s)=0,\\;0<\\Re(s)<1 \\Longrightarrow \\Re(s)=\\tfrac12.',
    leanName: 'riemann_hypothesis_registry',
    imports: ['Mathlib'],
    declaration: 'theorem riemann_hypothesis_registry : True',
    proof: 'by\n  -- Placeholder formal surface; the production claim needs the exact ζ statement.\n  trivial',
    recordedAxioms: [],
    library: 'mathlib4',
    tags: ['zeta', 'complex-analysis', 'open'],
    dependencies: []
  }
];

const auxiliaryClaims = {
  'CLM-0101': { id: 'CLM-0101', title: 'Prime divisor existence', status: 'verified', area: 'Number theory', statement: 'Every natural number greater than one has a prime divisor.' },
  'CLM-0102': { id: 'CLM-0102', title: 'Finite product divisibility lemma', status: 'verified', area: 'Arithmetic', statement: 'A prime dividing a finite product divides a factor.' },
  'CLM-0103': { id: 'CLM-0103', title: 'Compactness of closed bounded intervals', status: 'verified', area: 'Topology', statement: 'Closed bounded real intervals are compact.' },
  'CLM-0105': { id: 'CLM-0105', title: 'Existence of a real eigenvalue', status: 'partial', area: 'Linear algebra', statement: 'A real symmetric matrix admits a real eigenvalue.' },
  'CLM-0106': { id: 'CLM-0106', title: 'Orthogonal complement invariance', status: 'verified', area: 'Linear algebra', statement: 'An orthogonal complement is invariant under a symmetric operator.' }
};

const authors = [
  { name: 'Euclid', handle: 'historical/euclid', claims: 1, proofs: 0, areas: 'Geometry · Number theory', bio: 'Historical provenance for classical claims. Formal proof authorship remains separate.' },
  { name: 'Mathlib contributors', handle: '@mathlib', claims: 0, proofs: 2, areas: 'Formal mathematics', bio: 'Collective provenance for formal artifacts synchronized from mathlib.' },
  { name: 'Hank', handle: '@hank', claims: 1, proofs: 1, areas: 'Linear algebra · Systems', bio: 'Experimental formalization author. Contributions attach to claims and proofs, not a publication feed.' },
  { name: 'Christian Goldbach', handle: 'historical/goldbach', claims: 1, proofs: 0, areas: 'Number theory', bio: 'Historical provenance for the conjectural claim.' },
  { name: 'Bernhard Riemann', handle: 'historical/riemann', claims: 1, proofs: 0, areas: 'Analysis · Number theory', bio: 'Historical provenance for the hypothesis and its mathematical context.' },
  { name: 'Analysis Formalization', handle: '@analysis-formalization', claims: 0, proofs: 1, areas: 'Analysis · Topology', bio: 'Demonstration formalization identity for analysis artifacts.' }
];

const graphNodes = [
  { id: 'CLM-0102', x: 120, y: 140 }, { id: 'CLM-0101', x: 330, y: 190 },
  { id: 'CLM-0001', x: 560, y: 135 }, { id: 'CLM-0004', x: 790, y: 200 },
  { id: 'CLM-0103', x: 180, y: 420 }, { id: 'CLM-0002', x: 440, y: 410 },
  { id: 'CLM-0105', x: 350, y: 565 }, { id: 'CLM-0106', x: 590, y: 560 },
  { id: 'CLM-0003', x: 810, y: 505 }
];
const graphEdges = [
  ['CLM-0102','CLM-0101'], ['CLM-0101','CLM-0001'], ['CLM-0001','CLM-0004'],
  ['CLM-0103','CLM-0002'], ['CLM-0105','CLM-0003'], ['CLM-0106','CLM-0003']
];

const state = {
  view: 'registry', filter: 'all', search: '', selected: 'CLM-0001', graphSelected: 'CLM-0001',
  runtime: { checked: false, ready: false, label: 'checking local Lean…' },
  work: {}, audits: {}, draftAudit: null
};

const app = document.querySelector('#app');

function esc(v='') { return String(v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c])); }
function statusText(s) { return s === 'verified' ? 'Verified' : s === 'partial' ? 'Partial' : s === 'failed' ? 'Failed' : 'Open'; }
function badge(s) { return `<span class="status ${s}">${statusText(s)}</span>`; }
function getClaim(id) { return claims.find(c => c.id === id) || auxiliaryClaims[id]; }
function workFor(c) {
  if (!state.work[c.id]) state.work[c.id] = { imports: c.imports.join('\n'), declaration: c.declaration, proof: c.proof, theoremName: c.leanName };
  return state.work[c.id];
}

function topbar() {
  const nav = [['registry','Statements'],['graph','Connections'],['authors','People']];
  const dot = state.runtime.checked ? (state.runtime.ready ? 'ready' : state.runtime.setupRunning ? 'working' : 'bad') : '';
  const hosted = location.pathname.startsWith('/sources/mathhub');
  const parent = hosted ? '<a class="parent-link" href="/">Testamur</a><span class="product-separator">/</span>' : '';
  return `<header class="topbar"><div class="topbar-inner">
    <div class="product-lockup">${parent}<button class="wordmark" data-view="registry">MathHub</button><span class="product-tag">formal mathematics</span></div>
    <nav class="topnav" aria-label="MathHub navigation">${nav.map(([v,t]) => `<button class="${state.view===v?'active':''}" data-view="${v}">${t}</button>`).join('')}</nav>
    <div class="top-spacer"></div>
    <div class="runtime-pill" title="Lean runtime status"><span class="runtime-dot ${dot}"></span><span>${esc(state.runtime.label)}</span></div>
    <button class="btn btn-small top-register" data-view="submit">Add statement</button>
  </div></header>`;
}

function renderRegistry() {
  const q = state.search.trim().toLowerCase();
  const filtered = claims.filter(c => (state.filter==='all' || c.status===state.filter) && (!q || [c.id,c.title,c.statement,c.area,c.author,c.formalizer,...c.tags].join(' ').toLowerCase().includes(q)));
  const counts = { verified: claims.filter(c=>c.status==='verified').length, partial: claims.filter(c=>c.status==='partial').length, open: claims.filter(c=>c.status==='open').length };
  const areas = [...new Set(claims.map(c=>c.area))].sort();
  return `<main class="shell page registry-page">
    <section class="registry-head">
      <div>
        <div class="eyebrow">Formal mathematics registry</div>
        <h1 class="registry-title">Claims, proofs, and the routes between them.</h1>
        <p class="registry-deck">Search a mathematical claim, then open it to inspect the exact Lean declaration, proof artifact, imports, axioms, and dependency trail. A verified badge is a build result—not a vote of confidence.</p>
      </div>
      <div class="registry-runtime">
        <div class="registry-runtime-head"><span class="runtime-dot ${state.runtime.checked ? (state.runtime.ready ? 'ready' : 'bad') : ''}"></span><strong>Lean engine</strong></div>
        <div class="registry-runtime-value">${esc(state.runtime.label)}</div>
        <div class="registry-runtime-note">Verification records are tied to the checked environment and dependency lock.</div>
      </div>
    </section>

    <div class="registry-toolbar">
      <label class="registry-search"><span aria-hidden="true">⌕</span><input id="search" class="search-input" value="${esc(state.search)}" placeholder="Search claims, authors, fields, tags, or Lean names…" aria-label="Search MathHub claims"/></label>
      <button class="btn btn-primary" data-view="submit">Register claim</button>
    </div>

    <div class="overview-line">
      <div class="metric"><div class="metric-value">${claims.length}</div><div class="metric-label">Claims</div></div>
      <div class="metric"><div class="metric-value">${counts.verified}</div><div class="metric-label">Verified</div></div>
      <div class="metric"><div class="metric-value">${counts.partial}</div><div class="metric-label">Partial</div></div>
      <div class="metric"><div class="metric-value">${counts.open}</div><div class="metric-label">Open</div></div>
    </div>

    <div class="registry-layout">
      <section class="registry-main">
        <div class="section-head registry-list-head">
          <div><h2 class="section-title">Claims</h2><span class="section-note">${filtered.length} shown</span></div>
          <div class="filters">${['all','verified','partial','open'].map(f=>`<button class="filter ${state.filter===f?'active':''}" data-filter="${f}">${f==='all'?'All':statusText(f)}</button>`).join('')}</div>
        </div>
        <div class="claim-list">${filtered.length ? filtered.map(c=>`<button class="claim-card" data-claim="${c.id}">
          <div class="claim-card-main">
            <div class="claim-card-kicker"><span class="claim-id">${c.id}</span><span>·</span><span>${esc(c.area)}</span></div>
            <div class="claim-card-title">${esc(c.title)}</div>
            <div class="claim-card-text">${esc(c.statement)}</div>
            <div class="claim-card-meta"><span>Claim: ${esc(c.author)}</span><span>Proof: ${esc(c.formalizer)}</span><span class="mono">${esc(c.leanName)}</span></div>
          </div>
          <div class="claim-card-status">${badge(c.status)}<span class="claim-open-arrow" aria-hidden="true">→</span></div>
        </button>`).join('') : '<div class="empty">No claims match this search or filter.</div>'}</div>
      </section>

      <aside class="registry-sidebar">
        <section class="side-card">
          <div class="side-card-title">Browse by field</div>
          <div class="area-list">${areas.map(area=>`<span><strong>${esc(area)}</strong><small>${claims.filter(c=>c.area===area).length}</small></span>`).join('')}</div>
        </section>
        <section class="side-card">
          <div class="side-card-title">What “verified” means</div>
          <ol class="verification-steps">
            <li><strong>Claim</strong><span>Natural-language statement plus exact Lean declaration.</span></li>
            <li><strong>Proof</strong><span>Proof term compiled in the pinned environment.</span></li>
            <li><strong>Record</strong><span>Imports, axioms and dependency closure retained with the result.</span></li>
          </ol>
        </section>
        <section class="side-card side-card-quiet">
          <strong>Prototype registry</strong>
          <p>The sample records are demonstrators. Treat “verified” as authoritative only when backed by a stored production build record.</p>
        </section>
      </aside>
    </div>
  </main>`;
}

function auditHtml(c) {
  const r = state.audits[c.id];
  if (!r) return `<div class="callout">No local audit has been run in this session. Compile checks the declaration and proof; Audit additionally asks mathlib for minimal imports, proof-constant dependencies and Lean for transitive axioms.</div>`;
  if (r.error) return `<div class="callout bad"><strong>Audit service error.</strong><br>${esc(r.error)}</div>`;
  const status = r.ok ? (r.contains_sorry ? 'partial' : 'verified') : 'failed';
  const deps = r.dependencies || [], imps = r.minimal_imports || [], axioms = r.axioms || [];
  return `<div class="audit-result">
    <div class="audit-summary"><div class="audit-cell"><div>${badge(status)}</div><div class="audit-name">Build result</div></div><div class="audit-cell"><div class="audit-number">${r.duration_ms ?? '—'} ms</div><div class="audit-name">Lean time</div></div><div class="audit-cell"><div class="audit-number">${imps.length}</div><div class="audit-name">Minimal imports</div></div><div class="audit-cell"><div class="audit-number">${deps.length}</div><div class="audit-name">Proof constants</div></div></div>
    ${r.contains_sorry ? '<div class="callout warn" style="margin-top:24px"><strong>sorryAx detected.</strong> The file compiled, but this proof must not be registered as verified.</div>' : ''}
    <div class="audit-columns"><div><div class="section-head"><h3 class="artifact-heading">Minimal imports</h3><span class="section-note">#min_imports</span></div><ul class="audit-list">${imps.length?imps.map(x=>`<li>import ${esc(x)}</li>`).join(''):'<li>none reported</li>'}</ul></div><div><div class="section-head"><h3 class="artifact-heading">Axioms</h3><span class="section-note">#print axioms</span></div><ul class="audit-list">${axioms.length?axioms.map(x=>`<li>${esc(x)}</li>`).join(''):'<li>no axioms reported</li>'}</ul></div></div>
    <div class="section-head" style="margin-top:34px"><h3 class="artifact-heading">Referenced declarations</h3><span class="section-note">transitive proof constants · first ${Math.min(deps.length,80)} shown</span></div><ul class="audit-list">${deps.length?deps.slice(0,80).map(x=>`<li>${esc(x)}</li>`).join(''):'<li>none reported</li>'}</ul>
    <details style="margin-top:28px"><summary class="section-note" style="cursor:pointer">Compiler log</summary><pre class="log">${esc(r.output || '')}</pre></details>
  </div>`;
}

function renderClaim() {
  const c = claims.find(x=>x.id===state.selected) || claims[0];
  const w = workFor(c);
  return `<main class="shell page"><div class="page-narrow">
    <div class="breadcrumb"><button data-view="registry">Registry</button> &nbsp;/&nbsp; ${c.area} &nbsp;/&nbsp; ${c.id}</div>
    <header class="claim-header"><div>${badge(c.status)} <span class="claim-id" style="margin-left:12px">${c.id}</span></div><h1 class="claim-title">${c.title}</h1><p class="claim-summary">${c.statement}</p><div class="claim-byline">Claim author <strong>${c.author}</strong> &nbsp;·&nbsp; Formalization <strong>${c.formalizer}</strong></div></header>
    <div class="formal-block"><div class="formal-label">Mathematical statement</div><div class="formal-math">\\[${c.latex}\\]</div></div>

    <div class="claim-grid"><article>
      <section class="artifact-section"><h2 class="artifact-heading">Formal Lean declaration</h2><p class="artifact-intro">This is part of the Claim itself, not merely proof metadata. Two natural-language descriptions that elaborate to different declarations are different formal claims.</p><div class="code-panel"><div class="code-panel-head"><span class="code-label">claim.lean · declaration</span><span class="claim-id">${esc(c.leanName)}</span></div><pre class="code-view">${esc(c.declaration)}</pre></div></section>

      <section class="artifact-section"><h2 class="artifact-heading">Lean workbench</h2><p class="artifact-intro">Edit the formal declaration and proof locally, then compile against the repository’s pinned Lake environment. Audit runs the same proof with dependency instrumentation.</p>
        <div class="code-panel"><div class="code-panel-head"><span class="code-label">Imports</span><span class="section-note">one module per line</span></div><textarea id="imports-editor" class="code-editor import-editor" spellcheck="false">${esc(w.imports)}</textarea></div>
        <div class="code-panel" style="margin-top:16px"><div class="code-panel-head"><span class="code-label">Formal declaration</span><span class="section-note">theorem / lemma / def signature</span></div><textarea id="decl-editor" class="code-editor declaration-editor" spellcheck="false">${esc(w.declaration)}</textarea></div>
        <div class="code-panel" style="margin-top:16px"><div class="code-panel-head"><span class="code-label">Proof term</span><span class="section-note">normally begins with by</span></div><textarea id="proof-editor" class="code-editor" spellcheck="false">${esc(w.proof)}</textarea></div>
        <div class="workbench-actions"><button class="btn btn-primary" data-lean-action="compile">Compile proof</button><button class="btn" data-lean-action="audit">Audit dependencies</button><span class="section-note">Local service · 20 s timeout</span></div>
        <div id="audit-slot">${auditHtml(c)}</div>
      </section>

      <section class="artifact-section"><div class="section-head"><h2 class="artifact-heading">Registry-level dependencies</h2><span class="section-note">curated claim links</span></div><p class="artifact-intro">Lean’s raw proof constants are machine facts. MathHub can map selected constants back to registered Claims, producing the human-scale dependency graph.</p><ul class="audit-list">${c.dependencies.length?c.dependencies.map(d=>`<li><button data-graph-select="${d.id}" style="border:0;background:transparent;padding:0;font:inherit;color:inherit">${esc(d.label)} · ${d.id}</button> <span style="float:right;color:var(--muted)">${d.kind}</span></li>`).join(''):'<li>No registry-level dependencies recorded.</li>'}</ul></section>
    </article>
    <aside class="meta-rail"><div class="meta-row"><div class="meta-label">Formal identifier</div><div class="meta-value meta-mono">${c.leanName}</div></div><div class="meta-row"><div class="meta-label">Claim author</div><div class="meta-value">${c.author}</div></div><div class="meta-row"><div class="meta-label">Proof author</div><div class="meta-value">${c.formalizer}</div></div><div class="meta-row"><div class="meta-label">Library</div><div class="meta-value meta-mono">${c.library}</div></div><div class="meta-row"><div class="meta-label">Recorded axioms</div><div class="meta-value meta-mono">${c.recordedAxioms.length?c.recordedAxioms.join('<br>'):'none'}</div></div><div class="meta-row"><div class="meta-label">Tags</div><div class="meta-value">${c.tags.join(' · ')}</div></div><div class="meta-row"><button class="btn" data-view="graph" style="width:100%">Open in dependency graph</button></div></aside></div>
  </div></main>`;
}

function graphObject(id) { return getClaim(id) || { id, title:id, status:'verified', area:'', statement:'' }; }
function renderGraph() {
  const sel = graphObject(state.graphSelected);
  const map = Object.fromEntries(graphNodes.map(n=>[n.id,n]));
  const edges = graphEdges.map(([a,b])=>`<line class="edge" x1="${map[a].x}" y1="${map[a].y}" x2="${map[b].x}" y2="${map[b].y}"/>`).join('');
  const nodes = graphNodes.map(n=>{const c=graphObject(n.id), label=c.title.length>28?c.title.slice(0,27)+'…':c.title;return `<g class="node ${state.graphSelected===n.id?'selected':''}" data-node="${n.id}" transform="translate(${n.x},${n.y})"><circle r="27"></circle><text text-anchor="middle" y="48">${esc(label)}</text><text text-anchor="middle" y="63" class="graph-node-id">${n.id}</text></g>`}).join('');
  return `<main class="shell page">
    <section class="page-head"><div><div class="eyebrow">Dependency graph</div><h1 class="page-heading">Follow the proof route.</h1><p>Lean records the machine dependency closure. MathHub maps selected declarations back to mathematical claims so the route remains readable.</p></div></section>
    <div class="graph-shell"><div class="graph-canvas"><svg viewBox="0 0 930 650"><defs><marker id="arrow" viewBox="0 0 10 10" refX="30" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#8c959f"/></marker></defs>${edges}${nodes}</svg></div><aside class="graph-inspector"><div class="eyebrow">Selected claim</div><h2 class="section-title">${esc(sel.title)}</h2><div class="graph-status">${badge(sel.status)}</div><p>${esc(sel.statement||'')}</p><div class="meta-row"><div class="meta-label">Identifier</div><div class="meta-value meta-mono">${sel.id}</div></div>${claims.some(c=>c.id===sel.id)?`<button class="btn btn-primary" data-claim="${sel.id}">Open claim</button>`:''}</aside></div>
  </main>`;
}

function renderAuthors() {
  return `<main class="shell page"><section class="page-head"><div><div class="eyebrow">Provenance</div><h1 class="page-heading">People and provenance.</h1><p>Claim authorship, proof authorship, and formalization are separate lines. MathHub keeps them separate instead of collapsing them into one credit field.</p></div></section><div class="author-grid">${authors.map(a=>`<article class="author-card"><div class="author-avatar" aria-hidden="true">${esc(a.name.slice(0,1))}</div><div><div class="author-name">${esc(a.name)}</div><div class="author-handle">${esc(a.handle)}</div></div><div class="author-bio">${esc(a.bio)}</div><div class="author-stats"><span><strong>${a.claims}</strong>claims</span><span><strong>${a.proofs}</strong>proofs</span></div><div class="author-areas">${esc(a.areas)}</div></article>`).join('')}</div></main>`;
}

function renderSubmit() {
  return `<main class="shell page"><div class="page-narrow"><section class="page-head"><div><div class="eyebrow">New claim</div><h1 class="page-heading">Register the formal object first.</h1><p>The natural-language description helps humans navigate. The Lean declaration fixes the claim MathHub will actually track.</p></div></section><div class="submit-layout"><form id="claim-form" class="submit-form"><div class="field"><label>Title</label><input id="draft-title" required placeholder="Claim title"/></div><div class="field"><label>Natural-language statement</label><textarea id="draft-human" class="human-editor" required placeholder="State the mathematical claim in ordinary language."></textarea></div><div class="field"><label>Lean imports</label><textarea id="draft-imports">Mathlib</textarea></div><div class="field"><label>Formal Lean declaration</label><textarea id="draft-decl" required placeholder="theorem my_claim : ..."></textarea></div><div class="field"><label>Lean proof</label><textarea id="draft-proof" class="proof-draft" placeholder="by\n  ..."></textarea></div><div class="workbench-actions"><button class="btn" type="button" id="draft-test">Compile draft</button><button class="btn btn-primary" type="submit">Prepare registry object</button></div><div id="draft-result" class="draft-result"></div></form><aside class="submit-side"><div class="side-card"><div class="side-card-title">Verification pipeline</div><ol class="verification-steps"><li><strong>Compile</strong><span>Lean accepts or rejects the declaration and proof.</span></li><li><strong>Audit</strong><span>MathHub extracts imports, proof constants and axioms.</span></li><li><strong>Record</strong><span>The result is stored with its environment lock.</span></li></ol></div><div class="callout warn"><strong>Prototype runner</strong><br>The current runner executes submitted Lean code on this service. Production isolation remains a deployment boundary.</div></aside></div></div></main>`;
}

function current() { if(state.view==='claim') return renderClaim(); if(state.view==='graph') return renderGraph(); if(state.view==='authors') return renderAuthors(); if(state.view==='submit') return renderSubmit(); return renderRegistry(); }
function render() { app.innerHTML = `${topbar()}${current()}`; bind(); if(window.MathJax?.typesetPromise) window.MathJax.typesetPromise().catch(()=>{}); }

async function runLean(c, audit) {
  const w = workFor(c);
  state.audits[c.id] = { pending: true };
  const slot = document.querySelector('#audit-slot');
  if(slot) slot.innerHTML = '<div class="callout">Running Lean…</div>';
  try {
    const res = await fetch('/api/lean/check', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ imports:w.imports.split(/\n+/).map(x=>x.trim()).filter(Boolean), declaration:w.declaration, proof:w.proof, theorem_name:w.theoremName, audit }) });
    const data = await res.json();
    state.audits[c.id] = data;
  } catch (e) {
    state.audits[c.id] = { error: 'Local Lean service is unavailable. Start MathHub with `python3 server.py` rather than opening index.html directly. ' + e.message };
  }
  render();
}

async function runDraft() {
  const slot = document.querySelector('#draft-result'); if(slot) slot.innerHTML='<div class="callout">Running Lean…</div>';
  const decl = document.querySelector('#draft-decl')?.value || '';
  const nameMatch = decl.match(/(?:theorem|lemma|def)\s+([A-Za-z0-9_'.]+)/);
  try {
    const res = await fetch('/api/lean/check', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ imports:(document.querySelector('#draft-imports')?.value||'').split(/\n+/).map(x=>x.trim()).filter(Boolean), declaration:decl, proof:document.querySelector('#draft-proof')?.value||'', theorem_name:nameMatch?.[1]||'', audit:true }) });
    const d = await res.json(); state.draftAudit=d;
    if(slot) slot.innerHTML = d.error ? `<div class="callout bad">${esc(d.error)}</div>` : `<div class="callout ${d.ok&&!d.contains_sorry?'':'warn'}"><strong>${d.ok&&!d.contains_sorry?'Kernel check passed':'Not verified'}</strong><br>${d.ok?'Lean accepted the generated file.':'Lean returned an error.'} ${d.contains_sorry?'sorryAx was detected.':''}</div><pre class="log">${esc(d.output||'')}</pre>`;
  } catch(e) { if(slot) slot.innerHTML=`<div class="callout bad">Local Lean service unavailable: ${esc(e.message)}</div>`; }
}

function bind() {
  document.querySelectorAll('[data-view]').forEach(el=>el.onclick=()=>{state.view=el.dataset.view;render();});
  document.querySelectorAll('[data-claim]').forEach(el=>el.onclick=()=>{state.selected=el.dataset.claim;state.graphSelected=el.dataset.claim;state.view='claim';render();});
  document.querySelectorAll('[data-filter]').forEach(el=>el.onclick=()=>{state.filter=el.dataset.filter;render();});
  document.querySelectorAll('[data-node]').forEach(el=>el.onclick=()=>{state.graphSelected=el.dataset.node;render();});
  document.querySelectorAll('[data-graph-select]').forEach(el=>el.onclick=()=>{state.graphSelected=el.dataset.graphSelect;state.view='graph';render();});
  const s=document.querySelector('#search'); if(s) s.oninput=e=>{state.search=e.target.value; const pos=e.target.selectionStart; render(); const n=document.querySelector('#search'); n?.focus(); n?.setSelectionRange(pos,pos);};
  if(state.view==='claim') {
    const c=claims.find(x=>x.id===state.selected), w=workFor(c);
    const im=document.querySelector('#imports-editor'), de=document.querySelector('#decl-editor'), pr=document.querySelector('#proof-editor');
    if(im) im.oninput=e=>w.imports=e.target.value; if(de) de.oninput=e=>{w.declaration=e.target.value; const m=w.declaration.match(/(?:theorem|lemma|def)\s+([A-Za-z0-9_'.]+)/); if(m)w.theoremName=m[1];}; if(pr) pr.oninput=e=>w.proof=e.target.value;
    document.querySelectorAll('[data-lean-action]').forEach(el=>el.onclick=()=>runLean(c,el.dataset.leanAction==='audit'));
  }
  document.querySelector('#draft-test')?.addEventListener('click',runDraft);
  document.querySelector('#claim-form')?.addEventListener('submit',e=>{e.preventDefault();alert('Prototype: the registry persistence layer is not implemented yet. Compile/audit the draft first; the next backend step is storing the checked artifact and dependency lock.');});
}

let runtimePollTimer=null;
async function checkRuntime() {
  if(runtimePollTimer){clearTimeout(runtimePollTimer);runtimePollTimer=null;}
  try {
    const res=await fetch('/api/runtime'); const d=await res.json();
    state.runtime={
      checked:true,
      ready:!!d.ready,
      label:d.ready?'Math library ready':(d.message||'Preparing the math library…'),
      autoSetup:!!d.auto_setup,
      setupRunning:!!d.setup_running,
      setupError:d.setup_error||'',
    };
    if(!d.ready&&d.auto_setup&&!d.setup_error)runtimePollTimer=setTimeout(()=>checkRuntime(),2500);
  } catch (_) {
    state.runtime={checked:true,ready:false,label:'Math library service is unavailable',autoSetup:false,setupRunning:false,setupError:''};
  }
  render();
  if(typeof libraryBrowserState==='object'&&libraryBrowserState.open&&typeof renderLibraryBrowser==='function')renderLibraryBrowser();
}

render();
checkRuntime();
