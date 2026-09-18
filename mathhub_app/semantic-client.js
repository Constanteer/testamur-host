/* Semantic layer: formal identity, direct/transitive dependencies and Lean index.
   Loaded after app.js + registry-client.js so static mode remains usable. */

const semanticState = {
  inspect: null,
  inspecting: false,
  graph: null,
  graphLoading: false,
  graphFilter: 'direct',
};

function semanticShort(value = '') {
  return value ? `${value.slice(0, 12)}…${value.slice(-10)}` : '—';
}

function dependencyList(items, limit = 80) {
  if (!items?.length) return '<li>none reported</li>';
  return items.slice(0, limit).map(x => `<li><code>${esc(x)}</code></li>`).join('') +
    (items.length > limit ? `<li class="semantic-more">+ ${items.length - limit} more</li>` : '');
}

const semanticBaseAuditHtml = auditHtml;
auditHtml = function semanticAuditHtml(c) {
  const r = state.audits[c.id];
  if (!r || r.error || (!('direct_dependencies' in r) && !('formal_fingerprint' in r))) {
    return semanticBaseAuditHtml(c);
  }
  const status = r.ok ? (r.contains_sorry ? 'partial' : 'verified') : 'failed';
  const direct = r.direct_dependencies || [];
  const transitive = r.transitive_dependencies || r.dependencies || [];
  const imports = r.minimal_imports || [];
  const axioms = r.axioms || [];
  return `<div class="audit-result semantic-audit">
    <div class="audit-summary semantic-summary">
      <div class="audit-cell"><div>${badge(status)}</div><div class="audit-name">Kernel result</div></div>
      <div class="audit-cell"><div class="audit-number">${r.duration_ms ?? '—'} ms</div><div class="audit-name">Lean time</div></div>
      <div class="audit-cell"><div class="audit-number">${direct.length}</div><div class="audit-name">Direct constants</div></div>
      <div class="audit-cell"><div class="audit-number">${transitive.length}</div><div class="audit-name">Transitive closure</div></div>
    </div>
    ${r.contains_sorry ? '<div class="callout warn semantic-gap"><strong>sorryAx detected.</strong> Lean accepted the file, but this Build is partial rather than verified.</div>' : ''}
    ${r.audit_error ? `<div class="callout warn semantic-gap"><strong>Index incomplete.</strong> ${esc(r.audit_error)}</div>` : ''}
    <div class="formal-identity-card">
      <div><span>Environment-scoped formal fingerprint</span><code>${esc(r.formal_fingerprint || 'not available')}</code></div>
      <div><span>Lean Expr type hash</span><code>${esc(r.type_hash || '—')}</code></div>
      <p>The fingerprint identifies the elaborated formal type inside this exact Lean/mathlib environment. It survives a theorem rename, but is not treated as cross-environment semantic equivalence.</p>
    </div>
    <div class="semantic-dependency-grid">
      <section><div class="section-head"><h3 class="artifact-heading">Direct proof dependencies</h3><span class="section-note">one expression edge</span></div><ul class="audit-list constant-list">${dependencyList(direct)}</ul></section>
      <section><div class="section-head"><h3 class="artifact-heading">Transitive closure</h3><span class="section-note">Lean dependency closure</span></div><ul class="audit-list constant-list">${dependencyList(transitive)}</ul></section>
    </div>
    <div class="audit-columns semantic-secondary">
      <section><div class="section-head"><h3 class="artifact-heading">Minimal imports</h3><span class="section-note">#min_imports</span></div><ul class="audit-list">${imports.length ? imports.map(x=>`<li><code>import ${esc(x)}</code></li>`).join('') : '<li>none reported</li>'}</ul></section>
      <section><div class="section-head"><h3 class="artifact-heading">Axioms</h3><span class="section-note">#print axioms</span></div><ul class="audit-list">${axioms.length ? axioms.map(x=>`<li><code>${esc(x)}</code></li>`).join('') : '<li>none reported</li>'}</ul></section>
    </div>
    <details class="compiler-details"><summary>Compiler / index log</summary><pre class="log">${esc(r.output || '')}</pre></details>
  </div>`;
};

