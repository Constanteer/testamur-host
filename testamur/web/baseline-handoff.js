(() => {
  'use strict';

  // Presentation-only handoff after first-run onboarding. This infers no
  // verification, reliance, affectedness, validity, or generic trust state.
  function render() {
    document.querySelector('[data-baseline-handoff]')?.remove();
    if (location.pathname !== '/' && location.pathname !== '/app') return;

    const center = document.querySelector('.dashboard-center');
    if (!center || center.querySelector('.onboarding-card') || center.querySelector('.change-guide')) return;

    const feed = center.querySelector('.feed');
    if (!feed) return;
    const hasRecordedObservation = [...feed.querySelectorAll('.feed-item')].some(item => {
      const text = item.textContent || '';
      return /captured a new observation|recorded .*observation/i.test(text);
    });
    if (!hasRecordedObservation) return;

    const card = document.createElement('section');
    card.className = 'baseline-handoff';
    card.dataset.baselineHandoff = 'true';
    card.innerHTML = `
      <div class="baseline-handoff-mark" aria-hidden="true">✓</div>
      <div class="baseline-handoff-copy">
        <span>FIRST BASELINE RECORDED</span>
        <h2>You have a reference point. Nothing has been judged yet.</h2>
        <p>The first observation gives a later revision something concrete to compare against. It is recorded evidence, not a verification result or trust score. When the source changes, review the revision delta before following explicit reliance into impact and revalidation.</p>
        <div class="baseline-handoff-flow" aria-label="What happens next">
          <strong>Revision</strong><i>→</i><strong>Compare</strong><i>→</i><strong>Impact</strong><i>→</i><strong>Revalidation</strong>
        </div>
        <div class="baseline-handoff-actions">
          <a data-nav class="btn btn-primary" href="/monitoring">See monitoring</a>
          <a data-nav class="btn btn-secondary" href="/learn">Learn the review model</a>
          <a data-nav href="/docs">Open docs →</a>
        </div>
        <small>recorded ≠ verified · fetched ≠ relied · changed ≠ invalid · stale ≠ false · EXPOSED_TO_MODEL ≠ RELIED</small>
      </div>`;
    center.prepend(card);
  }

  let queued = false;
  const schedule = () => {
    if (queued) return;
    queued = true;
    queueMicrotask(() => { queued = false; render(); });
  };
  new MutationObserver(schedule).observe(document.documentElement, { childList: true, subtree: true });
  addEventListener('popstate', schedule);
  addEventListener('DOMContentLoaded', schedule);
})();
