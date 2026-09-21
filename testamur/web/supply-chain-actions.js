(() => {
  'use strict';

  const findReviewTarget = () => document.querySelector(
    '.advisory-review, .project-advisory-review, [data-advisory-review], .advisory-candidates, [data-advisory-candidates]'
  );

  const projectRef = () => {
    const match = location.pathname.match(/^\/projects\/([^/]+)\/?$/);
    return match ? decodeURIComponent(match[1]) : null;
  };

  const addActions = surface => {
    if (surface.dataset.compareActionsReady === '1') return;
    surface.dataset.compareActionsReady = '1';
    const ref = projectRef();
    if (!ref) return;

    const actions = document.createElement('div');
    actions.className = 'supply-chain-guidance-actions supply-chain-compare-actions';
    actions.innerHTML = `<button type="button" class="btn btn-primary" data-review-advisories>Review advisory candidates</button><a class="btn btn-secondary" data-nav href="/projects/${encodeURIComponent(ref)}?tab=activity">Review recorded activity</a>`;
    surface.append(actions);

    actions.querySelector('[data-review-advisories]')?.addEventListener('click', () => {
      const target = findReviewTarget();
      if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        target.setAttribute('tabindex', '-1');
        target.focus({ preventScroll: true });
        return;
      }
      location.href = `/projects/${encodeURIComponent(ref)}?tab=supply-chain#review`;
    });

    const note = document.createElement('p');
    note.className = 'form-help supply-chain-action-boundary';
    note.textContent = 'Comparison selects what to inspect next; it does not establish affectedness. Record scoped evidence in advisory review and revalidation.';
    actions.after(note);
  };

  const enhance = () => document
    .querySelectorAll('.supply-chain-compare[data-live-supply-chain-compare], .supply-chain-compare[data-supply-chain-diff]')
    .forEach(addActions);

  new MutationObserver(enhance).observe(document.documentElement, { childList: true, subtree: true });
  enhance();
})();
