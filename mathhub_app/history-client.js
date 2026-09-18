/* Incremental Claim history UI.
   Claim detail responses are intentionally bounded; older immutable Proof and
   Build records are fetched only when a reader asks for them. Full compiler
   logs are fetched only when an individual Build row is expanded. */

const historyState = {
  loadingProofs: new Set(),
  loadingBuilds: new Set(),
  loadingArtifacts: new Set(),
  artifacts: new Map(),
};

function mergeByKey(existing, incoming, key) {
  const seen = new Set(existing.map(item => item[key]));
  for (const item of incoming) {
    if (!seen.has(item[key])) {
      existing.push(item);
      seen.add(item[key]);
    }
  }
}

function findBuildInDetail(detail, buildId) {
  for (const proof of detail?.proofs || []) {
    const build = (proof.builds || []).find(item => item.build_id === buildId);
    if (build) return build;
  }
  return null;
}

async function loadBuildArtifact(claimId, buildId, row) {
  if (!buildId || historyState.loadingArtifacts.has(buildId)) return;
  const log = row?.querySelector(`[data-build-log="${buildId}"]`);
  const cached = historyState.artifacts.get(buildId);
  if (cached) {
    if (log) log.textContent = cached.output || 'No compiler output recorded.';
    return;
  }

  historyState.loadingArtifacts.add(buildId);
  if (log) log.textContent = 'Loading full compiler artifact…';
  try {
    const artifact = await apiJson(`/api/builds/${encodeURIComponent(buildId)}`);
    historyState.artifacts.set(buildId, artifact);
    const detail = registryState.details.get(claimId);
    const build = findBuildInDetail(detail, buildId);
    if (build) Object.assign(build, artifact);
    if (log) log.textContent = artifact.output || 'No compiler output recorded.';
  } catch (err) {
    if (log) log.textContent = `Could not load compiler artifact: ${err.message}`;
  } finally {
    historyState.loadingArtifacts.delete(buildId);
  }
}

async function loadOlderProofs(claimId) {
  const detail = registryState.details.get(claimId);
  const history = detail?.proof_history;
  if (!history?.has_more || !history.next_cursor || historyState.loadingProofs.has(claimId)) return;
  historyState.loadingProofs.add(claimId);
  render();
  try {
    const url = `/api/claims/${claimId}/proofs?limit=12&build_limit=6&cursor=${encodeURIComponent(history.next_cursor)}`;
    const page = await apiJson(url);
    mergeByKey(detail.proofs, page.proofs || [], 'proof_id');
    detail.proof_history = {has_more: !!page.has_more, next_cursor: page.next_cursor || null};
  } catch (err) {
    alert(`Could not load older proofs: ${err.message}`);
  } finally {
    historyState.loadingProofs.delete(claimId);
    render();
  }
}

async function loadOlderBuilds(claimId, proofId) {
  const detail = registryState.details.get(claimId);
  const proof = detail?.proofs?.find(item => item.proof_id === proofId);
  const history = proof?.build_history;
  if (!proof || !history?.has_more || !history.next_cursor || historyState.loadingBuilds.has(proofId)) return;
  historyState.loadingBuilds.add(proofId);
  render();
  try {
    const url = `/api/proofs/${proofId}/builds?limit=20&cursor=${encodeURIComponent(history.next_cursor)}`;
    const page = await apiJson(url);
    mergeByKey(proof.builds, page.builds || [], 'build_id');
    proof.build_history = {has_more: !!page.has_more, next_cursor: page.next_cursor || null};
  } catch (err) {
    alert(`Could not load older builds: ${err.message}`);
  } finally {
    historyState.loadingBuilds.delete(proofId);
    render();
  }
}

function decorateBuildArtifactLoading(claimId, section) {
  section.querySelectorAll('[data-build-record]').forEach(row => {
    if (row.dataset.artifactBound === '1') return;
    row.dataset.artifactBound = '1';
    row.addEventListener('toggle', () => {
      if (!row.open) return;
      const buildId = row.dataset.buildRecord;
      const detail = registryState.details.get(claimId);
      const build = findBuildInDetail(detail, buildId);
      if (!build?.output_available && !historyState.artifacts.has(buildId)) return;
      loadBuildArtifact(claimId, buildId, row);
    });
  });
}

function decorateHistoryPagination() {
  if (state.view !== 'claim' || !registryState.claims.has(state.selected)) return;
  const detail = registryState.details.get(state.selected);
  const section = document.querySelector('.registry-section');
  if (!detail || !section) return;

  decorateBuildArtifactLoading(state.selected, section);

  if (detail.proof_history?.has_more && !section.querySelector('[data-more-proofs]')) {
    const proofHistory = section.querySelector('.proof-history');
    if (proofHistory) {
      const row = document.createElement('div');
      row.className = 'history-more-row';
      const loading = historyState.loadingProofs.has(state.selected);
      row.innerHTML = `<button class="btn" data-more-proofs ${loading ? 'disabled' : ''}>${loading ? 'Loading…' : 'Older proofs'}</button><span>Earlier immutable Proof records stay out of the initial Claim payload.</span>`;
      proofHistory.after(row);
      row.querySelector('[data-more-proofs]')?.addEventListener('click', () => loadOlderProofs(state.selected));
    }
  }

  for (const proof of detail.proofs || []) {
    if (!proof.build_history?.has_more) continue;
    const buildButton = section.querySelector(`[data-build-proof="${proof.proof_id}"]`);
    if (!buildButton || buildButton.parentElement?.querySelector('[data-more-builds]')) continue;
    const button = document.createElement('button');
    button.className = 'btn btn-quiet-history';
    button.dataset.moreBuilds = proof.proof_id;
    const loading = historyState.loadingBuilds.has(proof.proof_id);
    button.disabled = loading;
    button.textContent = loading ? 'Loading…' : 'Older builds';
    buildButton.after(button);
    button.addEventListener('click', () => loadOlderBuilds(state.selected, proof.proof_id));
  }
}

const historyBaseRender = render;
render = function historyRender() {
  historyBaseRender();
  decorateHistoryPagination();
};
