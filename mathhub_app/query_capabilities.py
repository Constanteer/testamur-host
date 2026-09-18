from __future__ import annotations

import re
import sqlite3
from typing import Any, Iterable, Mapping


QUERY_CAPABILITY_VERSION = "query-capabilities-v1"
CLAIM_REF_RE = re.compile(r"CLM-\d+", re.IGNORECASE)

# One declarative table defines the storage/evidence families required by each
# selector kind. Query code may add stricter mathematical conditions, but it must
# not weaken these availability requirements or invent alternate evidence.
CAPABILITY_RULES: dict[str, dict[str, tuple[str, ...]]] = {
    "statement-ref": {"any": ("build_constant_refs",)},
    "proof-ref": {"any": ("build_constant_refs",)},
    "uses": {
        "claim_ref": ("proof_claim_dependencies",),
        "lean_name": ("proof_claim_dependencies", "formal_bindings"),
    },
    "used-by": {
        "claim_ref": ("proof_claim_dependencies",),
        "lean_name": ("proof_claim_dependencies", "formal_bindings"),
    },
    "path": {
        "claim_ref": ("proof_claim_dependencies",),
        "lean_name": ("proof_claim_dependencies", "formal_bindings"),
    },
    "via": {
        "claim_ref": ("proof_claim_dependencies",),
        "lean_name": ("proof_claim_dependencies", "formal_bindings"),
    },
    "route-through": {
        "claim_ref": ("proof_claim_dependencies",),
        "lean_name": ("proof_claim_dependencies", "formal_bindings"),
    },
    # Negative selectors are evidence-bearing exclusions. Claim-ref spelling does
    # not remove the need to map complete raw proof constants through FormalBinding.
    "not-uses": {
        "claim_ref": ("proof_claim_dependencies", "formal_bindings", "build_constant_refs"),
        "lean_name": ("proof_claim_dependencies", "formal_bindings", "build_constant_refs"),
    },
    "route-uses": {
        "claim_ref": ("proof_claim_dependencies",),
        "lean_name": ("proof_claim_dependencies", "formal_bindings"),
    },
    "route-not-uses": {
        "claim_ref": ("proof_claim_dependencies", "formal_bindings", "build_constant_refs"),
        "lean_name": ("proof_claim_dependencies", "formal_bindings", "build_constant_refs"),
    },
    "head": {"any": ("formal_type_shapes",)},
    "binders": {"any": ("formal_type_shapes",)},
    "binder-domain": {"any": ("formal_type_partitions", "formal_type_partition_refs")},
    "result-ref": {"any": ("formal_type_partitions", "formal_type_partition_refs")},
}


def selector_kind(value: str) -> str:
    return "claim_ref" if CLAIM_REF_RE.fullmatch(str(value or "").strip()) else "lean_name"


def schema_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


def capability_matrix(tables: Iterable[str]) -> dict[str, dict[str, dict[str, Any]]]:
    present = {str(name) for name in tables}
    matrix: dict[str, dict[str, dict[str, Any]]] = {}
    for operator, kinds in CAPABILITY_RULES.items():
        matrix[operator] = {}
        for kind, required in kinds.items():
            missing = [table for table in required if table not in present]
            matrix[operator][kind] = {
                "available": not missing,
                "requires": list(required),
                "missing": missing,
            }
    return matrix


def capability_matrix_for_connection(conn: sqlite3.Connection) -> dict[str, dict[str, dict[str, Any]]]:
    return capability_matrix(schema_tables(conn))


def public_capability_matrix(
    matrix: Mapping[str, Mapping[str, Mapping[str, Any]]]
) -> dict[str, dict[str, bool]]:
    return {
        operator: {
            kind: bool(detail.get("available"))
            for kind, detail in kinds.items()
        }
        for operator, kinds in matrix.items()
    }


def _term(operator: str, value: str, kind: str | None = None) -> dict[str, str]:
    return {
        "operator": operator,
        "value": str(value),
        "selector_kind": kind or selector_kind(str(value)),
    }


