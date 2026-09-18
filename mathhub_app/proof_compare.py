from __future__ import annotations

from typing import Any


def _uniq(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(value) for value in values if str(value)))


def _active_dependencies(route: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for dependency in route.get("depends_on") or []:
        if not isinstance(dependency, dict) or dependency.get("active") is False:
            continue
        claim_id = str(dependency.get("claim_id") or "")
        if claim_id:
            result[claim_id] = dependency
    return result


def _verification_axioms(route: dict[str, Any]) -> set[str]:
    verification = route.get("verification") or {}
    return set(_uniq(verification.get("axioms") or []))


def _route_summary(route: dict[str, Any]) -> dict[str, Any]:
    deps = _active_dependencies(route)
    raw = route.get("raw_refs") or {}
    coverage = dict(route.get("coverage") or {})
    verification = route.get("verification") or {}
    return {
        "proof_id": route.get("proof_id"),
        "claim_id": route.get("claim_id"),
        "claim_version_id": route.get("claim_version_id"),
        "proof_author": route.get("proof_author"),
        "status": route.get("status"),
        "verification": verification,
        "argument_build_id": route.get("argument_build_id"),
        "argument_index_complete": bool(route.get("argument_index_complete")),
        "coverage": coverage,
        "depends_on": list(deps.values()),
        "direct_claim_dependencies": len(deps),
        "raw_proof_constants": len(_uniq(raw.get("proof") or [])),
        "raw_transitive_constants": len(_uniq(raw.get("transitive") or [])),
        "statement_constants": len(_uniq(raw.get("statement") or [])),
        "axiom_count": len(_verification_axioms(route)),
    }


def compare_proof_routes(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Compare two Proof routes without manufacturing new mathematical relations.

    Direct Claim dependencies are the mathematical footprint. Raw proof and
    transitive constants are a separate formal footprint. The function is a pure
    read model over already-recorded ArgumentIndex/Build evidence.
    """
    if str(left.get("claim_id") or "") != str(right.get("claim_id") or ""):
        raise ValueError("proofs must establish the same Claim")
    if str(left.get("claim_version_id") or "") != str(right.get("claim_version_id") or ""):
        raise ValueError("proofs must establish the same Claim revision")
    if str(left.get("proof_id") or "") == str(right.get("proof_id") or ""):
        raise ValueError("choose two different Proofs")

    witnesses_available = bool(left.get("argument_index_complete")) and bool(right.get("argument_index_complete"))
    left_deps = _active_dependencies(left)
    right_deps = _active_dependencies(right)
    left_ids, right_ids = set(left_deps), set(right_deps)
    shared_ids = left_ids & right_ids
    union_ids = left_ids | right_ids

    left_raw = left.get("raw_refs") or {}
    right_raw = right.get("raw_refs") or {}
    left_proof_consts = set(_uniq(left_raw.get("proof") or []))
    right_proof_consts = set(_uniq(right_raw.get("proof") or []))
    left_transitive = set(_uniq(left_raw.get("transitive") or []))
    right_transitive = set(_uniq(right_raw.get("transitive") or []))
    left_axioms = _verification_axioms(left)
    right_axioms = _verification_axioms(right)

    overlap_percent = None
    formal_overlap_percent = None
    if witnesses_available:
        overlap_percent = 100.0 if not union_ids else round(100.0 * len(shared_ids) / len(union_ids), 1)
        formal_union = left_proof_consts | right_proof_consts
        formal_overlap_percent = 100.0 if not formal_union else round(
            100.0 * len(left_proof_consts & right_proof_consts) / len(formal_union), 1
        )

    def dep_items(ids: set[str], source: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        return [source[claim_id] for claim_id in sorted(ids, key=lambda cid: str(source[cid].get("title") or cid))]

    shared_dependencies: list[dict[str, Any]] = []
    for claim_id in sorted(shared_ids, key=lambda cid: str(left_deps[cid].get("title") or cid)):
        a, b = left_deps[claim_id], right_deps[claim_id]
        shared_dependencies.append(
            {
                "claim_id": claim_id,
                "title": a.get("title") or b.get("title"),
                "lean_name": a.get("lean_name") or b.get("lean_name"),
                "left_evidence_constants": _uniq(a.get("evidence_constants") or []),
                "right_evidence_constants": _uniq(b.get("evidence_constants") or []),
            }
        )

    left_coverage = str((left.get("coverage") or {}).get("status") or "unavailable")
    right_coverage = str((right.get("coverage") or {}).get("status") or "unavailable")
    fully_covered = witnesses_available and left_coverage == "complete" and right_coverage == "complete"

    return {
        "claim_id": left.get("claim_id"),
        "claim_version_id": left.get("claim_version_id"),
        "argument_witnesses_available": witnesses_available,
        "comparison_scope": "complete-current-dependencies" if fully_covered else (
            "registered-current-dependencies" if witnesses_available else "verification-only"
        ),
        "left": _route_summary(left),
        "right": _route_summary(right),
        "mathematical": {
            "shared_dependencies": shared_dependencies,
            "only_left": dep_items(left_ids - right_ids, left_deps),
            "only_right": dep_items(right_ids - left_ids, right_deps),
            "shared_count": len(shared_ids),
            "union_count": len(union_ids),
            "dependency_overlap_percent": overlap_percent,
        },
        "formal": {
            "shared_direct_constants": sorted(left_proof_consts & right_proof_consts)[:120],
            "only_left_direct_constants": sorted(left_proof_consts - right_proof_consts)[:120],
            "only_right_direct_constants": sorted(right_proof_consts - left_proof_consts)[:120],
            "direct_constant_overlap_percent": formal_overlap_percent,
            "left_direct_count": len(left_proof_consts),
            "right_direct_count": len(right_proof_consts),
            "left_transitive_count": len(left_transitive),
            "right_transitive_count": len(right_transitive),
            "shared_transitive_count": len(left_transitive & right_transitive),
        },
        "trust": {
            "shared_axioms": sorted(left_axioms & right_axioms),
            "only_left_axioms": sorted(left_axioms - right_axioms),
            "only_right_axioms": sorted(right_axioms - left_axioms),
            "left_trust_class": (left.get("verification") or {}).get("trust_class"),
            "right_trust_class": (right.get("verification") or {}).get("trust_class"),
        },
    }
