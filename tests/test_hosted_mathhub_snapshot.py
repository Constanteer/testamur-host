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
    assert "Browse library" in library
    assert "Exact Lean record" in library
    assert '<code class="library-lean-name">' not in library
    assert "Browse by area" in library
    assert "Advanced Lean settings" in library
    assert "data-library-retry-setup" in library
    assert "Your MathHub is ready for mathematics." in library
    assert "Browse the math library" in library
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


def test_hosted_mathhub_runtime_owns_automatic_setup_and_imports() -> None:
    server = read(MATHHUB / "server.py")
    mathhub = read(MATHHUB / "mathhub.py")
    start = read(ROOT / "deploy" / "start.sh")

    assert "RUNTIME_SETUP_MARKER" in server
    assert '[elan, "toolchain", "install", toolchain]' in server
    assert '[lake, "exe", "cache", "get"]' in server
    assert "start_import_worker()" in mathhub
    assert "start_library_warmup()" in mathhub
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


def test_hosted_mathhub_snapshot_marker_tracks_canonical_merge() -> None:
    marker = read(ROOT / "MATHHUB_SNAPSHOT").strip()
    assert marker == "Constanteer/Mathub main @ 977b0daf2ac0cd407b3db4c0478fa6db6a43860d"
