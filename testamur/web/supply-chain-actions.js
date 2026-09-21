(() => {
  'use strict';

  const findReviewTarget = () => document.querySelector(
    '.advisory-review, .project-advisory-review, [data-advisory-review], .advisory-candidates, [data-advisory-candidates]'
  );

  const projectRef = () => {
    const match = location.pathname.match(/^\/projects\/([^/]+)\/?$/);
    return match ? decodeURIComponent(match[1]) : null;
  };

  const focusReview = (ref, context = null) => {
    if (context) {
      sessionStorage.setItem('testamur.reviewContext', JSON.stringify(context));
      window.dispatchEvent(new CustomEvent('testamur:review-context', { detail: context }));
    }
    const target = findReviewTarget();
    if (target) {
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      target.setAttribute('tabindex', '-1');
      target.focus({ preventScroll: true });
      return;
    }
    const suffix = context?.dependency ? `&dependency=${encodeURIComponent(context.dependency)}` : '';
    location.href = `/projects/${encodeURIComponent(ref)}?tab=supply-chain${suffix}#review`;
  };

  const addRowActions = (surface, ref) => surface.querySelectorAll('.supply-chain-transition-row').forEach(row => {
    if (row.dataset.reviewActionReady === '1') return;
    row.dataset.reviewActionReady = '1';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn-secondary supply-chain-row-review';
    button.textContent = 'Review impact';
    button.setAttribute('aria-label', `Review impact and advisory candidates for ${row.dataset.dependencyName || 'dependency'}`);
    button.addEventListener('click', () => focusReview(ref, {
      dependency: row.dataset.dependencyName || '',
      component_id: row.dataset.componentId || '',
      before_version: row.dataset.beforeVersion || '',
      after_version: row.dataset.afterVersion || '',
      transition: row.dataset.transition || 'version-changed',
      source: 'supply-chain-compare'
    }));
    row.append(button);
  });

  const addActions = surface => {
    const ref = projectRef();
    if (!ref) return;
    addRowActions(surface, ref);
    if (surface.dataset.compareActionsReady === '1') return;
    surface.dataset.compareActionsReady = '1';

    const actions = document.createElement('div');
    actions.className = 'supply-chain-guidance-actions supply-chain-compare-actions';
    actions.innerHTML = `<button type="button" class="btn btn-primary" data-review-advisories>Review advisory candidates</button><a class="btn btn-secondary" data-nav href="/projects/${encodeURIComponent(ref)}?tab=activity">Review recorded activity</a>`;
    surface.append(actions);

    actions.querySelector('[data-review-advisories]')?.addEventListener('click', () => focusReview(ref));

    const note = document.createElement('p');
    note.className = 'form-help supply-chain-action-boundary';
    note.textContent = 'Comparison selects what to inspect next; it does not establish affectedness. A row-level review carries component context only. Record scoped evidence in advisory review and revalidation.';
    actions.after(note);
  };

  const enhance = () => document
    .querySelectorAll('.supply-chain-compare[data-live-supply-chain-compare], .supply-chain-compare[data-supply-chain-diff]')
    .forEach(addActions);

  new MutationObserver(enhance).observe(document.documentElement, { childList: true, subtree: true });
  enhance();
})();
