from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from testamur.advisory import AdverseEventClass, TestamurAdvisoryStore
from testamur.product_service import TestamurProductService
from testamur.web_app import dispatch_api_write


def test_advisory_assessment_write_route_records_engine_resolved_state() -> None:
    with TemporaryDirectory() as temporary:
        database = Path(temporary) / "testamur.sqlite3"
        revision = TestamurAdvisoryStore(database).record_adverse_event(
            provider="test",
            external_id="TEST-HOST-HTTP-1",
            event_class=AdverseEventClass.VULNERABILITY_ADVISORY,
            upstream_refs=["pkg:npm/example@1.2.3#sha256:abc"],
            source_refs=["https://example.invalid/advisory/host-http"],
        )
        service = TestamurProductService.integrated(database)

        response = dispatch_api_write(
            service,
            "/v1/advisory-assessments",
            {
                "event_revision_id": revision["event_revision_id"],
                "subject_revision": "component:demo@1",
                "evidence": [],
                "basis": [{"kind": "manual_review", "ref": "review:host-http"}],
            },
        )

        assert response["status"] == 200
        body = response["body"]
        assert body["state"] == "UNKNOWN"
        assert body["event_revision_id"] == revision["event_revision_id"]
        assert body["subject_revision"] == "component:demo@1"
        assert "trust_score" not in body


def test_advisory_assessment_route_rejects_caller_supplied_conclusions() -> None:
    with TemporaryDirectory() as temporary:
        database = Path(temporary) / "testamur.sqlite3"
        service = TestamurProductService.integrated(database)

        for forbidden in ("state", "verdict", "trust_score"):
            response = dispatch_api_write(
                service,
                "/v1/advisory-assessments",
                {
                    "event_revision_id": "advisory:any",
                    "subject_revision": "component:any",
                    "evidence": [],
                    "basis": [],
                    forbidden: "CONFIRMED_AFFECTED",
                },
            )
            assert response["status"] == 400
            assert response["body"]["error"]["code"] == "invalid_argument"
