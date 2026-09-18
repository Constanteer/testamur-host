const root = document.querySelector('#app');
const cache = { dashboard: null };
const accountState = { mode: 'unknown', authenticated: false, user: null, workspaces: [], activeWorkspace: null };

async function loadAccountState() {
  try {
    const response = await fetch('/v1/account/me', { headers: { Accept: 'application/json' } });
    if (response.status === 404) {
      accountState.mode = 'local';
      accountState.authenticated = false;
      accountState.user = null;
      accountState.workspaces = [];
      accountState.activeWorkspace = null;
      return;
    }
    const body = await response.json();
    if (!response.ok || !body.ok) throw new Error(body.error?.message || `HTTP ${response.status}`);
    accountState.mode = body.mode || 'hosted';
    accountState.authenticated = Boolean(body.authenticated);
    accountState.user = body.user || null;
    accountState.workspaces = body.workspaces || [];
    accountState.activeWorkspace = body.active_workspace || null;
  } catch (_) {
    accountState.mode = 'local';
    accountState.authenticated = false;
    accountState.user = null;
    accountState.workspaces = [];
    accountState.activeWorkspace = null;
  }
}

const esc = (value = '') => String(value).replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
}[char]));
const short = (value, length = 72) => {
  const text = String(value ?? '');
  return text.length > length ? `${text.slice(0, length)}…` : text;
};
const pretty = value => JSON.stringify(value, null, 2);
const objectPath = ref => `/object/${encodeURIComponent(ref)}`;
const projectPath = ref => `/projects/${encodeURIComponent(ref)}`;
const params = () => new URLSearchParams(location.search);
const safeNextPath = value => {
  const candidate = String(value || '');
  return candidate.startsWith('/') && !candidate.startsWith('//') ? candidate : '/';
};
const cadenceLabel = seconds => ({
  300: 'every 5m',
  900: 'every 15m',
  3600: 'hourly',
  21600: 'every 6h',
  86400: 'daily',
}[Number(seconds)] || 'manual');


async function api(path, query = {}) {
  const url = new URL(path, location.origin);
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue;
    if (Array.isArray(value)) value.forEach(item => url.searchParams.append(key, item));
    else url.searchParams.set(key, value);
  }
  const response = await fetch(url, { headers: { Accept: 'application/json' } });
  let body;
  try { body = await response.json(); }
  catch (_) { throw new Error(`HTTP ${response.status} without JSON`); }
  if (!body.ok) {
    const error = new Error(body.error?.message || `HTTP ${response.status}`);
    error.code = body.error?.code || 'testamur_error';
    error.status = response.status;
    error.details = body.error?.details;
    throw error;
  }
  return body;
}

async function apiWrite(path, payload) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  let body;
  try { body = await response.json(); }
  catch (_) { throw new Error(`HTTP ${response.status} without JSON`); }
  if (!body.ok) {
    const error = new Error(body.error?.message || `HTTP ${response.status}`);
    error.code = body.error?.code || 'testamur_error';
    error.status = response.status;
    error.details = body.error?.details;
    throw error;
  }
  return body;
}

async function dashboard(force = false) {
  if (force) cache.dashboard = null;
  if (!cache.dashboard) cache.dashboard = await api('/v1/dashboard', { limit: 100 });
  return cache.dashboard;
}

function activeNav(href) {
  const path = location.pathname;
  if (href === '/') return path === '/' || path === '/app';
  if (href === '/projects' && path.startsWith('/object/')) return true;
  return path.startsWith(href);
}

function accountNav() {
  if (accountState.mode === 'hosted' && accountState.authenticated && accountState.user) {
    const user = accountState.user;
    const label = user.display_name || user.username || 'Account';
    const initial = String(label).trim().slice(0, 1).toUpperCase() || 'U';
    const workspaces = accountState.workspaces || [];
    const active = accountState.activeWorkspace || workspaces[0] || null;
    const workspaceOptions = workspaces.map(workspace => {
      const selected = active?.workspace_id === workspace.workspace_id;
      const icon = workspace.kind === 'organization' ? '◇' : '○';
      return `<button type="button" class="workspace-option ${selected ? 'active' : ''}" data-workspace-id="${esc(workspace.workspace_id)}">
        <span aria-hidden="true">${icon}</span>
        <span><strong>${esc(short(workspace.label || workspace.slug || 'Workspace', 28))}</strong><small>${esc(workspace.kind === 'organization' ? `@${workspace.slug} · ${workspace.role}` : 'Personal workspace')}</small></span>
        ${selected ? '<span class="workspace-check" aria-hidden="true">✓</span>' : ''}
      </button>`;
    }).join('');
    return `<details class="user-menu">
      <summary class="user-chip" aria-label="Account and workspace menu">
        <span class="environment-avatar" aria-hidden="true">${esc(initial)}</span>
        <span class="environment-copy"><strong>${esc(short(active?.label || label, 24))}</strong><small>${esc(active?.kind === 'organization' ? `organization · ${active.role}` : `@${user.username || ''}`)}</small></span>
        <span class="menu-caret" aria-hidden="true">▾</span>
      </summary>
      <div class="user-menu-panel">
        <div class="user-menu-identity"><strong>${esc(label)}</strong><small>${esc(user.email || '')}</small></div>
        <div class="workspace-menu"><span class="menu-label">Workspace</span>${workspaceOptions}</div>
        <div class="user-menu-divider"></div>
        <a data-nav href="/users/${encodeURIComponent(user.username || '')}">View profile</a>
        <a data-nav href="/projects">Projects</a>
        <a data-nav href="/settings/organizations">Organizations</a>
        <a data-nav href="/settings/profile">Account settings</a>
        <a data-nav href="/settings/security">Security</a>
        <button type="button" data-signout>Sign out</button>
      </div>
    </details>`;
  }
  if (accountState.mode === 'hosted') {
    return `<div class="auth-actions"><a data-nav href="/signin">Sign in</a><a data-nav class="btn btn-primary" href="/signup">Sign up</a></div>`;
  }
  return `<a data-nav class="environment-chip ${activeNav('/status') ? 'active' : ''}" href="/status" title="Local workspace status"${activeNav('/status') ? ' aria-current="page"' : ''}>
    <span class="environment-avatar" aria-hidden="true">L</span>
    <span class="environment-copy"><strong>Local</strong><small>workspace</small></span>
  </a>`;
}

function helpNav() {
  const mathHub = accountState.mode === 'hosted'
    ? '<a href="/sources/mathhub/"><strong>MathHub</strong><small>Formal mathematics source</small></a>'
    : '';
  return `<details class="help-menu">
    <summary class="help-button" aria-label="Help and learning">?</summary>
    <div class="help-menu-panel">
      <span class="menu-label">Get started</span>
      <a data-nav href="/quickstart"><strong>5-minute quickstart</strong><small>Track one dependency end to end</small></a>
      <a data-nav href="/demo"><strong>Example project</strong><small>See a source change and downstream review</small></a>
      <a data-nav href="/learn"><strong>What is Testamur?</strong><small>Concepts without the internal jargon</small></a>
      <a data-nav href="/docs"><strong>Concept reference</strong><small>Sources, revisions, reliance, impact and agents</small></a>
      <div class="user-menu-divider"></div>
      ${mathHub}
      <a href="https://github.com/Constanteer/testamur-plugins" target="_blank" rel="noreferrer"><strong>Integrations</strong><small>Codex, MCP and agent setup</small></a>
      <div class="user-menu-divider"></div>
      <div class="help-shortcuts"><span><kbd>/</kbd> Search</span><span><kbd>?</kbd> Help</span></div>
    </div>
  </details>`;
}

function nav() {
  const item = (href, label) => {
    const active = activeNav(href);
    return `<a data-nav class="${active ? 'active' : ''}" href="${href}"${active ? ' aria-current="page"' : ''}>${label}</a>`;
  };
  return `<header class="topbar">
    <div class="topbar-left">
      <a data-nav href="/" class="brand" aria-label="Testamur home"><span class="mark">T</span><span>Testamur</span></a>
      <nav class="primary-nav" aria-label="Primary navigation">
        ${item('/', 'Dashboard')}
        ${item('/projects', 'Projects')}
        ${item('/monitoring', 'Monitoring')}
        ${item('/explore', 'Explore')}
      </nav>
    </div>
    <form class="global-search" data-search-form role="search">
      <span class="search-icon">⌕</span>
      <input name="q" autocomplete="off" placeholder="Search projects, records, activity…" aria-label="Search Testamur" />
      <kbd>/</kbd>
    </form>
    <div class="top-actions">${helpNav()}${accountNav()}</div>
  </header>`;
}

function shell(content, wide = false) {
  root.innerHTML = `${nav()}<main class="shell ${wide ? 'shell-wide' : ''}">${content}</main>
    <footer class="site-footer"><span>Testamur</span><span>Provenance, change, reliance and monitoring.</span></footer>`;
  bindNavigation();
}

const loading = text => `<div class="loading"><span class="spinner"></span>${esc(text || 'Loading…')}</div>`;
const empty = (title, body) => `<div class="empty"><strong>${esc(title)}</strong><p>${esc(body)}</p></div>`;
const badge = (text, toneName = 'neutral') => `<span class="badge badge-${toneName}">${esc(text)}</span>`;

function tone(value) {
  const normalized = String(value || '').toLowerCase();
  if (['captured', 'available', 'unchanged', 'recovered', 'active'].includes(normalized)) return 'good';
  if (['changed', 'unavailable', 'failed', 'error', 'timeout', 'attention'].includes(normalized)) return 'warn';
  return 'neutral';
}

function ago(value) {
  if (!value) return 'No activity yet';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const delta = Date.now() - date.getTime();
  const future = delta < 0;
  const amount = Math.abs(delta);
  const units = [
    ['year', 31536000000], ['month', 2592000000], ['day', 86400000],
    ['hour', 3600000], ['minute', 60000], ['second', 1000],
  ];
  const [unit, milliseconds] = units.find(([, size]) => amount >= size) || ['second', 1000];
  const count = Math.max(1, Math.floor(amount / milliseconds));
  return `${future ? 'in ' : ''}${count} ${unit}${count === 1 ? '' : 's'}${future ? '' : ' ago'}`;
}

function epochDate(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 'Unknown';
  const date = new Date(numeric * 1000);
  if (Number.isNaN(date.getTime())) return 'Unknown';
  return date.toLocaleString();
}

function projectName(project = {}) {
  const raw = project.name || project.locator || 'Untitled project';
  try {
    const url = new URL(raw);
    const path = url.pathname.replace(/^\/+|\/+$/g, '');
    return path ? `${url.hostname}/${short(path, 36)}` : url.hostname;
  } catch (_) {
    return short(raw, 60);
  }
}

function domain(value) {
  try { return new URL(value).hostname; }
  catch (_) { return short(value || 'Untitled project', 48); }
}

function projectCard(project) {
  const status = project.status || 'not observed';
  return `<article class="project-card">
    <div class="project-card-head">
      <div class="project-icon">◇</div>
      <div class="project-main">
        <a data-nav class="project-title" href="${esc(projectPath(project.slug || project.ref))}">${esc(projectName(project))}</a>
        <div class="project-locator">${esc(short(project.description || project.slug || '', 94))}</div>
      </div>
      ${badge(status, tone(status))}
    </div>
    <div class="project-meta">
      <span>${project.monitor_count || project.watch_count || 0} monitor${(project.monitor_count || project.watch_count) === 1 ? '' : 's'}</span>
      <span>${esc(project.visibility || 'private')}</span>
      <span>${esc(ago(project.last_activity_at || project.created_at))}</span>
    </div>
  </article>`;
}

function feedVerb(item) {
  if (item.kind === 'alert') return item.state === 'changed' ? 'changed and triggered a monitor' : `${item.state || 'event'} monitor event`;
  if (item.kind === 'record_revision') return `recorded ${item.state || 'a new revision'}`;
  return item.state === 'captured' ? 'captured a new observation' : `recorded ${item.state || 'an observation'}`;
}

function feedItem(item) {
  const ref = item.subject_ref || item.ref;
  const icon = { alert: '!', observation: '↻', record_revision: '◫' }[item.kind] || '·';
  return `<article class="feed-item">
    <div class="feed-icon feed-${esc(item.kind || 'item')}">${icon}</div>
    <div class="feed-body">
      <div class="feed-line"><a data-nav href="${esc(objectPath(ref))}">${esc(short(item.title || 'Recorded activity', 96))}</a> <span>${esc(feedVerb(item))}</span></div>
      <div class="feed-meta">${esc(ago(item.at))}${item.state ? ` · ${badge(item.state, tone(item.state))}` : ''}</div>
    </div>
  </article>`;
}

function recordTitle(record) {
  const revision = record.latest_revision || {};
  return revision.title || revision.statement || record.record_kind || 'Record';
}

function recordRow(record) {
  const revision = record.latest_revision || {};
  return `<a data-nav class="monitor-row" href="${esc(objectPath(record.record_id))}">
    <span class="project-icon">◫</span>
    <span><strong>${esc(short(recordTitle(record), 58))}</strong><small>${esc(record.record_kind || 'record')} · ${esc(ago(revision.recorded_at || record.created_at))}</small></span>
  </a>`;
}

function onboardingProgress(data) {
  const projects = data.projects || [];
  const watches = data.watches || [];
  const feed = data.feed || [];
  return {
    project: projects.length > 0,
    monitor: watches.length > 0,
    observation: watches.some(w => w.last_evaluated_at) || feed.some(item => item.kind === 'observation' || item.kind === 'alert'),
  };
}

