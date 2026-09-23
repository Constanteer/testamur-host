(() => {
  'use strict';

  // The canonical /v1/project projection composes exact advisory identity overlap
  // with immutable affectedness assessment heads. This module may record evidence,
  // but it never lets the browser submit an affectedness verdict or trust score.
  let latestSupplyChain = null;
  let latestProjectUrl = null;
  let projectionVersion = 0;
  let renderQueued = false;
  const nativeFetch = window.fetch.bind(window);

  const esc = value => String(value ?? '').replace(/[&<>\"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;', "'": '&#039;'
  })[char]);

  function projectContext() {
    let source = null;
    try {
      source = new URL(latestProjectUrl || location.href, location.origin);
    } catch (_) {
      return new URLSearchParams();
    }
    const context = new URLSearchParams();
    for (const key of ['project', 'project_id']) {
      const value = source.searchParams.get(key);
      if (value) context.set(key, value);
    }
    return context;
  }

  const objectTab = (ref, tab) => {
    const query = projectContext();
    query.set('tab', tab);
    return `/object/${encodeURIComponent(String(ref || ''))}?${query.toString()}`;
  };

  function queueRender() {
    if (renderQueued) return;
    renderQueued = true;
    queueMicrotask(() => {
      renderQueued = false;
      render();
    });
  }

  window.fetch = async (...args) => {
    const response = await nativeFetch(...args);
    try {
      const input = args[0];
      const url = new URL(typeof input === 'string' ? input : input.url, location.origin);
      if (url.pathname === '/v1/project' && response.ok) {
        const body = await response.clone().json();
        latestProjectUrl = url.toString();
        latestSupplyChain = body?.supply_chain || null;
        projectionVersion += 1;
        queueRender();
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

  const badge = (label, tone = 'neutral') => `<span class=\"badge badge-${tone}\">${esc(label)}</span>`;

  const signalOptions = () => [
    ['', 'No applicability signal yet → records UNKNOWN'],
    ['MATERIAL_PRESENT', 'Relevant material / condition observed'],
    ['MATERIAL_ABSENT', 'Relevant material / condition absent'],
    ['MITIGATION_ESTABLISHED', 'Mitigation established'],
    ['OUT_OF_SCOPE', 'Subject is outside the advisory scope'],
    ['INCONCLUSIVE', 'Review was inconclusive'],
    ['SCANNER_NO_MATCH', 'Scanner found no match'],
    ['MITIGATION_DECLARED', 'Mitigation declared (not mechanical proof)'],
  ].map(([value, label]) => `<option value=\"${esc(value)}\">${esc(label)}</option>`).join('');

  function assessmentForm(review, subject) {
    const heads = Array.isArray(subject.assessment_heads) ? subject.assessment_heads : [];
    const supersedes = heads.map(head => {
      const id = String(head.assessment_id || '');
      const state = String(head.state || 'UNKNOWN');
      return id ? `<option value=\"${esc(id)}\">${esc(state)} · ${esc(id)}</option>` : '';
    }).join('');
    const label = heads.length ? 'Record new evidence' : 'Assess candidate';
    return `<details class=\"advisory-assessment\">
      <summary>${esc(label)}</summary>
      <form data-advisory-assessment
        data-event-revision=\"${esc(review.event_revision_id || '')}\"
        data-subject-revision=\"${esc(subject.subject_revision || '')}\">
        <div class=\"advisory-assessment-grid\">
          <label><span>Evidence signal</span><select name=\"signal\">${signalOptions()}</select></label>
          <label><span>Evidence class</span><select name=\"evidence_class\">
            <option value=\"OBSERVED\">Observed</option>
            <option value=\"DERIVED\">Derived by analyzer</option>
            <option value=\"DECLARED\">Declared</option>
          </select></label>
          <label class=\"wide\"><span>Evidence reference</span><input name=\"evidence_ref\" placeholder=\"test:integration-142, scan:run-2026-09-19, note:review-7\" /></label>
          <label><span>Analyzer</span><input name=\"analyzer\" placeholder=\"required for DERIVED\" /></label>
          <label><span>Analyzer version</span><input name=\"analyzer_version\" placeholder=\"required for DERIVED\" /></label>
          <label class=\"wide\"><span>Review basis</span><input name=\"basis_ref\" required placeholder=\"review:ticket-142 or note:manual-review\" /></label>
          ${heads.length ? `<label class=\"wide\"><span>Supersession</span><select name=\"supersedes_assessment_id\">
            <option value=\"\">Record an independent head</option>${supersedes}
          </select></label>` : ''}
        </div>
        <p class=\"form-help\">You record evidence and provenance, not a verdict. Testamur resolves affectedness from the supplied evidence. Leaving the signal empty records an explicit UNKNOWN assessment; scanner non-detection is not disproof.</p>
        <div class=\"advisory-assessment-submit\">
          <button class=\"btn btn-primary\" type=\"submit\">Record assessment</button>
          <span data-assessment-status aria-live=\"polite\"></span>
        </div>
      </form>
    </details>`;
  }

  function subjectRow(review, subject) {
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
      return `<span class=\"mono\">${esc(id)}</span>`;
    }).join(', ');
    const ref = subject.subject_revision || '';
    return `<div class=\"advisory-candidate-row advisory-review-subject\">
      <span class=\"alert-mark\">${subject.requires_review ? '!' : '✓'}</span>
      <span>
        <strong>${esc(ref || 'component revision')}</strong>
        <small>${heads.length ? `${heads.length} immutable assessment head${heads.length === 1 ? '' : 's'}${headRefs ? ` · ${headRefs}` : ''}` : 'No recorded applicability assessment for this exact component revision.'}</small>
        <span class=\"advisory-review-links\">
          <a href=\"${esc(objectTab(ref, 'history'))}\">History / Compare</a>
          <a href=\"${esc(objectTab(ref, 'impact'))}\">Impact</a>
          <a href=\"${esc(objectTab(ref, 'revalidate'))}\">Revalidation</a>
        </span>
      </span>
      <span class=\"advisory-review-actions\">${status}${assessmentForm(review, subject)}</span>
    </div>`;
  }

  function reviewCard(review) {
    const subjects = Array.isArray(review.subjects) ? review.subjects : [];
    const needsReview = Boolean(review.requires_review);
    return `<article class=\"advisory-review-card\">
      <div class=\"section-head compact\">
        <div><h3>${esc(review.external_id || review.event_revision_id || 'Advisory')}</h3><p>${esc(review.provider || 'provider')} · exact recorded identity overlap</p></div>
        ${badge(needsReview ? 'review required' : 'recorded assessment', needsReview ? 'warn' : 'good')}
      </div>
      <div class=\"advisory-review-subjects\">${subjects.map(subject => subjectRow(review, subject)).join('') || '<p class=\"form-help\">No matching component revisions were projected.</p>'}</div>
      ${review.competing_subject_revision_ids?.length ? `<p class=\"form-help\"><strong>Competing heads preserved.</strong> ${esc(review.competing_subject_revision_ids.length)} component revision(s) have multiple unsuperseded recorded assessments; Testamur does not choose one by insertion order.</p>` : ''}
    </article>`;
  }

  async function refreshProjection() {
    if (!latestProjectUrl) return;
    const response = await nativeFetch(latestProjectUrl, { headers: { Accept: 'application/json' } });
    if (!response.ok) return;
    const body = await response.json();
    latestSupplyChain = body?.supply_chain || null;
    projectionVersion += 1;
    queueRender();
  }

  function bindAssessmentForms(surface) {
    surface.querySelectorAll('[data-advisory-assessment]').forEach(form => {
      form.onsubmit = async event => {
        event.preventDefault();
        const submit = form.querySelector('button[type=\"submit\"]');
        const status = form.querySelector('[data-assessment-status]');
        const data = new FormData(form);
        const signal = String(data.get('signal') || '').trim();
        const evidenceClass = String(data.get('evidence_class') || 'OBSERVED').trim();
        const evidenceRef = String(data.get('evidence_ref') || '').trim();
        const basisRef = String(data.get('basis_ref') || '').trim();
        const analyzer = String(data.get('analyzer') || '').trim();
        const analyzerVersion = String(data.get('analyzer_version') || '').trim();
        const supersedes = String(data.get('supersedes_assessment_id') || '').trim();

        if (!basisRef) {
          status.textContent = 'Review basis is required.';
          return;
        }
        if (signal && !evidenceRef) {
          status.textContent = 'Evidence reference is required when a signal is selected.';
          return;
        }
        if (signal && evidenceClass === 'DERIVED' && (!analyzer || !analyzerVersion)) {
          status.textContent = 'Derived evidence requires analyzer and analyzer version.';
          return;
        }

        const evidence = [];
        if (signal) {
          const item = {
            ref: evidenceRef,
            signal,
            evidence_class: evidenceClass,
          };
          if (evidenceClass === 'DERIVED') {
            item.analyzer = analyzer;
            item.analyzer_version = analyzerVersion;
          }
          evidence.push(item);
        }

        const payload = {
          event_revision_id: form.dataset.eventRevision,
          subject_revision: form.dataset.subjectRevision,
          evidence,
          basis: [{ kind: 'manual_review', ref: basisRef }],
        };
        if (supersedes) payload.supersedes_assessment_id = supersedes;

        submit.disabled = true;
        status.textContent = 'Recording…';
        try {
          const response = await nativeFetch('/v1/advisory-assessments', {
            method: 'POST',
            headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          let body = null;
          try { body = await response.json(); } catch (_) {}
          if (!response.ok) {
            throw new Error(body?.error?.message || `HTTP ${response.status}`);
          }
          status.textContent = `Recorded ${body?.state || 'assessment'}. Refreshing project…`;
          await refreshProjection();
        } catch (error) {
          status.textContent = error?.message || String(error);
        } finally {
          submit.disabled = false;
        }
      };
    });
  }

  function render() {
    const host = document.querySelector('.supply-chain-advisories');
    if (!host || !latestSupplyChain) return;

    const reviews = Array.isArray(latestSupplyChain.advisory_candidates)
      ? latestSupplyChain.advisory_candidates.filter(item => item?.schema === 'testamur.product.advisory-review.v1')
      : [];
    if (!reviews.length) return;

    const existing = host.querySelector('[data-advisory-review-state]');
    if (existing?.dataset.projectionVersion === String(projectionVersion)) return;
    existing?.remove();

    const required = Number(latestSupplyChain.advisory_review_required_count || 0);
    const competing = Number(latestSupplyChain.advisory_competing_count || 0);
    const surface = document.createElement('div');
    surface.dataset.advisoryReviewState = 'true';
    surface.dataset.projectionVersion = String(projectionVersion);
    surface.className = 'advisory-review-state';
    surface.innerHTML = `
      <div class=\"advisory-review-summary\">
        <div><strong>${reviews.length}</strong><span>exact candidate${reviews.length === 1 ? '' : 's'}</span></div>
        <div><strong>${required}</strong><span>requiring review</span></div>
        <div><strong>${competing}</strong><span>with competing heads</span></div>
      </div>
      <p class=\"supply-chain-advisory-note\"><strong>Recorded assessment state.</strong> Identity overlap only nominates work for review. Assessment heads below are immutable recorded conclusions about specific component revisions—not generic verification, validity, or trust scores.</p>
      <div class=\"advisory-review-list\">${reviews.map(reviewCard).join('')}</div>
      <div class=\"advisory-review-next form-help\">
        <strong>Review loop.</strong> Inspect History/Compare for the exact revision, record applicability evidence here, then follow Impact and Revalidation. A changed revision is not automatically invalid; an unassessed candidate is not an affectedness verdict; an assessment records scoped evidence rather than global truth.
      </div>`;
    const note = host.querySelector('.supply-chain-advisory-note');
    (note || host.firstElementChild)?.insertAdjacentElement('afterend', surface);
    bindAssessmentForms(surface);
  }

  new MutationObserver(queueRender).observe(document.documentElement, { childList: true, subtree: true });
})();
