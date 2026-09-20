(() => {
  'use strict';

  const nativeFetch = window.fetch.bind(window);
  let latest = null;

  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
  })[char]);

  window.fetch = async (...args) => {
    const response = await nativeFetch(...args);
    try {
      const input = args[0];
      const url = new URL(typeof input === 'string' ? input : input.url, location.origin);
      if (url.pathname === '/v1/project' && response.ok) {
        const body = await response.clone().json();
        latest = body?.supply_chain?.advisory_revalidation || null;
        queueMicrotask(render);
      }
    } catch (_) {}
    return response;
  };

  function itemCard(item) {
    const ref = item.subject_revision || item.component_revision || item.subject || '';
    const reason = item.reason || item.revalidation_reason || 'Recorded project state indicates this assessment should be reviewed again.';
    const href = ref ? `/object/${encodeURIComponent(ref)}?tab=revalidate` : '#';
    return `<li class="advisory-revalidation-item">
      <span><strong>${esc(ref || 'component revision')}</strong><small>${esc(reason)}</small></span>
      ${ref ? `<a class="btn btn-secondary" href="${esc(href)}">Open revalidation</a>` : ''}
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

    const surface = document.createElement('section');
    surface.dataset.advisoryRevalidation = 'true';
    surface.className = 'advisory-revalidation-state';
    surface.innerHTML = `<div class="section-head compact">
        <div><h3>Revalidation work</h3><p>${items.length} recorded assessment${items.length === 1 ? '' : 's'} worth revisiting after project state changed.</p></div>
      </div>
      <p class="supply-chain-advisory-note"><strong>Change is a review trigger, not a verdict.</strong> Changed ≠ invalid; stale ≠ false. Testamur preserves the recorded assessment and asks you to inspect the new evidence instead of silently rewriting it.</p>
      <ul class="advisory-revalidation-list">${items.map(itemCard).join('')}</ul>`;
    host.appendChild(surface);
  }

  new MutationObserver(render).observe(document.documentElement, { childList: true, subtree: true });
})();
