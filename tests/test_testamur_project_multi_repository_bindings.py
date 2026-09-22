from __future__ import annotations

from testamur.product_service import TestamurProductService
from testamur.supply_chain import scan_bound_project_supply_chain


def test_project_keeps_multiple_explicit_repository_bindings_and_scans_selected_binding(tmp_path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / "requirements.txt").write_text("requests==2.32.5\n", encoding="utf-8")
    (second_root / "requirements.txt").write_text("flask==3.1.2\n", encoding="utf-8")

    service = TestamurProductService(tmp_path / "state" / "evidence.db")
    project = service.projects.create_project(name="multi-repo")

    primary = service.projects.bind_repository(
        project["project_id"], locator=str(first_root), binding_key="primary"
    )
    docs = service.projects.bind_repository(
        project["project_id"], locator=str(second_root), binding_key="docs"
    )

    bindings = service.projects.repository_bindings(project["project_id"])
    assert [item["binding_key"] for item in bindings] == ["docs", "primary"]
    assert {item["binding_id"] for item in bindings} == {
        primary["binding"]["binding_id"],
        docs["binding"]["binding_id"],
    }

    first_scan = scan_bound_project_supply_chain(
        service, project["project_id"], binding_key="primary"
    )
    second_scan = scan_bound_project_supply_chain(
        service, project["project_id"], binding_key="docs"
    )

    assert first_scan["repository_binding_revision_id"] == primary["revision"]["binding_revision_id"]
    assert second_scan["repository_binding_revision_id"] == docs["revision"]["binding_revision_id"]
    assert first_scan["repository_binding_revision_id"] != second_scan["repository_binding_revision_id"]
    assert first_scan["dependency_count"] == 1
    assert second_scan["dependency_count"] == 1

    # Selecting/scanning one binding must not rewrite or infer the other binding.
    after = service.projects.repository_bindings(project["project_id"])
    assert [item["revision"]["binding_revision_id"] for item in after] == [
        docs["revision"]["binding_revision_id"],
        primary["revision"]["binding_revision_id"],
    ]
    assert all(
        item["revision"]["semantics"]["binding_implies_reliance"] is False
        for item in after
    )
