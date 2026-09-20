from __future__ import annotations

from typing import Any, Iterable, Mapping


def project_advisory_guidance(
    candidates: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project advisory review state into product guidance without a trust score.

    Candidate discovery and affectedness assessment stay distinct. This helper is
    intentionally presentation-oriented: it tells the Web/CLI what action is
    useful next without upgrading identity overlap, a recorded assessment, or a
    stale observation into verification.
    """

    items = [dict(candidate) for candidate in candidates]
    review_required = [item for item in items if item.get("requires_review") is True]
    competing = [item for item in items if item.get("competing_subject_revision_ids")]
    assessed = [
        item
        for item in items
        if item.get("current_assessment") is not None
        or item.get("assessment") is not None
    ]

    if competing:
        state = "competing-assessments"
        title = "Resolve competing assessments"
        next_action = "inspect-competing-heads"
        detail = (
            "Multiple recorded assessment heads exist for at least one advisory candidate. "
            "Inspect their evidence and scope before recording a superseding assessment."
        )
    elif review_required:
        state = "review-required"
        title = "Review advisory candidates"
        next_action = "review-candidates"
        detail = (
            "Exact component identity overlaps an advisory revision. Review applicability "
            "and evidence before recording an affectedness assessment."
        )
    elif items and len(assessed) == len(items):
        state = "assessments-recorded"
        title = "Assessments recorded"
        next_action = "inspect-assessments"
        detail = (
            "Current advisory candidates have recorded assessments. Revisit them when "
            "inputs, evidence, scope, or repository observations change."
        )
    elif items:
        state = "candidates-present"
        title = "Inspect advisory candidates"
        next_action = "inspect-candidates"
        detail = (
            "Advisory candidates were discovered from exact identity overlap. Identity "
            "overlap alone does not establish affectedness."
        )
    else:
        state = "no-candidates"
        title = "No exact advisory candidates"
        next_action = "inspect-inventory"
        detail = (
            "No exact advisory identity overlap is currently projected. This is not proof "
            "that the project is unaffected; inspect inventory completeness and scan inputs."
        )

    return {
        "schema": "testamur.product.advisory-guidance.v1",
        "state": state,
        "title": title,
        "detail": detail,
        "next_action": next_action,
        "candidate_count": len(items),
        "review_required_count": len(review_required),
        "competing_count": len(competing),
        "semantics": {
            "identity_overlap_is_affectedness_verdict": False,
            "recorded_assessment_is_verification": False,
            "no_candidate_proves_unaffected": False,
            "changed_implies_invalid": False,
            "stale_implies_false": False,
            "generic_trust_score_used": False,
        },
    }
