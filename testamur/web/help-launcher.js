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

  function contextualLink() {
    const path = location.pathname;
    const query = new URLSearchParams(location.search);
    if (/^\/projects\/[^/]+\/?$/.test(path) && query.get('tab') === 'supply-chain') {
      return ['/learn', 'Read Compare → Impact', 'Interpret scan changes without turning them into verdicts'];
    }
    if (/^\/sources\/[^/]+\/?$/.test(path)) {
      return ['/learn', 'Read Source → Revision', 'Separate source identity from recorded observations'];
    }
    if (/^\/revisions\/[^/]+\/?$/.test(path)) {
      return ['/learn', 'Read Revision → Impact', 'Trace consequences before recording revalidation'];
    }
    if (path === '/integrations') {
      return ['/quickstart', 'Run the 5-minute quickstart', 'Use Codex / MCP inside the same evidence model'];
    }
    return null;
  }

  function render() {
    document.querySelector('[data-help-launcher]')?.remove();
    if (location.pathname === '/signin' || location.pathname === '/signup') return;
    const launcher = document.createElement('aside');
    launcher.className = 'help-launcher';
    launcher.dataset.helpLauncher = 'true';
    const contextual = contextualLink();
    const visibleLinks = contextual ? [contextual, ...links.filter(([href]) => href !== contextual[0])] : links;
    launcher.innerHTML = `
      <details>
        <summary aria-label="Open Testamur help">Help</summary>
        <div class="help-launcher-panel">
          <div class="help-launcher-head"><span>HELP · DOCS · LEARN</span><strong>${contextual ? 'What should I understand here?' : 'Where do I go next?'}</strong></div>
          <nav aria-label="Testamur help resources">
            ${visibleLinks.map(([href, label, note], index) => `<a data-nav href="${href}"${contextual && index === 0 ? ' data-contextual-help="true"' : ''}><strong>${label}</strong><small>${note}</small></a>`).join('')}
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
