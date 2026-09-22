(() => {
  'use strict';

  // Presentation-only launch guidance. Completion is derived from the existing
  // dashboard checklist; this layer never infers verification or reliance.
  let renderQueued = false;

  function render() {
    document.querySelector('[data-first-run-journey]')?.remove();
    if (location.pathname !== '/' && location.pathname !== '/app') return;
    const onboarding = document.querySelector('.onboarding-card');
    if (!onboarding) return;

    const completed = onboarding.querySelectorAll('.onboarding-step.done').length;
    const panel = document.createElement('aside');
    panel.className = 'first-run-journey';
    panel.dataset.firstRunJourney = 'true';
    panel.setAttribute('aria-label', 'First-run learning paths');
    panel.innerHTML = `
      <div class="first-run-journey-copy">
        <span class="onboarding-kicker">FIRST RUN · ${completed}/3 RECORDED</span>
        <strong>Need a map while you set up the first project?</strong>
        <small>The checklist above follows real workspace state. These links stay available until the first observation exists.</small>
      </div>
      <nav class="first-run-journey-links" aria-label="Getting started resources">
        <a data-nav href="/quickstart"><strong>5-minute quickstart</strong><small>Project → monitor → baseline</small></a>
        <a data-nav href="/demo"><strong>Example project</strong><small>See baseline → change → Compare → review</small></a>
        <a data-nav href="/integrations"><strong>Codex / MCP</strong><small>Install an agent entry point</small></a>
        <a data-nav href="/learn"><strong>Learn the model</strong><small>Source, Revision, Compare, Impact, Revalidation</small></a>
      </nav>
      <div class="first-run-journey-boundary" aria-label="Semantic boundaries">
        <span>recorded ≠ verified</span><span>fetched ≠ relied</span><span>changed ≠ invalid</span><span>stale ≠ false</span><span>EXPOSED_TO_MODEL ≠ RELIED</span>
      </div>`;
    onboarding.after(panel);
    window.bindNavigation?.();
  }

  function queueRender() {
    if (renderQueued) return;
    renderQueued = true;
    queueMicrotask(() => {
      renderQueued = false;
      render();
    });
  }

  function isJourneyNode(node) {
    if (!(node instanceof Element)) return false;
    return node.matches('[data-first-run-journey]') || Boolean(node.closest('[data-first-run-journey]'));
  }

  const observer = new MutationObserver(mutations => {
    const externalMutation = mutations.some(mutation => {
      const changed = [...mutation.addedNodes, ...mutation.removedNodes].filter(node => node instanceof Element);
      return changed.some(node => !isJourneyNode(node));
    });
    if (externalMutation) queueRender();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  addEventListener('popstate', queueRender);
  addEventListener('DOMContentLoaded', queueRender);
})();
