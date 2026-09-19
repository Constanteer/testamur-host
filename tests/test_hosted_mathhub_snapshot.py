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
    assert "Browse by area" in library
    assert "Advanced Lean settings" in library
    assert "data-library-retry-setup" in library
    assert "Your MathHub is ready for mathematics." in library
    assert "Browse the math library" in library
    assert "Show Lean proof" in argument
    assert "How these results connect." in argument
    assert "<summary>Advanced formal search</summary>" in structural
    assert "button.textContent='Import tasks'" not in tasks


def test_hosted_mathhub_runtime_owns_automatic_setup_and_imports() -> None:
    server = read(MATHHUB / "server.py")
    mathhub = read(MATHHUB / "mathhub.py")
    start = read(ROOT / "deploy" / "start.sh")

    assert "RUNTIME_SETUP_MARKER" in server
    assert '[elan, "toolchain", "install", toolchain]' in server
    assert '[lake, "exe", "cache", "get"]' in server
    assert "start_import_worker()" in mathhub
    assert "TASKS.step(task_id)" in mathhub
    assert "bootstrap_mathhub()" not in start
    assert "MathHub immediately" in start


def test_hosted_mathhub_snapshot_marker_tracks_canonical_merge() -> None:
    marker = read(ROOT / "MATHHUB_SNAPSHOT").strip()
    assert marker == "Constanteer/Mathub main @ 5b07323e586673af49efb0e654aec7965547eaa2"
