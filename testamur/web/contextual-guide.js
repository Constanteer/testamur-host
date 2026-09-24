(() => {
  'use strict';

  // Presentation-only guidance over the canonical object model. It deliberately
  // does not infer verification, reliance, affectedness, or validity.
  const steps = [
    ['source', 'Source', 'Identify the upstream object you are reviewing and inspect what was actually recorded.'],
    ['history', 'Revision', 'Choose the recorded revision whose evidence you want to inspect; a revision is not a verification result.'],
    ['compare', 'Compare', 'Read the mechanical delta before making a judgment; change alone does not imply invalidity.'],
    ['impact', 'Impact', 'Follow explicit recorded reliance from this object to downstream work. Inspect the edge basis and recorded revision before deciding what the selected change means. The basis explains why reliance was recorded, not whether this change affected it. An edge says reliance was recorded; it does not say the change affected, invalidated, or disproved that downstream work.'],
    ['revalidate', 'Revalidation', 'Record the scoped check or reconsideration that follows; recording evidence does not itself verify it.'],
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

  function selectedContext(keys) {
    const current = new URLSearchParams(location.search);
    const context = new URLSearchParams();
    for (const key of keys) {
      const value = current.get(key);
      if (value) context.set(key, value);
    }
    return context;
  }

  function projectContext() {
    return selectedContext(['project', 'project_id']);
  }

  function reviewScopeContext() {
    return selectedContext(['project', 'project_id', 'dependency', 'component', 'from', 'to']);
  }

  function href(stage, objectRef) {
    const base = `/object/${encodeURIComponent(objectRef)}`;
    const query = ['compare', 'impact', 'revalidate'].includes(stage) ? reviewScopeContext() : projectContext();
    if (stage !== 'source') query.set('tab', stage);
    const suffix = query.toString();
    return suffix ? `${base}?${suffix}` : base;
  }

  function objectHref(objectRef) {
    const query = reviewScopeContext();
    const suffix = query.toString();
    const base = `/object/${encodeURIComponent(objectRef)}`;
    return suffix ? `${base}?${suffix}` : base;
  }

  function docsHref(stage) {
    if (stage === 'source' || stage === 'history') return '/docs#source-revision';
    if (stage === 'compare') return '/docs#change';
    if (stage === 'impact') return '/docs#impact';
    if (stage === 'revalidate') return '/docs#revalidation';
    return '/docs';
  }

  function stageAction(stage) {
    if (stage === 'impact') return '<span class="contextual-guide-action">For each downstream edge: open its recorded basis/revision first, then decide whether the selected change warrants scoped revalidation.</span>';
    return '';
  }

  function projectReturnHref() {
    const params = projectContext();
    const project = params.get('project') || params.get('project_id');
    return project ? `/projects/${encodeURIComponent(project)}` : '/projects';
  }

  function reviewContextLabel(objectRef) {
    const params = reviewScopeContext();
    const project = params.get('project') || params.get('project_id');
    const projectPart = project ? ` · Project ${project}` : '';
    const dependency = params.get('dependency') || params.get('component');
    const from = params.get('from');
    const to = params.get('to');
    const subjectPart = dependency ? ` · ${dependency}` : '';
    const pairPart = from || to ? ` · ${from || 'unknown'} → ${to || 'unknown'}` : '';
    return `Reviewing ${objectRef}${projectPart}${subjectPart}${pairPart}`;
  }

  function firstSupplied(item, keys) {
    for (const key of keys) {
      const value = item?.[key];
      if (value !== undefined && value !== null && String(value).trim()) return String(value);
    }
    return '';
  }

  async function enhanceImpactRows(objectRef) {
    if (currentStage() !== 'impact') return;
    const list = document.querySelector('.impact-list');
    if (!list || list.dataset.provenanceEnhanced === 'true' || list.dataset.provenanceLoading === 'true') return;
    list.dataset.provenanceLoading = 'true';
    try {
      const response = await fetch(`/v1/impact?ref=${encodeURIComponent(objectRef)}`, { headers: { Accept: 'application/json' } });
      const value = await response.json();
      if (!response.ok || value.ok === false) return;
      const items = value.items || value.affected || value.impacts || value.results;
      if (!Array.isArray(items)) return;
      const rows = [...list.querySelectorAll('.impact-row')];
      rows.forEach((row, index) => {
        const item = items[index];
        if (!item || typeof item !== 'object') return;
        const basis = firstSupplied(item, ['basis_ref', 'basis_revision_ref', 'basis_revision', 'recorded_basis_ref', 'revision_ref']);
        const relation = firstSupplied(item, ['relation_id', 'relation_ref', 'relation_kind', 'edge_id', 'edge_kind']);
        const recordedAt = firstSupplied(item, ['recorded_at', 'relation_recorded_at', 'created_at']);
        const detail = document.createElement('div');
        detail.className = 'impact-provenance';
        detail.innerHTML = `
          <span><strong>Recorded basis</strong> ${basis ? `<code>${esc(basis)}</code>` : '<em>Not supplied by this provider</em>'}</span>
          ${relation ? `<span><strong>Relation</strong> <code>${esc(relation)}</code></span>` : ''}
          ${recordedAt ? `<span><strong>Recorded at</strong> ${esc(recordedAt)}</span>` : ''}
          <small>Provenance explains why reliance was recorded; it does not say this change affected or invalidated the downstream work.</small>
          ${basis ? `<a data-nav href="${esc(objectHref(basis))}">Inspect basis →</a>` : ''}`;
        row.append(detail);
      });
      list.dataset.provenanceEnhanced = 'true';
    } catch (_) {
      // The canonical Impact panel remains usable if provenance enhancement fails.
    } finally {
      delete list.dataset.provenanceLoading;
    }
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
        <div><span class="contextual-guide-kicker">REVIEW WORKFLOW · ${activeIndex + 1}/5</span><strong>${esc(active[1])}</strong><p>${esc(active[2])}</p><small class="mono">${esc(reviewContextLabel(objectRef))}</small>${stageAction(stage)}</div>
        <div class="contextual-guide-help"><a data-nav href="/learn">Why these steps?</a><a data-nav href="${esc(docsHref(stage))}">${esc(active[1])} docs</a></div>
      </div>
      <nav class="contextual-guide-steps" aria-label="Source to revalidation">
        ${steps.map(([key, label], index) => `<a data-nav href="${esc(href(key, objectRef))}" class="${index === activeIndex ? 'active' : ''} ${index < activeIndex ? 'visited' : ''}"${index === activeIndex ? ' aria-current="step"' : ''}><span>${index < activeIndex ? '✓' : index + 1}</span>${esc(label)}</a>`).join('')}
      </nav>
      <div class="contextual-guide-boundary">
        <span>recorded ≠ verified</span><span>fetched ≠ relied</span><span>changed ≠ invalid</span><span>stale ≠ false</span><span>EXPOSED_TO_MODEL ≠ RELIED</span>
      </div>
      ${next ? `<div class="contextual-guide-next"><span>Next: ${esc(next[1])}</span><a data-nav class="btn btn-secondary" href="${esc(href(next[0], objectRef))}">${esc(next[1])} →</a></div>` : `<div class="contextual-guide-next"><span>Scoped revalidation evidence is now recorded. Return to the project to continue reviewing other bindings or advisory candidates; this completion is not a project-wide trust verdict.</span><a data-nav href="${esc(projectReturnHref())}">Back to project →</a></div>`}`;
    main.prepend(guide);
    enhanceImpactRows(objectRef);
  }

  function isGuideNode(node) {
    if (!(node instanceof Element)) return false;
    return node.matches('[data-contextual-workflow-guide]') || Boolean(node.closest('[data-contextual-workflow-guide]'));
  }

  function onlyGuideMutations(records) {
    return records.length > 0 && records.every((record) => {
      const changed = [...record.addedNodes, ...record.removedNodes];
      return changed.length > 0 && changed.every(isGuideNode);
    });
  }

  let renderQueued = false;
  function queueRender() {
    if (renderQueued) return;
    renderQueued = true;
    queueMicrotask(() => {
      renderQueued = false;
      render();
    });
  }

  const observer = new MutationObserver((records) => {
    if (onlyGuideMutations(records)) return;
    queueRender();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  addEventListener('popstate', queueRender);
  addEventListener('DOMContentLoaded', queueRender);
})();
