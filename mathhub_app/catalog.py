from __future__ import annotations

from typing import Any

from catalog_sql import *  # noqa: F401,F403 - compatibility re-export
from catalog_sql import ALLOWED_STATUSES, PATH_DEPTH, Catalog as _SqlCatalog
from quantifier_evidence import quantifier_witness_with_state
from query_capabilities import (
    QUERY_CAPABILITY_VERSION,
    capability_matrix_for_connection,
    catalog_availability_flags,
    parsed_query_unavailable,
    public_capability_matrix,
    quantifier_unavailable,
)
from query_evidence_state import QUERY_EVIDENCE_STATE_VERSION
from registry import claim_ref


class Catalog(_SqlCatalog):
    """Capability-gated facade over the stable catalog SQL implementation.

    ``catalog_sql.Catalog`` keeps the already-audited structural search SQL. This
    facade owns availability policy. Quantifier witnesses execute through one
    evidence audit that emits both the historical truth fields and route-level
    evidence state, avoiding a second pass over raw proof observations.
    """

    @staticmethod
    def _structural_filters(parsed: dict[str, Any]) -> dict[str, Any]:
        return {
            "namespace": str(parsed.get("namespace") or "") or None,
            "statement_refs": list(parsed.get("statement_refs") or []),
            "proof_refs": list(parsed.get("proof_refs") or []),
            "uses": list(parsed.get("uses") or []),
            "used_by": list(parsed.get("used_by") or []),
            "paths": list(parsed.get("paths") or []),
            "not_uses": list(parsed.get("not_uses") or []),
            "route_uses": list(parsed.get("route_uses") or []),
            "route_not_uses": list(parsed.get("route_not_uses") or []),
            "head": str(parsed.get("head") or "") or None,
            "binders": parsed.get("binders"),
            "binder_domains": list(parsed.get("binder_domains") or []),
            "result_refs": list(parsed.get("result_refs") or []),
        }

    @staticmethod
    def _decorate_capabilities(
        payload: dict[str, Any],
        matrix: dict[str, dict[str, dict[str, Any]]],
        gaps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload.update(catalog_availability_flags(matrix))
        payload["query_capability_version"] = QUERY_CAPABILITY_VERSION
        payload["query_capabilities"] = public_capability_matrix(matrix)
        payload["query_unavailable_requirements"] = gaps
        payload["path_supported_depths"] = [PATH_DEPTH] if payload["path_search_available"] else []
        payload["type_shape_version"] = TYPE_SHAPE_VERSION if payload["head_search_available"] else None
        payload["type_partition_version"] = (
            TYPE_PARTITION_VERSION if payload["binder_domain_search_available"] else None
        )
        return payload

    def _empty_unavailable_page(
        self,
        *,
        parsed: dict[str, Any],
        status: str,
        matrix: dict[str, dict[str, dict[str, Any]]],
        gaps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "claims": [],
            "next_cursor": None,
            "has_more": False,
            "page_size": 0,
            "query": str(parsed.get("raw") or ""),
            "text_query": str(parsed.get("text") or ""),
            "status": status or "all",
            "structural_filters": self._structural_filters(parsed),
        }
        return self._decorate_capabilities(payload, matrix, gaps)

    def page(
        self,
        *,
        limit: int = 40,
        before_internal_id: int | None = None,
        query: str = "",
        status: str = "all",
    ) -> dict[str, Any]:
        parsed = self.parse_query(query)
        normalized_status = str(status or "all").strip().lower()
        if normalized_status not in ALLOWED_STATUSES:
            raise ValueError(f"unsupported Claim status filter: {normalized_status}")
        if before_internal_id is not None and before_internal_id <= 0:
            raise ValueError("cursor must resolve to a positive Claim key")

        with self.connect() as conn:
            matrix = capability_matrix_for_connection(conn)
        gaps = parsed_query_unavailable(parsed, matrix)
        if gaps:
            return self._empty_unavailable_page(
                parsed=parsed,
                status=normalized_status,
                matrix=matrix,
                gaps=gaps,
            )

        payload = super().page(
            limit=limit,
            before_internal_id=before_internal_id,
            query=query,
            status=normalized_status,
        )
        return self._decorate_capabilities(payload, matrix, [])

    def quantifier_witness(
        self,
        source_claim_id: int,
        *,
        not_uses: list[str] | None = None,
        route_uses: list[str] | None = None,
        route_not_uses: list[str] | None = None,
    ) -> dict[str, Any]:
        not_uses = [self._canonical_graph_value(value, "not-uses") for value in (not_uses or [])][:8]
        route_uses = [self._canonical_graph_value(value, "route-uses") for value in (route_uses or [])][:8]
        route_not_uses = [
            self._canonical_graph_value(value, "route-not-uses")
            for value in (route_not_uses or [])
        ][:8]
        with self.connect() as conn:
            matrix = capability_matrix_for_connection(conn)
        gaps = quantifier_unavailable(
            not_uses=not_uses,
            route_uses=route_uses,
            route_not_uses=route_not_uses,
            matrix=matrix,
        )
        if gaps:
            return {
                "available": False,
                "source_claim_id": claim_ref(source_claim_id),
                "universal": [],
                "route_group": None,
                "query_capability_version": QUERY_CAPABILITY_VERSION,
                "query_unavailable_requirements": gaps,
                "query_evidence_state_version": QUERY_EVIDENCE_STATE_VERSION,
                "evidence_state": {
                    "version": QUERY_EVIDENCE_STATE_VERSION,
                    "schema_state": "unavailable",
                    "universal": None,
                    "route_group": None,
                },
            }

        payload = quantifier_witness_with_state(
            self,
            source_claim_id,
            not_uses=not_uses,
            route_uses=route_uses,
            route_not_uses=route_not_uses,
        )
        payload["query_capability_version"] = QUERY_CAPABILITY_VERSION
        payload["query_unavailable_requirements"] = []
        return payload
