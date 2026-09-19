from pathlib import Path

from testamur.web_app import dispatch_api


def test_hosted_project_route_uses_advisory_review_projection():
    source = Path("testamur/web_app.py").read_text(encoding="utf-8")

    assert "from .project_review_surface import project_with_advisory_reviews" in source
    assert "project_with_advisory_reviews(service, _one(query, \"ref\") or \"\")" in source
    assert "return _json(service.project(_one(query, \"ref\") or \"\"))" not in source


def test_hosted_project_route_rejects_unknown_parameters_before_projection():
    class _Service:
        def project(self, ref):  # pragma: no cover - must not be reached
            raise AssertionError("project projection should not run")

    response = dispatch_api(_Service(), "/v1/project?ref=project:test&trust_score=1")

    assert response["status"] == 400
    assert response["body"]["error"]["code"] == "invalid_argument"