function onboardingCard(data) {
  const progress = onboardingProgress(data);
  if (progress.project && progress.monitor && progress.observation) return '';
  const done = [progress.project, progress.monitor, progress.observation].filter(Boolean).length;
  const nextHref = !progress.project ? '/projects/new' : !progress.monitor ? `${projectPath((data.projects || [])[0]?.slug || (data.projects || [])[0]?.ref || '')}?tab=monitors` : '/monitoring';
  const nextLabel = !progress.project ? 'Create first project' : !progress.monitor ? 'Add first monitor' : 'Run the first check';
  const step = (ok, number, title, copy) => `<div class="onboarding-step ${ok ? 'done' : ''}"><span class="onboarding-check">${ok ? '✓' : number}</span><div><strong>${title}</strong><small>${copy}</small></div></div>`;
  return `<section class="onboarding-card">
    <div class="onboarding-head">
      <div><span class="onboarding-kicker">GET STARTED · ${done}/3</span><h2>Make Testamur useful in three steps.</h2><p>Give Testamur one thing your work depends on. It will remember the observed version and show you when that basis changes.</p></div>
      <a data-nav class="onboarding-demo-link" href="/demo">See an example first →</a>
    </div>
    <div class="onboarding-steps">
      ${step(progress.project, 1, 'Create a project', 'A container for one piece of work and its dependencies.')}
      ${step(progress.monitor, 2, 'Add something you rely on', 'A URL, repository, specification, artifact, or plugin target.')}
      ${step(progress.observation, 3, 'Record the first observation', 'Run the monitor once so Testamur has a revision to compare later.')}
    </div>
    <div class="onboarding-actions"><a data-nav class="btn btn-primary" href="${esc(nextHref)}">${esc(nextLabel)}</a><a data-nav class="btn btn-secondary" href="/quickstart">Open 5-minute quickstart</a></div>
  </section>`;
}

function changedGuidance(alerts, watches, projectByRef) {
  const changed = alerts.find(alert => String(alert.event_type || '').toLowerCase().includes('changed')) ||
    alerts.find(alert => watches.find(w => w.watch_id === alert.watch_id)?.latest_state === 'changed');
  if (!changed) return '';
  const watch = watches.find(item => item.watch_id === changed.watch_id);
  const project = projectByRef.get(watch?.project_id);
  const sourceRef = changed.source_id || watch?.source_id;
  return `<section class="change-guide">
    <div class="change-guide-icon">↻</div>
    <div class="change-guide-copy"><span>FIRST CHANGE WORKFLOW</span><h2>A monitored source changed. That does not mean your work is wrong.</h2><p>Inspect what was recorded, then check which downstream work may need review. Testamur keeps change detection separate from judgment.</p>
      <div class="change-guide-actions">
        ${sourceRef ? `<a data-nav class="btn btn-primary" href="${esc(objectPath(sourceRef))}?tab=history">View recorded history</a><a data-nav class="btn btn-secondary" href="/impact/${encodeURIComponent(sourceRef)}">See affected work</a>` : ''}
        ${project ? `<a data-nav class="btn btn-secondary" href="${esc(projectPath(project.slug || project.ref))}?tab=monitors">Open project monitors</a>` : ''}
      </div>
      <small>changed ≠ invalid · stale ≠ false</small>
    </div>
  </section>`;
}

async function homePage() {
  shell(`<div class="dashboard-grid"><aside class="dashboard-left">${loading('Loading projects…')}</aside><section class="dashboard-center">${loading('Loading activity…')}</section><aside class="dashboard-right">${loading('Loading monitoring…')}</aside></div>`, true);
  try {
    const data = await dashboard();
    const projects = data.projects || [];
    const feed = data.feed || [];
    const watches = data.watches || [];
    const alerts = data.alerts || [];
    const records = data.records || [];
    const status = data.status || {};
    const projectByRef = new Map(projects.map(project => [project.ref, project]));
    const watchById = new Map(watches.map(watch => [watch.watch_id, watch]));

    document.querySelector('.dashboard-left').innerHTML = `<section class="side-section">
      <div class="section-head compact"><h2>Your projects</h2><a data-nav href="/projects/new">New</a></div>
      <div class="project-list">${projects.length ? projects.slice(0, 10).map(projectCard).join('') : empty('No projects yet', 'Create a project to start tracking a source, then add monitoring when you want change alerts.')}</div>
      ${projects.length ? '<div class="project-meta"><a data-nav href="/projects">View all projects</a></div>' : '<div class="project-meta"><a data-nav href="/projects/new">Create your first project</a></div>'}
    </section>
    <section class="side-section">
      <div class="section-head compact"><h2>Your Testamur</h2><a data-nav href="/status">Details</a></div>
      <div class="mini-stats">
        <div><strong>${status.sources?.sources || 0}</strong><span>sources</span></div>
        <div><strong>${status.records?.records || 0}</strong><span>records</span></div>
        <div><strong>${status.watches?.watches || 0}</strong><span>monitors</span></div>
      </div>
    </section>`;

    document.querySelector('.dashboard-center').innerHTML = `${onboardingCard(data)}${changedGuidance(alerts, watches, projectByRef)}<div class="feed-header">
      <div><h1>Home</h1><p>What changed across your Testamur workspace.</p></div>
      <button class="btn btn-secondary" data-refresh>Refresh</button>
    </div>
    <div class="feed-tabs"><button class="active" type="button">For you</button><a data-nav href="/explore">Explore</a></div>
    <section class="feed">${feed.length ? feed.slice(0, 32).map(feedItem).join('') : empty('Nothing new yet', projects.length ? 'New observations, revisions and monitor events will appear here.' : 'Create a project first. Testamur will then have a stable place to show its recorded history and monitoring state.')}</section>`;

    document.querySelector('.dashboard-right').innerHTML = `<section class="right-card">
      <div class="section-head compact"><h2>Monitoring</h2><a data-nav href="/monitoring">View all</a></div>
      <div class="monitor-summary"><strong>${watches.length}</strong><span>active monitor${watches.length === 1 ? '' : 's'}</span></div>
      ${watches.length ? `<div class="monitor-list">${watches.slice(0, 6).map(watch => {
        const project = projectByRef.get(watch.project_id);
        const href = project ? `${projectPath(project.slug || project.ref)}?tab=monitors` : objectPath(watch.source_id);
        return `<a data-nav class="monitor-row" href="${esc(href)}"><span class="status-dot status-${tone(watch.latest_state)}"></span><span><strong>${esc(short(watch.label || domain(watch.source_id), 46))}</strong><small>${esc(project ? project.name : 'Unassigned monitor')} · ${esc(watch.latest_state || 'not evaluated')}</small></span></a>`;
      }).join('')}</div>` : empty('No monitors', projects.length ? 'Open a project and add a monitor for change, unavailable and recovery events.' : 'Create a project first, then add monitoring from its Monitoring tab.')}
    </section>
    <section class="right-card">
      <div class="section-head compact"><h2>Recent alerts</h2><span>${alerts.length}</span></div>
      ${alerts.length ? `<div class="alert-list">${alerts.slice(0, 5).map(alert => {
        const watch = watchById.get(alert.watch_id);
        const project = projectByRef.get(watch?.project_id);
        const href = project ? `${projectPath(project.slug || project.ref)}?tab=monitors` : objectPath(alert.source_id);
        const label = project?.name || watch?.label || alert.source_id;
        return `<a data-nav class="alert-row" href="${esc(href)}"><span class="alert-mark">!</span><span><strong>${esc(alert.event_type || 'alert')}</strong><small>${esc(short(label, 52))} · ${esc(ago(alert.recorded_at))}</small></span></a>`;
      }).join('')}</div>` : '<p class="muted small">No recent alerts.</p>'}
    </section>
    <section class="right-card">
      <div class="section-head compact"><h2>Recent records</h2><a data-nav href="/explore">Explore</a></div>
      ${records.length ? `<div class="monitor-list">${records.slice(0, 5).map(recordRow).join('')}</div>` : '<p class="muted small">No records yet.</p>'}
    </section>`;

    bindNavigation();
    document.querySelector('[data-refresh]')?.addEventListener('click', async () => {
      await dashboard(true);
      homePage();
    });
  } catch (error) {
    errorPage(error, 'Dashboard unavailable');
  }
}

async function projectsPage() {
  shell(`<div class="page-title"><div><h1>Projects</h1><p>Containers for related monitors, evidence and change history.</p></div></div>${loading('Loading projects…')}`, true);
  try {
    const data = await dashboard();
    const projects = [...(data.projects || [])].sort((a, b) => String(b.last_activity_at || b.created_at || '').localeCompare(String(a.last_activity_at || a.created_at || '')));
    const watched = projects.filter(project => project.watch_count > 0).length;
    const changed = (data.watches || []).filter(watch => watch.latest_state === 'changed').length;
    shell(`<div class="page-title"><div><h1>Projects</h1><p>Containers for related monitors, evidence and change history.</p></div><a data-nav class="btn btn-primary" href="/projects/new">New project</a></div>
      <div class="monitoring-layout">
        <div class="mini-stats">
          <div><strong>${projects.length}</strong><span>projects</span></div>
          <div><strong>${watched}</strong><span>monitored</span></div>
          <div><strong>${changed}</strong><span>changed monitors</span></div>
        </div>
        <section><div class="section-head"><h2>All projects</h2><span>Newest activity first</span></div><div class="project-grid">${projects.length ? projects.map(projectCard).join('') : empty('No projects yet', 'Choose New project to add the first source you want Testamur to track.')}</div></section>
      </div>`, true);
    bindNavigation();
    if (params().get('new') === '1') navigate('/projects/new');
  } catch (error) {
    errorPage(error, 'Projects unavailable');
  }
}

function newProjectFields() {
  const workspace = accountState.activeWorkspace;
  const owner = workspace?.label || (accountState.mode === 'hosted' ? 'Personal workspace' : 'Local workspace');
  const ownerInitial = String(owner).trim().slice(0, 1).toUpperCase() || 'W';
  const ownerMeta = workspace?.kind === 'organization' ? 'organization' : 'workspace';
  return `<form class="new-project-form" data-new-project>
    <section class="project-create-block">
      <div class="project-create-section-head">
        <div><strong>Project</strong><span>Name the container. Monitors are added inside it.</span></div>
      </div>
      <div class="create-project-path">
        <div class="project-owner-prefix" title="${esc(ownerMeta)}">
          <span class="project-owner-avatar" aria-hidden="true">${esc(ownerInitial)}</span>
          <strong>${esc(owner)}</strong>
          <span class="project-path-slash">/</span>
        </div>
        <label class="project-name-field"><span class="sr-only">Project name</span><input name="name" type="text" autocomplete="off" placeholder="project-name" maxlength="100" required /></label>
      </div>
      <label class="project-description-field">
        <span>Description <small>optional</small></span>
        <textarea name="description" autocomplete="off" maxlength="500" rows="3" placeholder="What is this project for?"></textarea>
      </label>
    </section>

    <section class="project-create-block project-create-block-tight">
      <div class="project-create-section-head">
        <div><strong>Visibility</strong><span>Controls who can open the project itself.</span></div>
      </div>
      <fieldset class="visibility-options">
        <legend class="sr-only">Visibility</legend>
        <label>
          <input type="radio" name="visibility" value="private" checked />
          <span class="visibility-radio-mark" aria-hidden="true"></span>
          <span><strong>Private</strong><small>Only this workspace can open it.</small></span>
        </label>
        <label>
          <input type="radio" name="visibility" value="public" />
          <span class="visibility-radio-mark" aria-hidden="true"></span>
          <span><strong>Public</strong><small>Project metadata can be viewed publicly.</small></span>
        </label>
      </fieldset>
    </section>

    <section class="project-create-block project-create-block-tight initialize-monitor">
      <label class="monitor-toggle">
        <input type="checkbox" name="initialize_monitor" />
        <span class="monitor-toggle-box" aria-hidden="true">+</span>
        <span><strong>Add the first monitor now</strong><small>Optional — you can also create an empty project and add monitors later.</small></span>
      </label>
      <div class="initial-monitor-fields" data-initial-monitor-fields hidden>
        <label><span>URL or locator</span><input name="locator" type="text" autocomplete="off" placeholder="https://example.com/spec" /></label>
        <label><span>Label <small>optional</small></span><input name="monitor_label" type="text" autocomplete="off" placeholder="Production spec" /></label>
      </div>
    </section>

    <div data-write-result></div>
    <div class="new-project-actions">
      <a data-nav class="btn btn-secondary" href="/projects">Cancel</a>
      <button class="btn btn-primary" type="submit">Create project</button>
    </div>
  </form>`;
}

async function newProjectPage() {
  if (accountState.mode === 'hosted' && !accountState.authenticated) {
    shell(`<div class="create-auth-gate">
      <div class="create-auth-mark" aria-hidden="true">T</div>
      <h1>Sign in to create a project</h1>
      <p>Your session is no longer active. Sign in again before making workspace changes.</p>
      <div class="create-auth-actions"><a data-nav class="btn btn-primary" href="/signin">Sign in</a><a data-nav class="btn btn-secondary" href="/projects">Back to projects</a></div>
    </div>`);
    return;
  }
  shell(`<div class="new-project-page">
    <div class="new-project-heading">
      <div>
        <a data-nav class="new-project-back" href="/projects">Projects</a>
        <h1>New project</h1>
        <p>Create the container first; monitors can be attached now or later.</p>
      </div>
    </div>
    <section class="new-project-card">${newProjectFields()}</section>
  </div>`, true);
  bindNavigation();
  const form = document.querySelector('[data-new-project]');
  form?.addEventListener('submit', createProjectFromForm);
  form?.elements.initialize_monitor?.addEventListener('change', event => {
    const fields = form.querySelector('[data-initial-monitor-fields]');
    if (fields) fields.hidden = !event.currentTarget.checked;
    if (event.currentTarget.checked) form.elements.locator?.focus();
  });
  form?.elements.name?.focus();
}

