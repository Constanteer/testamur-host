(() => {
  'use strict';

  // Presentation-only progress navigator. Completion comes from recorded
  // workspace state exposed by the existing onboarding checklist; this layer
  // never infers verification, reliance, affectedness, validity, or truth.
  const STORAGE_KEY = 'testamur.first-run-progress.v1';
  const steps = [
    ['project', 'Create a project', '/', 'Define the workspace boundary'],
    ['monitor', 'Add a monitor', '/', 'Choose what Testamur records'],
    ['baseline', 'Record a baseline', '/', 'Create the first observation'],
    ['compare', 'Open Compare', '/demo?stage=compare', 'Inspect change without calling it invalid'],
    ['impact', 'Review Impact', '/demo?stage=impact', 'Inspect consequences without guessing reliance'],
    ['revalidation', 'Revalidation', '/demo?stage=revalidate', 'Decide what evidence must run again'],
  ];

  function readProgress() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); }
    catch (_) { return {}; }
  }

  function writeProgress(progress) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(progress)); }
    catch (_) { /* private/locked storage must not break navigation */ }
  }

  function syncRecordedState(progress) {
    const onboarding = document.querySelector('.onboarding-card');
    if (!onboarding) return progress;
    const done = onboarding.querySelectorAll('.onboarding-step.done').length;
    ['project', 'monitor', 'baseline'].forEach((key, index) => {
      if (index < done) progress[key] = true;
    });
    writeProgress(progress);
    return progress;
  }

  function markVisited(progress) {
    const path = location.pathname;
    const query = new URLSearchParams(location.search);
    const demoStage = query.get('stage');
    const objectTab = query.get('tab');
    // Real object tabs and the read-only example both count as presentation
    // progress. Visiting them does not assert that any object was revalidated.
    if ((path.startsWith('/object/') && objectTab === 'compare') || (path === '/demo' && demoStage === 'compare')) progress.compare = true;
    if (path.startsWith('/impact/') || (path.startsWith('/object/') && objectTab === 'impact') || (path === '/demo' && demoStage === 'impact')) progress.impact = true;
    if ((path.startsWith('/object/') && objectTab === 'revalidate') || (path === '/demo' && demoStage === 'revalidate')) progress.revalidation = true;
    writeProgress(progress);
    return progress;
  }

  function render() {
    document.querySelector('[data-first-run-resume]')?.remove();
    if (location.pathname === '/signin' || location.pathname === '/signup') return;

    const progress = markVisited(syncRecordedState(readProgress()));
    const completed = steps.filter(([key]) => progress[key]).length;
    if (completed === steps.length) return;
    const next = steps.find(([key]) => !progress[key]);
    if (!next) return;

    const panel = document.createElement('aside');
    panel.className = 'first-run-resume';
    panel.dataset.firstRunResume = 'true';
    panel.setAttribute('aria-label', 'First-run progress');
    panel.innerHTML = `
      <div class="first-run-resume-copy">
        <span>FIRST RUN · ${completed}/${steps.length}</span>
        <strong>Next: ${next[1]}</strong>
        <small>${next[3]}</small>
      </div>
      <a data-nav href="${next[2]}">Continue setup →</a>
      <a data-nav class="first-run-resume-learn" href="/learn">Why these steps?</a>`;
    document.body.append(panel);
    window.bindNavigation?.();
  }

  addEventListener('DOMContentLoaded', render);
  addEventListener('popstate', render);
  new MutationObserver(() => queueMicrotask(render)).observe(document.documentElement, { childList: true, subtree: true });
})();
