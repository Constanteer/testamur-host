(() => {
  'use strict';

  // Presentation-only installation guidance. This surface does not infer
  // verification, reliance, affectedness, validity, or trust from installation.
  const SOURCE_INSTALL = 'git clone https://github.com/Constanteer/testamur.git && cd testamur && python3 -m venv .venv && . .venv/bin/activate && python -m pip install -e .';

  function enhance() {
    if (location.pathname !== '/integrations') return;
    const prerequisite = document.querySelector('.integration-prereq');
    if (!prerequisite || prerequisite.dataset.installGuidance === 'true') return;
    prerequisite.dataset.installGuidance = 'true';

    const oldNote = prerequisite.querySelector('.integration-note');
    if (oldNote) oldNote.remove();

    const block = document.createElement('div');
    block.className = 'integration-install-guidance';
    block.innerHTML = `
      <div class="integration-manual-step">
        <strong>New machine? Install from source first</strong>
        <span>Testamur currently ships from its canonical repository. Create an isolated Python 3.11+ environment, install the checkout, then verify both executables before configuring a host.</span>
      </div>
      <div class="integration-command">
        <div><span>Install core + gateway</span><code>${SOURCE_INSTALL}</code></div>
        <button class="btn btn-secondary btn-compact" type="button" data-copy-install-command>Copy install</button>
      </div>
      <div class="integration-manual-step">
        <strong>Already have a checkout?</strong>
        <span>Activate its environment and run <code>python -m pip install -e .</code>. Do not add Codex/MCP configuration until <code>command -v testamur</code> and <code>command -v testamur-gateway-mcp</code> both succeed.</span>
      </div>`;
    prerequisite.append(block);

    block.querySelector('[data-copy-install-command]')?.addEventListener('click', async event => {
      const button = event.currentTarget;
      const previous = button.textContent;
      try {
        await navigator.clipboard.writeText(SOURCE_INSTALL);
        button.textContent = 'Copied';
      } catch (_) {
        button.title = SOURCE_INSTALL;
        button.textContent = 'Copy failed';
      }
      window.setTimeout(() => { button.textContent = previous; }, 1400);
    });
  }

  addEventListener('DOMContentLoaded', enhance);
  addEventListener('popstate', enhance);
  new MutationObserver(enhance).observe(document.documentElement, { childList: true, subtree: true });
})();
