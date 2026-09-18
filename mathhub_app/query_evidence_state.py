from __future__ import annotations

from typing import Any, Iterable, Mapping


QUERY_EVIDENCE_STATE_VERSION = "query-evidence-state-v1"

ESTABLISHED = "established"
CONTRADICTED = "contradicted"
INCOMPLETE = "incomplete"
NO_CURRENT_ROUTES = "no-current-routes"
NO_POSITIVE_ROUTE = "no-positive-route"
NOT_REQUESTED = "not-requested"


def negative_selector_state(
    *,
    selector: str,
    resolved_claim_ids: Iterable[str],
    raw_used_claim_ids: Iterable[str],
) -> dict[str, Any]:
    resolved = list(dict.fromkeys(str(value) for value in resolved_claim_ids if str(value)))
    raw_used = set(str(value) for value in raw_used_claim_ids if str(value))
    if not resolved:
        return {
            "selector": str(selector),
            "state": INCOMPLETE,
            "reason": "selector-unresolved",
            "resolved_claim_ids": [],
            "contradicting_claim_ids": [],
        }
    contradicting = [claim_id for claim_id in resolved if claim_id in raw_used]
    if contradicting:
        return {
            "selector": str(selector),
            "state": CONTRADICTED,
            "reason": "raw-proof-ref-present",
            "resolved_claim_ids": resolved,
            "contradicting_claim_ids": contradicting,
        }
    return {
        "selector": str(selector),
        "state": ESTABLISHED,
        "reason": "complete-raw-proof-exclusion",
        "resolved_claim_ids": resolved,
        "contradicting_claim_ids": [],
    }


def incomplete_route(*, proof_id: str, reason: str = "missing-active-build") -> dict[str, Any]:
    return {
        "proof_id": str(proof_id),
        "build_id": None,
        "environment_hash": "",
        "state": INCOMPLETE,
        "reason": str(reason),
        "selectors": [],
    }


def complete_route(
    *,
    proof_id: str,
    build_id: str,
    environment_hash: str,
    selector_states: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    selectors = [dict(item) for item in selector_states]
    states = {str(item.get("state") or INCOMPLETE) for item in selectors}
    if CONTRADICTED in states:
        state = CONTRADICTED
        reason = "negative-selector-contradicted"
    elif INCOMPLETE in states:
        state = INCOMPLETE
        reason = "negative-selector-incomplete"
    else:
        state = ESTABLISHED
        reason = "all-negative-selectors-established"
    return {
        "proof_id": str(proof_id),
        "build_id": str(build_id),
        "environment_hash": str(environment_hash),
        "state": state,
        "reason": reason,
        "selectors": selectors,
    }


def summarize_universal(routes: Iterable[Mapping[str, Any]], *, requested: bool) -> dict[str, Any]:
    rows = [dict(route) for route in routes]
    if not requested:
        return {"state": NOT_REQUESTED, "established": None, "routes": []}
    if not rows:
        return {"state": NO_CURRENT_ROUTES, "established": False, "routes": []}
    states = {str(route.get("state") or INCOMPLETE) for route in rows}
    if CONTRADICTED in states:
        state = CONTRADICTED
    elif INCOMPLETE in states:
        state = INCOMPLETE
    else:
        state = ESTABLISHED
    return {"state": state, "established": state == ESTABLISHED, "routes": rows}


def summarize_route_group(
    candidate_routes: Iterable[Mapping[str, Any]],
    *,
    requested: bool,
    current_route_count: int,
) -> dict[str, Any]:
    rows = [dict(route) for route in candidate_routes]
    if not requested:
        return {"state": NOT_REQUESTED, "established": None, "candidate_routes": []}
    if current_route_count <= 0:
        return {"state": NO_CURRENT_ROUTES, "established": False, "candidate_routes": []}
    if not rows:
        return {"state": NO_POSITIVE_ROUTE, "established": False, "candidate_routes": []}
    states = [str(route.get("state") or INCOMPLETE) for route in rows]
    if ESTABLISHED in states:
        state = ESTABLISHED
    elif INCOMPLETE in states:
        state = INCOMPLETE
    elif CONTRADICTED in states:
        state = CONTRADICTED
    else:
        state = INCOMPLETE
    return {"state": state, "established": state == ESTABLISHED, "candidate_routes": rows}
