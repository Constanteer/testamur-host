from __future__ import annotations

import json

from testamur.advisory import AdverseEventClass
from testamur.product_service import TestamurProductService
from testamur.project_review_surface import project_with_advisory_reviews
from testamur.supply_chain import scan_bound_project_supply_chain


def _package_lock(root, *, package: str, version: str, integrity: str) -> None:
    payload = {
        "name": f"fixture-{package}",
        "lockfileVersion": 3,
        "packages": {
            "": {"dependencies": {package: version}},
            f"node_modules/{package}": {
                "name": package,
                "version": version,
                "integrity": integrity,
                "resolved": f"https://registry.example.invalid/{package}/-/{package}-{version}.tgz",
            },
        },
    }
    (root / "package-lock.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def _record_advisory(
    service: TestamurProductService,
    *,
    external_id: str,
    component_revision_id: str,
) -> dict:
    return service.advisories.record_adverse_event(
        provider="fixture",
        external_id=external_id,
        event_class=AdverseEventClass.VULNERABILITY_ADVISORY,
        upstream_refs=[component_revision_id],
        source_refs=[f"https://example.invalid/advisories/{external_id}"],
    )


def test_project_review_unions_exact_candidates_across_latest_binding_scans(tmp_path) -> None:
    primary_root = tmp_path / "primary"
    docs_root = tmp_path / "docs"
    primary_root.mkdir()
    docs_root.mkdir()
    _package_lock(
        primary_root,
        package="alpha",
        version="1.0.0",
        integrity="sha512-alpha-exact",
    )
    _package_lock(
        docs_root,
        package="beta",
        version="2.0.0",
        integrity="sha512-beta-exact",
    )

    service = TestamurProductService(tmp_path / "state" / "evidence.db")
    project = service.projects.create_project(name="multi-binding-advisories")
    primary = service.projects.bind_repository(
        project["project_id"], locator=str(primary_root), binding_key="primary"
    )
    docs = service.projects.bind_repository(
        project["project_id"], locator=str(docs_root), binding_key="docs"
    )

    primary_scan = scan_bound_project_supply_chain(
        service, project["project_id"], binding_key="primary"
    )
    docs_scan = scan_bound_project_supply_chain(
        service, project["project_id"], binding_key="docs"
    )
    primary_dependency = primary_scan["dependencies"][0]
    docs_dependency = docs_scan["dependencies"][0]
    assert primary_dependency["component_revision"]["is_exact_revision"] is True
    assert docs_dependency["component_revision"]["is_exact_revision"] is True

    first_advisory = _record_advisory(
        service,
        external_id="ADV-PRIMARY",
        component_revision_id=primary_dependency["component_revision_id"],
    )
    second_advisory = _record_advisory(
        service,
        external_id="ADV-DOCS",
        component_revision_id=docs_dependency["component_revision_id"],
    )

    detail = service.project(project["project_id"])
    assert detail["ok"] is True
    assert len(detail["supply_chains"]) == 2
    assert detail["supply_chain"]["scan_revision_id"] == docs_scan["scan_revision_id"]
    by_binding = {
        item["repository_binding"]["binding_key"]: item
        for item in detail["supply_chains"]
    }
    assert set(by_binding) == {"primary", "docs"}
    assert by_binding["primary"]["repository_binding"]["binding_id"] == primary["binding"]["binding_id"]
    assert by_binding["docs"]["repository_binding"]["binding_id"] == docs["binding"]["binding_id"]
    assert {
        item["external_id"]
        for chain in detail["supply_chains"]
        for item in chain["advisory_candidates"]
    } == {"ADV-PRIMARY", "ADV-DOCS"}

    reviewed = project_with_advisory_reviews(service, project["project_id"])
    projection = reviewed["advisory_revalidation"]
    assert projection["requires_revalidation"] is True
    assert projection["review_count"] == 2
    assert {
        item["event_revision_id"] for item in projection["reviews"]
    } == {
        first_advisory["event_revision_id"],
        second_advisory["event_revision_id"],
    }
    assert reviewed["advisory_candidate_count"] == 2
    assert reviewed["semantics"]["inventory_is_not_merged_across_repository_bindings"] is True
    assert reviewed["semantics"]["project_advisory_review_unions_exact_candidates_across_bindings"] is True
    assert reviewed["semantics"]["candidate_union_is_not_affectedness_verdict"] is True
    assert reviewed["semantics"]["generic_trust_score_used"] is False


