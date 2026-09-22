from __future__ import annotations

from typing import Any, Mapping

from .project_advisory_revalidation import project_advisory_revalidation
from .project_advisory_review import project_advisory_reviews
from .repository_binding_lifecycle import repository_binding_with_state
from .supply_chain import diff_project_supply_chain


def _merge_project_candidates(
    supply_chains: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for supply_chain in supply_chains:
        binding = supply_chain.get("repository_binding")
        binding_id = (
            str(binding.get("binding_id") or "").strip()
            if isinstance(binding, Mapping)
            else ""
        )
        candidates = supply_chain.get("advisory_candidates")
        if not isinstance(candidates, list):
            continue
        for raw in candidates:
            if not isinstance(raw, Mapping):
                continue
            candidate = dict(raw)
            event_revision_id = str(candidate.get("event_revision_id") or "").strip()
            if not event_revision_id:
                continue
            current = merged.get(event_revision_id)
            if current is None:
                current = candidate
                current["matching_component_revision_ids"] = sorted(
                    {
                        str(value)
                        for value in candidate.get("matching_component_revision_ids") or []
                        if str(value)
                    }
                )
                current["repository_binding_ids"] = [binding_id] if binding_id else []
                merged[event_revision_id] = current
                continue
            current["matching_component_revision_ids"] = sorted(
                set(current.get("matching_component_revision_ids") or [])
                | {
                    str(value)
                    for value in candidate.get("matching_component_revision_ids") or []
                    if str(value)
                }
            )
            if binding_id:
                current["repository_binding_ids"] = sorted(
                    set(current.get("repository_binding_ids") or []) | {binding_id}
                )

    values = list(merged.values())
    values.sort(
        key=lambda item: (
            str(item.get("provider") or ""),
            str(item.get("external_id") or ""),
            str(item.get("event_revision_id") or ""),
        )
    )
    return values


def _enrich_supply_chain(
    service: Any,
    project_ref: str,
    supply_chain: Mapping[str, Any],
) -> dict[str, Any]:
    enriched = dict(supply_chain)
    candidates = enriched.get("advisory_candidates")
    if not isinstance(candidates, list):
        candidates = []
    reviews = project_advisory_reviews(service.database_path, candidates)
    enriched["advisory_candidates"] = reviews
    enriched["advisory_candidate_count"] = len(reviews)
    enriched["advisory_review_required_count"] = sum(
        1 for review in reviews if review.get("requires_review") is True
    )
    enriched["advisory_competing_count"] = sum(
        1 for review in reviews if review.get("competing_subject_revision_ids")
    )

    supply_chain_diff = enriched.get("diff")
    if not isinstance(supply_chain_diff, dict):
        scan_revision_id = str(enriched.get("scan_revision_id") or "").strip()
        try:
            candidate_diff = diff_project_supply_chain(
                service,
                project_ref,
                to_scan_revision_id=scan_revision_id or None,
            )
        except ValueError as exc:
            candidate_diff = None
            enriched["diff_unavailable_reason"] = str(exc)
        if isinstance(candidate_diff, dict) and candidate_diff.get("ok") is True:
            supply_chain_diff = candidate_diff
            enriched["diff"] = candidate_diff
        else:
            supply_chain_diff = None

    enriched["advisory_revalidation"] = project_advisory_revalidation(
        reviews,
        supply_chain_diff=supply_chain_diff,
    )
    enriched["semantics"] = {
        **dict(enriched.get("semantics") or {}),
        "latest_binding_scan_diff_is_mechanical": True,
        "version_direction_implies_safety": False,
        "advisory_review_is_read_projection": True,
        "advisory_revalidation_is_work_projection": True,
        "changed_implies_invalid": False,
        "stale_implies_false": False,
        "recorded_assessment_is_not_generic_verification": True,
        "competing_heads_are_preserved": True,
        "generic_trust_score_used": False,
    }
    return enriched


def _aggregate_diff_counts(
    supply_chains: list[Mapping[str, Any]],
) -> dict[str, int]:
    keys = (
        "manifests_added",
        "manifests_removed",
        "manifests_changed",
        "dependencies_added",
        "dependencies_removed",
        "dependencies_changed",
        "dependencies_upgraded",
        "dependencies_downgraded",
        "dependencies_version_changed_unclassified",
    )
    totals = {key: 0 for key in keys}
    for supply_chain in supply_chains:
        diff = supply_chain.get("diff")
        if not isinstance(diff, Mapping):
            continue
        counts = diff.get("counts")
        if not isinstance(counts, Mapping):
            continue
        for key in keys:
            totals[key] += int(counts.get(key) or 0)
    return totals


def project_with_advisory_reviews(service: Any, ref: str) -> dict[str, Any]:
    """Return Project inventory plus binding-scoped and Project-wide review work.

    Inventory remains scoped to immutable repository-binding observations. Exact
    advisory candidates are unioned only at the Project review layer so a newer
    scan of one repository cannot hide review work discovered in another bound
    repository. This is a read/work projection: it never manufactures affectedness,
    reliance, verification, invalidity, or a generic trust score.
    """

    payload = dict(service.project(ref))
    if payload.get("ok") is not True:
        return payload

    project = payload.get("project")
    project_ref = (
        str(project.get("project_id") or ref) if isinstance(project, dict) else ref
    )
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

    raw_supply_chains = payload.get("supply_chains")
    if not isinstance(raw_supply_chains, list):
        singular = payload.get("supply_chain")
        raw_supply_chains = [singular] if isinstance(singular, Mapping) else []
    supply_chains = [
        dict(item) for item in raw_supply_chains if isinstance(item, Mapping)
    ]

    project_candidates = _merge_project_candidates(supply_chains)
    project_reviews = project_advisory_reviews(
        service.database_path,
        project_candidates,
    )

    enriched_supply_chains = [
        _enrich_supply_chain(service, project_ref, item)
        for item in supply_chains
    ]
    payload["supply_chains"] = enriched_supply_chains

    singular = payload.get("supply_chain")
    singular_scan_revision_id = (
        str(singular.get("scan_revision_id") or "").strip()
        if isinstance(singular, Mapping)
        else ""
    )
    enriched_singular = next(
        (
            item
            for item in enriched_supply_chains
            if str(item.get("scan_revision_id") or "") == singular_scan_revision_id
        ),
        enriched_supply_chains[0] if enriched_supply_chains else None,
    )
    payload["supply_chain"] = enriched_singular

    aggregate_counts = _aggregate_diff_counts(enriched_supply_chains)
    payload["advisory_revalidation"] = project_advisory_revalidation(
        project_reviews,
        supply_chain_diff={"counts": aggregate_counts},
    )
    payload["advisory_review_count"] = len(project_reviews)
    payload["advisory_candidate_count"] = len(project_candidates)
    payload["semantics"] = {
        **dict(payload.get("semantics") or {}),
        "repository_binding_state_is_scanner_eligibility": True,
        "repository_binding_state_implies_content_observed": False,
        "repository_binding_state_implies_verification": False,
        "repository_binding_state_implies_reliance": False,
        "repository_binding_state_implies_affectedness": False,
        "inventory_is_not_merged_across_repository_bindings": True,
        "project_advisory_review_unions_exact_candidates_across_bindings": True,
        "candidate_union_is_not_affectedness_verdict": True,
        "changed_implies_invalid": False,
        "stale_implies_false": False,
        "generic_trust_score_used": False,
    }
    return payload
