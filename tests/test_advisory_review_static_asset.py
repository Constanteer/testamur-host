from testamur.web_app import dispatch_web_get


class _UnusedService:
    pass


def test_hosted_advisory_review_module_is_served_as_static_javascript():
    response = dispatch_web_get(_UnusedService(), "/advisory-review-ui.js")

    assert response["status"] == 200
    assert response["headers"]["Content-Type"] == "text/javascript; charset=utf-8"
    body = response["body"].decode("utf-8")
    assert "testamur.product.advisory-review.v1" in body
    assert "Identity overlap only nominates work for review" in body
