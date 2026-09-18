/* Scalable registry catalog.
   The live server returns a bounded page; this layer keeps the browser from
   assuming the entire mathematical registry fits in one response. */

const catalogState = {
  live: false,
  loading: false,
  nextCursor: null,
  hasMore: false,
  pageSize: 40,
  requestSerial: 0,
  searchTimer: null,
};

function catalogUrl({cursor = '', query = state.search, status = state.filter} = {}) {
  const params = new URLSearchParams();
  params.set('limit', String(catalogState.pageSize));
  if (cursor) params.set('cursor', cursor);
  if (query) params.set('q', query);
  if (status && status !== 'all') params.set('status', status);
  return `/api/claims?${params.toString()}`;
}

function replaceCatalogClaims(items) {
  claims.splice(0, claims.length);
  registryState.claims.clear();
  registryState.details.clear();
  for (const item of items) {
    registryState.claims.set(item.claim_id, item);
    claims.push(registryToUi(item));
  }
}

function appendCatalogClaims(items) {
  for (const item of items) {
    registryState.claims.set(item.claim_id, item);
    const ui = registryToUi(item);
    const at = claims.findIndex(c => c.id === ui.id);
    if (at >= 0) claims[at] = ui;
    else claims.push(ui);
  }
}

async function loadCatalogPage({reset = false, cursor = ''} = {}) {
  if (catalogState.loading && !reset) return;
  const serial = ++catalogState.requestSerial;
  catalogState.loading = true;
  decorateCatalogFooter();
  try {
    const payload = await apiJson(catalogUrl({cursor}));
    if (serial !== catalogState.requestSerial) return;
    catalogState.live = true;
    registryState.ready = true;
    if (reset || !catalogState.liveInitialized) {
      replaceCatalogClaims(payload.claims || []);
      catalogState.liveInitialized = true;
    } else {
      appendCatalogClaims(payload.claims || []);
    }
    catalogState.nextCursor = payload.next_cursor || null;
    catalogState.hasMore = !!payload.has_more;
  } catch (_) {
    // Static mode / lower-level server: leave the existing prototype untouched.
  } finally {
    if (serial === catalogState.requestSerial) catalogState.loading = false;
    render();
  }
}

function scheduleCatalogRefresh() {
  clearTimeout(catalogState.searchTimer);
  catalogState.searchTimer = setTimeout(() => loadCatalogPage({reset: true}), 260);
}

function decorateCatalogFooter() {
  if (state.view !== 'registry' || !catalogState.live) return;
  const list = document.querySelector('.claim-list');
  if (!list || document.querySelector('.catalog-footer')) return;
  const footer = document.createElement('div');
  footer.className = 'catalog-footer';
  const loaded = claims.length;
  footer.innerHTML = `<div><strong>${loaded}</strong><span>Claim${loaded === 1 ? '' : 's'} loaded in this view</span><small>cursor-paginated registry; no full-table count required</small></div>${catalogState.hasMore ? `<button class="btn" data-catalog-more ${catalogState.loading ? 'disabled' : ''}>${catalogState.loading ? 'Loading…' : 'Load more'}</button>` : '<span class="catalog-end">end of current result set</span>'}`;
  list.after(footer);
  footer.querySelector('[data-catalog-more]')?.addEventListener('click', () => {
    if (catalogState.nextCursor) loadCatalogPage({cursor: catalogState.nextCursor});
  });

  const firstMetric = document.querySelector('.overview-line .metric .metric-label');
  if (firstMetric) firstMetric.textContent = 'Claims loaded';
}

const catalogBaseBind = bind;
bind = function catalogBind() {
  catalogBaseBind();
  if (!catalogState.live) return;

  const search = document.querySelector('#search');
  if (search) {
    const baseInput = search.oninput;
    search.oninput = event => {
      if (baseInput) baseInput(event);
      scheduleCatalogRefresh();
    };
  }

  document.querySelectorAll('[data-filter]').forEach(button => {
    const baseClick = button.onclick;
    button.onclick = event => {
      if (baseClick) baseClick.call(button, event);
      scheduleCatalogRefresh();
    };
  });
};

const catalogBaseRender = render;
render = function catalogRender() {
  catalogBaseRender();
  decorateCatalogFooter();
};

loadCatalogPage({reset: true});
