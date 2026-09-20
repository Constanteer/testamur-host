from __future__ import annotations

from testamur.product_service import TestamurProductService
from testamur.supply_chain import (
    diff_project_supply_chain,
    project_supply_chain_history,
    scan_bound_project_supply_chain,
)


def test_identical_rescan_appends_observation_without_revising_state(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "requirements.txt").write_text("requests==2.32.5\n", encoding="utf-8")

    service = TestamurProductService(tmp_path / "state" / "evidence.db")
    project = service.projects.create_project(name="stable")
    service.projects.bind_repository(project["project_id"], locator=str(root))

    first = scan_bound_project_supply_chain(service, project["project_id"])
    second = scan_bound_project_supply_chain(service, project["project_id"])

    assert second["scan_revision_id"] != first["scan_revision_id"]
    assert second["scan_revision_created"] is True
    assert second["manifest_revisions_created"] == 0
    assert second["dependency_revisions_created"] == 0

    history = project_supply_chain_history(service, project["project_id"])
    assert [item["revision_id"] for item in history[:2]] == [
        second["scan_revision_id"],
        first["scan_revision_id"],
    ]

    diff = diff_project_supply_chain(service, project["project_id"])
    assert diff["counts"] == {
        "manifests_added": 0,
        "manifests_removed": 0,
        "manifests_changed": 0,
        "dependencies_added": 0,
        "dependencies_removed": 0,
        "dependencies_changed": 0,
    }
    assert diff["semantics"]["mechanical_only"] is True
    assert diff["semantics"]["changed_implies_invalid"] is False
    assert diff["semantics"]["affectedness_inferred"] is False