function semanticRelationHtml(detail) {
  const s = detail?.semantics;
  if (!s) return '<div class="callout">No semantic index has been recorded for this Claim yet. Build a Proof with dependency audit first.</div>';
  const binding = s.bindings?.[0];
  const direct = (s.outgoing || []).filter(x => x.relation === 'uses-directly');
  const transitive = (s.outgoing || []).filter(x => x.relation === 'uses-transitively');
  const manual = (s.outgoing || []).filter(x => !x.relation.startsWith('uses-'));
  const matches = s.same_formal_type || [];
  return `<section class="artifact-section semantic-registry-section">
    <div class="section-head"><div><h2 class="artifact-heading">Formal identity & semantic graph</h2><p class="artifact-intro">Lean constants are the machine layer. Claim relations are the mathematical layer. Name equality alone is not accepted as identity.</p></div><span class="version-chip">semantic index</span></div>
    ${binding ? `<div class="formal-identity-card registry-identity">
      <div><span>Formal fingerprint</span><code>${semanticShort(binding.formal_fingerprint)}</code></div>
      <div><span>Bound Lean name</span><code>${esc(binding.lean_name)}</code></div>
      <div><span>Environment</span><code>${semanticShort(binding.environment_hash)}</code></div>
      <div><span>Evidence</span><code>${esc(binding.source_build_id || '—')}</code></div>
    </div>` : '<div class="callout">This Claim has no successful formal binding yet.</div>'}

    <div class="semantic-claim-columns">
      <div><h3 class="artifact-heading">Direct Claim dependencies</h3><p class="section-note">target declaration occurs directly in the proof/type expression</p><ul class="audit-list">${direct.length ? direct.map(r=>`<li><button data-open-semantic-claim="${r.related_claim_id}">${esc(r.related_claim_title)} · ${r.related_claim_id}</button><span>${esc(r.source_build_id || '')}</span></li>`).join('') : '<li>none mapped</li>'}</ul></div>
      <div><h3 class="artifact-heading">Transitive Claim dependencies</h3><p class="section-note">reachable only through the Lean dependency closure</p><ul class="audit-list">${transitive.length ? transitive.map(r=>`<li><button data-open-semantic-claim="${r.related_claim_id}">${esc(r.related_claim_title)} · ${r.related_claim_id}</button></li>`).join('') : '<li>none mapped</li>'}</ul></div>
    </div>

    <div class="same-type-block"><div class="section-head"><h3 class="artifact-heading">Same formal type candidates</h3><span class="section-note">same fingerprint + same environment; never auto-merged</span></div>
      ${matches.length ? `<ul class="audit-list">${matches.map(m=>`<li><button data-open-semantic-claim="${m.claim_id}">${esc(m.title)} · ${m.claim_id}</button><span><code>${esc(m.lean_name)}</code></span></li>`).join('')}</ul>` : '<div class="empty">No other Claim currently shares this environment-scoped formal fingerprint.</div>'}
    </div>

    <div class="manual-relations"><div class="section-head"><h3 class="artifact-heading">Explicit semantic relations</h3><span class="section-note">human/proof-level assertion, never inferred from wording</span></div>
      ${manual.length ? `<ul class="audit-list">${manual.map(r=>`<li><button data-open-semantic-claim="${r.related_claim_id}">${esc(r.related_claim_title)} · ${r.related_claim_id}</button><span>${esc(r.relation)} · ${esc(r.evidence_kind)}</span></li>`).join('')}</ul>` : ''}
      <form id="semantic-relation-form" class="relation-form">
        <input id="relation-target" placeholder="CLM-000002" pattern="CLM-[0-9]+" required />
        <select id="relation-kind"><option value="equivalent-to">equivalent-to</option><option value="specializes">specializes</option><option value="generalizes">generalizes</option><option value="alias-of">alias-of</option></select>
        <input id="relation-author" placeholder="author / handle" />
        <input id="relation-note" placeholder="note or proof reference" />
        <button class="btn" type="submit">Record relation</button>
      </form>
    </div>
  </section>`;
}

