from testamur.component_identity import ComponentIdentity, ComponentRevisionIdentity
from testamur.project_advisory_candidates import project_advisory_candidates


def _revision(*, digest: str = "", version: str = "1.0") -> dict:
    component = ComponentIdentity.create(
        kind="software-package",
        namespace="pypi",
        name="example",
        external_ids={"ecosystem": "pypi"},
    )
    return ComponentRevisionIdentity.create(
        component=component,
        version=version,
        digest=digest or None,
    ).as_dict()


def test_candidate_requires_exact_revision_overlap():
    exact = _revision(digest="sha256:abc")
    declared_only = _revision(version="2.0")
    event = {
        "event_id": "tst:advisory:event",
        "event_revision_id": "tst:advisory:revision",
        "provider": "fixture",
        "external_id": "ADV-1",
        "upstream_refs": [
            exact["component_revision_id"],
            declared_only["component_revision_id"],
        ],
    }

    result = project_advisory_candidates([event], [exact, declared_only])

    assert len(result) == 1
    assert result[0]["matching_component_revision_ids"] == [
        exact["component_revision_id"]
    ]
    assert result[0]["status"] == "exact_identity_overlap"
    assert result[0]["semantics"]["identity_overlap_is_affectedness_verdict"] is False
    assert result[0]["semantics"]["candidate_is_verification"] is False
    assert result[0]["semantics"]["candidate_establishes_reliance"] is False


def test_declared_version_match_does_not_create_candidate():
    declared_only = _revision(version="2.0")
    event = {
        "event_revision_id": "tst:advisory:revision",
        "provider": "fixture",
        "external_id": "ADV-2",
        "upstream_refs": [declared_only["component_revision_id"]],
    }

    assert project_advisory_candidates([event], [declared_only]) == []
