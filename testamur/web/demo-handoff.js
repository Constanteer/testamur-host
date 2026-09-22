(() => {
  'use strict';

  // Presentation-only handoff. The demo never writes workspace data and this
  // guide does not infer verification, reliance, affectedness, or validity.
  function render() {
    document.querySelector('[data-demo-real-handoff]')?.remove();
    if (location.pathname !== '/demo') return;
    const shell = document.querySelector('.demo-shell');
    if (!shell) return;

    const section = document.createElement('section');
    section.className = 'demo-real-handoff';
    section.dataset.demoRealHandoff = 'true';
    section.innerHTML = `
      <div class="demo-real-handoff-copy">
        <span class="onboarding-kicker">MAKE IT YOURS</span>
        <h2>Move from the example to a real dependency.</h2>
        <p>The demo is read-only. Create a project for your work, then attach a monitor to the upstream source it actually depends on. Testamur will build the revision history from recorded observations.</p>
        <div class="demo-real-handoff-boundary" aria-label="Semantic boundaries">
          <span>recorded ≠ verified</span><span>fetched ≠ relied</span><span>changed ≠ invalid</span><span>stale ≠ false</span>
        </div>
      </div>
      <ol class="demo-real-handoff-steps">
        <li><span>1</span><div><strong>Create your project</strong><small>Name the work; choose Private or Public explicitly.</small></div></li>
        <li><span>2</span><div><strong>Add a real monitor</strong><small>Use a repository, documentation URL, package, or plugin-provided source.</small></div></li>
        <li><span>3</span><div><strong>Record the baseline</strong><small>Refresh once. A recorded observation is evidence, not a verification verdict.</small></div></li>
      </ol>
      <div class="demo-real-handoff-actions">
        <a data-nav class="btn btn-primary" href="/projects/new">Create real project →</a>
        <a data-nav class="btn btn-secondary" href="/integrations">Connect Codex / MCP</a>
      </div>`;
    shell.append(section);
    window.bindNavigation?.();
  }

  function isOwnMutation(mutation) {
    const nodes = [...mutation.addedNodes, ...mutation.removedNodes];
    return nodes.length > 0 && nodes.every((node) =>
      node.nodeType === Node.ELEMENT_NODE &&
      (node.matches?.('[data-demo-real-handoff]') || node.closest?.('[data-demo-real-handoff]'))
    );
  }

  let renderQueued = false;
  const observer = new MutationObserver((mutations) => {
    if (mutations.length && mutations.every(isOwnMutation)) return;
    if (renderQueued) return;
    renderQueued = true;
    queueMicrotask(() => {
      renderQueued = false;
      render();
    });
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  addEventListener('popstate', render);
  addEventListener('DOMContentLoaded', render);
})();