const semanticBaseDecorate = decorateRegistryClaim;
decorateRegistryClaim = function semanticDecorateRegistryClaim() {
  semanticBaseDecorate();
  if (state.view !== 'claim' || !registryState.claims.has(state.selected)) return;
  const detail = registryState.details.get(state.selected);
  const article = document.querySelector('.claim-grid article');
  if (!detail || !article || article.querySelector('.semantic-registry-section')) return;
  const wrap = document.createElement('div');
  wrap.innerHTML = semanticRelationHtml(detail);
  article.appendChild(wrap.firstElementChild);
};

function renderLeanIndex() {
  const r = semanticState.inspect;
  return `<main class="shell page"><div class="page-narrow lean-index-page">
    <div class="eyebrow">Lean Index</div>
    <h1 class="display-title">Inspect the formal library directly.</h1>
    <p class="display-deck">Query any declaration available in the pinned Lean/mathlib environment. MathHub reads the kernel environment: direct constants come from the declaration's elaborated type/value; the transitive layer comes from Lean's dependency closure.</p>
    <form id="lean-index-form" class="lean-index-form">
      <div class="field"><label>Declaration name</label><input id="lean-index-name" value="${esc(r?.declaration_name || 'Nat.exists_infinite_primes')}" placeholder="Nat.exists_infinite_primes" required /></div>
      <div class="field"><label>Imports</label><textarea id="lean-index-imports">${esc((r?.imports || ['Mathlib']).join('\n'))}</textarea></div>
      <button class="btn btn-primary" type="submit" ${semanticState.inspecting?'disabled':''}>${semanticState.inspecting?'Inspecting…':'Inspect declaration'}</button>
    </form>
    <div class="example-row"><span>Examples</span><button data-inspect-example="Nat.exists_infinite_primes">Nat.exists_infinite_primes</button><button data-inspect-example="Nat.Prime">Nat.Prime</button><button data-inspect-example="Real.exp_add">Real.exp_add</button></div>
    <div id="lean-index-result">${renderLeanInspection(r)}</div>
  </div></main>`;
}

function renderLeanInspection(r) {
  if (!r) return '<div class="lean-index-empty">No declaration inspected yet.</div>';
  if (r.error) return `<div class="callout bad"><strong>Inspection failed.</strong><br>${esc(r.error)}</div>`;
  const direct = r.direct_dependencies || [];
  const transitive = r.transitive_dependencies || [];
  return `<section class="lean-inspection">
    <div class="section-head"><div><div class="eyebrow">${r.ok?'Resolved declaration':'Lean error'}</div><h2 class="inspection-title">${esc(r.declaration_name)}</h2></div>${r.ok?badge('verified'):badge('failed')}</div>
    <div class="formal-identity-card"><div><span>Formal fingerprint</span><code>${esc(r.formal_fingerprint || '—')}</code></div><div><span>Expr type hash</span><code>${esc(r.type_hash || '—')}</code></div><div><span>Direct constants</span><strong>${direct.length}</strong></div><div><span>Transitive constants</span><strong>${transitive.length}</strong></div></div>
    <div class="semantic-dependency-grid"><section><h3 class="artifact-heading">Direct dependencies</h3><ul class="audit-list constant-list">${dependencyList(direct,120)}</ul></section><section><h3 class="artifact-heading">Transitive closure</h3><ul class="audit-list constant-list">${dependencyList(transitive,120)}</ul></section></div>
    <div class="section-head semantic-secondary"><h3 class="artifact-heading">Axioms</h3><span class="section-note">${(r.axioms||[]).length}</span></div><ul class="audit-list">${(r.axioms||[]).length ? r.axioms.map(x=>`<li><code>${esc(x)}</code></li>`).join('') : '<li>none reported</li>'}</ul>
    <details class="compiler-details"><summary>Lean output</summary><pre class="log">${esc(r.output || '')}</pre></details>
  </section>`;
}

