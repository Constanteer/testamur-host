(() => {
  'use strict';

  // The canonical /v1/project projection already composes advisory candidates with
  // immutable affectedness assessment heads. Keep this module presentation-only:
  // overlap is not affectedness, and a recorded assessment is not a trust score.
  let latestSupplyChain = null;
  const nativeFetch = window.fetch.bind(window);

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
        latestSupplyChain = body?.supply_chain || null;
        queueMicrotask(render);
      }
    } catch (_) {
      // Product rendering must never make the underlying request fail.
    }
    return response;
  };

  const stateTone = state => {
    const normalized = String(state || '').toUpperCase();
    if (['CONFIRMED_AFFECTED'].includes(normalized)) return 'warn';
    if (['MITIGATED', 'DISPROVEN', 'NOT_APPLICABLE'].includes(normalized)) return 'good';
    return 'neutral';
  };

  const badge = (label, tone = 'neutral') => `<span class="badge badge-${tone}">${esc(label)}</span>`;

  function subjectRow(subject) {
    const heads = Array.isArray(subject.assessment_heads) ? subject.assessment_heads : [];
    const states = Array.isArray(subject.states) ? subject.states : [];
    const competing = Boolean(subject.has_competing_heads);
    const status = !heads.length
      ? badge('unassessed', 'warn')
      : competing
        ? badge('competing heads', 'warn')
        : states.map(state => badge(state, stateTone(state))).join(' ');
    const headRefs = heads.map(head => {
      const id = head.assessment_id || 'recorded assessment';
      return `<span class="mono">${esc(id)}</span>`;
    }).join(', ');
    return `<div class="advisory-candidate-row advisory-review-subject">
      <span class="alert-mark">${subject.requires_review ? '!' : '✓'}</span>
      <span><strong>${esc(subject.subject_revision || 'component revision')}</strong><small>${heads.length ? `${heads.length} immutable assessment head${heads.length === 1 ? '' : 's'}${headRefs ? ` · ${headRefs}` : ''}` : 'No recorded applicability assessment for this exact component revision.'}</small></span>
      <span>${status}</span>
    </div>`;
  }

  function reviewCard(review) {
    const subjects = Array.isArray(review.subjects) ? review.subjects : [];
    const needsReview = Boolean(review.requires_review);
    return `<article class="advisory-review-card">
      <div class="section-head compact">
        <div><h3>${esc(review.external_id || review.event_revision_id || 'Advisory')}</h3><p>${esc(review.provider || 'provider')} · exact recorded identity overlap</p></div>
        ${badge(needsReview ? 'review required' : 'recorded assessment', needsReview ? 'warn' : 'good')}
      </div>
      <div class="advisory-review-subjects">${subjects.map(subjectRow).join('') || '<p class="form-help">No matching component revisions were projected.</p>'}</div>
      ${review.competing_subject_revision_ids?.length ? `<p class="form-help"><strong>Competing heads preserved.</strong> ${esc(review.competing_subject_revision_ids.length)} component revision(s) have multiple unsuperseded recorded assessments; Testamur does not choose one by insertion order.</p>` : ''}
    </article>`;
  }

  function render() {
    const host = document.querySelector('.supply-chain-advisories');
    if (!host || !latestSupplyChain || host.querySelector('[data-advisory-review-state]')) return;
    const reviews = Array.isArray(latestSupplyChain.advisory_candidates)
      ? latestSupplyChain.advisory_candidates.filter(item => item?.schema === 'testamur.product.advisory-review.v1')
      : [];
    if (!reviews.length) return;

    const required = Number(latestSupplyChain.advisory_review_required_count || 0);
    const competing = Number(latestSupplyChain.advisory_competing_count || 0);
    const surface = document.createElement('div');
    surface.dataset.advisoryReviewState = 'true';
    surface.className = 'advisory-review-state';
    surface.innerHTML = `
      <div class="advisory-review-summary">
        <div><strong>${reviews.length}</strong><span>exact candidate${reviews.length === 1 ? '' : 's'}</span></div>
        <div><strong>${required}</strong><span>requiring review</span></div>
        <div><strong>${competing}</strong><span>with competing heads</span></div>
      </div>
      <p class="supply-chain-advisory-note"><strong>Recorded assessment state.</strong> Identity overlap only nominates work for review. Assessment heads below are immutable recorded conclusions about specific component revisions—not generic verification, validity, or trust scores.</p>
      <div class="advisory-review-list">${reviews.map(reviewCard).join('')}</div>`;
    const note = host.querySelector('.supply-chain-advisory-note');
    (note || host.firstElementChild)?.insertAdjacentElement('afterend', surface);
  }

  new MutationObserver(render).observe(document.documentElement, { childList: true, subtree: true });
})();
