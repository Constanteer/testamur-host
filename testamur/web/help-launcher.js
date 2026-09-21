(() => {
  'use strict';

  // Navigation-only help surface. It never derives verification, reliance,
  // affectedness, validity, staleness, or a generic trust score.
  const links = [
    ['/quickstart', '5-minute quickstart', 'Project → monitor → baseline'],
    ['/demo', 'Example project', 'Baseline → change → Compare → review'],
    ['/learn', 'Learn the model', 'Source → Revision → Compare → Impact → Revalidation'],
    ['/integrations', 'Codex / MCP', 'Agent and tool entry points'],
    ['/docs', 'Docs', 'Reference and operational guidance'],
  ];

  function render() {
    document.querySelector('[data-help-launcher]')?.remove();
    if (location.pathname === '/login' || location.pathname === '/signup') return;
    const launcher = document.createElement('aside');
    launcher.className = 'help-launcher';
    launcher.dataset.helpLauncher = 'true';
    launcher.innerHTML = `
      <details>
        <summary aria-label="Open Testamur help">Help</summary>
        <div class="help-launcher-panel">
          <div class="help-launcher-head"><span>HELP · DOCS · LEARN</span><strong>Where do I go next?</strong></div>
          <nav aria-label="Testamur help resources">
            ${links.map(([href, label, note]) => `<a data-nav href="${href}"><strong>${label}</strong><small>${note}</small></a>`).join('')}
          </nav>
          <small class="help-launcher-boundary">recorded ≠ verified · fetched ≠ relied · changed ≠ invalid · stale ≠ false · EXPOSED_TO_MODEL ≠ RELIED</small>
        </div>
      </details>`;
    document.body.append(launcher);
    window.bindNavigation?.();
  }

  addEventListener('DOMContentLoaded', render);
  addEventListener('popstate', render);
})();
