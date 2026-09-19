from __future__ import annotations

from typing import Any

from .project_advisory_review import project_advisory_reviews


def project_with_advisory_reviews(service: Any, ref: str) -> dict[str, Any]:
    """Return the canonical project projection with advisory review state attached.

    The underlying project service remains authoritative for inventory and exact
    advisory/component identity overlap. This read-only composition adds current
    immutable affectedness heads; it does not reinterpret overlap as affectedness,
    collapse competing heads, or manufacture a trust score.
    """

    payload = dict(service.project(ref))
    if payload.get("ok") is not True:
        return payload

    supply_chain = payload.get("supply_chain")
    if not isinstance(supply_chain, dict):
        return payload

    candidates = supply_chain.get("advisory_candidates")
    if not isinstance(candidates, list):
        candidates = []

    reviews = project_advisory_reviews(service.database_path, candidates)
    enriched_supply_chain = dict(supply_chain)
    enriched_supply_chain["advisory_candidates"] = reviews
    enriched_supply_chain["advisory_candidate_count"] = len(reviews)
    enriched_supply_chain["advisory_review_required_count"] = sum(
        1 for review in reviews if review.get("requires_review") is True
    )
    enriched_supply_chain["advisory_competing_count"] = sum(
        1 for review in reviews if review.get("competing_subject_revision_ids")
    )
    enriched_supply_chain["semantics"] = {
        **dict(enriched_supply_chain.get("semantics") or {}),
        "advisory_review_is_read_projection": True,
        "recorded_assessment_is_not_generic_verification": True,
        "competing_heads_are_preserved": True,
        "generic_trust_score_used": False,
    }

    payload["supply_chain"] = enriched_supply_chain
    return payload