const semanticBaseTopbar = topbar;
topbar = function semanticTopbar() {
  const html = semanticBaseTopbar();
  const active = state.view === 'lean-index' ? 'active' : '';
  return html.replace('</nav>', `<button class="${active}" data-view="lean-index">Lean index</button></nav>`);
};

const semanticBaseCurrent = current;
current = function semanticCurrent() {
  if (state.view === 'lean-index') return renderLeanIndex();
  return semanticBaseCurrent();
};

function visibleSemanticEdges() {
  const all = semanticState.graph?.edges || [];
  if (semanticState.graphFilter === 'all') return all;
  if (semanticState.graphFilter === 'direct') return all.filter(e => e.relation === 'uses-directly' || !e.relation.startsWith('uses-'));
  if (semanticState.graphFilter === 'transitive') return all.filter(e => e.relation.startsWith('uses-') || !e.relation.startsWith('uses-'));
  return all;
}

function semanticGraphLayout(nodes) {
  const cols = Math.max(1, Math.ceil(Math.sqrt(nodes.length || 1)));
  return Object.fromEntries(nodes.map((n,i)=>[n.id,{...n,x:130+(i%cols)*240,y:110+Math.floor(i/cols)*175}]));
}

renderGraph = function semanticGraph() {
  if (!semanticState.graph) return `<main class="shell page"><div class="eyebrow">Semantic dependency graph</div><h1 class="display-title">Claim graph, not citation graph.</h1><div class="callout">${semanticState.graphLoading?'Loading semantic graph…':'No persistent semantic graph loaded yet.'}</div></main>`;
  const nodes = semanticState.graph.nodes || [];
  const edges = visibleSemanticEdges();
  const layout = semanticGraphLayout(nodes);
  const rows = Math.max(1, Math.ceil(nodes.length / Math.max(1,Math.ceil(Math.sqrt(nodes.length||1)))));
  const height = Math.max(520, 180 + rows*175);
  const edgeSvg = edges.filter(e=>layout[e.from]&&layout[e.to]).map(e=>{
    const a=layout[e.from],b=layout[e.to];
    const cls=e.relation==='uses-directly'?'semantic-direct':e.relation==='uses-transitively'?'semantic-transitive':'semantic-manual';
    return `<line class="edge ${cls}" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/>`;
  }).join('');
  const nodeSvg = nodes.map(n=>{const p=layout[n.id];const label=n.title.length>29?n.title.slice(0,28)+'…':n.title;return `<g class="node ${state.graphSelected===n.id?'selected':''}" data-semantic-node="${n.id}" transform="translate(${p.x},${p.y})"><circle r="28"></circle><text text-anchor="middle" y="49">${esc(label)}</text><text text-anchor="middle" y="64" class="graph-id">${n.id}</text></g>`}).join('');
  const selected = nodes.find(n=>n.id===state.graphSelected) || nodes[0];
  return `<main class="shell page"><div class="eyebrow">Semantic dependency graph</div><h1 class="display-title">Lean facts below; mathematical relations above.</h1><p class="display-deck">A direct edge means the target Claim's bound Lean declaration occurs in the source proof/type expression. A transitive edge is reachable only through the proof dependency closure. Manual equivalence/generalization edges remain visibly distinct.</p>
    <div class="graph-toolbar"><button class="filter ${semanticState.graphFilter==='direct'?'active':''}" data-graph-filter="direct">Direct + semantic</button><button class="filter ${semanticState.graphFilter==='transitive'?'active':''}" data-graph-filter="transitive">Include transitive</button><button class="filter ${semanticState.graphFilter==='all'?'active':''}" data-graph-filter="all">All relations</button><span>${nodes.length} claims · ${edges.length} visible edges</span></div>
    ${nodes.length ? `<div class="graph-shell"><div class="graph-canvas semantic-graph-canvas"><svg viewBox="0 0 ${Math.max(930,Math.ceil(Math.sqrt(nodes.length))*240+100)} ${height}"><defs><marker id="arrow" viewBox="0 0 10 10" refX="30" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#aaa69a"/></marker></defs>${edgeSvg}${nodeSvg}</svg></div><aside class="graph-inspector">${selected?`<div class="eyebrow">Selected Claim</div><h2 class="section-title graph-selected-title">${esc(selected.title)}</h2><div>${badge(selected.status)}</div><div class="meta-row"><div class="meta-label">Identifier</div><div class="meta-value meta-mono">${selected.id}</div></div><div class="meta-row"><div class="meta-label">Lean binding</div><div class="meta-value meta-mono">${esc(selected.lean_name||'—')}</div></div><button class="btn btn-primary" data-open-semantic-claim="${selected.id}" style="width:100%">Open Claim</button>`:''}</aside></div>` : '<div class="callout">Register and build at least one local Claim to populate the semantic graph.</div>'}
    <div class="graph-legend semantic-legend"><span><i class="legend-line direct"></i>direct proof dependency</span><span><i class="legend-line transitive"></i>transitive dependency</span><span><i class="legend-line manual"></i>explicit semantic relation</span></div>
  </main>`;
};

async function loadSemanticGraph() {
  if (semanticState.graphLoading) return;
  semanticState.graphLoading = true;
  try { semanticState.graph = await apiJson('/api/graph'); }
  catch (_) { semanticState.graph = {nodes:[],edges:[]}; }
  finally { semanticState.graphLoading = false; if(state.view==='graph') render(); }
}

async function inspectLean(name, imports) {
  semanticState.inspecting = true; render();
  try {
    semanticState.inspect = await apiJson('/api/lean/inspect', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({declaration_name:name,imports})});
  } catch (err) {
    semanticState.inspect = {error:err.message,declaration_name:name,imports};
  } finally {
    semanticState.inspecting = false; render();
  }
}

async function recordSemanticRelation(claimId) {
  const target=document.querySelector('#relation-target')?.value.trim().toUpperCase()||'';
  const relation=document.querySelector('#relation-kind')?.value||'equivalent-to';
  const author=document.querySelector('#relation-author')?.value||'';
  const note=document.querySelector('#relation-note')?.value||'';
  await apiJson(`/api/claims/${claimId}/relations`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({related_claim_id:target,relation,author,note})});
  registryState.details.delete(claimId);
  semanticState.graph=null;
  await ensureClaimDetail(claimId);
  await loadSemanticGraph();
  render();
}

const semanticBaseBind = bind;
bind = function semanticBind() {
  semanticBaseBind();
  document.querySelectorAll('[data-open-semantic-claim]').forEach(el=>el.onclick=async()=>{
    const id=el.dataset.openSemanticClaim;
    if(registryState.claims.has(id)) await ensureClaimDetail(id);
    state.selected=id;state.graphSelected=id;state.view='claim';render();
  });
  document.querySelectorAll('[data-semantic-node]').forEach(el=>el.onclick=()=>{state.graphSelected=el.dataset.semanticNode;render();});
  document.querySelectorAll('[data-graph-filter]').forEach(el=>el.onclick=()=>{semanticState.graphFilter=el.dataset.graphFilter;render();});
  document.querySelector('#lean-index-form')?.addEventListener('submit',event=>{event.preventDefault();const name=document.querySelector('#lean-index-name')?.value.trim()||'';const imports=(document.querySelector('#lean-index-imports')?.value||'Mathlib').split(/\n+/).map(x=>x.trim()).filter(Boolean);inspectLean(name,imports);});
  document.querySelectorAll('[data-inspect-example]').forEach(el=>el.onclick=()=>inspectLean(el.dataset.inspectExample,['Mathlib']));
  document.querySelector('#semantic-relation-form')?.addEventListener('submit',async event=>{event.preventDefault();try{await recordSemanticRelation(state.selected);}catch(err){alert(`Could not record relation: ${err.message}`);}});
};

const semanticBaseRender = render;
render = function semanticRender() {
  semanticBaseRender();
  if (state.view === 'graph' && !semanticState.graph && !semanticState.graphLoading) loadSemanticGraph();
};

loadSemanticGraph();