def parsed_query_terms(parsed: Mapping[str, Any]) -> list[dict[str, str]]:
    terms: list[dict[str, str]] = []
    terms.extend(_term("statement-ref", value, "any") for value in parsed.get("statement_refs", []))
    terms.extend(_term("proof-ref", value, "any") for value in parsed.get("proof_refs", []))
    terms.extend(_term("uses", value) for value in parsed.get("uses", []))
    terms.extend(_term("used-by", value) for value in parsed.get("used_by", []))
    for path in parsed.get("paths", []):
        terms.append(_term("path", str(path.get("target") or "")))
        if path.get("via"):
            terms.append(_term("via", str(path["via"])))
        terms.extend(_term("route-through", value) for value in path.get("route_through", []))
    terms.extend(_term("not-uses", value) for value in parsed.get("not_uses", []))
    terms.extend(_term("route-uses", value) for value in parsed.get("route_uses", []))
    terms.extend(_term("route-not-uses", value) for value in parsed.get("route_not_uses", []))
    if parsed.get("head"):
        terms.append(_term("head", str(parsed["head"]), "any"))
    if parsed.get("binders") is not None:
        terms.append(_term("binders", str(parsed["binders"]), "any"))
    terms.extend(_term("binder-domain", value, "any") for value in parsed.get("binder_domains", []))
    terms.extend(_term("result-ref", value, "any") for value in parsed.get("result_refs", []))
    return terms


def unavailable_terms(
    terms: Iterable[Mapping[str, str]],
    matrix: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for term in terms:
        operator = str(term["operator"])
        kind = str(term["selector_kind"])
        detail = matrix.get(operator, {}).get(kind)
        if detail is None:
            gaps.append({**term, "missing": ["capability-rule"]})
            continue
        if not bool(detail.get("available")):
            gaps.append({**term, "missing": list(detail.get("missing") or [])})
    return gaps


def parsed_query_unavailable(
    parsed: Mapping[str, Any],
    matrix: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    return unavailable_terms(parsed_query_terms(parsed), matrix)


def quantifier_unavailable(
    *,
    not_uses: Iterable[str],
    route_uses: Iterable[str],
    route_not_uses: Iterable[str],
    matrix: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    terms = [*(_term("not-uses", value) for value in not_uses)]
    terms.extend(_term("route-uses", value) for value in route_uses)
    terms.extend(_term("route-not-uses", value) for value in route_not_uses)
    return unavailable_terms(terms, matrix)


def path_witness_terms(
    *,
    target: str,
    via: str | None = None,
    route_through: Iterable[str] = (),
) -> list[dict[str, str]]:
    terms = [_term("path", target)]
    if via:
        terms.append(_term("via", via))
    terms.extend(_term("route-through", value) for value in route_through)
    return terms


def path_witness_unavailable(
    *,
    target: str,
    via: str | None = None,
    route_through: Iterable[str] = (),
    matrix: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    return unavailable_terms(
        path_witness_terms(target=target, via=via, route_through=route_through),
        matrix,
    )


def catalog_availability_flags(
    matrix: Mapping[str, Mapping[str, Mapping[str, Any]]]
) -> dict[str, bool]:
    def available(operator: str, kind: str) -> bool:
        return bool(matrix[operator][kind]["available"])

    return {
        "statement_ref_search_available": available("statement-ref", "any"),
        "proof_ref_search_available": available("proof-ref", "any"),
        "uses_search_available": available("uses", "claim_ref"),
        "uses_lean_name_search_available": available("uses", "lean_name"),
        "used_by_search_available": available("used-by", "claim_ref"),
        "used_by_lean_name_search_available": available("used-by", "lean_name"),
        "path_search_available": available("path", "claim_ref"),
        "path_lean_name_search_available": available("path", "lean_name"),
        "not_uses_search_available": available("not-uses", "claim_ref"),
        "not_uses_lean_name_search_available": available("not-uses", "lean_name"),
        "route_uses_search_available": available("route-uses", "claim_ref"),
        "route_uses_lean_name_search_available": available("route-uses", "lean_name"),
        "route_not_uses_search_available": available("route-not-uses", "claim_ref"),
        "route_not_uses_lean_name_search_available": available("route-not-uses", "lean_name"),
        "head_search_available": available("head", "any"),
        "binder_count_search_available": available("binders", "any"),
        "binder_domain_search_available": available("binder-domain", "any"),
        "result_ref_search_available": available("result-ref", "any"),
    }
