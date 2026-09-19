(() => {
  'use strict';

  // Presentation-only guidance over the canonical object model. It deliberately
  // does not infer verification, reliance, affectedness, or validity.
  const steps = [
    ['source', 'Source', 'Identify the upstream object you are reviewing.'],
    ['history', 'Revision', 'Choose the recorded state whose evidence you want to inspect.'],
    ['compare', 'Compare', 'Read the mechanical delta before making a judgment.'],
    ['impact', 'Impact', 'Follow explicit recorded reliance to find work that deserves attention.'],
    ['revalidate', 'Revalidation', 'Record the scoped check or reconsideration that follows.'],
  ];

  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
  })[char]);

  function currentStage() {
    const path = location.pathname;
    const tab = new URLSearchParams(location.search).get('tab');
    if (path.startsWith('/impact/')) return 'impact';
    if (!path.startsWith('/object/')) return null;
    if (tab === 'history') return 'history';
    if (tab === 'impact') return 'impact';
    if (tab === 'revalidate') return 'revalidate';
    if (tab === 'compare') return 'compare';
    return 'source';
  }

  function ref() {
    if (location.pathname.startsWith('/object/')) return decodeURIComponent(location.pathname.slice('/object/'.length));
    if (location.pathname.startsWith('/impact/')) return decodeURIComponent(location.pathname.slice('/impact/'.length));
    return '';
  }

  function href(stage, objectRef) {
    const base = `/object/${encodeURIComponent(objectRef)}`;
    if (stage === 'source') return base;
    return `${base}?tab=${stage}`;
  }

  function render() {
    const stage = currentStage();
    const objectRef = ref();
    document.querySelector('[data-contextual-workflow-guide]')?.remove();
    if (!stage || !objectRef) return;

    const main = document.querySelector('main.shell');
    if (!main) return;
    const activeIndex = Math.max(0, steps.findIndex(([key]) => key === stage));
    const next = steps[activeIndex + 1];
    const active = steps[activeIndex];
    const guide = document.createElement('aside');
    guide.className = 'contextual-workflow-guide';
    guide.dataset.contextualWorkflowGuide = 'true';
    guide.setAttribute('aria-label', 'Evidence review workflow');
    guide.innerHTML = `
      <div class="contextual-guide-head">
        <div><span class="contextual-guide-kicker">REVIEW WORKFLOW · ${activeIndex + 1}/5</span><strong>${esc(active[1])}</strong><p>${esc(active[2])}</p></div>
        <a data-nav href="/learn">Why these steps?</a>
      </div>
      <nav class="contextual-guide-steps" aria-label="Source to revalidation">
        ${steps.map(([key, label], index) => `<a data-nav href="${esc(href(key, objectRef))}" class="${index === activeIndex ? 'active' : ''} ${index < activeIndex ? 'visited' : ''}"${index === activeIndex ? ' aria-current="step"' : ''}><span>${index < activeIndex ? '✓' : index + 1}</span>${esc(label)}</a>`).join('')}
      </nav>
      <div class="contextual-guide-boundary">
        <span>recorded ≠ verified</span><span>fetched ≠ relied</span><span>changed ≠ invalid</span><span>stale ≠ false</span>
      </div>
      ${next ? `<div class="contextual-guide-next"><span>Next: ${esc(next[1])}</span><a data-nav class="btn btn-secondary" href="${esc(href(next[0], objectRef))}">${esc(next[1])} →</a></div>` : '<div class="contextual-guide-next"><span>Review loop complete when the revalidation evidence is recorded.</span><a data-nav href="/projects">Back to projects →</a></div>'}`;
    main.prepend(guide);
  }

  const observer = new MutationObserver(() => queueMicrotask(render));
  observer.observe(document.documentElement, { childList: true, subtree: true });
  addEventListener('popstate', render);
  addEventListener('DOMContentLoaded', render);
})();
