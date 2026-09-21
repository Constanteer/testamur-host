(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));

  const transitionLabel = value => ({
    upgraded: 'upgraded',
    downgraded: 'downgraded',
    'version-changed': 'version changed',
    version_changed: 'version changed'
  }[value] || String(value || 'changed').replaceAll('_', ' '));

  const transitionRows = surface => {
    if (surface.querySelector('.supply-chain-transition-list')) return;
    const raw = surface.dataset.supplyChainDiff || surface.dataset.diff;
    if (!raw) return;
    let diff;
    try { diff = JSON.parse(raw); } catch (_) { return; }
    const changed = diff.dependencies_changed || diff.dependenciesChanged || diff.changed_dependencies || [];
    if (!Array.isArray(changed) || !changed.length) return;

    const list = document.createElement('div');
    list.className = 'supply-chain-transition-list';
    list.setAttribute('aria-label', 'Dependency version transitions');
    list.innerHTML = changed.map(item => {
      const name = item.name || item.package || item.dependency || 'dependency';
      const before = item.before_version ?? item.old_version ?? item.before ?? '—';
      const after = item.after_version ?? item.new_version ?? item.after ?? '—';
      const transition = item.version_transition || item.transition || 'version-changed';
      return `<div class="supply-chain-transition-row"><code class="supply-chain-transition-package">${esc(name)}</code><span class="supply-chain-transition-version">${esc(before)}</span><span class="supply-chain-transition-arrow" aria-hidden="true">→</span><span class="supply-chain-transition-version">${esc(after)}</span><span class="supply-chain-transition-kind" data-transition="${esc(transition)}">${esc(transitionLabel(transition))}</span></div>`;
    }).join('');
    surface.append(list);
  };

  const addCompareSemantics = () => {
    document.querySelectorAll('.supply-chain-diff, .supply-chain-compare, [data-supply-chain-diff]').forEach(surface => {
      transitionRows(surface);
      if (surface.dataset.compareGuidanceReady === '1') return;
      surface.dataset.compareGuidanceReady = '1';
      const note = document.createElement('div');
      note.className = 'supply-chain-guidance-note supply-chain-compare-note';
      note.innerHTML = `<strong>How to read this comparison</strong><p><code>upgraded</code>, <code>downgraded</code>, and <code>version-changed</code> describe only the mechanical version transition between two recorded scan observations. An upgrade is not a safety verdict; a downgrade is not a vulnerability verdict; a changed dependency is not therefore invalid or affected.</p><p>Use the transition as evidence for the next review step: inspect impact and advisory candidates, then record scoped revalidation evidence where appropriate.</p>`;
      surface.prepend(note);
    });
  };

  const enhance = () => {
    const empty = document.querySelector('.supply-chain-empty');
    if (empty && empty.dataset.guidanceReady !== '1') {
      const command = empty.querySelector('.supply-chain-command code');
      if (command) {
        empty.dataset.guidanceReady = '1';
        const actions = document.createElement('div');
        actions.className = 'supply-chain-guidance-actions';
        actions.innerHTML = `<button type="button" class="btn btn-primary" data-copy-supply-command>Copy import command</button><a class="btn btn-secondary" data-nav href="/docs#project-monitor">Why is import local?</a>`;
        empty.querySelector('.supply-chain-command')?.after(actions);
        const note = document.createElement('div');
        note.className = 'supply-chain-guidance-note';
        note.innerHTML = `<strong>What happens next</strong><ol><li>Run the command from the repository root.</li><li>Refresh this tab to inspect the recorded manifests and dependency revisions.</li><li>Review advisory candidates explicitly; a package identity match is not an affectedness verdict.</li></ol><p><strong>Rescans are observations.</strong> Every successful scan is recorded even when it sees the same dependency state. An identical rescan does not by itself verify dependencies, establish reliance, or prove that an earlier assessment is still valid.</p>`;
        actions.after(note);
        actions.querySelector('[data-copy-supply-command]')?.addEventListener('click', async event => {
          const button = event.currentTarget;
          try { await navigator.clipboard.writeText(command.textContent || ''); button.textContent = 'Copied'; window.setTimeout(() => { button.textContent = 'Copy import command'; }, 1600); }
          catch (_) { button.textContent = 'Select command above'; }
        });
      }
    }
    addCompareSemantics();
  };

  const observer = new MutationObserver(enhance);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  enhance();
})();
