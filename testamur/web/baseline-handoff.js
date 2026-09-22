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
    const recordedObservations = [...feed.querySelectorAll('.feed-item')].filter(item => {
      const text = item.textContent || '';
      return /captured a new observation|recorded .*observation/i.test(text);
    });
    if (!recordedObservations.length) return;

    const hasComparisonCandidate = recordedObservations.length >= 2;
    const card = document.createElement('section');
    card.className = 'baseline-handoff';
    card.dataset.baselineHandoff = 'true';
    card.innerHTML = hasComparisonCandidate ? `
      <div class="baseline-handoff-mark" aria-hidden="true">↔</div>
      <div class="baseline-handoff-copy">
        <span>ANOTHER OBSERVATION IS RECORDED</span>
        <h2>You can compare revisions now. A difference is not a verdict.</h2>
        <p>There is now more than one recorded observation in this activity view. Open monitoring to inspect the revision history and mechanical delta, then follow explicit reliance into Impact and Revalidation where it exists. A changed source is not automatically invalid.</p>
        <div class="baseline-handoff-flow" aria-label="Review the new observation">
          <strong>Revision</strong><i>→</i><strong>Compare</strong><i>→</i><strong>Impact</strong><i>→</i><strong>Revalidation</strong>
        </div>
        <div class="baseline-handoff-actions">
          <a data-nav class="btn btn-primary" href="/monitoring">Compare observations</a>
          <a data-nav class="btn btn-secondary" href="/learn">Learn the review model</a>
          <a data-nav href="/docs">Open docs →</a>
        </div>
        <small>recorded ≠ verified · fetched ≠ relied · changed ≠ invalid · stale ≠ false · EXPOSED_TO_MODEL ≠ RELIED</small>
      </div>` : `
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

  function isHandoffNode(node) {
    if (!(node instanceof Element)) return false;
    return node.matches('[data-baseline-handoff]') || Boolean(node.closest('[data-baseline-handoff]'));
  }

  function onlyHandoffMutations(records) {
    return records.length > 0 && records.every(record => {
      if (isHandoffNode(record.target)) return true;
      const changed = [...record.addedNodes, ...record.removedNodes];
      return changed.length > 0 && changed.every(isHandoffNode);
    });
  }

  let queued = false;
  const schedule = () => {
    if (queued) return;
    queued = true;
    queueMicrotask(() => { queued = false; render(); });
  };
  new MutationObserver((records) => {
    // render() removes and recreates this presentation card. Ignore those
    // self-authored mutations so the observer cannot schedule itself forever.
    if (onlyHandoffMutations(records)) return;
    schedule();
  }).observe(document.documentElement, { childList: true, subtree: true });
  addEventListener('popstate', schedule);
  addEventListener('DOMContentLoaded', schedule);
})();
