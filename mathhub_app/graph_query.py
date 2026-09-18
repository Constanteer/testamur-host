from __future__ import annotations

import sqlite3
from collections import deque
from pathlib import Path
from typing import Any

from registry import build_ref, claim_ref
from version_state import current_version_state


class GraphQuery:
    """Bounded semantic graph reader over the *current* mathematical state.

    Historical Lean-derived edges remain stored, but a current graph edge is active
    only when the source Build belongs to the source Claim's current Version and
    the target Claim's current Version still has a matching formal binding in the
    same environment. Manual Claim-level semantic relations remain active across
    versions because they are assertions about durable Claim identity.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _bounds(depth: int, limit: int) -> tuple[int, int]:
        return max(0, min(int(depth), 4)), max(1, min(int(limit), 200))

    def neighborhood(
        self,
        *,
        root_internal_id: int | None = None,
        depth: int = 1,
        limit: int = 80,
        relation_mode: str = "all",
    ) -> dict[str, Any]:
        depth, limit = self._bounds(depth, limit)
        relation_mode = relation_mode.strip().lower() or "all"
        if relation_mode not in {"all", "direct", "uses", "semantic"}:
            raise ValueError(f"unsupported graph relation mode: {relation_mode}")

        with self.connect() as conn:
            if root_internal_id is not None:
                if conn.execute("SELECT 1 FROM claims WHERE id = ?", (root_internal_id,)).fetchone() is None:
                    raise KeyError(claim_ref(root_internal_id))
                node_ids, truncated = self._bfs(conn, root_internal_id, depth, limit, relation_mode)
                mode = "neighborhood"
            else:
                node_ids = self._recent_seed_ids(conn, limit)
                truncated = False
                mode = "recent"

            nodes = self._nodes(conn, node_ids)
            edges = self._edges(conn, node_ids, relation_mode)

        return {
            "mode": mode,
            "root": claim_ref(root_internal_id) if root_internal_id is not None else None,
            "depth": depth,
            "node_limit": limit,
            "truncated": truncated,
            "nodes": nodes,
            "edges": edges,
        }

    @staticmethod
    def _relation_clause(mode: str, alias: str = "sr") -> str:
        if mode == "direct":
            return f"AND {alias}.relation = 'uses-directly'"
        if mode == "uses":
            return f"AND {alias}.relation IN ('uses-directly', 'uses-transitively')"
        if mode == "semantic":
            return f"AND {alias}.relation NOT IN ('uses-directly', 'uses-transitively')"
        return ""

    @staticmethod
    def _active_relation_clause(alias: str = "sr") -> str:
        return f"""AND (
            {alias}.evidence_kind != 'lean-dependency'
            OR (
                EXISTS (
                    SELECT 1 FROM builds sb
                    JOIN proofs sp ON sp.id = sb.proof_id
                    WHERE sb.id = {alias}.source_build_id
                      AND sp.claim_version_id = (
                          SELECT cv.id FROM claim_versions cv
                          WHERE cv.claim_id = {alias}.claim_id
                          ORDER BY cv.version_no DESC LIMIT 1
                      )
                )
                AND EXISTS (
                    SELECT 1 FROM builds sb
                    JOIN build_constant_edges ce ON ce.build_id = sb.id
                    JOIN formal_bindings fb
                      ON fb.claim_id = {alias}.related_claim_id
                     AND fb.environment_hash = sb.environment_hash
                     AND fb.lean_name = ce.dependency_name
                    WHERE sb.id = {alias}.source_build_id
                      AND fb.claim_version_id = (
                          SELECT cv.id FROM claim_versions cv
                          WHERE cv.claim_id = {alias}.related_claim_id
                          ORDER BY cv.version_no DESC LIMIT 1
                      )
                      AND (
                          ({alias}.relation = 'uses-directly' AND ce.depth_kind = 'direct')
                          OR ({alias}.relation = 'uses-transitively' AND ce.depth_kind = 'transitive')
                      )
                )
            )
        )"""

    def _bfs(
        self,
        conn: sqlite3.Connection,
        root: int,
        depth: int,
        limit: int,
        relation_mode: str,
    ) -> tuple[list[int], bool]:
        relation_clause = self._relation_clause(relation_mode)
        active_clause = self._active_relation_clause()
        seen = {root}
        ordered = [root]
        queue: deque[tuple[int, int]] = deque([(root, 0)])
        truncated = False

        while queue:
            current, level = queue.popleft()
            if level >= depth:
                continue
            rows = conn.execute(
                f"""SELECT sr.claim_id, sr.related_claim_id
                    FROM semantic_relations sr
                    WHERE (sr.claim_id = ? OR sr.related_claim_id = ?)
                    {relation_clause} {active_clause}
                    ORDER BY sr.id DESC""",
                (current, current),
            ).fetchall()
            for row in rows:
                a, b = int(row["claim_id"]), int(row["related_claim_id"])
                neighbor = b if a == current else a
                if neighbor in seen:
                    continue
                if len(ordered) >= limit:
                    truncated = True
                    return ordered, truncated
                seen.add(neighbor)
                ordered.append(neighbor)
                queue.append((neighbor, level + 1))
        return ordered, truncated

    def _recent_seed_ids(self, conn: sqlite3.Connection, limit: int) -> list[int]:
        rows = conn.execute(
            """SELECT claim_id FROM (
                   SELECT claim_id, MAX(id) AS last_edge FROM semantic_relations GROUP BY claim_id
                   UNION ALL
                   SELECT related_claim_id AS claim_id, MAX(id) AS last_edge
                   FROM semantic_relations GROUP BY related_claim_id
               ) GROUP BY claim_id ORDER BY MAX(last_edge) DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        ids = [int(row["claim_id"]) for row in rows]
        if len(ids) < limit:
            placeholders = ",".join("?" for _ in ids)
            if ids:
                fill = conn.execute(
                    f"SELECT id FROM claims WHERE id NOT IN ({placeholders}) ORDER BY id DESC LIMIT ?",
                    (*ids, limit - len(ids)),
                ).fetchall()
            else:
                fill = conn.execute("SELECT id FROM claims ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            ids.extend(int(row["id"]) for row in fill)
        return ids

    def _nodes(self, conn: sqlite3.Connection, node_ids: list[int]) -> list[dict[str, Any]]:
        if not node_ids:
            return []
        placeholders = ",".join("?" for _ in node_ids)
        rows = conn.execute(
            f"""SELECT c.id AS claim_id, cv.id AS version_id, cv.title, cv.lean_name, cv.area
                FROM claims c JOIN claim_versions cv ON cv.claim_id = c.id
                WHERE c.id IN ({placeholders})
                  AND cv.version_no = (
                      SELECT MAX(v.version_no) FROM claim_versions v WHERE v.claim_id = c.id
                  )""",
            tuple(node_ids),
        ).fetchall()
        by_id: dict[int, dict[str, Any]] = {}
        for row in rows:
            cid = int(row["claim_id"])
            state = current_version_state(conn, cid, int(row["version_id"]))
            by_id[cid] = {
                "id": claim_ref(cid),
                "title": row["title"],
                "status": state["status"],
                "lean_name": row["lean_name"],
                "area": row["area"],
            }
        return [by_id[cid] for cid in node_ids if cid in by_id]

    def _edges(
        self,
        conn: sqlite3.Connection,
        node_ids: list[int],
        relation_mode: str,
    ) -> list[dict[str, Any]]:
        if not node_ids:
            return []
        placeholders = ",".join("?" for _ in node_ids)
        relation_clause = self._relation_clause(relation_mode)
        active_clause = self._active_relation_clause()
        rows = conn.execute(
            f"""SELECT sr.* FROM semantic_relations sr
                WHERE sr.claim_id IN ({placeholders})
                  AND sr.related_claim_id IN ({placeholders})
                  {relation_clause} {active_clause}
                  AND sr.id IN (
                      SELECT MAX(x.id) FROM semantic_relations x
                      GROUP BY x.claim_id, x.related_claim_id, x.relation, x.evidence_kind
                  )
                ORDER BY sr.id DESC""",
            (*node_ids, *node_ids),
        ).fetchall()
        return [
            {
                "from": claim_ref(int(row["related_claim_id"])),
                "to": claim_ref(int(row["claim_id"])),
                "relation": row["relation"],
                "evidence_kind": row["evidence_kind"],
                "source_build_id": build_ref(int(row["source_build_id"]))
                    if row["source_build_id"] else None,
            }
            for row in rows
        ]
