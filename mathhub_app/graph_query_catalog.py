from __future__ import annotations

from typing import Any

from build_selection import ensure_active_argument_build_index
from dependency_index import ensure_proof_dependency_route_index
from formal_binding_index import ensure_formal_binding_name_index
from graph_query_catalog_sql import *  # noqa: F401,F403 - compatibility re-export
from graph_query_catalog_sql import GraphQueryCatalog as _GraphQuerySqlCatalog
from query_capabilities import (
    QUERY_CAPABILITY_VERSION,
    capability_matrix_for_connection,
    path_witness_unavailable,
)
from registry import claim_ref


class GraphQueryCatalog(_GraphQuerySqlCatalog):
    """Capability-gated facade over exact graph-query execution.

    The SQL implementation stays in ``graph_query_catalog_sql``. This public
    class owns request-level evidence availability and stable compatibility
    metadata for graph-query consumers.
    """

    def ensure_query_indexes(self) -> None:
        """Delegate shared index lifecycle through the public facade owners."""
        with self.connect() as conn:
            ensure_active_argument_build_index(conn)
            ensure_proof_dependency_route_index(conn)
            ensure_formal_binding_name_index(conn)
            if self._table_exists(conn, "proofs"):
                conn.execute(
                    """CREATE INDEX IF NOT EXISTS idx_graph_proofs_version
                       ON proofs(claim_version_id,id)"""
                )

    def page(
        self,
        *,
        limit: int = 40,
        before_internal_id: int | None = None,
        query: str = "",
        status: str = "all",
    ) -> dict[str, Any]:
        payload = super().page(
            limit=limit,
            before_internal_id=before_internal_id,
            query=query,
            status=status,
        )
        # ``path_via_execution`` predates route-through support. Preserve that
        # compatibility label while the generalized field records the newer
        # route-aware batched executor.
        if payload.get("path_via_execution") == GRAPH_PATH_EXECUTION:
            payload["path_via_execution"] = "batched-exists-v1"
        return payload

    def path_witness(
        self,
        source_claim_id: int,
        *,
        depth: int,
        target: str,
        via: str | None = None,
        route_through: list[str] | None = None,
    ) -> dict[str, Any]:
        if int(depth) != PATH_DEPTH:
            raise ValueError("path witness currently supports exact depth 2 only")

        target_selector = self._canonical_graph_value(target, "path")
        via_selector = self._canonical_graph_value(via, "via") if via else ""
        selectors = list(
            dict.fromkeys(
                self._canonical_graph_value(str(value), "route-through")
                for value in (route_through or [])
            )
        )[:8]

        with self.connect() as conn:
            matrix = capability_matrix_for_connection(conn)
        gaps = path_witness_unavailable(
            target=target_selector,
            via=via_selector or None,
            route_through=selectors,
            matrix=matrix,
        )
        if gaps:
            return {
                "available": False,
                "matched": False,
                "depth": PATH_DEPTH,
                "source_claim_id": claim_ref(source_claim_id),
                "target_selector": target_selector,
                "via_selector": via_selector or None,
                "route_through_selectors": selectors,
                "steps": [],
                "source_route_constraints": [],
                "query_capability_version": QUERY_CAPABILITY_VERSION,
                "query_unavailable_requirements": gaps,
            }

        payload = super().path_witness(
            source_claim_id,
            depth=depth,
            target=target_selector,
            via=via_selector or None,
            route_through=selectors,
        )
        payload["query_capability_version"] = QUERY_CAPABILITY_VERSION
        payload["query_unavailable_requirements"] = []
        return payload
