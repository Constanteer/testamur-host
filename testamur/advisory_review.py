from __future__ import annotations

from typing import Any, Iterable, Mapping


_TERMINAL = frozenset({
    "CONFIRMED_AFFECTED",
    "MITIGATED",
    "DISPROVEN",
    "NOT_APPLICABLE",
})


def project_advisory_review(
    candidate: Mapping[str, Any],
    assessment_heads: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project an exact advisory candidate into a product-facing review summary.

    This is presentation/composition only. The candidate remains an identity-overlap
    observation and affectedness remains owned by immutable W5 assessments. Multiple
    unsuperseded heads are preserved instead of choosing whichever row was recorded
    last. A terminal assessment is therefore shown as a recorded assessment, not as
    a generic trust or validity score.
    """

    event_revision_id = str(candidate.get("event_revision_id") or "").strip()
    if not event_revision_id:
        raise ValueError("candidate.event_revision_id must not be empty")

    matching = sorted({
        str(value).strip()
        for value in candidate.get("matching_component_revision_ids") or []
        if str(value).strip()
    })
    heads = [
        dict(item)
        for item in assessment_heads
        if str(item.get("event_revision_id") or "").strip() == event_revision_id
        and str(item.get("subject_revision") or "").strip() in matching
    ]
    heads.sort(key=lambda item: (
        str(item.get("subject_revision") or ""),
        str(item.get("assessment_id") or ""),
    ))

    by_subject: dict[str, list[dict[str, Any]]] = {ref: [] for ref in matching}
    for item in heads:
        by_subject.setdefault(str(item["subject_revision"]), []).append(item)

    subjects: list[dict[str, Any]] = []
    for subject_revision in matching:
        current = by_subject.get(subject_revision, [])
        states = sorted({str(item.get("state") or "UNKNOWN") for item in current})
        terminal = [item for item in current if str(item.get("state") or "") in _TERMINAL]
        subjects.append({
            "subject_revision": subject_revision,
            "assessment_heads": current,
            "assessment_count": len(current),
            "states": states,
            "requires_review": not current or len(states) != 1 or not terminal,
            "has_competing_heads": len(current) > 1,
        })

    unassessed = [item["subject_revision"] for item in subjects if not item["assessment_heads"]]
    competing = [item["subject_revision"] for item in subjects if item["has_competing_heads"]]
    terminal_subjects = [
        item["subject_revision"]
        for item in subjects
        if item["assessment_heads"]
        and not item["has_competing_heads"]
        and item["assessment_heads"][0].get("state") in _TERMINAL
    ]

    return {
        "schema": "testamur.product.advisory-review.v1",
        "event_id": candidate.get("event_id"),
        "event_revision_id": event_revision_id,
        "provider": candidate.get("provider"),
        "external_id": candidate.get("external_id"),
        "candidate_status": candidate.get("status") or "exact_identity_overlap",
        "subjects": subjects,
        "unassessed_subject_revision_ids": unassessed,
        "competing_subject_revision_ids": competing,
        "terminally_assessed_subject_revision_ids": terminal_subjects,
        "requires_review": any(item["requires_review"] for item in subjects),
        "semantics": {
            "candidate_is_affectedness_verdict": False,
            "assessment_heads_are_immutable_recorded_conclusions": True,
            "multiple_heads_are_not_collapsed": True,
            "terminal_assessment_is_generic_validity_verdict": False,
            "generic_trust_score_used": False,
            "changed_implies_invalid": False,
            "stale_implies_false": False,
        },
    }
