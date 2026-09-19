(() => {
  const enhance = () => {
    const empty = document.querySelector('.supply-chain-empty');
    if (!empty || empty.dataset.guidanceReady === '1') return;
    const command = empty.querySelector('.supply-chain-command code');
    if (!command) return;
    empty.dataset.guidanceReady = '1';

    const actions = document.createElement('div');
    actions.className = 'supply-chain-guidance-actions';
    actions.innerHTML = `
      <button type="button" class="btn btn-primary" data-copy-supply-command>Copy import command</button>
      <a class="btn btn-secondary" data-nav href="/docs#project-monitor">Why is import local?</a>`;
    empty.querySelector('.supply-chain-command')?.after(actions);

    const note = document.createElement('div');
    note.className = 'supply-chain-guidance-note';
    note.innerHTML = `<strong>What happens next</strong><ol><li>Run the command from the repository root.</li><li>Refresh this tab to inspect the recorded manifests and dependency revisions.</li><li>Review advisory candidates explicitly; a package identity match is not an affectedness verdict.</li></ol><p><strong>Rescans are observations.</strong> Every successful scan is recorded even when it sees the same dependency state. An identical rescan does not by itself verify dependencies, establish reliance, or prove that an earlier assessment is still valid.</p>`;
    actions.after(note);

    actions.querySelector('[data-copy-supply-command]')?.addEventListener('click', async event => {
      const button = event.currentTarget;
      try {
        await navigator.clipboard.writeText(command.textContent || '');
        button.textContent = 'Copied';
        window.setTimeout(() => { button.textContent = 'Copy import command'; }, 1600);
      } catch (_) {
        button.textContent = 'Select command above';
      }
    });
  };

  const observer = new MutationObserver(enhance);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  enhance();
})();