async function createProjectFromForm(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const result = form.querySelector('[data-write-result]');
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  result.innerHTML = loading('Creating project…');
  try {
    const created = await apiWrite('/v1/projects', {
      name: form.elements.name.value.trim(),
      description: form.elements.description.value.trim() || null,
      visibility: form.elements.visibility.value,
    });
    if (form.elements.initialize_monitor.checked) {
      const locator = form.elements.locator.value.trim();
      if (!locator) throw new Error('Enter a locator for the initial monitor.');
      await apiWrite('/v1/monitors', {
        project_id: created.project_ref,
        locator,
        label: form.elements.monitor_label.value.trim() || null,
        alert_on: ['changed', 'unavailable', 'recovered'],
      });
    }
    await dashboard(true);
    document.body.classList.remove('modal-open');
    navigate(projectPath(created.project?.slug || created.project_ref));
  } catch (error) {
    result.innerHTML = `<div class="flash flash-danger"><strong>${esc(error.code || 'error')}</strong><span>${esc(error.message || error)}</span></div>`;
    button.disabled = false;
  }
}

function hostedAccountUnavailable(title) {
  shell(`<div class="account-page"><section class="account-card"><div class="account-mark">T</div><h1>${esc(title)}</h1><p>This is a local Testamur workspace. Hosted accounts are only available from the hosted Web entrypoint.</p><a data-nav class="btn btn-secondary" href="/">Back to workspace</a></section></div>`);
}

async function signInPage() {
  if (accountState.mode !== 'hosted') return hostedAccountUnavailable('Sign in');
  const next = params().get('next');
  if (accountState.authenticated) return navigate(safeNextPath(next));
  shell(`<div class="account-page"><section class="account-card">
    <div class="account-mark">T</div>
    <h1>Sign in to Testamur</h1>
    <p>${next ? 'Sign in to continue to the requested Testamur workspace page.' : 'Open your projects, monitoring state and recorded activity.'}</p>
    <form class="account-form" data-signin data-next="${esc(safeNextPath(next))}">
      <label><span>Username or email</span><input name="identifier" autocomplete="username" required /></label>
      <label><span>Password</span><input name="password" type="password" autocomplete="current-password" required /></label>
      <div data-account-result></div>
      <button class="btn btn-primary account-submit" type="submit">Sign in</button>
    </form>
    <div class="account-switch">New to Testamur? <a data-nav href="/signup${next ? `?next=${encodeURIComponent(next)}` : ''}">Create an account</a></div>
  </section></div>`);
  document.querySelector('[data-signin]')?.addEventListener('submit', submitSignIn);
}

async function signUpPage() {
  if (accountState.mode !== 'hosted') return hostedAccountUnavailable('Create account');
  const next = params().get('next');
  if (accountState.authenticated) return navigate(safeNextPath(next));
  shell(`<div class="account-page"><section class="account-card">
    <div class="account-mark">T</div>
    <h1>Create your Testamur account</h1>
    <p>Your hosted workspace is isolated from other users by default.</p>
    <form class="account-form" data-signup data-next="${esc(safeNextPath(next))}">
      <label><span>Username</span><input name="username" autocomplete="username" minlength="3" maxlength="39" required /></label>
      <label><span>Email</span><input name="email" type="email" autocomplete="email" required /></label>
      <label><span>Display name</span><input name="display_name" autocomplete="name" maxlength="80" placeholder="Optional" /></label>
      <label><span>Password</span><input name="password" type="password" autocomplete="new-password" minlength="10" required /></label>
      <div data-account-result></div>
      <button class="btn btn-primary account-submit" type="submit">Create account</button>
    </form>
    <div class="account-switch">Already have an account? <a data-nav href="/signin${next ? `?next=${encodeURIComponent(next)}` : ''}">Sign in</a></div>
  </section></div>`);
  document.querySelector('[data-signup]')?.addEventListener('submit', submitSignUp);
}

function settingsNav(selected) {
  const link = (href, label, key) => `<a data-nav class="${selected === key ? 'active' : ''}" href="${href}">${label}</a>`;
  return `<aside class="settings-nav">
    <strong>Account settings</strong>
    ${link('/settings/profile', 'Profile', 'profile')}
    ${link('/settings/security', 'Security', 'security')}
    ${link('/settings/organizations', 'Organizations', 'organizations')}
    <span>Billing <small>not connected</small></span>
  </aside>`;
}

async function profilePage() {
  if (accountState.mode !== 'hosted') return hostedAccountUnavailable('Profile');
  if (!accountState.authenticated || !accountState.user) return navigate('/signin');
  const user = accountState.user;
  shell(`<div class="settings-layout">
    ${settingsNav('profile')}
    <section class="settings-card">
      <div class="page-title"><div><h1>Public profile</h1><p>Manage the identity shown across your Testamur workspace.</p></div></div>
      <form class="settings-form" data-profile>
        <label><span>Username</span><input value="${esc(user.username || '')}" disabled /></label>
        <label><span>Email</span><input value="${esc(user.email || '')}" disabled /></label>
        <label><span>Display name</span><input name="display_name" maxlength="80" value="${esc(user.display_name || '')}" required /></label>
        <div data-account-result></div>
        <div><button class="btn btn-primary" type="submit">Save profile</button></div>
      </form>
    </section>
  </div>`, true);
  document.querySelector('[data-profile]')?.addEventListener('submit', submitProfile);
}

async function organizationsPage() {
  if (accountState.mode !== 'hosted') return hostedAccountUnavailable('Organizations');
  if (!accountState.authenticated || !accountState.user) return navigate('/signin');
  shell(`<div class="settings-layout">${settingsNav('organizations')}<section class="settings-card">${loading('Loading organizations…')}</section></div>`, true);
  try {
    const [orgResponse, invitationResponse] = await Promise.all([
      api('/v1/account/organizations'),
      api('/v1/account/invitations'),
    ]);
    const organizations = orgResponse.organizations || [];
    const invitations = invitationResponse.invitations || [];
    const card = document.querySelector('.settings-card');
    card.innerHTML = `
      <div class="page-title"><div><h1>Organizations</h1><p>Shared private Testamur workspaces with explicit membership.</p></div></div>
      <section class="security-section">
        <div class="security-section-head"><div><h2>Create organization</h2><p>Create a shared workspace. You become its owner.</p></div></div>
        <form class="settings-form organization-create-form" data-create-organization>
          <label><span>Name</span><input name="display_name" maxlength="100" placeholder="Constanteer Research" required /></label>
          <label><span>Slug</span><input name="slug" minlength="3" maxlength="39" pattern="[A-Za-z0-9][A-Za-z0-9_-]{1,37}[A-Za-z0-9]" placeholder="constanteer-research" required /></label>
          <div data-organization-result></div>
          <div><button class="btn btn-primary" type="submit">Create organization</button></div>
        </form>
      </section>
      ${invitations.length ? `<section class="security-section">
        <div class="security-section-head"><div><h2>Invitations</h2><p>Organizations that invited you to a shared workspace.</p></div></div>
        <div class="organization-list">${invitations.map(invitation => `<article class="organization-row">
          <div class="project-icon">◇</div>
          <div class="row-main"><strong>${esc(invitation.organization?.display_name || invitation.organization?.slug || 'Organization')}</strong><small>@${esc(invitation.organization?.slug || '')} · invited by @${esc(invitation.inviter_username || '')} as ${esc(invitation.role || 'member')}</small></div>
          <div class="organization-actions"><button class="btn btn-secondary" type="button" data-invitation-decline="${esc(invitation.invitation_id)}">Decline</button><button class="btn btn-primary" type="button" data-invitation-accept="${esc(invitation.invitation_id)}">Accept</button></div>
        </article>`).join('')}</div>
      </section>` : ''}
      <section class="security-section">
        <div class="security-section-head"><div><h2>Your organizations</h2><p>${organizations.length} organization${organizations.length === 1 ? '' : 's'}.</p></div></div>
        <div class="organization-list">${organizations.length ? organizations.map(org => `<a data-nav class="organization-row organization-link" href="/organizations/${encodeURIComponent(org.slug)}">
          <div class="project-icon">◇</div>
          <div class="row-main"><strong>${esc(org.display_name)}</strong><small>@${esc(org.slug)} · ${esc(org.role)}</small></div>
          <span>›</span>
        </a>`).join('') : empty('No organizations yet', 'Create one above, or accept an invitation when another user invites you.')}</div>
      </section>
    `;
    card.querySelector('[data-create-organization]')?.addEventListener('submit', createOrganization);
    card.querySelectorAll('[data-invitation-accept]').forEach(button => button.addEventListener('click', () => respondInvitation(button.dataset.invitationAccept, true)));
    card.querySelectorAll('[data-invitation-decline]').forEach(button => button.addEventListener('click', () => respondInvitation(button.dataset.invitationDecline, false)));
    bindNavigation();
  } catch (error) {
    errorPage(error, 'Organizations unavailable');
  }
}

async function createOrganization(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const result = form.querySelector('[data-organization-result]');
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  result.innerHTML = loading('Creating organization…');
  try {
    const response = await apiWrite('/v1/account/organizations', {
      slug: form.elements.slug.value.trim(),
      display_name: form.elements.display_name.value.trim(),
    });
    await loadAccountState();
    navigate(`/organizations/${encodeURIComponent(response.organization.slug)}`);
  } catch (error) {
    accountError(result, error);
    button.disabled = false;
  }
}

async function respondInvitation(invitationId, accept) {
  if (!invitationId) return;
  try {
    await apiWrite('/v1/account/invitations/respond', { invitation_id: invitationId, accept: Boolean(accept) });
    await loadAccountState();
    await organizationsPage();
  } catch (error) {
    errorPage(error, 'Unable to respond to invitation');
  }
}

async function organizationPage(orgRef) {
  if (accountState.mode !== 'hosted') return hostedAccountUnavailable('Organization');
  if (!accountState.authenticated || !accountState.user) return navigate('/signin');
  shell(loading('Loading organization…'), true);
  try {
    const response = await api(`/v1/account/organizations/${encodeURIComponent(orgRef)}`);
    const org = response.organization || {};
    const members = response.members || [];
    const canInvite = ['owner', 'admin'].includes(org.role);
    const isOwner = org.role === 'owner';
    const active = accountState.activeWorkspace?.workspace_id === org.org_id;
    shell(`<section class="object-header">
      <div class="breadcrumbs"><a data-nav href="/settings/organizations">Organizations</a><span>/</span><span>@${esc(org.slug || orgRef)}</span></div>
      <div class="object-title-row"><div><h1>${esc(org.display_name || org.slug || 'Organization')}</h1><p>@${esc(org.slug || '')} · private shared Testamur workspace</p></div>${badge(org.role || 'member')}</div>
    </section>
    <div class="organization-layout">
      <section class="settings-card">
        <div class="security-section-head"><div><h2>Workspace</h2><p>Projects, monitoring, history and evidence in this organization are visible to its members.</p></div>${active ? badge('Current workspace', 'good') : `<button class="btn btn-primary" type="button" data-switch-workspace="${esc(org.org_id)}">Switch to workspace</button>`}</div>
      </section>
      ${canInvite ? `<section class="settings-card">
        <div class="security-section-head"><div><h2>Invite member</h2><p>Invite an existing Testamur user by username.</p></div></div>
        <form class="organization-invite-form" data-org-invite data-org-ref="${esc(org.org_id)}">
          <label><span>Username</span><input name="username" autocomplete="off" placeholder="username" required /></label>
          <label><span>Role</span><select name="role"><option value="member">Member</option><option value="admin">Admin</option></select></label>
          <button class="btn btn-primary" type="submit">Invite</button>
        </form>
        <div data-invite-result></div>
      </section>` : ''}
      <section class="settings-card">
        <div class="security-section-head"><div><h2>Members</h2><p>${members.length} member${members.length === 1 ? '' : 's'}.</p></div></div>
        <div class="organization-list">${members.map(member => `<a data-nav class="organization-row organization-link" href="/users/${encodeURIComponent(member.username)}">
          <div class="member-avatar" aria-hidden="true">${esc(String(member.display_name || member.username || 'U').slice(0,1).toUpperCase())}</div>
          <div class="row-main"><strong>${esc(member.display_name || member.username)}</strong><small>@${esc(member.username)} · joined ${esc(epochDate(member.joined_at))}</small></div>
          ${badge(member.role || 'member')}
        </a>`).join('')}</div>
      </section>
      ${isOwner ? `<section class="danger-zone">
        <div class="danger-zone-copy"><h2>Delete organization</h2><p>Permanently delete the organization and its shared private workspace. Members immediately lose access.</p></div>
        <form class="settings-form danger-form" data-delete-organization data-org-ref="${esc(org.org_id)}" data-org-slug="${esc(org.slug || '')}">
          <label><span>Type <strong>${esc(org.slug || '')}</strong> to confirm</span><input name="confirmation" autocomplete="off" required /></label>
          <div data-delete-org-result></div>
          <div><button class="btn btn-danger" type="submit">Delete organization</button></div>
        </form>
      </section>` : ''}
    </div>`, true);
    document.querySelector('[data-switch-workspace]')?.addEventListener('click', event => switchWorkspace(event.currentTarget.dataset.switchWorkspace));
    document.querySelector('[data-org-invite]')?.addEventListener('submit', inviteOrganizationMember);
    document.querySelector('[data-delete-organization]')?.addEventListener('submit', deleteOrganization);
    bindNavigation();
  } catch (error) {
    errorPage(error, 'Organization unavailable');
  }
}

async function inviteOrganizationMember(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const result = form.parentElement.querySelector('[data-invite-result]');
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  result.innerHTML = loading('Sending invitation…');
  try {
    const response = await apiWrite(`/v1/account/organizations/${encodeURIComponent(form.dataset.orgRef)}/invite`, {
      username: form.elements.username.value.trim(),
      role: form.elements.role.value,
    });
    result.innerHTML = `<div class="flash flash-success"><strong>Invitation sent</strong><span>@${esc(response.invitation?.invitee?.username || form.elements.username.value.trim())} can accept it from Organizations.</span></div>`;
    form.reset();
    button.disabled = false;
  } catch (error) {
    accountError(result, error);
    button.disabled = false;
  }
}

