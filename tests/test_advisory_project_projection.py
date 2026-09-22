from __future__ import annotations

from pathlib import Path

from testamur.advisory import AdverseEventClass, TestamurAdvisoryStore
from testamur.advisory_project_lookup import project_advisory_revisions_for_upstream_refs


def _record(
    store: TestamurAdvisoryStore,
    external_id: str,
    *,
    upstream_refs: list[str] | None = None,
    upstream_identity: dict | None = None,
) -> dict:
    return store.record_adverse_event(
        provider="projection-contract",
        external_id=external_id,
        event_class=AdverseEventClass.VULNERABILITY_ADVISORY,
        upstream_refs=upstream_refs or [],
        upstream_identity=upstream_identity,
        source_refs=[f"source:{external_id}"],
    )


def test_project_projection_keeps_candidate_membership_and_unresolved_identity_separate(
    tmp_path: Path,
) -> None:
    store = TestamurAdvisoryStore(tmp_path / "testamur.sqlite3")
    target_ref = "component-revision:target"
    exact = _record(store, "exact", upstream_refs=[target_ref])
    _record(
        store,
        "unresolved",
        upstream_identity={"ecosystem": "pypi", "name": "requests"},
    )
    _record(store, "unrelated", upstream_refs=["component-revision:other"])

    candidates, unresolved_count = project_advisory_revisions_for_upstream_refs(
        store, [target_ref]
    )

    assert [item["event_revision_id"] for item in candidates] == [
        exact["event_revision_id"]
    ]
    assert unresolved_count == 1


def test_project_projection_uses_only_latest_advisory_revision(tmp_path: Path) -> None:
    store = TestamurAdvisoryStore(tmp_path / "testamur.sqlite3")
    target_ref = "component-revision:target"
    first = _record(store, "moves-away", upstream_refs=[target_ref])
    store.revise_adverse_event(
        first["event_id"],
        upstream_refs=["component-revision:replacement"],
        source_refs=["source:moves-away-v2"],
    )

    candidates, unresolved_count = project_advisory_revisions_for_upstream_refs(
        store, [target_ref]
    )

    assert candidates == []
    assert unresolved_count == 0
