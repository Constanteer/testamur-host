from __future__ import annotations

from testamur import source_gateway_project_mcp as project_mcp
from testamur.product_service import TestamurProductService


def _state(monkeypatch, tmp_path):
    db = tmp_path / "state" / "evidence.db"
    monkeypatch.setattr(project_mcp._base, "_db_path", lambda: db)
    return TestamurProductService.integrated(db)


def test_supply_chain_tools_are_host_neutral_and_non_verdict_bearing() -> None:
    project_mcp._install()
    tools = {tool["name"]: tool for tool in project_mcp._base.TOOLS}
    assert "testamur.project_scan" in tools
    assert "testamur.project_supply_chain" in tools
    assert "testamur.project_supply_chain_diff" in tools
    diff = tools["testamur.project_supply_chain_diff"]
    assert "verdict" not in diff["inputSchema"]["properties"]
    assert "trust_score" not in diff["inputSchema"]["properties"]


def test_project_scan_and_diff_delegate_to_canonical_immutable_lifecycle(monkeypatch, tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    requirements = root / "requirements.txt"
    requirements.write_text("requests==2.32.4\nflask==3.1.2\n", encoding="utf-8")

    service = _state(monkeypatch, tmp_path)
    project = service.projects.create_project(name="mcp-existing")
    service.projects.bind_repository(project["project_id"], locator=str(root))

    first = project_mcp._project_scan({"project_ref": project["project_id"]})
    assert first["ok"] is True
    assert first["semantics"]["recorded_is_not_verified"] is True
    assert first["semantics"]["recorded_is_not_relied"] is True

    requirements.write_text("requests==2.32.5\n", encoding="utf-8")
    second = project_mcp._project_scan({"project_ref": project["project_id"]})
    assert second["scan_revision_id"] != first["scan_revision_id"]

    current = project_mcp._project_supply_chain({"project_ref": project["project_id"]})
    assert current["supply_chain"]["scan_revision_id"] == second["scan_revision_id"]
    assert current["supply_chain"]["dependency_count"] == 1

    diff = project_mcp._project_supply_chain_diff({"project_ref": project["project_id"]})
    assert diff["counts"]["dependencies_removed"] == 1
    assert diff["counts"]["dependencies_upgraded"] == 1
    assert diff["dependencies"]["removed"][0]["name"] == "flask"
    assert diff["dependencies"]["changed"][0]["version_transition"] == "upgraded"
    assert diff["semantics"]["changed_implies_invalid"] is False
    assert diff["semantics"]["dependency_change_implies_vulnerable"] is False
    assert diff["semantics"]["lineage_is_not_affectedness_verdict"] is True
    assert diff["semantics"]["generic_trust_score_used"] is False
