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
    assert "Exact Lean record" in library
    assert '<code class="library-lean-name">' not in library
    assert "Fields" in library
    assert "<summary>Lean settings</summary>" in library
    assert "data-library-retry-setup" in library
    assert "Your MathHub will not stay empty." in library
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
    assert "Import a file or link" in source_ui
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
    assert "last_error" in mathhub
    assert "/api/library/cluster" in mathhub
    assert "/api/library/theorem" in mathhub
    assert "/api/library/random" in mathhub
    assert 'MATHHUB_AUTO_STARTER_CLAIMS","0"' in mathhub
    app = read(MATHHUB / "app.js")
    library = read(MATHHUB / "library-browser-ui.js")
    server = read(MATHHUB / "server.py")
    assert '"setup_stage"' in server
    assert "setupStage:d.setup_stage" in app
    assert "library-setup-progress" in library
    assert 'MATHHUB_AUTO_INDEX_NAMESPACES","Nat"' in mathhub
    assert "TASKS.step(task_id)" in mathhub
    assert "bootstrap_mathhub()" not in start
    assert "MathHub immediately" in start
    graph_global = read(MATHHUB / "graph-global.js")
    graph_css = read(MATHHUB / "graph-global.css")
    assert "Mathlib, directly from the pinned environment." in graph_global
    assert "/api/library/world" in graph_global
    assert "/api/library/cluster" in graph_global
    assert "Random theorem" in graph_global
    assert "Import as Claim" in graph_global
    assert "Statement references" in graph_global
    assert "Proof references" in graph_global
    assert "Mathlib index retrying" in graph_global
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
    assert marker == "Constanteer/Mathub main @ 4db85d2c4d574601a4a31156afe73b248a6d253c"
