from __future__ import annotations

import json

from testamur.product_service import TestamurProductService
from testamur.supply_chain import scan_bound_project_supply_chain


def _statement(service: TestamurProductService, revision_id: str) -> dict:
    revision = service.records.get_revision(revision_id)
    assert revision is not None
    return json.loads(str(revision["statement"]))


def test_bound_scan_revision_records_selected_repository_binding_provenance(tmp_path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / "requirements.txt").write_text("requests==2.32.5\n", encoding="utf-8")
    (second_root / "requirements.txt").write_text("flask==3.1.2\n", encoding="utf-8")

    service = TestamurProductService(tmp_path / "state" / "evidence.db")
    project = service.projects.create_project(name="binding provenance")
    primary = service.projects.bind_repository(
        project["project_id"], locator=str(first_root), binding_key="primary"
    )
    docs = service.projects.bind_repository(
        project["project_id"], locator=str(second_root), binding_key="docs"
    )

    first = scan_bound_project_supply_chain(
        service, project["project_id"], binding_key="primary"
    )
    second = scan_bound_project_supply_chain(
        service, project["project_id"], binding_key="docs"
    )

    first_statement = _statement(service, first["scan_revision_id"])
    second_statement = _statement(service, second["scan_revision_id"])

    assert first_statement["repository_binding"] == {
        "binding_id": primary["binding"]["binding_id"],
        "binding_key": "primary",
        "binding_revision_id": primary["revision"]["binding_revision_id"],
    }
    assert second_statement["repository_binding"] == {
        "binding_id": docs["binding"]["binding_id"],
        "binding_key": "docs",
        "binding_revision_id": docs["revision"]["binding_revision_id"],
    }
    assert first_statement["repository_binding"] != second_statement["repository_binding"]

    for statement in (first_statement, second_statement):
        semantics = statement["semantics"]
        assert semantics["repository_binding_is_scanner_input"] is True
        assert semantics["repository_binding_provenance_implies_reliance"] is False
        assert semantics["repository_binding_provenance_implies_verification"] is False
        assert semantics["repository_binding_provenance_implies_affectedness"] is False
