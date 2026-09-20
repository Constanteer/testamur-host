from __future__ import annotations

from testamur.project_review_surface import project_with_advisory_reviews


class _Service:
    def __init__(self, database_path, payload):
        self.database_path = database_path
        self._payload = payload

    def project(self, ref):
        assert ref == "project:demo"
        return self._payload


def test_project_surface_enriches_unassessed_candidate_without_verdict(tmp_path):
    payload = {
        "ok": True,
        "schema": "testamur.product.project.v2",
        "supply_chain": {
            "advisory_candidates": [{
                "event_id": "event:1",
                "event_revision_id": "eventrev:1",
                "provider": "osv",
                "external_id": "CVE-TEST-1",
                "matching_component_revision_ids": ["componentrev:a"],
                "status": "exact_identity_overlap",
            }],
            "semantics": {"advisory_candidate_is_not_affectedness_verdict": True},
        },
    }
    service = _Service(tmp_path / "testamur.db", payload)

    result = project_with_advisory_reviews(service, "project:demo")

    supply_chain = result["supply_chain"]
    assert supply_chain["advisory_candidate_count"] == 1
    assert supply_chain["advisory_review_required_count"] == 1
    assert supply_chain["advisory_competing_count"] == 0
    review = supply_chain["advisory_candidates"][0]
    assert review["requires_review"] is True
    assert review["unassessed_subject_revision_ids"] == ["componentrev:a"]
    assert review["semantics"]["candidate_is_affectedness_verdict"] is False
    assert supply_chain["semantics"]["generic_trust_score_used"] is False


def test_project_surface_leaves_project_without_supply_chain_unchanged(tmp_path):
    payload = {"ok": True, "schema": "testamur.product.project.v2", "supply_chain": None}
    service = _Service(tmp_path / "testamur.db", payload)

    result = project_with_advisory_reviews(service, "project:demo")

    assert result["supply_chain"] is None


def test_project_surface_preserves_errors(tmp_path):
    payload = {"ok": False, "error": {"code": "object_not_found"}}
    service = _Service(tmp_path / "testamur.db", payload)

    assert project_with_advisory_reviews(service, "project:demo") == payload
