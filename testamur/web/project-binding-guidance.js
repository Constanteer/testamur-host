(() => {
  'use strict';

  // Presentation-only guidance. Repository bindings scope observations and
  // comparisons; they do not imply verification, reliance, or affectedness.
  let queued = false;

  function render() {
    document.querySelector('[data-project-binding-guidance]')?.remove();
    const match = location.pathname.match(/^\/projects\/([^/]+)\/?$/);
    if (!match || new URLSearchParams(location.search).get('tab') !== 'supply-chain') return;

    const inputs = document.querySelector('.supply-chain-layout .object-main-card');
    if (!inputs || !inputs.textContent.includes('Repository scanner inputs')) return;
    const fields = [...inputs.querySelectorAll('.field')];
    if (!fields.length) return;

    const labels = fields.map(field => {
      const key = field.querySelector(':scope > span')?.textContent?.trim() || 'binding';
      const locator = field.querySelector('code')?.textContent?.trim() || 'repository locator unavailable';
      return { key, locator };
    });

    const note = document.createElement('aside');
    note.className = 'supply-chain-guidance-note';
    note.dataset.projectBindingGuidance = '1';
    note.setAttribute('aria-label', 'Repository comparison scope');
    note.innerHTML = `<strong>Repository context stays attached</strong>
      <p>Each scanner input below is a separate repository binding with its own immutable observation history. Compare observations only inside the same binding; two scans from different repositories are not a before/after pair.</p>
      <div class="supply-chain-boundaries"><code>same binding → comparable history</code><code>different binding → separate history</code><code>candidate != affectedness verdict</code></div>
      <p><strong>${labels.length} binding${labels.length === 1 ? '' : 's'} in this project.</strong> ${labels.map(({key, locator}) => `<code>${escapeHtml(key)}</code> ${escapeHtml(locator)}`).join(' · ')}</p>
      <p>Project-level advisory review may aggregate exact candidates across bindings so you can inspect them together. That aggregation does not assert that any repository is affected. Open the candidate and preserve its repository, package/version, advisory, and observation context through explicit revalidation.</p>`;
    inputs.after(note);
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  }

  function queueRender() {
    if (queued) return;
    queued = true;
    queueMicrotask(() => { queued = false; render(); });
  }

  const observer = new MutationObserver(records => {
    const external = records.some(record => [...record.addedNodes, ...record.removedNodes].some(node =>
      node instanceof Element && !node.matches('[data-project-binding-guidance]') && !node.closest('[data-project-binding-guidance]')));
    if (external) queueRender();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  addEventListener('popstate', queueRender);
  addEventListener('DOMContentLoaded', queueRender);
})();