async function deleteOrganization(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const result = form.querySelector('[data-delete-org-result]');
  const button = form.querySelector('button[type="submit"]');
  const orgRef = form.dataset.orgRef;
  const expected = form.dataset.orgSlug || '';
  if (form.elements.confirmation.value.trim().toLowerCase() !== expected.toLowerCase()) {
    result.innerHTML = '<div class="flash flash-danger"><strong>confirmation_mismatch</strong><span>Type the organization slug exactly to confirm deletion.</span></div>';
    return;
  }
  button.disabled = true;
  result.innerHTML = loading('Deleting organization workspace…');
  try {
    await apiWrite(`/v1/account/organizations/${encodeURIComponent(orgRef)}/delete`, {
      confirmation: form.elements.confirmation.value.trim(),
    });
    await loadAccountState();
    cache.dashboard = null;
    navigate('/settings/organizations');
  } catch (error) {
    accountError(result, error);
    button.disabled = false;
  }
}

async function switchWorkspace(workspaceId) {
  if (!workspaceId) return;
  try {
    await apiWrite('/v1/account/workspace', { workspace_id: workspaceId });
    await loadAccountState();
    cache.dashboard = null;
    navigate('/');
  } catch (error) {
    errorPage(error, 'Unable to switch workspace');
  }
}

async function publicProfilePage(username) {
  if (accountState.mode !== 'hosted') return hostedAccountUnavailable('User profile');
  shell(loading('Loading profile…'), true);
  try {
    const response = await api(`/v1/users/${encodeURIComponent(username)}`);
    const profile = response.profile || {};
    const label = profile.display_name || profile.username || username;
    const initial = String(label).trim().slice(0, 1).toUpperCase() || 'U';
    const isSelf = accountState.authenticated && accountState.user?.username === profile.username;
    shell(`<div class="public-profile">
      <aside class="profile-sidebar">
        <div class="profile-avatar" aria-hidden="true">${esc(initial)}</div>
        <h1>${esc(label)}</h1>
        <div class="profile-handle">@${esc(profile.username || username)}</div>
        <div class="profile-meta">Joined ${esc(epochDate(profile.created_at))}</div>
        ${isSelf ? '<a data-nav class="btn btn-secondary profile-edit" href="/settings/profile">Edit profile</a>' : ''}
      </aside>
      <section class="profile-content">
        <div class="page-title"><div><h1>Profile</h1><p>Hosted Testamur identity.</p></div></div>
        <div class="right-card">
          <div class="section-head"><h2>Activity</h2><span>Private by default</span></div>
          <p class="muted">Projects and recorded workspace activity are not exposed on public profiles. Shared/public project visibility will be handled by the hosted permission model rather than inferred from local evidence state.</p>
        </div>
      </section>
    </div>`, true);
  } catch (error) {
    errorPage(error, 'User not found');
  }
}

async function securityPage() {
  if (accountState.mode !== 'hosted') return hostedAccountUnavailable('Security');
  if (!accountState.authenticated || !accountState.user) return navigate('/signin');
  const user = accountState.user;
  shell(`<div class="settings-layout">${settingsNav('security')}<section class="settings-card">${loading('Loading security settings…')}</section></div>`, true);
  try {
    const response = await api('/v1/account/sessions');
    const sessions = response.sessions || [];
    const otherCount = sessions.filter(session => !session.current).length;
    const card = document.querySelector('.settings-card');
    card.innerHTML = `
      <div class="page-title">
        <div><h1>Security</h1><p>Password and active account sessions.</p></div>
      </div>

      <section class="security-section">
        <div class="security-section-head">
          <div><h2>Sessions</h2><p>Devices and browsers currently signed in to your Testamur account.</p></div>
          ${otherCount ? '<button class="btn btn-secondary" type="button" data-revoke-others>Sign out other sessions</button>' : ''}
        </div>
        <div class="session-list">
          ${sessions.length ? sessions.map(session => `<article class="session-row">
            <div class="session-icon" aria-hidden="true">◉</div>
            <div class="session-main">
              <strong>${session.current ? 'This session' : 'Signed-in session'}</strong>
              <small>Last active ${esc(epochDate(session.last_seen_at))} · created ${esc(epochDate(session.created_at))}</small>
              <small>Expires ${esc(epochDate(session.expires_at))}</small>
            </div>
            <div>${session.current ? badge('Current', 'good') : `<button class="btn btn-secondary" type="button" data-revoke-session="${esc(session.session_id)}">Revoke</button>`}</div>
          </article>`).join('') : empty('No active sessions', 'There are no active sessions for this account.')}
        </div>
      </section>

      <section class="security-section">
        <div class="security-section-head"><div><h2>Change password</h2><p>Changing your password signs out every other active session.</p></div></div>
        <form class="settings-form security-form" data-password-change>
          <label><span>Current password</span><input name="current_password" type="password" autocomplete="current-password" required /></label>
          <label><span>New password</span><input name="new_password" type="password" autocomplete="new-password" minlength="10" required /></label>
          <label><span>Confirm new password</span><input name="confirm_password" type="password" autocomplete="new-password" minlength="10" required /></label>
          <div data-password-result></div>
          <div><button class="btn btn-primary" type="submit">Update password</button></div>
        </form>
      </section>

      <section class="danger-zone">
        <div class="danger-zone-copy"><h2>Delete account</h2><p>Permanently delete this hosted account and its private Testamur workspace. This action cannot be undone.</p></div>
        <form class="settings-form danger-form" data-delete-account>
          <label><span>Password</span><input name="password" type="password" autocomplete="current-password" required /></label>
          <label><span>Type <strong>${esc(user.username || '')}</strong> to confirm</span><input name="confirmation" autocomplete="off" required /></label>
          <div data-delete-result></div>
          <div><button class="btn btn-danger" type="submit">Delete account</button></div>
        </form>
      </section>
    `;
    card.querySelectorAll('[data-revoke-session]').forEach(button => {
      button.addEventListener('click', () => revokeSession(button.dataset.revokeSession));
    });
    card.querySelector('[data-revoke-others]')?.addEventListener('click', revokeOtherSessions);
    card.querySelector('[data-password-change]')?.addEventListener('submit', submitPasswordChange);
    card.querySelector('[data-delete-account]')?.addEventListener('submit', submitAccountDeletion);
  } catch (error) {
    errorPage(error, 'Security settings unavailable');
  }
}

async function submitPasswordChange(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const result = form.querySelector('[data-password-result]');
  const button = form.querySelector('button[type="submit"]');
  const next = form.elements.new_password.value;
  const confirm = form.elements.confirm_password.value;
  if (next !== confirm) {
    result.innerHTML = '<div class="flash flash-danger"><strong>password_mismatch</strong><span>New passwords do not match.</span></div>';
    return;
  }
  button.disabled = true;
  result.innerHTML = loading('Updating password…');
  try {
    const response = await apiWrite('/v1/account/password', {
      current_password: form.elements.current_password.value,
      new_password: next,
    });
    await securityPage();
    const refreshed = document.querySelector('[data-password-result]');
    if (refreshed) {
      const count = Number(response.revoked_other_sessions || 0);
      refreshed.innerHTML = `<div class="flash flash-success"><strong>Password updated</strong><span>${count ? `${count} other session${count === 1 ? '' : 's'} signed out.` : 'Your current session remains signed in.'}</span></div>`;
    }
  } catch (error) {
    accountError(result, error);
    button.disabled = false;
  }
}

async function submitAccountDeletion(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const result = form.querySelector('[data-delete-result]');
  const button = form.querySelector('button[type="submit"]');
  const expected = accountState.user?.username || '';
  if (form.elements.confirmation.value.trim().toLowerCase() !== expected.toLowerCase()) {
    result.innerHTML = '<div class="flash flash-danger"><strong>confirmation_mismatch</strong><span>Type your username exactly to confirm deletion.</span></div>';
    return;
  }
  button.disabled = true;
  result.innerHTML = loading('Deleting account and workspace…');
  try {
    await apiWrite('/v1/account/delete', {
      password: form.elements.password.value,
      confirmation: form.elements.confirmation.value.trim(),
    });
    accountState.authenticated = false;
    accountState.user = null;
    accountState.workspaces = [];
    accountState.activeWorkspace = null;
    cache.dashboard = null;
    navigate('/signup');
  } catch (error) {
    accountError(result, error);
    button.disabled = false;
  }
}

async function revokeSession(sessionId) {
  if (!sessionId) return;
  try {
    await apiWrite('/v1/account/sessions/revoke', { session_id: sessionId });
    await securityPage();
  } catch (error) {
    errorPage(error, 'Unable to revoke session');
  }
}

async function revokeOtherSessions() {
  try {
    await apiWrite('/v1/account/sessions/revoke-others', {});
    await securityPage();
  } catch (error) {
    errorPage(error, 'Unable to revoke sessions');
  }
}

function accountError(result, error) {
  result.innerHTML = `<div class="flash flash-danger"><strong>${esc(error.code || 'error')}</strong><span>${esc(error.message || error)}</span></div>`;
}

async function submitSignIn(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const result = form.querySelector('[data-account-result]');
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  result.innerHTML = loading('Signing in…');
  try {
    const response = await apiWrite('/v1/account/signin', {
      identifier: form.elements.identifier.value.trim(),
      password: form.elements.password.value,
    });
    await loadAccountState();
    cache.dashboard = null;
    const next = form.dataset.next;
    navigate(safeNextPath(next));
  } catch (error) {
    accountError(result, error);
    button.disabled = false;
  }
}

async function submitSignUp(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const result = form.querySelector('[data-account-result]');
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  result.innerHTML = loading('Creating account…');
  try {
    const response = await apiWrite('/v1/account/signup', {
      username: form.elements.username.value.trim(),
      email: form.elements.email.value.trim(),
      display_name: form.elements.display_name.value.trim() || null,
      password: form.elements.password.value,
    });
    await loadAccountState();
    cache.dashboard = null;
    const next = form.dataset.next;
    navigate(safeNextPath(next));
  } catch (error) {
    accountError(result, error);
    button.disabled = false;
  }
}

async function submitProfile(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const result = form.querySelector('[data-account-result]');
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  result.innerHTML = loading('Saving profile…');
  try {
    const response = await apiWrite('/v1/account/profile', {
      display_name: form.elements.display_name.value.trim(),
    });
    accountState.user = response.user;
    result.innerHTML = '<div class="flash flash-success"><strong>Saved</strong><span>Your profile has been updated.</span></div>';
    button.disabled = false;
    document.querySelector('.top-actions').innerHTML = accountNav();
    bindNavigation();
  } catch (error) {
    accountError(result, error);
    button.disabled = false;
  }
}

async function signOut() {
  try { await apiWrite('/v1/account/signout', {}); }
  finally {
    accountState.authenticated = false;
    accountState.user = null;
    accountState.workspaces = [];
    accountState.activeWorkspace = null;
    cache.dashboard = null;
    navigate('/signin');
  }
}

async function explorePage() {
  shell(`<div class="page-title"><div><h1>Explore</h1><p>Recent projects, records and activity.</p></div></div>${loading('Loading…')}`, true);
  try {
    const data = await dashboard();
    const query = (params().get('q') || '').trim().toLowerCase();
    const matches = value => !query || JSON.stringify(value).toLowerCase().includes(query);
    const projects = (data.projects || []).filter(matches);
    const records = (data.records || []).filter(matches);
    const feed = (data.feed || []).filter(matches);
    shell(`<div class="page-title"><div><h1>${query ? 'Search' : 'Explore'}</h1><p>${query ? `Results for “${esc(query)}”` : 'Recent projects, records and recorded activity.'}</p></div></div>
      <div class="explore-grid">
        <section>
          <div class="section-head"><h2>Projects</h2><span>${projects.length}</span></div>
          <div class="project-grid">${projects.length ? projects.map(projectCard).join('') : empty('No matching projects', 'Try another term.')}</div>
          <div class="section-head"><h2>Records</h2><span>${records.length}</span></div>
          <div class="right-card">${records.length ? `<div class="monitor-list">${records.map(recordRow).join('')}</div>` : '<p class="muted small">No matching records.</p>'}</div>
        </section>
        <section>
          <div class="section-head"><h2>Recent activity</h2><span>${feed.length}</span></div>
          <div class="feed panel-feed">${feed.length ? feed.map(feedItem).join('') : empty('No matching activity', 'Nothing recorded matches this query.')}</div>
        </section>
      </div>`, true);
  } catch (error) {
    errorPage(error, 'Explore unavailable');
  }
}