def test_latest_projection_per_binding_replaces_older_scan_without_hiding_other_binding(tmp_path) -> None:
    primary_root = tmp_path / "primary"
    docs_root = tmp_path / "docs"
    primary_root.mkdir()
    docs_root.mkdir()
    _package_lock(primary_root, package="alpha", version="1.0.0", integrity="sha512-alpha-v1")
    _package_lock(docs_root, package="beta", version="2.0.0", integrity="sha512-beta")

    service = TestamurProductService(tmp_path / "state" / "evidence.db")
    project = service.projects.create_project(name="latest-per-binding")
    service.projects.bind_repository(project["project_id"], locator=str(primary_root), binding_key="primary")
    service.projects.bind_repository(project["project_id"], locator=str(docs_root), binding_key="docs")

    first_primary = scan_bound_project_supply_chain(service, project["project_id"], binding_key="primary")
    docs_scan = scan_bound_project_supply_chain(service, project["project_id"], binding_key="docs")
    _package_lock(primary_root, package="alpha", version="1.1.0", integrity="sha512-alpha-v2")
    second_primary = scan_bound_project_supply_chain(service, project["project_id"], binding_key="primary")

    detail = service.project(project["project_id"])
    assert len(detail["supply_chains"]) == 2
    by_binding = {
        item["repository_binding"]["binding_key"]: item
        for item in detail["supply_chains"]
    }
    assert by_binding["primary"]["scan_revision_id"] == second_primary["scan_revision_id"]
    assert by_binding["primary"]["scan_revision_id"] != first_primary["scan_revision_id"]
    assert by_binding["docs"]["scan_revision_id"] == docs_scan["scan_revision_id"]


def test_per_binding_projection_is_not_capped_by_other_binding_activity(tmp_path) -> None:
    primary_root = tmp_path / "primary"
    docs_root = tmp_path / "docs"
    primary_root.mkdir()
    docs_root.mkdir()
    _package_lock(primary_root, package="alpha", version="1.0.0", integrity="sha512-alpha")
    _package_lock(docs_root, package="beta", version="2.0.0", integrity="sha512-beta")

    service = TestamurProductService(tmp_path / "state" / "evidence.db")
    project = service.projects.create_project(name="uncapped-binding-projection")
    service.projects.bind_repository(
        project["project_id"], locator=str(primary_root), binding_key="primary"
    )
    service.projects.bind_repository(
        project["project_id"], locator=str(docs_root), binding_key="docs"
    )

    docs_scan = scan_bound_project_supply_chain(
        service, project["project_id"], binding_key="docs"
    )
    primary_scan = scan_bound_project_supply_chain(
        service, project["project_id"], binding_key="primary"
    )

    # Create more than the bounded relation/history read window using canonical
    # immutable record APIs. This models a very active primary binding without
    # spending the test on repeated filesystem parsing.
    record_id = primary_scan["scan_record_id"]
    latest = service.records.latest_revision(record_id)
    assert latest is not None
    for index in range(501):
        next_revision = service.records.append_revision(
            record_id,
            expected_parent_revision_id=latest["revision_id"],
            statement=latest["statement"],
            basis=latest["basis"],
            title=latest.get("title"),
            created_by="test:binding-projection-window",
        )
        service.records.create_relation(
            "cites",
            from_ref=project["project_id"],
            to_ref=next_revision["revision_id"],
            basis=[{"kind": "fixture_scan", "ref": next_revision["revision_id"]}],
            created_by="test:binding-projection-window",
        )
        latest = next_revision

    detail = service.project(project["project_id"])
    by_binding = {
        item["repository_binding"]["binding_key"]: item
        for item in detail["supply_chains"]
    }
    assert set(by_binding) == {"primary", "docs"}
    assert by_binding["docs"]["scan_revision_id"] == docs_scan["scan_revision_id"]
    assert by_binding["primary"]["scan_revision_id"] == latest["revision_id"]
