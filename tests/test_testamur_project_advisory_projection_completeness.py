from __future__ import annotations

from pathlib import Path

from testamur.advisory import AdverseEventClass
from testamur.product_service import TestamurProductService
from testamur.supply_chain import scan_bound_project_supply_chain


def _advisory(service, external_id: str, upstream_ref: str) -> dict:
    return service.advisories.record_adverse_event(
        provider="projection-fixture",
        external_id=external_id,
        event_class=AdverseEventClass.VULNERABILITY_ADVISORY,
        upstream_refs=[upstream_ref],
        source_refs=[f"source:{external_id}"],
    )


def test_project_projection_does_not_hide_old_exact_advisory_behind_global_window(tmp_path: Path) -> None:
    """The user-facing Project projection must consume the exact-ref lookup primitive.

    Exact overlap is candidate membership only. This regression is about completeness
    of that candidate set; it does not infer affectedness, verification, or reliance.
    """
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "requirements.txt").write_text("requests==2.32.4\n", encoding="utf-8")

    service = TestamurProductService(tmp_path / "testamur.sqlite3")
    project = service.projects.create_project(name="projection-completeness")
    project_id = str(project["project_id"])
    service.projects.bind_repository(project_id, locator=str(repository))
    scan_bound_project_supply_chain(service, project_id)

    first_projection = service.project(project_id)
    dependency = first_projection["supply_chain"]["dependencies"][0]
    target_ref = str(dependency["component_revision_id"])
    target = _advisory(service, "target", target_ref)

    # More recent unrelated events must not evict an exact candidate from the
    # Project surface merely because an inventory helper historically used 1000.
    for index in range(1001):
        _advisory(service, f"noise-{index}", f"component-revision:noise-{index}")

    projection = service.project(project_id)
    supply_chain = projection["supply_chain"]
    candidates = supply_chain["advisory_candidates"]
    assert [item["event_revision_id"] for item in candidates] == [
        target["event_revision_id"]
    ]
    assert candidates[0]["matching_component_revision_ids"] == [target_ref]
    assert candidates[0]["semantics"]["exact_identity_overlap_is_affectedness_verdict"] is False
    assert candidates[0]["semantics"]["applicability_assessment_required"] is True
    assert supply_chain["semantics"]["advisory_candidate_is_not_affectedness_verdict"] is True
    assert supply_chain["semantics"]["unresolved_identity_is_not_fuzzy_matched"] is True
