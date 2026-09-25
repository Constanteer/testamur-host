from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATHHUB = ROOT / "mathhub_app"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_hosted_mathhub_snapshot_is_math_first() -> None:
    argument = read(MATHHUB / "argument-ui.js")
    library = read(MATHHUB / "library-browser-ui.js")
    structural = read(MATHHUB / "structural-search-ui.js")
    tasks = read(MATHHUB / "import-task-ui.js")

    assert "Explore formal mathematics." in argument
    assert "Browse Mathlib as mathematics." in library
    assert "Source location" in library
    assert '<code class="library-lean-name">' not in library
    assert "Fields" in library
    assert "<summary>Lean settings</summary>" in library
    assert "data-library-retry-setup" in library
    assert "Mathlib is already available." in library
    assert "Open Math library" in library
    assert "Show Lean proof" in argument
    assert "How these results connect." in argument
    registry = read(MATHHUB / "registry-client.js")
    catalog = read(MATHHUB / "catalog-client.js")
    assert "Browse existing mathematics" in registry
    assert "Formalization needed before publishing." in registry
    assert "Statements loaded" in catalog
    assert "<summary>Advanced formal search</summary>" in structural
    assert "button.textContent='Import tasks'" not in tasks
    source_ui = read(MATHHUB / "source-ingest-ui.js")
    source_css = read(MATHHUB / "source-ingest-ui.css")
    source_backend = read(MATHHUB / "source_ingest.py")
    index = read(MATHHUB / "index.html")
    assert "Add Lean" in source_ui
    assert "Browse the math library" in source_ui
    assert "source-ingest-ui.js" in index
    assert "source-ingest-ui.css" in index
    assert "normalize_remote_url" in source_backend
    assert "address.is_global" in source_backend
    assert "mathhub-dropzone" in source_css
    assert "function mhOwnedClaims()" in source_ui
    assert 'c.registry===true' in source_ui
    assert 'id="source-draft-test"' in source_ui
    assert 'id="draft-test"' not in source_ui
    assert "created.build_error" in source_ui
    assert "Loading your MathHub…" in source_ui
    assert "state.runtime.setupStep" in source_ui
    assert "state.runtime.setupSteps" in source_ui
    assert "mathhub-home-progress" in source_css
    assert "if _END_RE.match(lines[cursor])" in source_backend


def test_hosted_mathhub_runtime_owns_automatic_setup_and_imports() -> None:
    server = read(MATHHUB / "server.py")
    mathhub = read(MATHHUB / "mathhub.py")
    start = read(ROOT / "deploy" / "start.sh")

    assert "RUNTIME_SETUP_MARKER" in server
    assert '[elan, "toolchain", "install", toolchain]' in server
    assert '[lake, "exe", "cache", "get"]' in server
    assert "_mathlib_checkout_is_corrupt" in server
    assert "_reset_mathlib_checkout" in server
    assert "_transient_git_failure" in server
    assert "start_import_worker()" in mathhub
    assert "start_library_warmup()" in mathhub
    assert "ensure_global_library_scan" in mathhub
    assert "/api/library/world" in mathhub
    # Global Mathlib scan retry state is owned by namespace_browser, not the HTTP router.
    assert "/api/library/cluster" in mathhub
    assert "/api/library/module" in mathhub
    assert "/api/library/theorem" in mathhub
    assert "/api/library/dependencies" in mathhub
    assert "/api/library/dependencies/batch" in mathhub
    assert "/api/library/random" in mathhub
    assert 'MATHHUB_AUTO_STARTER_CLAIMS","0"' in mathhub
    app = read(MATHHUB / "app.js")
    library = read(MATHHUB / "library-browser-ui.js")
    server = read(MATHHUB / "server.py")
    assert '"setup_stage"' in server
    assert "setupStage:d.setup_stage" in app
    assert "library-setup-progress" in library
    assert 'MATHHUB_AUTO_INDEX_NAMESPACES","Nat"' in mathhub
    namespace_browser = read(MATHHUB / "namespace_browser.py")
    assert "last_error" in namespace_browser
    assert "library_declaration_dependencies" in namespace_browser
    assert '"dependency_graph_version": "lean-direct-fast-v2"' in namespace_browser
    assert "inspect_direct_dependencies" in namespace_browser
    assert "inspect_direct_dependencies_batch" in namespace_browser
    assert "_scan_mathlib_source_tree" in namespace_browser
    assert '"index_method": "mathlib-source-files"' in namespace_browser
    assert '"lean_invocations": 0' in namespace_browser
    assert "TASKS.step(task_id)" in mathhub
    assert "bootstrap_mathhub()" not in start
    assert "MathHub immediately" in start
    graph_global = read(MATHHUB / "graph-global.js")
    graph_css = read(MATHHUB / "graph-global.css")
    assert "Explore Mathlib by module." in graph_global
    assert "/api/library/world" in graph_global
    assert "/api/library/cluster" in graph_global
    assert "/api/library/module?path=" in graph_global
    assert "top-level modules" in graph_global
    assert "Mathlib module" in graph_global
    assert "mathhub-global-world-v3" in graph_global
    assert "fitGlobalGraphNodes" in graph_global
    assert "focusGlobalBranch" in graph_global
    assert "libraryBreadcrumbHtml" in graph_global
    assert "openLibraryModule" in graph_global
    assert "data-library-module-back" in graph_global
    assert "Module open failed" in graph_global
    graph_budgets = read(MATHHUB / "graph-budgets.js")
    assert "graphBudgetBaseExpandGlobalArea" in graph_budgets
    assert "graphGlobalState.libraryMode" in graph_budgets
    assert "return graphBudgetBaseExpandGlobalArea(clusterId)" in graph_budgets
    assert "Focus branch" in graph_global
    assert "Random theorem" in graph_global
    assert "Open dependencies" in graph_global
    assert "source index" in graph_global
    assert "Lean dependency graph" in graph_global
    assert "Every arrow means “depends on”" in graph_global
    assert "0 Lean calls" in graph_global
    assert "Inspecting theorem with Lean" not in graph_global
    assert "Mathlib index retrying" in graph_global
    assert "/api/lean/inspect" not in library
    assert "data-library-open-dependencies" in library
    assert "Expand next level" in graph_global
    assert "data-library-open-dependency-graph" in graph_global
    assert "dependencyFocusCard" in graph_global
    assert "caching the next step" in graph_global
    assert "prefetchDependencyLookahead" in graph_global
    assert "Add to MathHub (" not in library
    assert ".global-starter-empty" in graph_css
    assert ".library-theorem-type" in graph_css


def test_healthz_exposes_mathhub_runtime_progress() -> None:
    hosted = read(ROOT / "apps" / "web" / "hosted_app.py")
    assert '"setup_running"' in hosted
    assert '"setup_stage"' in hosted
    assert '"setup_step"' in hosted
    assert '"setup_steps"' in hosted
    assert '"mathlib_revision"' in hosted
    assert '"library"' in hosted
    assert "/api/library/status" in hosted
    assert '"snapshot"' in hosted


def test_hosted_mathhub_snapshot_marker_tracks_canonical_merge() -> None:
    marker = read(ROOT / "MATHHUB_SNAPSHOT").strip()
    assert marker == "Constanteer/Mathub main @ dbdeb1822234a09f63b0caa4049d0ebc745e31bd"
