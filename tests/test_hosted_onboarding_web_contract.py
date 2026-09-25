from pathlib import Path

from apps.web.hosted_app import _LEGACY_PUBLIC_ROUTE_ALIASES


APP = (Path(__file__).parents[1] / "testamur" / "web" / "app.js").read_text(encoding="utf-8")


def test_hosted_bundle_keeps_launch_learning_routes():
    for route in ("/quickstart", "/demo", "/learn", "/docs", "/integrations"):
        assert f'href="{route}"' in APP

    assert "Make Testamur useful in three steps." in APP
    assert "A monitored source changed. That does not mean your work is wrong." in APP
    assert "Revalidation is where judgment belongs." in APP


def test_hosted_bundle_keeps_demo_and_agent_entry_points():
    assert "function demoPage" in APP
    assert "mechanical text diff" in APP
    assert "Affectedness narrows attention." in APP
    assert "Codex" in APP
    assert "Any MCP client" in APP
    assert "testamur-gateway-mcp" in APP


def test_hosted_launch_copy_keeps_semantic_firewall():
    assert "recorded ≠ verified" in APP
    assert "fetched ≠ relied" in APP
    assert "changed ≠ invalid" in APP
    assert "stale ≠ false" in APP
    assert "EXPOSED_TO_MODEL ≠ RELIED" in APP

    launch_start = APP.index("function learnPage")
    launch_end = APP.index("async function statusPage")
    launch_surface = APP[launch_start:launch_end].lower()
    assert "trust score" not in launch_surface
    assert "confidence score" not in launch_surface


def test_hosted_project_overview_keeps_primary_first_monitor_action():
    assert "object-about-primary" in APP
    assert "Add first monitor" in APP
    assert "upstream things your work depends on" in APP
    assert '<span>Slug</span><strong>${esc(project.slug)}</strong>' not in APP


def test_hosted_monitor_form_keeps_clear_primary_action():
    assert "What should Testamur watch?" in APP
    assert "monitor-submit-primary" in APP
    assert "Only when I ask" in APP
    assert "a detected change is not an invalidity verdict" in APP


def test_hosted_monitor_flow_keeps_first_baseline_step():
    assert "monitor-baseline-card" in APP
    assert "Record the first observation." in APP
    assert "Check now" in APP
    assert "recorded ≠ verified · fetched ≠ relied" in APP


def test_legacy_launch_urls_handoff_to_current_hosted_routes():
    assert _LEGACY_PUBLIC_ROUTE_ALIASES == {
        "/index.html": "/",
        "/learn.html": "/learn",
        "/docs.html": "/docs",
        "/quickstart.html": "/quickstart",
        "/demo.html": "/demo",
        "/plugins.html": "/integrations",
    }
