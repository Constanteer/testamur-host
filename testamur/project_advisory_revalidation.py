from __future__ import annotations

from typing import Any, Iterable, Mapping


def project_advisory_revalidation(
    reviews: Iterable[Mapping[str, Any]],
    *,
    supply_chain_diff: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project advisory review work after a supply-chain observation.

    This is a queue/read projection, never an affectedness or validity inference.
    A changed dependency can make a recorded assessment worth revisiting without
    making that assessment false or the project invalid. Exact advisory candidates
    still require explicit affectedness assessment through the canonical store.
    """

    items = [dict(item) for item in reviews]
    requiring_review = [item for item in items if item.get("requires_review") is True]
    competing = [
        item for item in items if item.get("competing_subject_revision_ids")
    ]

    counts = dict((supply_chain_diff or {}).get("counts") or {})
    dependency_changes = sum(
        int(counts.get(key) or 0)
        for key in (
            "dependencies_added",
            "dependencies_removed",
            "dependencies_changed",
        )
    )
    observation_changed = dependency_changes > 0

    reasons: list[str] = []
    if requiring_review:
        reasons.append("advisory-review-required")
    if competing:
        reasons.append("competing-assessment-heads")
    if observation_changed:
        reasons.append("dependency-observation-changed")

    # A mechanical dependency delta is a reason to revisit the projection, not a
    # reason to rewrite immutable affectedness conclusions. If there are no exact
    # candidates, the delta alone cannot manufacture an advisory verdict.
    requires_revalidation = bool(requiring_review or competing)

    return {
        "schema": "testamur.product.advisory-revalidation.v1",
        "requires_revalidation": requires_revalidation,
        "reasons": reasons,
        "review_count": len(requiring_review),
        "competing_count": len(competing),
        "dependency_observation_changed": observation_changed,
        "reviews": items,
        "semantics": {
            "changed_implies_invalid": False,
            "changed_implies_affected": False,
            "stale_implies_false": False,
            "candidate_is_affectedness_verdict": False,
            "recorded_assessment_is_verification": False,
            "mechanical_diff_rewrites_assessment": False,
            "generic_trust_score_used": False,
        },
    }
