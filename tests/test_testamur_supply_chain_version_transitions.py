from __future__ import annotations

from testamur.product_service import TestamurProductService
from testamur.supply_chain import diff_project_supply_chain, scan_bound_project_supply_chain


def _scan(service, project_id, requirements, text):
    requirements.write_text(text, encoding="utf-8")
    return scan_bound_project_supply_chain(service, project_id)


def test_numeric_dependency_upgrade_is_explicit_but_not_a_verdict(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    requirements = root / "requirements.txt"
    service = TestamurProductService(tmp_path / "state" / "evidence.db")
    project = service.projects.create_project(name="upgrade-demo")
    service.projects.bind_repository(project["project_id"], locator=str(root))

    first = _scan(service, project["project_id"], requirements, "requests==2.32.4\n")
    second = _scan(service, project["project_id"], requirements, "requests==2.32.5\n")
    diff = diff_project_supply_chain(
        service,
        project["project_id"],
        from_scan_revision_id=first["scan_revision_id"],
        to_scan_revision_id=second["scan_revision_id"],
    )

    changed = diff["dependencies"]["changed"][0]
    assert changed["version_transition"] == "upgraded"
    assert diff["counts"]["dependencies_upgraded"] == 1
    assert diff["counts"]["dependencies_downgraded"] == 0
    assert diff["semantics"]["version_transition_is_mechanical"] is True
    assert diff["semantics"]["version_order_implies_validity_or_safety"] is False
    assert diff["semantics"]["affectedness_inferred"] is False


def test_non_numeric_version_change_does_not_guess_order(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    requirements = root / "requirements.txt"
    service = TestamurProductService(tmp_path / "state" / "evidence.db")
    project = service.projects.create_project(name="opaque-version-demo")
    service.projects.bind_repository(project["project_id"], locator=str(root))

    first = _scan(service, project["project_id"], requirements, "demo==release-a\n")
    second = _scan(service, project["project_id"], requirements, "demo==release-b\n")
    diff = diff_project_supply_chain(
        service,
        project["project_id"],
        from_scan_revision_id=first["scan_revision_id"],
        to_scan_revision_id=second["scan_revision_id"],
    )

    assert diff["dependencies"]["changed"][0]["version_transition"] == "version-changed"
    assert diff["counts"]["dependencies_version_changed_unclassified"] == 1
    assert diff["semantics"]["unrecognized_version_order_is_not_guessed"] is True
