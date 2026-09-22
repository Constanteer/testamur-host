from __future__ import annotations

from typing import Any

from .project_advisory_revalidation import project_advisory_revalidation
from .project_advisory_review import project_advisory_reviews
from .repository_binding_lifecycle import repository_binding_with_state
from .supply_chain import diff_project_supply_chain


def project_with_advisory_reviews(service: Any, ref: str) -> dict[str, Any]:
    """Return the canonical project projection with review and comparison work attached.

    The underlying project service remains authoritative for inventory and exact
    advisory/component identity overlap. This read-only composition adds current
    immutable affectedness heads, the latest mechanical scan comparison when two
    observations exist, and a revalidation work projection. It does not reinterpret
    overlap or version direction as affectedness, collapse competing heads, or
    manufacture a trust score.
    """

    payload = dict(service.project(ref))
    if payload.get("ok") is not True:
        return payload

    project = payload.get("project")
    project_ref = str(project.get("project_id") or ref) if isinstance(project, dict) else ref
    repository_bindings: list[dict[str, Any]] = []
    for item in service.projects.repository_bindings(project_ref):
        binding = repository_binding_with_state(
            service.projects,
            project_ref,
            binding_key=str(item["binding_key"]),
        )
        if binding is not None:
            repository_bindings.append(binding)
    payload["repository_bindings"] = repository_bindings
    payload["semantics"] = {
        **dict(payload.get("semantics") or {}),
        "repository_binding_state_is_scanner_eligibility": True,
        "repository_binding_state_implies_content_observed": False,
        "repository_binding_state_implies_verification": False,
        "repository_binding_state_implies_reliance": False,
        "repository_binding_state_implies_affectedness": False,
    }

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

    supply_chain_diff = enriched_supply_chain.get("diff")
    if not isinstance(supply_chain_diff, dict):
        try:
            candidate_diff = diff_project_supply_chain(service, ref)
        except ValueError as exc:
            # Zero/one scan is normal first-run state. Preserve it as an explicit
            # absence of comparison rather than manufacturing a changed/unchanged verdict.
            candidate_diff = None
            enriched_supply_chain["diff_unavailable_reason"] = str(exc)
        if isinstance(candidate_diff, dict) and candidate_diff.get("ok") is True:
            supply_chain_diff = candidate_diff
            enriched_supply_chain["diff"] = candidate_diff
        else:
            supply_chain_diff = None

    enriched_supply_chain["advisory_revalidation"] = project_advisory_revalidation(
        reviews,
        supply_chain_diff=supply_chain_diff,
    )
    enriched_supply_chain["semantics"] = {
        **dict(enriched_supply_chain.get("semantics") or {}),
        "latest_scan_diff_is_mechanical": True,
        "version_direction_implies_safety": False,
        "advisory_review_is_read_projection": True,
        "advisory_revalidation_is_work_projection": True,
        "changed_implies_invalid": False,
        "stale_implies_false": False,
        "recorded_assessment_is_not_generic_verification": True,
        "competing_heads_are_preserved": True,
        "generic_trust_score_used": False,
    }

    payload["supply_chain"] = enriched_supply_chain
    return payload
