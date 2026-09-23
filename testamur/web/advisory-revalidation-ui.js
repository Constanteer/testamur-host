(() => {
  'use strict';

  const nativeFetch = window.fetch.bind(window);
  let latest = null;
  let latestProjectContext = null;
  let renderQueued = false;

  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
  })[char]);

  const reviewContext = () => {
    try {
      const value = JSON.parse(sessionStorage.getItem('testamur.reviewContext') || 'null');
      return value?.source === 'supply-chain-compare' && value?.dependency ? value : null;
    } catch (_) { return null; }
  };

  const projectContext = url => {
    const context = {};
    for (const key of ['project', 'project_id']) {
      const value = url.searchParams.get(key);
      if (value) context[key] = value;
    }
    return context;
  };

  const contextMatches = (item, context) => {
    if (!context) return false;
    const haystack = [
      item.component_id, item.component, item.dependency, item.package,
      item.subject_revision, item.component_revision, item.subject
    ].filter(Boolean).join(' ').toLowerCase();
    return [context.component_id, context.dependency]
      .filter(Boolean)
      .some(value => haystack.includes(String(value).toLowerCase()));
  };

  window.fetch = async (...args) => {
    const response = await nativeFetch(...args);
    try {
      const input = args[0];
      const url = new URL(typeof input === 'string' ? input : input.url, location.origin);
      if (url.pathname === '/v1/project' && response.ok) {
        const body = await response.clone().json();
        latest = body?.supply_chain?.advisory_revalidation || null;
        latestProjectContext = projectContext(url);
        queueRender();
      }
    } catch (_) {}
    return response;
  };

  function itemCard(item, context) {
    const ref = item.subject_revision || item.component_revision || item.subject || '';
    const reason = item.reason || item.revalidation_reason || 'Recorded project state indicates this assessment should be reviewed again.';
    const matched = contextMatches(item, context);
    const params = new URLSearchParams({ tab: 'revalidate' });
    for (const [key, value] of Object.entries(latestProjectContext || {})) params.set(key, value);
    if (matched) {
      params.set('dependency', context.dependency);
      if (context.component_id) params.set('component', context.component_id);
      if (context.before_version) params.set('from', context.before_version);
      if (context.after_version) params.set('to', context.after_version);
    }
    const href = ref ? `/object/${encodeURIComponent(ref)}?${params.toString()}` : '#';
    return `<li class="advisory-revalidation-item"${matched ? ' data-revalidation-context-match="true"' : ''}>
      <span><strong>${esc(ref || 'component revision')}</strong><small>${esc(reason)}</small></span>
      ${ref ? `<a class="btn btn-secondary" href="${esc(href)}">${matched ? 'Open scoped revalidation' : 'Open revalidation'}</a>` : ''}
    </li>`;
  }

  function render() {
    const host = document.querySelector('.supply-chain-advisories');
    if (!host) return;
    host.querySelector('[data-advisory-revalidation]')?.remove();
    if (!latest) return;

    const items = Array.isArray(latest.items)
      ? latest.items
      : Array.isArray(latest.work_items)
        ? latest.work_items
        : [];
    if (!items.length) return;

    const context = reviewContext();
    const scoped = context && items.some(item => contextMatches(item, context));
    const surface = document.createElement('section');
    surface.dataset.advisoryRevalidation = 'true';
    surface.className = 'advisory-revalidation-state';
    surface.innerHTML = `<div class="section-head compact">
        <div><h3>Revalidation work</h3><p>${items.length} recorded assessment${items.length === 1 ? '' : 's'} worth revisiting after project state changed.</p></div>
      </div>
      ${scoped ? `<p class="supply-chain-advisory-note" data-revalidation-review-context><strong>Continue scoped review: ${esc(context.dependency)} ${esc(context.before_version || 'unknown')} → ${esc(context.after_version || 'unknown')}.</strong> This mechanical Compare context narrows navigation only; it does not establish affectedness, validity, safety, or reliance.</p>` : ''}
      <p class="supply-chain-advisory-note"><strong>Change is a review trigger, not a verdict.</strong> Changed ≠ invalid; stale ≠ false. Testamur preserves the recorded assessment and asks you to inspect the new evidence instead of silently rewriting it.</p>
      <ul class="advisory-revalidation-list">${items.map(item => itemCard(item, context)).join('')}</ul>`;
    host.appendChild(surface);
  }

  function queueRender() {
    if (renderQueued) return;
    renderQueued = true;
    queueMicrotask(() => {
      renderQueued = false;
      render();
    });
  }

  const observer = new MutationObserver(records => {
    const external = records.some(record => [...record.addedNodes, ...record.removedNodes].some(node =>
      node instanceof Element && !node.matches('[data-advisory-revalidation]') && !node.closest('[data-advisory-revalidation]')));
    if (external) queueRender();
  });

  window.addEventListener('testamur:review-context', queueRender);
  observer.observe(document.documentElement, { childList: true, subtree: true });
})();
