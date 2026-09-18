from __future__ import annotations

from typing import Any

from query_evidence_state import (
    QUERY_EVIDENCE_STATE_VERSION,
    ESTABLISHED,
    complete_route,
    incomplete_route,
    negative_selector_state,
    summarize_route_group,
    summarize_universal,
)
from registry import build_ref, claim_ref, proof_ref


def quantifier_witness_with_state(
    catalog: Any,
    source_claim_id: int,
    *,
    not_uses: list[str],
    route_uses: list[str],
    route_not_uses: list[str],
) -> dict[str, Any]:
    """Execute historical quantifier truth and evidence-state audit in one pass.

    Capability preflight belongs to the public Catalog facade. This function assumes
    all schema/evidence families required by the requested selector kinds exist.
    Mathematical truth remains the historical current-route semantics; the richer
    state is derived from the same observations rather than from a second audit.
    """
    with catalog.connect() as conn:
        routes = catalog._current_routes(conn, source_claim_id)
        raw_cache: dict[tuple[int, str], set[int]] = {}
        resolved_cache: dict[tuple[str, str], list[int]] = {}

        def raw_used(build_id: int, environment_hash: str) -> set[int]:
            key = (build_id, environment_hash)
            if key not in raw_cache:
                raw_cache[key] = catalog._raw_used_claim_ids(conn, build_id, environment_hash)
            return raw_cache[key]

        def resolved(environment_hash: str, selector: str) -> list[int]:
            key = (environment_hash, selector)
            if key not in resolved_cache:
                resolved_cache[key] = catalog._resolved_claim_ids(conn, environment_hash, selector)
            return resolved_cache[key]

        def negative_route_state(route: Any, selectors: list[str]) -> dict[str, Any]:
            proof_id = int(route["proof_id"])
            if route["build_id"] is None:
                return incomplete_route(proof_id=proof_ref(proof_id))
            build_id = int(route["build_id"])
            environment_hash = str(route["environment_hash"] or "")
            if not selectors:
                return complete_route(
                    proof_id=proof_ref(proof_id),
                    build_id=build_ref(build_id),
                    environment_hash=environment_hash,
                    selector_states=[],
                )
            used = {claim_ref(claim_id) for claim_id in raw_used(build_id, environment_hash)}
            selector_states = []
            for selector in selectors:
                target_ids = [claim_ref(claim_id) for claim_id in resolved(environment_hash, selector)]
                selector_states.append(
                    negative_selector_state(
                        selector=selector,
                        resolved_claim_ids=target_ids,
                        raw_used_claim_ids=used,
                    )
                )
            return complete_route(
                proof_id=proof_ref(proof_id),
                build_id=build_ref(build_id),
                environment_hash=environment_hash,
                selector_states=selector_states,
            )

        universal_route_states = (
            [negative_route_state(route, not_uses) for route in routes]
            if not_uses
            else []
        )
        universal_state = summarize_universal(
            universal_route_states,
            requested=bool(not_uses),
        )

        universal_rows: list[dict[str, Any]] = []
        universal_ok = bool(routes)
        for selector in not_uses:
            selector_ok = bool(routes)
            checked = 0
            for route_state in universal_route_states:
                if route_state.get("reason") == "missing-active-build":
                    selector_ok = False
                    break
                selector_state = next(
                    (
                        item
                        for item in route_state.get("selectors", [])
                        if str(item.get("selector")) == selector
                    ),
                    None,
                )
                if selector_state is None or selector_state.get("state") != ESTABLISHED:
                    selector_ok = False
                    break
                checked += 1
            universal_rows.append(
                {
                    "selector": selector,
                    "matched": selector_ok,
                    "complete_routes_checked": checked,
                }
            )
            universal_ok = universal_ok and selector_ok

        route_group = None
        route_candidates: list[dict[str, Any]] = []
        route_requested = bool(route_uses or route_not_uses)
        if route_requested:
            for route in routes:
                proof_id = int(route["proof_id"])
                if route["build_id"] is None:
                    route_candidates.append(incomplete_route(proof_id=proof_ref(proof_id)))
                    continue
                build_id = int(route["build_id"])
                environment_hash = str(route["environment_hash"] or "")
                positives: list[dict[str, Any]] = []
                positive_ok = True
                for selector in route_uses:
                    match = catalog._route_positive_match(conn, proof_id, build_id, selector)
                    if match is None:
                        positive_ok = False
                        break
                    positives.append(match)
                if not positive_ok:
                    continue

                route_state = negative_route_state(route, route_not_uses)
                route_state["positive"] = positives
                route_candidates.append(route_state)
                if route_state["state"] != ESTABLISHED:
                    continue

                negatives = []
                for selector_state in route_state.get("selectors", []):
                    negatives.append(
                        {
                            "selector": selector_state["selector"],
                            "resolved_claim_ids": list(selector_state.get("resolved_claim_ids") or []),
                        }
                    )
                route_group = {
                    "matched": True,
                    "proof_id": proof_ref(proof_id),
                    "build_id": build_ref(build_id),
                    "environment_hash": environment_hash,
                    "positive": positives,
                    "negative": negatives,
                }
                # Historical witness chooses the first current route satisfying the
                # whole same-route group. The aggregate evidence state is already
                # established once such a route exists, so later routes add no truth.
                break

            if route_group is None:
                route_group = {"matched": False, "positive": [], "negative": []}

        route_state_summary = summarize_route_group(
            route_candidates,
            requested=route_requested,
            current_route_count=len(routes),
        )

    return {
        "available": True,
        "source_claim_id": claim_ref(source_claim_id),
        "universal_matched": universal_ok if not_uses else None,
        "universal": universal_rows,
        "route_group": route_group,
        "query_evidence_state_version": QUERY_EVIDENCE_STATE_VERSION,
        "evidence_state": {
            "version": QUERY_EVIDENCE_STATE_VERSION,
            "universal": universal_state,
            "route_group": route_state_summary,
            "audit_counters": {
                "current_routes": len(routes),
                "raw_build_mappings": len(raw_cache),
                "selector_resolutions": len(resolved_cache),
            },
        },
    }
