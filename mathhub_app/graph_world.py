from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from argument_index import ArgumentIndex
from registry import claim_ref


DEFAULT_DETAIL_NODE_LIMIT = 300
DEFAULT_DETAIL_EDGE_LIMIT = 1500
DEFAULT_CLUSTER_SLICE_LIMIT = 100


class GraphWorld:
    """Persistent global graph with deterministic hierarchical level of detail.

    Readability budgets decide whether the browser receives all current Claims or
    a global skeleton. Curated ``area`` metadata wins when present. Claims without
    an area fall back to their preferred Lean namespace. Imported library Claims
    use their original external FormalBinding name rather than the local
    ``MathHub.Import.*`` alias, so bulk mathlib import retains useful structure.

    Namespace clusters are hierarchical: ``Topology`` may unfold to
    ``Topology.Algebra`` and then to real Claims. Direct Claims inside one cluster
    are cursor-paginated, while child clusters remain stable across pages.

    Cluster membership, child aggregation and cursor slicing are performed inside
    SQLite. Python only receives aggregate child rows and the current direct-Claim
    page; it never materializes the whole namespace merely to reveal the next 100
    nodes. Every expansion remains a read model: Claim/Proof ontology and
    ProofDependency evidence are unchanged.
    """

    def __init__(self, path: Path, argument: ArgumentIndex) -> None:
        self.path = Path(path)
        self.argument = argument
        with self.connect() as conn:
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_graph_binding_version_origin
                   ON formal_bindings(claim_version_id,origin,id DESC)"""
            )

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    @classmethod
    def root_cluster_key(cls, area: Any, lean_name: Any) -> str:
        curated = cls._clean(area)
        if curated:
            return f"area:{curated}"
        name = cls._clean(lean_name)
        if not name:
            return "ns:Unclassified"
        return f"ns:{name.split('.', 1)[0]}"

    @staticmethod
    def cluster_id(cluster_key: str) -> str:
        digest = hashlib.sha256(cluster_key.encode("utf-8")).hexdigest()[:12].upper()
        return f"AREA-{digest}"

    @staticmethod
    def cluster_title(cluster_key: str) -> str:
        _, _, value = cluster_key.partition(":")
        return value or "Unclassified"

    @staticmethod
    def cluster_source(cluster_key: str) -> str:
        return "curated-area" if cluster_key.startswith("area:") else "lean-namespace"

    @staticmethod
    def cluster_depth(cluster_key: str) -> int:
        if cluster_key.startswith("area:"):
            return 1
        value = cluster_key.partition(":")[2]
        return max(1, len([part for part in value.split(".") if part]))

    @classmethod
    def cluster_node(
        cls,
        cluster_key: str,
        claim_count: int,
        *,
        parent_cluster_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "id": cls.cluster_id(cluster_key),
            "kind": "cluster",
            "title": cls.cluster_title(cluster_key),
            "cluster_key": cluster_key,
            "cluster_source": cls.cluster_source(cluster_key),
            "namespace_path": cls.cluster_title(cluster_key)
            if cluster_key.startswith("ns:")
            else None,
            "depth": cls.cluster_depth(cluster_key),
            "claim_count": int(claim_count),
            "parent_cluster_id": parent_cluster_id,
            "status": "cluster",
        }

    @staticmethod
    def _preferred_name_sql(alias: str) -> str:
        return f"""COALESCE((
          SELECT fb.lean_name FROM formal_bindings fb
          WHERE fb.claim_version_id={alias}.id
            AND fb.origin='imported-declaration'
          ORDER BY fb.id DESC LIMIT 1
        ), {alias}.lean_name)"""

    @classmethod
    def _root_key_sql(cls, alias: str) -> str:
        name = cls._preferred_name_sql(alias)
        return f"""CASE
          WHEN trim(COALESCE({alias}.area,''))<>''
            THEN 'area:' || trim({alias}.area)
          WHEN trim(COALESCE({name},''))=''
            THEN 'ns:Unclassified'
          WHEN instr({name},'.')>0
            THEN 'ns:' || substr({name},1,instr({name},'.')-1)
          ELSE 'ns:' || {name}
        END"""

    @staticmethod
    def _current_version_sql(alias: str) -> str:
        return f"""{alias}.version_no=(
          SELECT MAX(x.version_no) FROM claim_versions x
          WHERE x.claim_id={alias}.claim_id
        )"""

    def _projected_edge_count(self, conn: sqlite3.Connection) -> int:
        active_build = self.argument._active_argument_build_sql("p")
        row = conn.execute(
            f"""SELECT COUNT(*) edge_count FROM (
                  SELECT p.claim_id source_claim_id,d.target_claim_id target_claim_id
                  FROM proof_claim_dependencies d
                  JOIN proofs p ON p.id=d.proof_id
                  WHERE p.claim_version_id=(
                    SELECT id FROM claim_versions x
                    WHERE x.claim_id=p.claim_id ORDER BY version_no DESC LIMIT 1
                  )
                  AND d.target_claim_version_id=(
                    SELECT id FROM claim_versions y
                    WHERE y.claim_id=d.target_claim_id ORDER BY version_no DESC LIMIT 1
                  )
                  AND d.source_build_id={active_build}
                  GROUP BY p.claim_id,d.target_claim_id
                ) projected"""
        ).fetchone()
        return int(row["edge_count"] if row else 0)

    def overview(
        self,
        *,
        detail_limit: int = DEFAULT_DETAIL_NODE_LIMIT,
        edge_limit: int = DEFAULT_DETAIL_EDGE_LIMIT,
    ) -> dict[str, Any]:
        detail_limit = max(1, min(int(detail_limit), 1200))
        edge_limit = max(0, min(int(edge_limit), 10000))
        active_build = self.argument._active_argument_build_sql("p")

        with self.connect() as conn:
            count_row = conn.execute("SELECT COUNT(*) claim_count FROM claims").fetchone()
            claim_count = int(count_row["claim_count"] if count_row else 0)
            projected_edge_count: int | None = None
            lod_reason = "node-budget" if claim_count > detail_limit else ""

            if claim_count <= detail_limit:
                projected_edge_count = self._projected_edge_count(conn)
                if projected_edge_count <= edge_limit:
                    ids = [int(row["id"]) for row in conn.execute("SELECT id FROM claims ORDER BY id").fetchall()]
                    return {
                        "mode": "world-detail",
                        "persistent": True,
                        "session_id": "mathhub-global-world-v2",
                        "root": None,
                        "nodes": self.argument._nodes(conn, ids),
                        "edges": self.argument._edges(conn, ids),
                        "cluster_count": 0,
                        "claim_count": claim_count,
                        "projected_edge_count": projected_edge_count,
                        "detail_node_limit": detail_limit,
                        "detail_edge_limit": edge_limit,
                        "lod_reason": None,
                    }
                lod_reason = "edge-budget"

            source_key = self._root_key_sql("scv")
            target_key = self._root_key_sql("tcv")
            root_key = self._root_key_sql("cv")
            clusters = conn.execute(
                f"""SELECT {root_key} cluster_key,COUNT(*) claim_count
                    FROM claim_versions cv
                    WHERE {self._current_version_sql('cv')}
                    GROUP BY cluster_key ORDER BY claim_count DESC,cluster_key"""
            ).fetchall()
            edge_rows = conn.execute(
                f"""SELECT {source_key} source_key,{target_key} target_key,
                          COUNT(DISTINCT p.id) proof_count,
                          COUNT(DISTINCT CAST(p.claim_id AS TEXT)||':'||CAST(d.target_claim_id AS TEXT)) claim_pair_count
                    FROM proof_claim_dependencies d
                    JOIN proofs p ON p.id=d.proof_id
                    JOIN claim_versions scv ON scv.id=p.claim_version_id
                    JOIN claim_versions tcv ON tcv.id=d.target_claim_version_id
                    WHERE p.claim_version_id=(
                      SELECT id FROM claim_versions x WHERE x.claim_id=p.claim_id ORDER BY version_no DESC LIMIT 1
                    )
                    AND d.target_claim_version_id=(
                      SELECT id FROM claim_versions y WHERE y.claim_id=d.target_claim_id ORDER BY version_no DESC LIMIT 1
                    )
                    AND d.source_build_id={active_build}
                    GROUP BY source_key,target_key"""
            ).fetchall()

        nodes = [self.cluster_node(str(row["cluster_key"]), int(row["claim_count"])) for row in clusters]
        edges = [
            {
                "from": self.cluster_id(str(row["source_key"])),
                "to": self.cluster_id(str(row["target_key"])),
                "relation": "uses",
                "evidence_kind": "cluster-projection",
                "proof_count": int(row["proof_count"]),
                "claim_pair_count": int(row["claim_pair_count"]),
            }
            for row in edge_rows
            if str(row["source_key"]) != str(row["target_key"])
        ]
        return {
            "mode": "world-clustered",
            "persistent": True,
            "session_id": "mathhub-global-world-v2",
            "root": None,
            "nodes": nodes,
            "edges": edges,
            "cluster_count": len(nodes),
            "claim_count": claim_count,
            "projected_edge_count": projected_edge_count,
            "detail_node_limit": detail_limit,
            "detail_edge_limit": edge_limit,
            "lod_reason": lod_reason,
            "cluster_policy": "curated-area-then-external-binding-then-lean-namespace",
        }

    def _cluster_filter_sql(self, cluster_key: str, alias: str = "cv") -> tuple[str, list[Any]]:
        if cluster_key.startswith("area:"):
            area = cluster_key.partition(":")[2]
            if not area:
                raise ValueError("area cluster is empty")
            return f"trim(COALESCE({alias}.area,''))=?", [area]

        if not cluster_key.startswith("ns:"):
            raise ValueError("cluster key must start with area: or ns:")
        prefix = cluster_key.partition(":")[2]
        if not prefix:
            raise ValueError("namespace cluster is empty")
        name = self._preferred_name_sql(alias)
        if prefix == "Unclassified":
            return (
                f"trim(COALESCE({alias}.area,''))='' AND trim(COALESCE({name},''))=''",
                [],
            )
        marker = prefix + "."
        return (
            f"trim(COALESCE({alias}.area,''))='' AND "
            f"({name}=? OR substr({name},1,?)=?)",
            [prefix, len(marker), marker],
        )

    def _child_key_sql(self, cluster_key: str, alias: str = "cv") -> tuple[str, list[Any]]:
        if not cluster_key.startswith("ns:") or cluster_key == "ns:Unclassified":
            return "NULL", []
        prefix = cluster_key.partition(":")[2]
        start = len(prefix) + 2
        name = self._preferred_name_sql(alias)
        remainder = f"substr({name},{start})"
        return (
            f"""CASE
              WHEN instr({remainder},'.')>0
                THEN 'ns:' || ? || '.' || substr({remainder},1,instr({remainder},'.')-1)
              ELSE NULL
            END""",
            [prefix],
        )

    def _prepare_cluster_members(self, conn: sqlite3.Connection, cluster_key: str) -> None:
        conn.execute(
            """CREATE TEMP TABLE IF NOT EXISTS graph_cluster_members(
                 claim_id INTEGER PRIMARY KEY,
                 child_key TEXT
               )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS graph_cluster_members_child_idx ON graph_cluster_members(child_key)")
        conn.execute("DELETE FROM graph_cluster_members")
        child_expr, child_params = self._child_key_sql(cluster_key, "cv")
        filter_sql, filter_params = self._cluster_filter_sql(cluster_key, "cv")
        conn.execute(
            f"""INSERT INTO graph_cluster_members(claim_id,child_key)
                SELECT cv.claim_id,{child_expr}
                FROM claim_versions cv
                WHERE {self._current_version_sql('cv')}
                  AND {filter_sql}""",
            child_params + filter_params,
        )

    def _visual_edges_from_temp_map(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        active_build = self.argument._active_argument_build_sql("p")
        rows = conn.execute(
            f"""SELECT sm.visual_id source_visual,tm.visual_id target_visual,
                       COUNT(DISTINCT p.id) proof_count,
                       COUNT(DISTINCT CAST(p.claim_id AS TEXT)||':'||CAST(d.target_claim_id AS TEXT)) claim_pair_count
                FROM proof_claim_dependencies d
                JOIN proofs p ON p.id=d.proof_id
                JOIN graph_visual_map sm ON sm.claim_id=p.claim_id
                JOIN graph_visual_map tm ON tm.claim_id=d.target_claim_id
                WHERE p.claim_version_id=(
                  SELECT id FROM claim_versions x WHERE x.claim_id=p.claim_id ORDER BY version_no DESC LIMIT 1
                )
                AND d.target_claim_version_id=(
                  SELECT id FROM claim_versions y WHERE y.claim_id=d.target_claim_id ORDER BY version_no DESC LIMIT 1
                )
                AND d.source_build_id={active_build}
                GROUP BY sm.visual_id,tm.visual_id"""
        ).fetchall()
        return [
            {
                "from": str(row["source_visual"]),
                "to": str(row["target_visual"]),
                "relation": "uses",
                "evidence_kind": "cluster-projection"
                if str(row["source_visual"]).startswith("AREA-")
                or str(row["target_visual"]).startswith("AREA-")
                else "proof-projection",
                "proof_count": int(row["proof_count"]),
                "claim_pair_count": int(row["claim_pair_count"]),
            }
            for row in rows
            if str(row["source_visual"]) != str(row["target_visual"])
        ]

    def _prepare_visual_projection(
        self,
        conn: sqlite3.Connection,
        *,
        parent_id: str,
        child_nodes: list[dict[str, Any]],
        page_ids: list[int],
        before_internal_id: int | None,
    ) -> None:
        conn.execute(
            "CREATE TEMP TABLE IF NOT EXISTS graph_visual_map(claim_id INTEGER PRIMARY KEY,visual_id TEXT NOT NULL)"
        )
        conn.execute("DELETE FROM graph_visual_map")
        conn.execute(
            "CREATE TEMP TABLE IF NOT EXISTS graph_child_visual_map(child_key TEXT PRIMARY KEY,visual_id TEXT NOT NULL)"
        )
        conn.execute("DELETE FROM graph_child_visual_map")
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS graph_page_claims(claim_id INTEGER PRIMARY KEY)")
        conn.execute("DELETE FROM graph_page_claims")

        if child_nodes:
            conn.executemany(
                "INSERT INTO graph_child_visual_map(child_key,visual_id) VALUES(?,?)",
                [(str(node["cluster_key"]), str(node["id"])) for node in child_nodes],
            )
        if page_ids:
            conn.executemany(
                "INSERT INTO graph_page_claims(claim_id) VALUES(?)",
                [(claim_id,) for claim_id in page_ids],
            )

        previous_visible_sql = "0"
        params: list[Any] = []
        if before_internal_id is not None:
            previous_visible_sql = "m.claim_id>=?"
            params.append(before_internal_id)
        params.append(parent_id)
        conn.execute(
            f"""INSERT INTO graph_visual_map(claim_id,visual_id)
                SELECT m.claim_id,
                  CASE
                    WHEN m.child_key IS NOT NULL THEN child.visual_id
                    WHEN ({previous_visible_sql}) OR page.claim_id IS NOT NULL
                      THEN printf('CLM-%06d',m.claim_id)
                    ELSE ?
                  END
                FROM graph_cluster_members m
                LEFT JOIN graph_child_visual_map child ON child.child_key=m.child_key
                LEFT JOIN graph_page_claims page ON page.claim_id=m.claim_id""",
            params,
        )

    def cluster_slice(
        self,
        cluster_key: str,
        *,
        limit: int = DEFAULT_CLUSTER_SLICE_LIMIT,
        before_internal_id: int | None = None,
    ) -> dict[str, Any]:
        cluster_key = self._clean(cluster_key)
        limit = max(1, min(int(limit), 200))
        before_internal_id = int(before_internal_id) if before_internal_id is not None else None
        parent_id = self.cluster_id(cluster_key)

        with self.connect() as conn:
            self._prepare_cluster_members(conn, cluster_key)
            counts = conn.execute(
                """SELECT COUNT(*) claim_count,
                          SUM(CASE WHEN child_key IS NULL THEN 1 ELSE 0 END) direct_claim_count
                   FROM graph_cluster_members"""
            ).fetchone()
            claim_count = int(counts["claim_count"] or 0)
            direct_claim_count = int(counts["direct_claim_count"] or 0)

            if before_internal_id is not None:
                valid = conn.execute(
                    "SELECT 1 FROM graph_cluster_members WHERE claim_id=? AND child_key IS NULL",
                    (before_internal_id,),
                ).fetchone()
                if not valid:
                    raise ValueError("cluster cursor does not belong to a direct Claim in this cluster")

            child_rows = conn.execute(
                """SELECT child_key,COUNT(*) claim_count
                   FROM graph_cluster_members
                   WHERE child_key IS NOT NULL
                   GROUP BY child_key
                   ORDER BY child_key"""
            ).fetchall()
            child_nodes = [
                self.cluster_node(str(row["child_key"]), int(row["claim_count"]), parent_cluster_id=parent_id)
                for row in child_rows
            ]

            page_rows = conn.execute(
                """SELECT claim_id
                   FROM graph_cluster_members
                   WHERE child_key IS NULL
                     AND (? IS NULL OR claim_id < ?)
                   ORDER BY claim_id DESC
                   LIMIT ?""",
                (before_internal_id, before_internal_id, limit + 1),
            ).fetchall()
            visible_rows = page_rows[:limit]
            page_ids = [int(row["claim_id"]) for row in visible_rows]
            claim_nodes = self.argument._nodes(conn, page_ids)
            for node in claim_nodes:
                node["parent_cluster_id"] = parent_id

            last_visible_id = page_ids[-1] if page_ids else None
            if last_visible_id is None:
                remaining_claim_count = 0
            else:
                remaining_claim_count = int(
                    conn.execute(
                        """SELECT COUNT(*) remaining
                           FROM graph_cluster_members
                           WHERE child_key IS NULL AND claim_id < ?""",
                        (last_visible_id,),
                    ).fetchone()["remaining"]
                )

            self._prepare_visual_projection(
                conn,
                parent_id=parent_id,
                child_nodes=child_nodes,
                page_ids=page_ids,
                before_internal_id=before_internal_id,
            )
            edges = self._visual_edges_from_temp_map(conn)

        next_cursor = claim_ref(last_visible_id) if last_visible_id is not None and remaining_claim_count else None
        return {
            "mode": "cluster",
            "persistent": True,
            "session_id": "mathhub-global-world-v2",
            "cluster_key": cluster_key,
            "cluster_id": parent_id,
            "cluster_source": self.cluster_source(cluster_key),
            "cluster_depth": self.cluster_depth(cluster_key),
            "claim_count": claim_count,
            "child_cluster_count": len(child_nodes),
            "direct_claim_count": direct_claim_count,
            "page_claim_count": len(page_ids),
            "remaining_claim_count": remaining_claim_count,
            "node_limit": limit,
            "cursor": claim_ref(before_internal_id) if before_internal_id is not None else None,
            "next_cursor": next_cursor,
            "truncated": remaining_claim_count > 0,
            "nodes": child_nodes + claim_nodes,
            "edges": edges,
            "projection_scope": f"cluster:{cluster_key}",
            "query_model": "sqlite-aggregated-v1",
        }

    def area_slice(
        self,
        area: str,
        *,
        limit: int = DEFAULT_CLUSTER_SLICE_LIMIT,
        before_internal_id: int | None = None,
    ) -> dict[str, Any]:
        return self.cluster_slice(
            f"area:{self._clean(area)}",
            limit=limit,
            before_internal_id=before_internal_id,
        )