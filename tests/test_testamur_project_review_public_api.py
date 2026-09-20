from __future__ import annotations

import testamur
from testamur.project_review_surface import project_with_advisory_reviews


def test_advisory_aware_project_read_is_public_canonical_api():
    assert testamur.project_with_advisory_reviews is project_with_advisory_reviews
    assert "project_with_advisory_reviews" in testamur.__all__
