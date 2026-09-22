from __future__ import annotations

import pytest

from testamur.product_service import TestamurProductService
from testamur.supply_chain import diff_project_supply_chain, scan_bound_project_supply_chain


def test_implicit_diff_uses_previous_scan_from_same_repository_binding(tmp_path) -> None:
    primary_root = tmp_path / "primary"
    docs_root = tmp_path / "docs"
    primary_root.mkdir()
    docs_root.mkdir()
    (primary_root / "requirements.txt").write_text("requests==2.32.4\n", encoding="utf-8")
    (docs_root / "requirements.txt").write_text("flask==3.1.2\n", encoding="utf-8")

    service = TestamurProductService(tmp_path / "state" / "evidence.db")
    project = service.projects.create_project(name="binding-scoped-diff")
    primary = service.projects.bind_repository(
        project["project_id"], locator=str(primary_root), binding_key="primary"
    )
    docs = service.projects.bind_repository(
        project["project_id"], locator=str(docs_root), binding_key="docs"
    )

    first_primary = scan_bound_project_supply_chain(
        service, project["project_id"], binding_key="primary"
    )
    docs_scan = scan_bound_project_supply_chain(
        service, project["project_id"], binding_key="docs"
    )
    (primary_root / "requirements.txt").write_text("requests==2.32.5\n", encoding="utf-8")
    second_primary = scan_bound_project_supply_chain(
        service, project["project_id"], binding_key="primary"
    )

    diff = diff_project_supply_chain(service, project["project_id"])
    assert diff["from_scan_revision_id"] == first_primary["scan_revision_id"]
    assert diff["to_scan_revision_id"] == second_primary["scan_revision_id"]
    assert diff["repository_bindings"]["from"]["binding_id"] == primary["binding"]["binding_id"]
    assert diff["repository_bindings"]["to"]["binding_id"] == primary["binding"]["binding_id"]
    assert diff["repository_bindings"]["same_binding"] is True
    assert diff["counts"]["dependencies_added"] == 0
    assert diff["counts"]["dependencies_removed"] == 0
    assert diff["counts"]["dependencies_changed"] == 1
    assert diff["dependencies"]["changed"][0]["name"] == "requests"
    assert docs_scan["repository_binding_id"] == docs["binding"]["binding_id"]


def test_explicit_diff_rejects_known_cross_binding_comparison(tmp_path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / "requirements.txt").write_text("requests==2.32.5\n", encoding="utf-8")
    (second_root / "requirements.txt").write_text("flask==3.1.2\n", encoding="utf-8")

    service = TestamurProductService(tmp_path / "state" / "evidence.db")
    project = service.projects.create_project(name="reject-cross-binding-diff")
    service.projects.bind_repository(
        project["project_id"], locator=str(first_root), binding_key="primary"
    )
    service.projects.bind_repository(
        project["project_id"], locator=str(second_root), binding_key="docs"
    )

    primary_scan = scan_bound_project_supply_chain(
        service, project["project_id"], binding_key="primary"
    )
    docs_scan = scan_bound_project_supply_chain(
        service, project["project_id"], binding_key="docs"
    )

    with pytest.raises(ValueError, match="different repository bindings"):
        diff_project_supply_chain(
            service,
            project["project_id"],
            from_scan_revision_id=primary_scan["scan_revision_id"],
            to_scan_revision_id=docs_scan["scan_revision_id"],
        )
