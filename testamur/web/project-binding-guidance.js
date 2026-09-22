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
      <div class="project-binding-list" aria-label="Repository bindings">${labels.map(({key, locator}) => `<div class="project-binding-item"><span><strong>${escapeHtml(key)}</strong><small>${escapeHtml(locator)}</small></span><span class="project-binding-scope">Compare within this repository</span></div>`).join('')}</div>
      <p><strong>${labels.length} binding${labels.length === 1 ? '' : 's'} in this project.</strong> Pick two observations from one repository when you need a mechanical before/after comparison. If you switch repository, start a separate comparison.</p>
      <div class="supply-chain-guidance-actions project-binding-actions"><button type="button" class="btn btn-primary" data-project-binding-observations>Review observation history</button><button type="button" class="btn btn-secondary" data-project-binding-advisories>Review advisory candidates</button></div>
      <p>Project-level advisory review may aggregate exact candidates across bindings so you can inspect them together. That aggregation does not assert that any repository is affected. Open the candidate and preserve its repository, package/version, advisory, and observation context through explicit revalidation.</p>`;
    inputs.after(note);

    note.querySelector('[data-project-binding-observations]')?.addEventListener('click', () => focusSurface(note, [
      '.supply-chain-history', '[data-supply-chain-history]', '.supply-chain-observations', '[data-supply-chain-observations]', '.supply-chain-compare'
    ]));
    note.querySelector('[data-project-binding-advisories]')?.addEventListener('click', () => focusSurface(note, [
      '.project-advisory-review', '[data-advisory-review]', '.advisory-candidates', '[data-advisory-candidates]', '.supply-chain-advisories'
    ], 'review'));
  }

  function focusSurface(origin, selectors, hash = '') {
    const target = selectors.map(selector => document.querySelector(selector)).find(Boolean);
    if (target) {
      target.setAttribute('tabindex', '-1');
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      target.focus({ preventScroll: true });
      return;
    }
    const project = location.pathname.match(/^\/projects\/([^/]+)\/?$/)?.[1];
    if (!project) return;
    const destination = `/projects/${project}?tab=supply-chain${hash ? `#${hash}` : ''}`;
    if (location.pathname + location.search + location.hash !== destination) location.href = destination;
    else origin.querySelector('strong')?.focus?.();
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
