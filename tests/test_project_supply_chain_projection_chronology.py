from __future__ import annotations

import json

from testamur.product_service import TestamurProductService
from testamur.supply_chain import scan_bound_project_supply_chain


def _package_lock(root, *, package: str, version: str) -> None:
    payload = {
        "name": f"fixture-{package}",
        "lockfileVersion": 3,
        "packages": {
            "": {"dependencies": {package: version}},
            f"node_modules/{package}": {
                "name": package,
                "version": version,
                "integrity": f"sha512-{package}-{version}",
                "resolved": f"https://registry.example.invalid/{package}/-/{package}-{version}.tgz",
            },
        },
    }
    (root / "package-lock.json").write_text(json.dumps(payload), encoding="utf-8")


def test_project_compat_projection_uses_cross_record_chronology_not_local_ordinal(tmp_path) -> None:
    """The compatibility projection must select the newest binding observation globally.

    Revision ordinal is record-local. A binding with a longer scan history must not
    outrank a later observation from another repository merely because its local
    ordinal is larger.
    """
    primary_root = tmp_path / "primary"
    docs_root = tmp_path / "docs"
    primary_root.mkdir()
    docs_root.mkdir()
    _package_lock(primary_root, package="alpha", version="1.0.0")
    _package_lock(docs_root, package="beta", version="2.0.0")

    service = TestamurProductService(tmp_path / "state" / "evidence.db")
    project = service.projects.create_project(name="cross-binding-chronology")
    service.projects.bind_repository(
        project["project_id"], locator=str(primary_root), binding_key="primary"
    )
    service.projects.bind_repository(
        project["project_id"], locator=str(docs_root), binding_key="docs"
    )

    scan_bound_project_supply_chain(service, project["project_id"], binding_key="primary")
    _package_lock(primary_root, package="alpha", version="1.1.0")
    latest_primary = scan_bound_project_supply_chain(
        service, project["project_id"], binding_key="primary"
    )
    latest_docs = scan_bound_project_supply_chain(
        service, project["project_id"], binding_key="docs"
    )

    detail = service.project(project["project_id"])
    assert detail["ok"] is True
    assert len(detail["supply_chains"]) == 2
    assert detail["supply_chain"]["scan_revision_id"] == latest_docs["scan_revision_id"]

    by_binding = {
        chain["repository_binding"]["binding_key"]: chain
        for chain in detail["supply_chains"]
    }
    assert by_binding["primary"]["scan_revision_id"] == latest_primary["scan_revision_id"]
    assert by_binding["docs"]["scan_revision_id"] == latest_docs["scan_revision_id"]
