/* Bounded semantic graph navigation.
   The registry may contain millions of Claims; graph pages load a local slice
   around the selected Claim instead of requesting a global graph. */

const graphQueryState = {
  depth: 1,
  limit: 80,
  loading: false,
  centeredOn: null,
};

async function loadGraphNeighborhood(root = '') {
  if (graphQueryState.loading) return;
  graphQueryState.loading = true;
  semanticState.graphLoading = true;
  if (state.view === 'graph') render();
  try {
    const params = new URLSearchParams({
      depth: String(graphQueryState.depth),
      limit: String(graphQueryState.limit),
      relations: 'all',
    });
    if (root) params.set('claim', root);
    semanticState.graph = await apiJson(`/api/graph?${params.toString()}`);
    graphQueryState.centeredOn = semanticState.graph.root || null;
    const nodes = semanticState.graph.nodes || [];
    if (root && nodes.some(n => n.id === root)) state.graphSelected = root;
    else if (!nodes.some(n => n.id === state.graphSelected)) state.graphSelected = nodes[0]?.id || '';
  } catch (err) {
    semanticState.graph = {nodes:[], edges:[], error:err.message};
  } finally {
    graphQueryState.loading = false;
    semanticState.graphLoading = false;
    if (state.view === 'graph') render();
  }
}

// Replace the old all-graph loader. Calls made after this script loads are bounded.
loadSemanticGraph = function boundedSemanticGraphLoader() {
  const root = registryState?.claims?.has(state.graphSelected) ? state.graphSelected : '';
  return loadGraphNeighborhood(root);
};

const graphQueryBaseRenderGraph = renderGraph;
renderGraph = function boundedGraphView() {
  let html = graphQueryBaseRenderGraph();
  if (!semanticState.graph) return html;
  const meta = semanticState.graph;
  const rootNode = (meta.nodes || []).find(n => n.id === meta.root);
  const centerLabel = rootNode?.title || (meta.mode === 'recent' ? 'recent registry slice' : 'current Claim');
  const controls = `<div class="graph-scope-bar">
    <div><span class="graph-scope-label">Graph scope</span><strong>${esc(centerLabel)}</strong><small>${meta.mode === 'neighborhood' ? `${meta.depth} hop neighborhood` : 'bounded recent slice'}${meta.truncated ? ' · node cap reached' : ''}</small></div>
    <div class="graph-scope-actions">
      <button class="filter ${graphQueryState.depth===1?'active':''}" data-graph-depth="1">1 hop</button>
      <button class="filter ${graphQueryState.depth===2?'active':''}" data-graph-depth="2">2 hops</button>
      <button class="btn" data-center-graph ${!state.graphSelected?'disabled':''}>Center on selected</button>
      <button class="btn" data-recent-graph>Recent slice</button>
    </div>
  </div>`;
  return html.replace('<div class="graph-toolbar">', controls + '<div class="graph-toolbar">');
};

const graphQueryBaseBind = bind;
bind = function boundedGraphBind() {
  graphQueryBaseBind();
  if (state.view !== 'graph') return;

  // Node selection remains cheap; double-clicking or the explicit button recenters.
  document.querySelectorAll('[data-semantic-node]').forEach(el => {
    el.onclick = event => {
      state.graphSelected = el.dataset.semanticNode;
      if (event.detail >= 2) loadGraphNeighborhood(state.graphSelected);
      else render();
    };
  });

  document.querySelectorAll('[data-graph-depth]').forEach(el => {
    el.onclick = () => {
      graphQueryState.depth = Number(el.dataset.graphDepth) || 1;
      loadGraphNeighborhood(state.graphSelected || '');
    };
  });
  document.querySelector('[data-center-graph]')?.addEventListener('click', () => {
    if (state.graphSelected) loadGraphNeighborhood(state.graphSelected);
  });
  document.querySelector('[data-recent-graph]')?.addEventListener('click', () => loadGraphNeighborhood(''));
};

// The earlier semantic client may already have loaded a bounded recent slice via
// the canonical server. No extra request is made here until the graph is opened
// or a user explicitly recenters it.