async function monitoringPage() {
  shell(`<div class="page-title"><div><h1>Monitoring</h1><p>Monitors and operational events across your projects.</p></div></div>${loading('Loading monitoring…')}`, true);
  try {
    const data = await dashboard();
    const projects = data.projects || [];
    const watches = data.watches || [];
    const alerts = data.alerts || [];
    const projectByRef = new Map(projects.map(project => [project.ref, project]));
    const watchById = new Map(watches.map(watch => [watch.watch_id, watch]));
    shell(`<div class="page-title"><div><h1>Monitoring</h1><p>Monitors and operational events across your projects.</p></div><div class="page-title-actions">${badge(`${alerts.length} alerts`, alerts.length ? 'warn' : 'neutral')}<button class="btn btn-secondary" type="button" data-run-due-monitors>Run due</button></div></div>
      ${changedGuidance(alerts, watches, projectByRef)}
      <div class="monitoring-layout">
        <section class="table-card"><div class="table-title"><h2>Monitors</h2><span>${watches.length}</span></div>
          ${watches.length ? `<div class="data-table">${watches.map(watch => {
            const project = projectByRef.get(watch.project_id);
            const href = project ? `${projectPath(project.slug || project.ref)}?tab=monitors` : objectPath(watch.source_id);
            return `<a data-nav class="data-row monitor-table-row" href="${esc(href)}"><span class="status-dot status-${tone(watch.latest_state)}"></span><span class="row-main"><strong>${esc(watch.label || 'Monitor')}</strong><small>${esc(short(project?.name || 'Unassigned monitor', 70))} · ${esc(cadenceLabel(watch.interval_seconds))}</small></span><span>${esc((watch.alert_on || []).join(', ') || 'no alerts')}</span><span>${esc(watch.latest_state || 'not evaluated')}</span><span>${esc(ago(watch.last_evaluated_at))}</span></a>`;
          }).join('')}</div>` : empty('Nothing monitored', projects.length ? 'Open a project and add a monitor.' : 'Create a project first, then add monitoring from its project page.')}
        </section>
        <section class="table-card"><div class="table-title"><h2>Alerts</h2><span>${alerts.length}</span></div>
          ${alerts.length ? `<div class="data-table">${alerts.map(alert => {
            const watch = watchById.get(alert.watch_id);
            const project = projectByRef.get(watch?.project_id);
            const href = project ? `${projectPath(project.slug || project.ref)}?tab=monitors` : objectPath(alert.source_id);
            return `<a data-nav class="data-row alert-table-row" href="${esc(href)}"><span class="alert-mark">!</span><span class="row-main"><strong>${esc(alert.event_type || 'alert')}</strong><small>${esc(project?.name || watch?.label || alert.source_id)}</small></span><span>${esc(ago(alert.recorded_at))}</span></a>`;
          }).join('')}</div>` : empty('No alerts', 'Monitor events will appear here.')}
        </section>
      </div>`, true);
    document.querySelector('[data-run-due-monitors]')?.addEventListener('click', event => {
      runDueMonitorsFromButton(event.currentTarget);
    });
  } catch (error) {
    errorPage(error, 'Monitoring unavailable');
  }
}

const INTERNAL_FIELDS = new Set([
  'semantics', 'identity_semantics', 'retrieval_metadata', 'source_id', 'revision_id', 'snapshot_id',
  'watch_id', 'record_id', 'alert_id', 'evaluation_id', 'relation_id', 'parent_revision_id',
  'previous_evaluation_id', 'previous_snapshot_id', 'watch_revision_id',
]);

function displayValue(value) {
  if (value === null || value === undefined || value === '') return '<span class="muted">—</span>';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'object') return `<code class="inline-json">${esc(short(pretty(value), 260))}</code>`;
  return esc(value);
}

function objectTitle(identity, data) {
  return data.title || data.label ||
    (data.initial_locator ? projectName({ name: data.initial_locator }) : null) ||
    (data.locator ? projectName({ name: data.locator }) : null) ||
    (data.statement ? short(data.statement, 80) : null) ||
    String(identity.kind || 'Object').replaceAll('_', ' ');
}

function objectSummary(identity, data) {
  return data.initial_locator || data.locator || data.statement ||
    (data.event_type ? `${data.event_type} monitor event` : `${String(identity.kind || 'Testamur object').replaceAll('_', ' ')} recorded in this environment.`);
}

function genericOverview(identity, data) {
  const priority = ['status', 'event_type', 'operational_state', 'title', 'statement', 'record_kind', 'content_hash', 'recorded_at', 'observed_at', 'created_at', 'ordinal', 'alert_on'];
  const fields = Object.entries(data || {})
    .filter(([key]) => !INTERNAL_FIELDS.has(key))
    .sort(([left], [right]) => {
      const a = priority.indexOf(left), b = priority.indexOf(right);
      return (a < 0 ? 999 : a) - (b < 0 ? 999 : b);
    })
    .slice(0, 14);
  return `<section class="object-overview-grid">
    <div class="object-main-card"><h2>Overview</h2><div class="fields">${fields.map(([key, value]) => `<div class="field"><span>${esc(key.replaceAll('_', ' '))}</span><div>${displayValue(value)}</div></div>`).join('')}</div></div>
    <aside class="object-about"><h3>About</h3><p>${esc(objectSummary(identity, data))}</p>${data.status ? `<div class="about-row"><span>Status</span>${badge(data.status, tone(data.status))}</div>` : ''}${data.recorded_at ? `<div class="about-row"><span>Recorded</span><strong>${esc(ago(data.recorded_at))}</strong></div>` : ''}${data.created_at ? `<div class="about-row"><span>Created</span><strong>${esc(ago(data.created_at))}</strong></div>` : ''}</aside>
  </section>`;
}

async function sourceOverview(ref, source) {
  const data = await dashboard();
  const watches = (data.watches || []).filter(item => item.source_id === ref);
  const alerts = (data.alerts || []).filter(item => item.source_id === ref);
  const activity = (data.feed || []).filter(item => item.subject_ref === ref).slice(0, 8);
  const latestWatchState = watches.find(item => item.latest_state)?.latest_state || 'not evaluated';
  return `<div class="monitoring-layout">
    <section class="object-overview-grid">
      <div class="object-main-card"><h2>Source overview</h2><div class="fields">
        <div class="field"><span>Locator</span><div>${esc(source.initial_locator || source.locator || '—')}</div></div>
        <div class="field"><span>Referenced by monitors</span><div>${watches.length}</div></div>
        <div class="field"><span>Latest monitor state</span><div>${badge(latestWatchState, tone(latestWatchState))}</div></div>
        <div class="field"><span>Alerts</span><div>${alerts.length}</div></div>
        <div class="field"><span>Created</span><div>${esc(ago(source.created_at))}</div></div>
      </div></div>
      <aside class="object-about"><h3>Source</h3><p>Canonical monitor target. Sources are not Projects; Projects group one or more monitors that may point here.</p><div class="about-row"><span>Monitors</span><strong>${watches.length}</strong></div><div class="about-row"><span>Alerts</span><strong>${alerts.length}</strong></div><div class="about-row"><span>Monitoring</span><a data-nav href="/monitoring">View monitors</a></div></aside>
    </section>
    <section><div class="section-head"><h2>Recent recorded activity</h2><a data-nav href="${esc(objectPath(ref))}?tab=history">View history</a></div><div class="feed panel-feed">${activity.length ? activity.map(feedItem).join('') : empty('No recent activity', 'Recorded observations and monitor events for this source will appear here.')}</div></section>
  </div>`;
}

