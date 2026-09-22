from __future__ import annotations

from pathlib import Path

import testamur

from testamur.product_service import TestamurProductService
from testamur.project_review_surface import project_with_advisory_reviews
from testamur.web_app import dispatch_api_write


def test_project_web_surface_exposes_and_updates_repository_binding_lifecycle(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("requests==2.32.0\n", encoding="utf-8")

    service = TestamurProductService.integrated(tmp_path / "testamur.sqlite3")
    project = service.projects.create_project(name="Web binding lifecycle")
    binding = service.projects.bind_repository(project["project_id"], locator=str(repo))
    revision_id = binding["revision"]["binding_revision_id"]

    projected = project_with_advisory_reviews(service, project["project_id"])
    assert projected["ok"] is True
    assert len(projected["repository_bindings"]) == 1
    assert projected["repository_bindings"][0]["enabled"] is True
    assert projected["semantics"]["repository_binding_state_is_scanner_eligibility"] is True
    assert projected["semantics"]["repository_binding_state_implies_verification"] is False
    assert projected["semantics"]["repository_binding_state_implies_reliance"] is False
    assert projected["semantics"]["repository_binding_state_implies_affectedness"] is False

    response = dispatch_api_write(
        service,
        "/v1/projects/repository-binding-state",
        {"project_ref": project["project_id"], "binding": "primary", "enabled": False},
    )
    assert response["status"] == 200
    body = response["body"]
    assert body["binding"]["enabled"] is False
    assert body["binding"]["revision"]["binding_revision_id"] == revision_id
    assert body["semantics"]["binding_state_is_scanner_eligibility"] is True
    assert body["semantics"]["binding_state_implies_invalidity"] is False
    assert body["semantics"]["binding_state_implies_affectedness"] is False
    assert body["semantics"]["generic_trust_score_used"] is False

    projected = project_with_advisory_reviews(service, project["project_id"])
    assert projected["repository_bindings"][0]["enabled"] is False
    assert projected["repository_bindings"][0]["revision"]["binding_revision_id"] == revision_id


def test_project_web_app_explains_binding_state_without_semantic_shortcuts():
    script = (Path(testamur.__file__).with_name("web") / "app.js").read_text(encoding="utf-8")
    for phrase in (
        "Repository scanners",
        "Repository scanner inputs",
        "Manage inputs",
        "Pause scans",
        "Enable scans",
        "scanner eligibility only",
        "Each binding has independent scanner eligibility",
        "does not mean the repository is invalid, affected, verified, or relied upon",
        "/v1/projects/repository-binding-state",
    ):
        assert phrase in script


def test_project_web_projection_preserves_multiple_repository_binding_states(tmp_path):
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    service = TestamurProductService.integrated(tmp_path / "multi.sqlite3")
    project = service.projects.create_project(name="Multiple repository inputs")
    service.projects.bind_repository(project["project_id"], locator=str(one), binding_key="primary")
    service.projects.bind_repository(project["project_id"], locator=str(two), binding_key="docs")

    response = dispatch_api_write(
        service,
        "/v1/projects/repository-binding-state",
        {"project_ref": project["project_id"], "binding": "docs", "enabled": False},
    )
    assert response["status"] == 200

    projected = project_with_advisory_reviews(service, project["project_id"])
    states = {item["binding_key"]: item["enabled"] for item in projected["repository_bindings"]}
    assert states == {"docs": False, "primary": True}
