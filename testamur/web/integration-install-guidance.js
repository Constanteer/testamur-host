(() => {
  'use strict';

  // Presentation-only installation guidance. This surface does not infer
  // verification, reliance, affectedness, validity, or trust from installation.
  const SOURCE_INSTALL = 'git clone https://github.com/Constanteer/testamur.git && cd testamur && python3 -m venv .venv && . .venv/bin/activate && python -m pip install -e .';
  const INSTALL_CHECK = 'python --version && python -m pip show testamur && command -v testamur && command -v testamur-gateway-mcp';
  const MCP_SMOKE = 'printf \'%s\\n\' \'{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{}}\' | testamur-gateway-mcp';

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
      </div>
      <div class="integration-manual-step">
        <strong>Host says the command is missing?</strong>
        <span>Run this in the same shell environment the host will inherit. It shows the Python version, installed Testamur package, and the exact executable paths. A missing line is an installation/PATH problem to fix before debugging MCP configuration.</span>
      </div>
      <div class="integration-command">
        <div><span>Diagnose installation</span><code>${INSTALL_CHECK}</code></div>
        <button class="btn btn-secondary btn-compact" type="button" data-copy-check-command>Copy check</button>
      </div>
      <div class="integration-manual-step">
        <strong>Executable exists, but the MCP host still cannot connect?</strong>
        <span>Run a protocol-level discovery request outside Codex/Claude/OpenCode. A JSON-RPC response with supportedVersions and capabilities means the Testamur MCP process can start and speak its current MCP protocol; if this succeeds while the host still fails, debug the host command, environment, working directory, or stdio configuration next. This discovery only diagnoses transport/bootstrap — it does not verify any source or establish reliance.</span>
      </div>
      <div class="integration-command">
        <div><span>Smoke-test MCP discovery</span><code>${MCP_SMOKE}</code></div>
        <button class="btn btn-secondary btn-compact" type="button" data-copy-mcp-smoke>Copy smoke test</button>
      </div>`;
    prerequisite.append(block);

    const copy = (selector, text) => {
      block.querySelector(selector)?.addEventListener('click', async event => {
        const button = event.currentTarget;
        const previous = button.textContent;
        try {
          await navigator.clipboard.writeText(text);
          button.textContent = 'Copied';
        } catch (_) {
          button.title = text;
          button.textContent = 'Copy failed';
        }
        window.setTimeout(() => { button.textContent = previous; }, 1400);
      });
    };
    copy('[data-copy-install-command]', SOURCE_INSTALL);
    copy('[data-copy-check-command]', INSTALL_CHECK);
    copy('[data-copy-mcp-smoke]', MCP_SMOKE);
  }

  addEventListener('DOMContentLoaded', enhance);
  addEventListener('popstate', enhance);
  new MutationObserver(enhance).observe(document.documentElement, { childList: true, subtree: true });
})();