function projectOverview(project, monitors, supplyChain = null) {
  const active = monitors.filter(monitor => monitor.latest_state && monitor.latest_state !== 'not evaluated').length;
  const attention = monitors.filter(monitor => ['changed', 'unavailable'].includes(monitor.latest_state)).length;
  const supplySummary = supplyChain
    ? `<div class="field"><span>Supply chain</span><div><strong>${esc(String(supplyChain.dependency_count || 0))}</strong> dependencies from ${esc(String(supplyChain.manifest_count || 0))} manifest(s) · <a data-nav href="${esc(projectPath(project.slug))}?tab=supply-chain">Open inventory</a></div></div>`
    : `<div class="field"><span>Supply chain</span><div><span class="muted">Not imported yet</span> · <a data-nav href="${esc(projectPath(project.slug))}?tab=supply-chain">Set up</a></div></div>`;
  return `<div class="monitoring-layout">
    <section class="object-overview-grid">
      <div class="object-main-card"><h2>Project overview</h2><div class="fields">
        <div class="field"><span>Description</span><div>${esc(project.description || 'No description')}</div></div>
        <div class="field"><span>Visibility</span><div>${badge(project.visibility || 'private')}</div></div>
        <div class="field"><span>Monitors</span><div>${monitors.length}</div></div>
        ${supplySummary}
        <div class="field"><span>Active</span><div>${active}</div></div>
        <div class="field"><span>Needs attention</span><div>${attention}</div></div>
        <div class="field"><span>Created</span><div>${esc(ago(project.created_at))}</div></div>
      </div></div>
      <aside class="object-about"><h3>About</h3><p>A project groups related monitors and recorded dependency evidence. Sources are monitor targets, not project identity.</p><div class="about-row"><span>Slug</span><strong>${esc(project.slug)}</strong></div><div class="about-row"><span>Monitors</span><strong>${monitors.length}</strong></div><div class="about-row"><span>Dependencies</span><strong>${esc(String(supplyChain?.dependency_count || 0))}</strong></div><div class="about-row"><span>Next</span><a data-nav href="${esc(projectPath(project.slug))}?tab=${supplyChain ? 'supply-chain' : 'monitors'}">${supplyChain ? 'Inspect supply chain' : monitors.length ? 'Manage monitors' : 'Add first monitor'}</a></div></aside>
    </section>
    <section><div class="section-head"><h2>Monitors</h2><a data-nav href="${esc(projectPath(project.slug))}?tab=monitors">View all</a></div>
      <div class="right-card">${monitors.length ? `<div class="monitor-list">${monitors.slice(0, 6).map(monitor => `<a data-nav class="monitor-row" href="${esc(objectPath(monitor.watch_id))}"><span class="status-dot status-${tone(monitor.latest_state)}"></span><span><strong>${esc(monitor.label || domain(monitor.locator || monitor.source_id))}</strong><small>${esc(short(monitor.locator || monitor.source_id, 80))} · ${esc(monitor.latest_state || 'not evaluated')}</small></span></a>`).join('')}</div>` : `<div class="empty-project-guide"><span class="onboarding-kicker">NEXT STEP</span><h3>This project is not watching anything yet.</h3><p>Add something this work depends on. A monitor gives Testamur a stable target to observe over time.</p><div class="monitor-examples"><span><strong>Documentation URL</strong><code>https://example.com/api</code></span><span><strong>GitHub repository</strong><code>Constanteer/slate-lang</code></span><span><strong>Agent integration</strong><small>Capture sources used by Codex or another MCP host.</small></span></div><a data-nav class="btn btn-primary" href="${esc(projectPath(project.slug))}?tab=monitors">Add first monitor</a></div>`}</div>
    </section>
  </div>`;
}

function supplyChainPanel(project, supplyChain) {
  if (!supplyChain) {
    return `<div class="supply-chain-layout">
      <section class="supply-chain-empty">
        <span class="onboarding-kicker">SOFTWARE SUPPLY CHAIN</span>
        <h2>Import what this repository declares it depends on.</h2>
        <p>Run the import from the repository root. Testamur hashes the manifest/lockfile bytes and records canonical package/component revision observations without pretending that a declaration proves runtime use.</p>
        <div class="supply-chain-command"><code>testamur project import . --name ${esc(project.name || project.slug || 'project')}</code></div>
        <div class="supply-chain-boundaries">
          <code>manifest declaration != runtime use</code>
          <code>name/version match != affectedness</code>
          <code>changed != invalid</code>
        </div>
        <p class="form-help">Recognized today: requirements*.txt, uv.lock, poetry.lock, package-lock.json / npm-shrinkwrap.json, Cargo.lock and go.sum.</p>
      </section>
    </div>`;
  }
  const warnings = supplyChain.warnings || [];
  return `<div class="supply-chain-layout">
    <section class="supply-chain-summary">
      <div class="supply-chain-heading">
        <div><span class="onboarding-kicker">RECORDED INVENTORY</span><h2>Declared software dependencies</h2><p>Derived from exact local manifest/lockfile observations. This is provenance evidence, not a vulnerability verdict or proof of runtime loading.</p></div>
        ${badge(warnings.length ? `${warnings.length} warning${warnings.length === 1 ? '' : 's'}` : 'recorded', warnings.length ? 'warn' : 'good')}
      </div>
      <div class="supply-chain-stats">
        <div><strong>${esc(String(supplyChain.manifest_count || 0))}</strong><span>manifests</span></div>
        <div><strong>${esc(String(supplyChain.dependency_count || 0))}</strong><span>dependencies</span></div>
        <div><strong>${esc(String(warnings.length))}</strong><span>warnings</span></div>
      </div>
      <div class="fields supply-chain-meta">
        <div class="field"><span>Latest scan</span><div>${esc(ago(supplyChain.recorded_at))}</div></div>
        <div class="field"><span>Scan revision</span><div><a data-nav class="mono ref-link" href="${esc(objectPath(supplyChain.scan_revision_id))}">${esc(short(supplyChain.scan_revision_id, 72))}</a></div></div>
        <div class="field"><span>Persistent scan</span><div><a data-nav class="mono ref-link" href="${esc(objectPath(supplyChain.scan_record_id))}">${esc(short(supplyChain.scan_record_id, 72))}</a></div></div>
      </div>
    </section>
    ${warnings.length ? `<section class="supply-chain-warnings"><div class="section-head compact"><h2>Import warnings</h2><span>${warnings.length}</span></div><div class="warning-list">${warnings.map(item => `<div><span>!</span><p>${esc(item)}</p></div>`).join('')}</div></section>` : ''}
    <section class="supply-chain-advisories">
      <div class="section-head compact"><h2>Advisory candidates</h2><span>${supplyChain.advisory_candidate_count || 0}</span></div>
      <p class="supply-chain-advisory-note">Only exact upstream revision-reference overlap appears here. A candidate still requires an explicit applicability / affectedness assessment.</p>
      ${(supplyChain.advisory_candidates || []).length ? `<div class="advisory-candidate-list">${supplyChain.advisory_candidates.map(item => `<a data-nav class="advisory-candidate-row" href="${esc(objectPath(item.event_revision_id))}"><span class="alert-mark">!</span><span><strong>${esc(item.external_id || item.event_class || 'Advisory')}</strong><small>${esc(item.provider || 'provider')} · ${esc(String((item.matching_component_revision_ids || []).length))} exact component revision match(es) · needs applicability assessment</small></span><span>${badge('candidate', 'warn')}</span></a>`).join('')}</div>` : '<div class="empty advisory-empty"><strong>No exact advisory candidates</strong><p>No recorded advisory currently has exact upstream revision refs overlapping this project inventory.</p></div>'}
      ${supplyChain.unresolved_advisory_count ? `<p class="form-help">${esc(String(supplyChain.unresolved_advisory_count))} recorded advisory identity/identities remain unresolved and were intentionally not fuzzy-matched into this project.</p>` : ''}
    </section>
    <section class="supply-chain-boundary-card">
      <span class="onboarding-kicker">SEMANTIC BOUNDARY</span>
      <h2>What this inventory does—and does not—say.</h2>
      <div class="supply-chain-boundaries">
        <code>manifest declaration != runtime use</code>
        <code>declared version != exact content unless digest/locator evidence supports it</code>
        <code>advisory identity match != affectedness verdict</code>
        <code>changed != invalid</code>
      </div>
      <p>Advisories can be resolved against these canonical component identities later, but affectedness remains a separate recorded assessment rather than a generic trust score.</p>
    </section>
  </div>`;
}

async function projectMonitorsPanel(project, monitors) {
  const data = await dashboard();
  const providers = data.status?.extensions?.monitor_target_providers || [];
  const specs = data.status?.extensions?.monitor_target_provider_specs || {};
  const providerOptions = [
    '<option value="source">URL / locator</option>',
    ...providers.map(name => {
      const spec = specs[name] || {};
      return `<option value="${esc(name)}">${esc(spec.label || name)} (plugin)</option>`;
    }),
  ].join('');
  const providerFields = providers.map(name => {
    const spec = specs[name] || {};
    const fields = Object.entries(spec.fields || {}).map(([fieldName, field]) => {
      const required = field.required !== false;
      return `<label class="grow"><span>${esc(field.label || fieldName)}${required ? '' : ' <small>optional</small>'}</span><input data-provider-field="${esc(fieldName)}" type="text" autocomplete="off" placeholder="${esc(field.placeholder || '')}" ${field.pattern ? `pattern="${esc(field.pattern)}"` : ''} ${required ? 'required' : ''} />${field.description ? `<small class="provider-field-help">${esc(field.description)}</small>` : ''}</label>`;
    }).join('');
    return `<div class="provider-config-fields" data-plugin-monitor-target="${esc(name)}" hidden>${spec.description ? `<p class="form-help">${esc(spec.description)}</p>` : ''}${fields}</div>`;
  }).join('');
  return `<div class="monitoring-layout">
    ${!monitors.length ? `<section class="monitor-first-run"><div><span class="onboarding-kicker">STEP 2 OF 3</span><h2>Add the first dependency.</h2><p>Choose a target your project genuinely relies on. Testamur will observe it now, then make later change visible without pretending that change automatically invalidates your work.</p></div><div class="monitor-first-run-examples"><span>Docs URL</span><span>Repository</span><span>Specification</span><span>Plugin target</span></div></section>` : ''}
    <section class="time-query-card">
      <div><h2>Add monitor</h2><p>A project can contain many monitors. Each monitor resolves a target Source and owns its own change/unavailable/recovery configuration.</p></div>
      <form data-add-project-monitor data-project-ref="${esc(project.project_id)}">
        <label><span>Type</span><select name="provider">${providerOptions}</select></label>
        <label class="grow" data-source-monitor-target><span>URL or locator</span><input name="locator" type="text" autocomplete="off" placeholder="https://example.com/spec" /></label>
        ${providerFields}
        <label class="grow"><span>Label <small>optional</small></span><input name="label" type="text" autocomplete="off" placeholder="Production spec" /></label>
        <label><span>Cadence</span><select name="interval"><option value="">Manual</option><option value="300">Every 5 minutes</option><option value="900">Every 15 minutes</option><option value="3600">Hourly</option><option value="21600">Every 6 hours</option><option value="86400">Daily</option></select></label>
        <button class="btn btn-primary" type="submit">Add monitor</button>
      </form>
      <div data-write-result></div>
      ${providers.length ? `<p class="form-help">Plugin monitor providers registered: ${providers.map(name => esc(specs[name]?.label || name)).join(', ')}.</p>` : '<p class="form-help">No plugin monitor providers are registered in this environment yet.</p>'}
    </section>
    <section class="table-card"><div class="table-title"><h2>Monitors</h2><div class="table-title-actions"><span>${monitors.length}</span>${monitors.length ? `<button class="btn btn-secondary btn-compact" type="button" data-refresh-project="${esc(project.slug || project.project_id)}">Refresh all</button>` : ''}</div></div>
      ${monitors.length ? `<div class="data-table">${monitors.map(monitor => `<div class="data-row monitor-table-row"><span class="status-dot status-${tone(monitor.latest_state)}"></span><a data-nav class="row-main monitor-object-link" href="${esc(objectPath(monitor.watch_id))}"><strong>${esc(monitor.label || domain(monitor.locator || monitor.source_id))}</strong><small>${esc(short(monitor.locator || monitor.source_id, 72))} · ${esc(cadenceLabel(monitor.interval_seconds))}</small></a><span>${esc((monitor.alert_on || []).join(', ') || 'no alerts')}</span><span>${esc(monitor.latest_state || 'not evaluated')}</span><span>${esc(ago(monitor.last_evaluated_at))}</span><div class="monitor-row-actions"><button class="btn btn-secondary btn-compact" type="button" data-refresh-monitor="${esc(monitor.watch_id)}" data-project-ref="${esc(project.slug || project.project_id)}">Refresh</button><a data-nav class="btn btn-secondary btn-compact" href="${esc(objectPath(monitor.watch_id))}">Open</a></div></div>`).join('')}</div>` : empty('No monitors', 'Add the first monitor above. Projects can contain as many independent monitors as needed.')}
    </section>
  </div>`;
}

async function projectPage(ref) {
  shell(loading('Loading project…'), true);
  try {
    const response = await api('/v1/project', { ref });
    const project = response.project || {};
    const monitors = response.monitors || [];
    const supplyChain = response.supply_chain || null;
    const requested = params().get('tab') || 'overview';
    const selected = ['overview', 'monitors', 'supply-chain'].includes(requested) ? requested : 'overview';
    const panel = selected === 'monitors'
      ? await projectMonitorsPanel(project, monitors)
      : selected === 'supply-chain'
        ? supplyChainPanel(project, supplyChain)
        : projectOverview(project, monitors, supplyChain);
    shell(`<section class="object-header">
      <div class="breadcrumbs"><a data-nav href="/projects">Projects</a><span>/</span><span>${esc(project.slug || ref)}</span></div>
      <div class="object-title-row"><div><h1>${esc(project.name || project.slug || 'Project')}</h1><p>${esc(project.description || 'No description')}</p></div>${badge(project.visibility || 'private')}</div>
    </section>
    <nav class="object-tabs"><a data-nav class="${selected === 'overview' ? 'active' : ''}" href="${esc(projectPath(project.slug || ref))}?tab=overview">Overview</a><a data-nav class="${selected === 'monitors' ? 'active' : ''}" href="${esc(projectPath(project.slug || ref))}?tab=monitors">Monitors <span class="tab-count">${monitors.length}</span></a><a data-nav class="${selected === 'supply-chain' ? 'active' : ''}" href="${esc(projectPath(project.slug || ref))}?tab=supply-chain">Supply chain <span class="tab-count">${supplyChain?.dependency_count || 0}</span></a></nav>
    <section id="object-panel" class="object-panel">${panel}</section>`, true);
    bindNavigation();
    const form = document.querySelector('[data-add-project-monitor]');
    form?.addEventListener('submit', addProjectMonitorFromForm);
    const updateProviderFields = () => {
      if (!form) return;
      const provider = form.elements.provider.value;
      const sourceField = form.querySelector('[data-source-monitor-target]');
      if (sourceField) sourceField.hidden = provider !== 'source';
      form.querySelectorAll('[data-plugin-monitor-target]').forEach(section => {
        section.hidden = section.dataset.pluginMonitorTarget !== provider;
        section.querySelectorAll('[data-provider-field]').forEach(input => {
          input.disabled = section.hidden;
        });
      });
    };
    form?.elements.provider?.addEventListener('change', updateProviderFields);
    updateProviderFields();
    document.querySelectorAll('[data-refresh-monitor]').forEach(button => {
      button.addEventListener('click', () => refreshMonitorFromButton(button));
    });
    document.querySelector('[data-refresh-project]')?.addEventListener('click', event => {
      refreshProjectMonitorsFromButton(event.currentTarget);
    });
  } catch (error) {
    errorPage(error, 'Project unavailable');
  }
}

async function runDueMonitorsFromButton(button) {
  const previous = button.textContent;
  button.disabled = true;
  button.textContent = 'Running…';
  try {
    const response = await apiWrite('/v1/monitors/run-due', {});
    await dashboard(true);
    await monitoringPage();
    const summary = response.summary || {};
    const heading = document.querySelector('.page-title h1');
    if (heading) {
      heading.title = `Due: ${summary.due || 0}; succeeded: ${summary.succeeded || 0}; failed: ${summary.failed || 0}; alerts: ${summary.alerts || 0}`;
    }
  } catch (error) {
    button.disabled = false;
    button.textContent = previous;
    button.title = error.message || String(error);
  }
}

async function refreshProjectMonitorsFromButton(button) {
  const projectRef = button.dataset.refreshProject;
  if (!projectRef) return;
  const result = document.querySelector('[data-write-result]');
  const previous = button.textContent;
  button.disabled = true;
  button.textContent = 'Refreshing…';
  if (result) result.innerHTML = loading('Refreshing all project monitors…');
  try {
    const response = await apiWrite('/v1/projects/refresh', { project_ref: projectRef });
    await dashboard(true);
    const summary = response.summary || {};
    await projectPage(projectRef);
    const refreshedResult = document.querySelector('[data-write-result]');
    if (refreshedResult) {
      refreshedResult.innerHTML = `<div class="flash ${Number(summary.failed || 0) ? 'flash-danger' : 'flash-success'}"><strong>${esc(String(summary.succeeded || 0))}/${esc(String(summary.total || 0))} refreshed</strong><span>${esc(String(summary.alerts || 0))} alert(s), ${esc(String(summary.failed || 0))} failed.</span></div>`;
    }
  } catch (error) {
    if (result) result.innerHTML = `<div class="flash flash-danger"><strong>${esc(error.code || 'refresh_failed')}</strong><span>${esc(error.message || error)}</span></div>`;
    button.disabled = false;
    button.textContent = previous;
  }
}

async function refreshMonitorFromButton(button) {
  const watchId = button.dataset.refreshMonitor;
  const projectRef = button.dataset.projectRef;
  if (!watchId || !projectRef) return;
  const result = document.querySelector('[data-write-result]');
  const previous = button.textContent;
  button.disabled = true;
  button.textContent = 'Refreshing…';
  if (result) result.innerHTML = loading('Refreshing monitor target and evaluating change…');
  try {
    const response = await apiWrite('/v1/monitors/refresh', { watch_id: watchId });
    await dashboard(true);
    if (result) {
      const state = response.evaluation?.operational_state || 'evaluated';
      const alert = response.alert?.event_type;
      result.innerHTML = `<div class="flash flash-success"><strong>${esc(state)}</strong><span>${alert ? `Alert: ${esc(alert)}.` : 'Monitor refreshed without a new alert.'}</span></div>`;
    }
    await projectPage(projectRef);
  } catch (error) {
    if (result) result.innerHTML = `<div class="flash flash-danger"><strong>${esc(error.code || 'refresh_failed')}</strong><span>${esc(error.message || error)}</span></div>`;
    button.disabled = false;
    button.textContent = previous;
  }
}

async function addProjectMonitorFromForm(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const result = form.parentElement.querySelector('[data-write-result]');
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  result.innerHTML = loading('Adding monitor…');
  try {
    const provider = form.elements.provider.value;
    const payload = {
      project_id: form.dataset.projectRef,
      provider,
      label: form.elements.label.value.trim() || null,
      interval_seconds: form.elements.interval.value ? Number(form.elements.interval.value) : null,
      alert_on: ['changed', 'unavailable', 'recovered'],
    };
    if (provider === 'source') {
      payload.locator = form.elements.locator.value.trim();
      if (!payload.locator) throw new Error('Enter a URL or locator.');
    } else {
      const section = [...form.querySelectorAll('[data-plugin-monitor-target]')]
        .find(item => item.dataset.pluginMonitorTarget === provider);
      payload.provider_config = {};
      section?.querySelectorAll('[data-provider-field]').forEach(input => {
        payload.provider_config[input.dataset.providerField] = input.value.trim();
      });
    }
    await apiWrite('/v1/monitors', payload);
    await dashboard(true);
    const detail = await api('/v1/project', { ref: form.dataset.projectRef });
    navigate(`${projectPath(detail.project.slug || form.dataset.projectRef)}?tab=monitors`);
  } catch (error) {
    result.innerHTML = `<div class="flash flash-danger"><strong>${esc(error.code || 'error')}</strong><span>${esc(error.message || error)}</span></div>`;
    button.disabled = false;
  }
}

function historyItemRef(item = {}) {
  return item.snapshot_id || item.revision_id || item.record_revision_id || item.watch_revision_id || '';
}

function historyItemLabel(item = {}, index = 0) {
  const ref = historyItemRef(item);
  const ordinal = item.ordinal ? `Revision ${item.ordinal}` : '';
  const at = item.observed_at || item.recorded_at || item.created_at;
  return [ordinal || short(ref, 18) || `Version ${index + 1}`, at ? new Date(at).toLocaleString() : ''].filter(Boolean).join(' · ');
}

function comparisonFacts(comparison = {}) {
  const ignored = new Set(['semantics', 'source_id', 'record_id']);
  return Object.entries(comparison)
    .filter(([key, value]) => !ignored.has(key) && typeof value !== 'object')
    .map(([key, value]) => {
      const label = key.replaceAll('_', ' ');
      const rendered = typeof value === 'boolean' ? (value ? 'Yes' : 'No') : value === null ? 'Not assessable' : String(value);
      const factTone = (key.endsWith('_changed') && value === true) ? 'warn' : (key === 'same_revision' && value === true) ? 'good' : 'neutral';
      return `<div class="compare-fact"><span>${esc(label)}</span><strong class="compare-fact-${factTone}">${esc(rendered)}</strong></div>`;
    }).join('');
}

async function comparePanel(ref) {
  try {
    const history = await api('/v1/history', { ref, limit: 100 });
    const items = (history.items || []).filter(item => historyItemRef(item));
    if (items.length < 2) {
      return empty('Nothing to compare yet', 'Testamur needs at least two recorded versions of this object before it can make a mechanical comparison.');
    }
    const requestedLeft = params().get('left');
    const requestedRight = params().get('right');
    const defaultRight = historyItemRef(items[0]);
    const defaultLeft = historyItemRef(items[1]);
    const knownRefs = new Set(items.map(historyItemRef));
    const left = knownRefs.has(requestedLeft) ? requestedLeft : defaultLeft;
    const right = knownRefs.has(requestedRight) ? requestedRight : defaultRight;
    const result = await api('/v1/compare', { left, right });
    const comparison = result.comparison || {};
    const changedFields = Object.entries(comparison).filter(([key]) => key.endsWith('_changed'));
    const changedKeys = changedFields.filter(([, value]) => value === true);
    const contentChanged = comparison.content_changed;
    const summary = contentChanged === true || changedKeys.length
      ? { text: 'Mechanical difference recorded', tone: 'warn' }
      : contentChanged === false || (changedFields.length > 0 && changedKeys.length === 0)
        ? { text: 'No mechanical difference recorded', tone: 'good' }
        : { text: 'Difference not fully assessable', tone: 'neutral' };
    const options = items.map((item, index) => {
      const value = historyItemRef(item);
      return `<option value="${esc(value)}">${esc(historyItemLabel(item, index))}</option>`;
    }).join('');
    return `<div class="compare-layout">
      <section class="compare-toolbar">
        <div><h2>Compare recorded versions</h2><p>Mechanical comparison only. Testamur does not infer semantic equivalence, truth, validity, or downstream breakage from this result.</p></div>
        <form data-compare-form data-ref="${esc(ref)}">
          <label><span>From</span><select name="left">${options}</select></label>
          <span class="compare-arrow">→</span>
          <label><span>To</span><select name="right">${options}</select></label>
          <button class="btn btn-secondary" type="submit">Compare</button>
        </form>
      </section>
      <section class="compare-result">
        <div class="compare-result-head"><div><span class="onboarding-kicker">MECHANICAL RESULT</span><h2>${esc(summary.text)}</h2></div>${badge(summary.text, summary.tone)}</div>
        <div class="compare-facts">${comparisonFacts(comparison)}</div>
        <div class="compare-boundary"><strong>What this means</strong><p>These fields describe identity-level or stored-field differences between two recorded versions.</p><strong>What it does not mean</strong><p>A change does not automatically make downstream work invalid. Use Impact / reliance evidence to decide what deserves review.</p></div>
      </section>
    </div>`;
  } catch (error) {
    if ([400, 404, 422].includes(error.status)) return empty('Comparison unavailable', error.message || 'These versions cannot be compared.');
    throw error;
  }
}

async function historyPanel(ref) {
  try {
    const history = await api('/v1/history', { ref, limit: 100 });
    const items = history.items || [];
    if (!items.length) return empty('No recorded history', 'This object has no additional recorded history.');
    return `<div class="timeline">${items.map(item => {
      const at = item.recorded_at || item.observed_at || item.created_at;
      const label = item.title || item.statement || (item.ordinal ? `Revision ${item.ordinal}` : item.status ? `${item.status} observation` : 'Recorded history item');
      const detail = item.content_hash ? short(item.content_hash, 48) : item.status || item.record_kind || '';
      return `<article class="timeline-item"><div class="timeline-dot"></div><div><div class="timeline-title"><strong>${esc(short(label, 100))}</strong><span>${esc(ago(at))}</span></div><div class="timeline-meta">${esc(detail)}</div></div></article>`;
    }).join('')}</div>`;
  } catch (error) {
    if ([400, 404, 422].includes(error.status)) return empty('No version history', 'History is not defined for this object type.');
    throw error;
  }
}

async function impactPanel(ref) {
  try {
    const value = await api('/v1/impact', { ref });
    const items = value.items || value.affected || value.impacts || value.results;
    if (Array.isArray(items)) {
      if (!items.length) return empty('No recorded downstream impact', 'Nothing currently projects as affected.');
      return `<div class="impact-list">${items.map(item => `<div class="impact-row"><div><strong>${esc(item.title || item.name || item.kind || 'Affected object')}</strong><small>${esc(item.reason || item.kind || '')}</small></div>${item.ref ? `<a data-nav href="${esc(objectPath(item.ref))}">Open</a>` : ''}</div>`).join('')}</div>`;
    }
    return `<div class="raw-block"><pre>${esc(pretty(value))}</pre></div>`;
  } catch (error) {
    if (error.status === 503) return empty('Impact provider not connected', 'This environment has no canonical reliance/lineage provider.');
    throw error;
  }
}

function timePanel(ref) {
  return `<section class="time-query-card"><div><h2>Time</h2><p>Ask what this environment had recorded at a specific time.</p></div><form data-time-form data-ref="${esc(ref)}"><label><span>Perspective</span><select name="mode"><option>KNOWN_AT</option><option>AVAILABLE_BY</option><option>EFFECTIVE_AT</option></select></label><label class="grow"><span>Timestamp</span><input name="at" type="datetime-local" required /></label><button class="btn btn-primary">Query</button></form><div data-time-result></div></section>`;
}

async function objectPage(ref, forcedTab = null) {
  shell(loading('Loading…'), true);
  try {
    const dto = await api('/v1/object', { ref });
    const identity = dto.object || {};
    const data = dto.data || {};
    const isSource = identity.kind === 'source';
    const comparable = ['source', 'record'].includes(identity.kind);
    const tabs = ['overview', 'history', ...(comparable ? ['compare'] : []), 'impact', 'time', 'raw'];
    const requested = forcedTab || params().get('tab') || 'overview';
    const selected = tabs.includes(requested) ? requested : 'overview';
    let panel;
    if (selected === 'overview') panel = isSource ? await sourceOverview(ref, data) : genericOverview(identity, data);
    else if (selected === 'history') panel = await historyPanel(ref);
    else if (selected === 'compare') panel = await comparePanel(ref);
    else if (selected === 'impact') panel = await impactPanel(ref);
    else if (selected === 'time') panel = timePanel(ref);
    else panel = `<div class="raw-block"><div class="raw-head"><h2>Raw canonical envelope</h2><span>Advanced</span></div><pre>${esc(pretty(dto))}</pre></div>`;

    const heading = objectTitle(identity, data);
    const summary = objectSummary(identity, data);
    shell(`<section class="object-header">
      <div class="breadcrumbs"><a data-nav href="/">Testamur</a><span>/</span><span>${esc(identity.kind || 'object')}</span></div>
      <div class="object-title-row"><div><h1>${esc(heading)}</h1><p>${esc(short(summary, 180))}</p></div>${badge(identity.kind || 'object')}</div>
    </section>
    <nav class="object-tabs">${tabs.map(tab => `<a data-nav class="${selected === tab ? 'active' : ''}" href="${esc(objectPath(ref))}?tab=${tab}">${tab[0].toUpperCase() + tab.slice(1)}</a>`).join('')}</nav>
    <section id="object-panel" class="object-panel">${panel}</section>`, true);
    bindNavigation();
    document.querySelector('[data-time-form]')?.addEventListener('submit', runTimeQuery);
    const compareForm = document.querySelector('[data-compare-form]');
    if (compareForm) {
      const requestedLeft = params().get('left');
      const requestedRight = params().get('right');
      if (requestedLeft && [...compareForm.elements.left.options].some(option => option.value === requestedLeft)) compareForm.elements.left.value = requestedLeft;
      else if (compareForm.elements.left.options.length > 1) compareForm.elements.left.selectedIndex = 1;
      if (requestedRight && [...compareForm.elements.right.options].some(option => option.value === requestedRight)) compareForm.elements.right.value = requestedRight;
      else compareForm.elements.right.selectedIndex = 0;
      compareForm.addEventListener('submit', event => {
        event.preventDefault();
        const next = new URL(objectPath(compareForm.dataset.ref), location.origin);
        next.searchParams.set('tab', 'compare');
        next.searchParams.set('left', compareForm.elements.left.value);
        next.searchParams.set('right', compareForm.elements.right.value);
        navigate(next.pathname + next.search);
      });
    }
  } catch (error) {
    errorPage(error, 'Object unavailable');
  }
}

async function runTimeQuery(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const result = form.parentElement.querySelector('[data-time-result]');
  result.innerHTML = loading('Querying…');
  try {
    const date = new Date(form.elements.at.value);
    const at = Number.isNaN(date.getTime()) ? form.elements.at.value : date.toISOString();
    const value = await api('/v1/temporal', { ref: form.dataset.ref, clause: `${form.elements.mode.value}=${at}` });
    result.innerHTML = `<div class="raw-block temporal-result"><pre>${esc(pretty(value))}</pre></div>`;
  } catch (error) {
    result.innerHTML = `<div class="flash flash-danger">${esc(error.message)}</div>`;
  }
}

function learnPage() {
  shell(`<div class="learn-shell">
    <section class="learn-hero">
      <span class="onboarding-kicker">WHAT IS TESTAMUR?</span>
      <h1>Remember what your work depended on.</h1>
      <p>You build something using documentation, a repository, a paper, an artifact, or an AI agent. Testamur records the exact basis. When that basis changes later, it shows you what may need review.</p>
      <div class="learn-actions"><a data-nav class="btn btn-primary" href="/quickstart">Start the 5-minute quickstart</a><a data-nav class="btn btn-secondary" href="/demo">Open example project</a></div>
    </section>
    <section class="learn-story">
      <article><span>1</span><div><h2>You use something.</h2><p>An API specification is part of the work behind your project.</p></div></article>
      <article><span>2</span><div><h2>Testamur keeps the observed version.</h2><p>The dependency is not just a URL. Its recorded revision remains inspectable later.</p></div></article>
      <article><span>3</span><div><h2>The upstream source changes.</h2><p>Testamur detects the change mechanically. It does not jump from “different” to “wrong”.</p></div></article>
      <article><span>4</span><div><h2>You review what depended on it.</h2><p>History, reliance, and affectedness tell you where revalidation is worth spending attention.</p></div></article>
    </section>
    <section class="concept-grid">
      <article><strong>Project</strong><p>A container for one piece of work and the things around it.</p></article>
      <article><strong>Monitor</strong><p>One operational watch on a URL, repository, artifact, or plugin target.</p></article>
      <article><strong>Revision</strong><p>The exact version Testamur observed at a point in time.</p></article>
      <article><strong>Reliance</strong><p>An explicit statement that some result actually depended on recorded evidence.</p></article>
      <article><strong>Affectedness</strong><p>A projection of what may need attention after upstream change—not a verdict that it is false.</p></article>
      <article><strong>Revalidation</strong><p>The act of checking downstream work again against the changed basis.</p></article>
    </section>
    <section class="semantic-firewall"><h2>Four rules worth remembering</h2><div><code>recorded ≠ verified</code><code>fetched ≠ relied</code><code>changed ≠ invalid</code><code>stale ≠ false</code></div></section>
  </div>`);
}

function docsPage() {
  shell(`<div class="docs-shell">
    <div class="docs-layout">
      <aside class="docs-nav">
        <strong>Testamur concepts</strong>
        <a href="#watch">What it watches</a>
        <a href="#project-monitor">Project vs Monitor</a>
        <a href="#source-revision">Source & Revision</a>
        <a href="#change">Change</a>
        <a href="#reliance">Reliance</a>
        <a href="#impact">Impact</a>
        <a href="#revalidation">Revalidation</a>
        <a href="#agents">Agents</a>
        <a href="#advanced">Advanced model</a>
      </aside>
      <article class="docs-content">
        <header class="docs-hero"><span class="onboarding-kicker">CONCEPT REFERENCE</span><h1>The model behind the UI.</h1><p>Start with the Quickstart if you have not completed the basic loop. This page explains the concepts you will encounter as Testamur records more history and provenance.</p><div class="learn-actions"><a data-nav class="btn btn-primary" href="/quickstart">5-minute quickstart</a><a data-nav class="btn btn-secondary" href="/demo">Example project</a></div></header>

        <section id="watch" class="docs-section"><h2>What Testamur watches</h2><p>A monitor points at something observable: a documentation URL, repository branch, specification, artifact, package, model release, or another target supplied by an integration. Monitoring records change and availability states; it does not automatically decide whether the new state is better, worse, true, or false.</p><div class="docs-rule">Monitor target → observed Source → recorded version/state</div></section>

        <section id="project-monitor" class="docs-section"><h2>Project vs Monitor</h2><p>A <strong>Project</strong> is the work you care about. A <strong>Monitor</strong> is one upstream thing around that work. One Project can therefore contain many Monitors.</p><div class="docs-diagram"><span>api-client</span><b>contains</b><span>API docs monitor</span><span>SDK repository monitor</span><span>schema artifact monitor</span></div></section>

        <section id="source-revision" class="docs-section"><h2>Source and Revision</h2><p>A Source gives an upstream object persistent identity. A Revision or Snapshot records what Testamur actually observed at a particular point. The locator can stay the same while the recorded revision changes.</p><div class="docs-rule">same URL ≠ same recorded revision</div><p>History is therefore first-class: an old result can remain tied to the basis that existed when the work happened.</p></section>

        <section id="change" class="docs-section"><h2>What “changed” means</h2><p>Change is mechanical evidence: hashes, stored fields, revision identity, or another provider-specific observation differs. The Compare surface deliberately reports those facts before interpretation.</p><div class="docs-invariants"><code>changed ≠ invalid</code><code>stale ≠ false</code></div><p>Whether a change matters depends on what downstream work relied on and which part of the basis moved.</p></section>

        <section id="reliance" class="docs-section"><h2>Reliance</h2><p>Reliance is stronger than exposure. A source may have been fetched, displayed, or present in an agent context without becoming a durable dependency of the result.</p><div class="docs-invariants"><code>fetched ≠ relied</code><code>EXPOSED_TO_MODEL ≠ RELIED</code></div><p>When Testamur records reliance, it creates an inspectable reason that later change can propagate into a review candidate.</p></section>

        <section id="impact" class="docs-section"><h2>Impact / affectedness</h2><p>Impact asks: given a recorded change or object, what downstream work is reachable through the available reliance/lineage evidence? It narrows attention. It is not a global trust score and it is not a proof of failure.</p><div class="docs-rule">upstream change + recorded reliance → review candidates</div></section>

        <section id="revalidation" class="docs-section"><h2>Revalidation</h2><p>Revalidation is the explicit review after the basis changes. You inspect the change, rerun or reconsider downstream work where necessary, then record what happened against the new basis. This is where judgment belongs.</p><div class="docs-flow"><span>exact basis</span><b>→</b><span>change</span><b>→</b><span>affected work</span><b>→</b><span>review</span><b>→</b><span>new evidence</span></div></section>

        <section id="agents" class="docs-section"><h2>Agents and integrations</h2><p>Codex and MCP-capable hosts can use Testamur's Source Gateway and integration contracts. The host may observe tool events and source access, but durable reliance remains an explicit semantic step rather than an inference from everything that appeared in context.</p><p><a href="https://github.com/Constanteer/testamur-plugins" target="_blank" rel="noreferrer">Open Testamur integrations →</a></p></section>

        <section id="advanced" class="docs-section"><h2>Advanced model</h2><div class="docs-definition-grid">
          <div><strong>Record</strong><span>A persistent assertion, requirement, observation, result, or other referable object with version history.</span></div>
          <div><strong>WorkSession</strong><span>A bounded episode of work in which evidence may be observed and reliance may be recorded.</span></div>
          <div><strong>Lineage</strong><span>The recorded derivational/provenance structure behind an object.</span></div>
          <div><strong>Affectedness</strong><span>A downstream projection from explicit evidence relationships; not a truth verdict.</span></div>
          <div><strong>Temporal clauses</strong><span>Queries such as KNOWN_AT / AVAILABLE_BY / EFFECTIVE_AT that keep different notions of time separate.</span></div>
          <div><strong>Canonical IDs / Raw</strong><span>Machine-facing identity and envelopes exposed for inspection, debugging, and integrations.</span></div>
        </div></section>

        <section class="semantic-firewall"><h2>The semantic firewall</h2><div><code>recorded ≠ verified</code><code>fetched ≠ relied</code><code>changed ≠ invalid</code><code>stale ≠ false</code></div></section>
      </article>
    </div>
  </div>`);
}

function quickstartPage() {
  shell(`<div class="guide-shell">
    <div class="page-title"><div><span class="onboarding-kicker">5-MINUTE QUICKSTART</span><h1>Track one dependency end to end.</h1><p>At the end, Testamur has one project, one monitored dependency, and one recorded observation it can compare against later.</p></div><a data-nav class="btn btn-secondary" href="/learn">Why this works</a></div>
    <ol class="quickstart-steps">
      <li><div class="quickstart-number">1</div><div><h2>Create a project</h2><p>Use the name of the work, not the dependency itself. For example <code>api-client</code>.</p><a data-nav class="btn btn-primary" href="/projects/new">Create project</a></div></li>
      <li><div class="quickstart-number">2</div><div><h2>Add something the work depends on</h2><p>Open the Project's Monitors tab. Paste a documentation URL, repository locator, or select a plugin-provided target.</p><div class="quickstart-example"><strong>Example</strong><code>https://example.com/api/spec</code></div></div></li>
      <li><div class="quickstart-number">3</div><div><h2>Record the first observation</h2><p>Choose <strong>Refresh</strong> for the monitor. This creates the baseline Testamur can compare with future observations.</p><a data-nav class="btn btn-secondary" href="/monitoring">Open monitoring</a></div></li>
      <li><div class="quickstart-number">4</div><div><h2>Come back after it changes</h2><p>A changed monitor gives you a history trail and, where reliance data exists, an affectedness view. Review the evidence before drawing a conclusion.</p><div class="quickstart-rules"><span>changed ≠ invalid</span><span>stale ≠ false</span></div></div></li>
      <li><div class="quickstart-number">5</div><div><h2>Connect your agent workflow</h2><p>Once the manual loop makes sense, install the Codex plugin or MCP gateway so agent work can use the same source/revision model.</p><a href="https://github.com/Constanteer/testamur-plugins" class="btn btn-secondary" target="_blank" rel="noreferrer">Open integrations</a></div></li>
    </ol>
    <section class="guide-next"><div><h2>Want to see the whole loop immediately?</h2><p>The example project starts after an upstream API spec has changed, so you can inspect history, compare the revisions, and see the downstream review path without waiting.</p></div><a data-nav class="btn btn-primary" href="/demo">Open example project</a></section>
  </div>`);
}

function demoPage() {
  const stage = ['overview','history','compare','impact','revalidate'].includes(params().get('stage')) ? params().get('stage') : 'overview';
  const tabs = [['overview','Overview'],['history','History'],['compare','Compare'],['impact','Affected work'],['revalidate','Revalidate']];
  const panels = {
    overview: `<div class="demo-overview"><section class="demo-primary"><div class="demo-source-head"><span class="status-dot status-warn"></span><div><strong>Payments API specification</strong><small>docs.example.test/payments/v2</small></div>${badge('changed','warn')}</div><div class="demo-facts"><span><small>Current revision</small><strong>rev_0194</strong></span><span><small>Last changed</small><strong>2 hours ago</strong></span><span><small>Observed revisions</small><strong>18</strong></span></div><p>The project previously relied on <code>rev_0193</code>. Testamur has now observed a mechanically different revision.</p></section><aside class="demo-note"><strong>What Testamur is saying</strong><p>The source changed.</p><strong>What it is not saying</strong><p>Your parser is wrong.</p></aside></div>`,
    history: `<div class="demo-timeline"><article><span class="demo-dot"></span><div><strong>rev_0194 · content changed</strong><small>Today, 20:11 · +12 −4 lines</small></div></article><article><span class="demo-line-dot"></span><div><strong>rev_0193 · observed unchanged</strong><small>Yesterday, 09:02</small></div></article><article><span class="demo-line-dot"></span><div><strong>rev_0192 · content changed</strong><small>Sep 12 · +3 −1 lines</small></div></article></div>`,
    compare: `<div class="demo-diff"><div class="demo-diff-head"><strong>rev_0193 → rev_0194</strong><span>mechanical text diff</span></div><pre><span class="diff-context">POST /charges</span>
<span class="diff-remove">- idempotency_key: optional string</span>
<span class="diff-add">+ idempotency_key: required string</span>
<span class="diff-add">+ requests without a key return HTTP 400</span></pre><div class="demo-diff-stats"><span>+12 additions</span><span>−4 deletions</span><span>2 sections changed</span></div></div>`,
    impact: `<div class="demo-impact"><article><div><strong>src/payments.ts</strong><small>Relied on request semantics from rev_0193</small></div>${badge('review suggested','warn')}</article><article><div><strong>docs/integration.md</strong><small>Cites the old optional idempotency behavior</small></div>${badge('review suggested','warn')}</article><article><div><strong>benchmark/read-path.ts</strong><small>No recorded reliance on the changed section</small></div>${badge('no projection','neutral')}</article><div class="demo-principle">Affectedness narrows attention. It is not a truth score and does not automatically invalidate downstream work.</div></div>`,
    revalidate: `<div class="demo-revalidate"><h2>Review the changed dependency</h2><p>You update the client to always send an idempotency key, rerun its tests, and record the new review against <code>rev_0194</code>.</p><div class="demo-revalidation-record"><span>✓</span><div><strong>Revalidation recorded</strong><small>src/payments.ts · based on rev_0194 · tests passed</small></div></div><p class="muted">This is the full Testamur loop: exact basis → change → affected work → explicit review.</p></div>`,
  };
  shell(`<div class="demo-shell">
    <div class="demo-banner"><span>INTERACTIVE EXAMPLE · no workspace data is changed</span><a data-nav href="/quickstart">Build this for real →</a></div>
    <div class="page-title"><div><h1>api-client</h1><p>Demo project · one upstream API specification changed after this client was built.</p></div>${badge('example project')}</div>
    <nav class="object-tabs">${tabs.map(([key,label])=>`<a data-nav class="${stage===key?'active':''}" href="/demo?stage=${key}">${label}</a>`).join('')}</nav>
    <section class="demo-panel">${panels[stage]}</section>
    <div class="demo-step-footer"><span>Follow the loop:</span>${tabs.map(([key,label],i)=>`<a data-nav class="${stage===key?'active':''}" href="/demo?stage=${key}">${i+1}. ${label}</a>`).join('')}</div>
  </div>`);
}

async function statusPage() {
  const hosted = accountState.mode === 'hosted';
  const workspace = accountState.activeWorkspace;
  const contextLabel = hosted ? (workspace?.label || 'Hosted workspace') : 'Local';
  const contextKind = hosted ? (workspace?.kind === 'organization' ? 'Organization workspace' : 'Personal workspace') : 'Local Testamur environment';
  shell(`<div class="page-title"><div><h1>Environment</h1><p>${esc(contextKind)}.</p></div></div>${loading('Reading status…')}`);
  try {
    const status = await api('/v1/status');
    const groups = [['Sources', status.sources || {}], ['Records', status.records || {}], ['Monitoring', status.watches || {}], ['Capabilities', status.extensions || {}]];
    shell(`<div class="page-title"><div><h1>Environment</h1><p>${esc(contextKind)} and available capabilities.</p></div>${badge(contextLabel, hosted ? 'neutral' : 'good')}</div><div class="status-grid">${groups.map(([name, value]) => `<section class="status-card"><h2>${esc(name)}</h2><div class="status-rows">${Object.entries(value).map(([key, item]) => `<div><span>${esc(key.replaceAll('_', ' '))}</span><strong>${esc(typeof item === 'object' ? short(pretty(item), 100) : item)}</strong></div>`).join('')}</div></section>`).join('')}</div>`);
  } catch (error) {
    errorPage(error, 'Status unavailable');
  }
}

function errorPage(error, title = 'Unable to load') {
  shell(`<div class="page-title"><h1>${esc(title)}</h1></div><section class="flash flash-danger"><strong>${esc(error.code || 'error')}</strong><span>${esc(error.message || error)}</span></section>`);
}

function navigate(path) {
  history.pushState({}, '', path);
  render();
}

function bindNavigation() {
  document.querySelectorAll('[data-nav]').forEach(anchor => {
    anchor.onclick = event => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      navigate(anchor.getAttribute('href'));
    };
  });
  document.querySelectorAll('[data-search-form]').forEach(form => {
    form.onsubmit = event => {
      event.preventDefault();
      const query = form.elements.q.value.trim();
      navigate(query ? `/explore?q=${encodeURIComponent(query)}` : '/explore');
    };
  });
  document.querySelectorAll('[data-signout]').forEach(button => {
    button.onclick = signOut;
  });
  document.querySelectorAll('[data-workspace-id]').forEach(button => {
    button.onclick = () => switchWorkspace(button.dataset.workspaceId);
  });
}

async function render() {
  document.body.classList.remove('modal-open');
  window.scrollTo(0, 0);
  const path = location.pathname;
  if (path === '/signin') return signInPage();
  if (path === '/signup') return signUpPage();

  const publicHostedRoute =
    path === '/learn' ||
    path === '/docs' ||
    path === '/quickstart' ||
    path === '/demo' ||
    path === '/status' ||
    path.startsWith('/users/');
  if (accountState.mode === 'hosted' && !accountState.authenticated && !publicHostedRoute) {
    const next = `${path}${location.search || ''}`;
    history.replaceState({}, '', `/signin?next=${encodeURIComponent(next)}`);
    return signInPage();
  }

  if (path === '/settings/profile') return profilePage();
  if (path === '/settings/security') return securityPage();
  if (path === '/settings/organizations') return organizationsPage();
  if (path.startsWith('/organizations/')) return organizationPage(decodeURIComponent(path.slice(15)));
  if (path.startsWith('/users/')) return publicProfilePage(decodeURIComponent(path.slice(7)));
  if (path === '/' || path === '/app') return homePage();
  if (path === '/projects') return projectsPage();
  if (path === '/projects/new') return newProjectPage();
  if (path.startsWith('/projects/')) return projectPage(decodeURIComponent(path.slice(10)));
  if (path === '/explore') return explorePage();
  if (path === '/monitoring') return monitoringPage();
  if (path === '/learn') return learnPage();
  if (path === '/docs') return docsPage();
  if (path === '/quickstart') return quickstartPage();
  if (path === '/demo') return demoPage();
  if (path === '/status') return statusPage();
  if (path.startsWith('/object/')) return objectPage(decodeURIComponent(path.slice(8)));
  if (path.startsWith('/impact/')) return objectPage(decodeURIComponent(path.slice(8)), 'impact');
  if (path.startsWith('/temporal/')) return objectPage(decodeURIComponent(path.slice(10)), 'time');
  if (path.startsWith('/compare/')) return errorPage({ code: 'legacy_compare_route', message: 'Comparison has moved into the object workflow.' }, 'Comparison moved');
  return errorPage({ code: 'route_not_found', message: 'This Testamur page does not exist.' }, 'Not found');
}

window.addEventListener('popstate', render);
window.addEventListener('keydown', event => {
  const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName);
  if (event.key === '/' && !typing) {
    event.preventDefault();
    document.querySelector('.global-search input')?.focus();
  }
  if (event.key === '?' && !typing) {
    event.preventDefault();
    const menu = document.querySelector('.help-menu');
    if (menu) menu.open = !menu.open;
  }
});

async function bootstrap() {
  await loadAccountState();
  await render();
}

bootstrap();
