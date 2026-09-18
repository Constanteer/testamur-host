from __future__ import annotations

import json
import sqlite3
from collections import deque
from typing import Any, Iterable

from .model import EdgeKind, canonical_hash, new_id, normalize_edge_kind
from .store import utc_now
from .versioned_store import TestamurVersionedStore


class RelationIndex:
    """Append-only lifecycle and graph queries for historical semantic relations.

    This is the migrated semantic graph relation index used by the historical
    versioned project store. It is distinct from the newer durable
    ``testamur.record_store`` Relation object family. Edges are immutable
    observations; ending an active relation creates an immutable retraction.
    """

    DEFAULT_DEPENDENCY_KINDS = (
        EdgeKind.DEPENDS_ON.value,
        EdgeKind.ASSUMES.value,
        EdgeKind.DERIVED_FROM.value,
    )

    def __init__(self, store: TestamurVersionedStore) -> None:
        self.store = store
        self._init_schema()

    def _init_schema(self) -> None:
        with self.store.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS witness_edge_retractions(
                  id TEXT PRIMARY KEY,
                  edge_id TEXT NOT NULL UNIQUE REFERENCES witness_edges(id),
                  project_id TEXT NOT NULL REFERENCES witness_projects(id),
                  reason TEXT NOT NULL DEFAULT '',
                  actor_ref TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_witness_retractions_project
                  ON witness_edge_retractions(project_id,created_at);
                CREATE TRIGGER IF NOT EXISTS witness_edge_retractions_no_update
                BEFORE UPDATE ON witness_edge_retractions BEGIN
                  SELECT RAISE(ABORT,'Witness edge retractions are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS witness_edge_retractions_no_delete
                BEFORE DELETE ON witness_edge_retractions BEGIN
                  SELECT RAISE(ABORT,'Witness edge retractions are immutable');
                END;
                """
            )

    @staticmethod
    def _serialize(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "source_object_id": row["source_object_id"],
            "target_object_id": row["target_object_id"],
            "kind": row["kind"],
            "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
            "retracted": bool(row["retraction_id"]),
            "retraction": None if not row["retraction_id"] else {
                "id": row["retraction_id"],
                "reason": row["retraction_reason"],
                "actor_ref": row["retraction_actor_ref"],
                "created_at": row["retracted_at"],
            },
        }

    def edges(self, project: str, *, include_retracted: bool = False) -> list[dict[str, Any]]:
        project_id = self.store._project_id(project)
        clause = "" if include_retracted else " AND r.id IS NULL"
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""SELECT e.*,r.id retraction_id,r.reason retraction_reason,
                           r.actor_ref retraction_actor_ref,r.created_at retracted_at
                    FROM witness_edges e
                    LEFT JOIN witness_edge_retractions r ON r.edge_id=e.id
                    WHERE e.project_id=?{clause}
                    ORDER BY e.created_at,e.id""",
                (project_id,),
            ).fetchall()
        return [self._serialize(row) for row in rows]

    def retract(self, edge_id: str, *, reason: str = "", actor_ref: str = "") -> dict[str, Any]:
        reason_text = str(reason or "").strip()
        if not reason_text:
            raise ValueError("relation retraction requires a non-empty provenance reason")
        retraction_id = new_id("wrt")
        now = utc_now()
        with self.store.connect() as conn:
            edge = conn.execute("SELECT * FROM witness_edges WHERE id=?", (edge_id,)).fetchone()
            if not edge:
                raise KeyError(edge_id)
            try:
                edge_payload = json.loads(edge["payload_json"])
            except json.JSONDecodeError as exc:
                raise ValueError("cannot retract relation with invalid stored payload JSON") from exc
            if (
                str(edge["kind"]) == EdgeKind.DERIVED_FROM.value
                and edge_payload.get("role") == "semantic_copy_forward"
            ):
                raise ValueError(
                    "semantic migration lineage is immutable provenance and cannot be retracted; "
                    "supersede or reject the migrated object instead"
                )
            existing = conn.execute(
                "SELECT * FROM witness_edge_retractions WHERE edge_id=?", (edge_id,)
            ).fetchone()
            if existing:
                return {
                    "id": existing["id"],
                    "edge_id": existing["edge_id"],
                    "project_id": existing["project_id"],
                    "reason": existing["reason"],
                    "actor_ref": existing["actor_ref"],
                    "created_at": existing["created_at"],
                    "already_retracted": True,
                }
            project_id = str(edge["project_id"])
            conn.execute(
                """INSERT INTO witness_edge_retractions(
                     id,edge_id,project_id,reason,actor_ref,created_at
                   ) VALUES(?,?,?,?,?,?)""",
                (retraction_id, edge_id, project_id, reason_text, actor_ref, now),
            )
            self.store._event(
                conn,
                project_id,
                "edge_retracted",
                edge_id,
                {"retraction_id": retraction_id, "reason": reason_text},
            )
        return {
            "id": retraction_id,
            "edge_id": edge_id,
            "project_id": project_id,
            "reason": reason_text,
            "actor_ref": actor_ref,
            "created_at": now,
            "already_retracted": False,
        }

    def _active_rows(
        self,
        conn: sqlite3.Connection,
        project_id: str,
        kinds: Iterable[str],
    ) -> list[sqlite3.Row]:
        normalized = [normalize_edge_kind(kind) for kind in kinds]
        if not normalized:
            return []
        placeholders = ",".join("?" for _ in normalized)
        return conn.execute(
            f"""SELECT e.* FROM witness_edges e
                LEFT JOIN witness_edge_retractions r ON r.edge_id=e.id
                WHERE e.project_id=? AND r.id IS NULL AND e.kind IN({placeholders})
                ORDER BY e.id""",
            [project_id, *normalized],
        ).fetchall()

    def dependency_closure(
        self,
        project: str,
        root_object_id: str,
        *,
        kinds: Iterable[str] = DEFAULT_DEPENDENCY_KINDS,
        max_nodes: int = 5000,
    ) -> dict[str, Any]:
        project_id = self.store._project_id(project)
        with self.store.connect() as conn:
            if self.store._object_project(conn, root_object_id) != project_id:
                raise ValueError("root object is not part of this project")
            rows = self._active_rows(conn, project_id, kinds)
        outgoing: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            outgoing.setdefault(str(row["source_object_id"]), []).append(
                {
                    "edge_id": str(row["id"]),
                    "target": str(row["target_object_id"]),
                    "kind": str(row["kind"]),
                }
            )
        queue = deque([root_object_id])
        seen = {root_object_id}
        traversed: list[dict[str, str]] = []
        truncated = False
        while queue:
            source = queue.popleft()
            for edge in outgoing.get(source, []):
                traversed.append({"source": source, **edge})
                target = edge["target"]
                if target not in seen:
                    if len(seen) >= max_nodes:
                        truncated = True
                        continue
                    seen.add(target)
                    queue.append(target)
        dependency_ids = sorted(seen - {root_object_id})
        canonical_edges = sorted(
            (edge["source"], edge["kind"], edge["target"], edge["edge_id"])
            for edge in traversed
        )
        return {
            "root_object_id": root_object_id,
            "dependency_object_ids": dependency_ids,
            "edges": traversed,
            "truncated": truncated,
            "topology_fingerprint": canonical_hash(canonical_edges),
        }

    def impact(
        self,
        project: str,
        changed_object_id: str,
        *,
        kinds: Iterable[str] = DEFAULT_DEPENDENCY_KINDS,
        max_nodes: int = 5000,
    ) -> dict[str, Any]:
        project_id = self.store._project_id(project)
        with self.store.connect() as conn:
            if self.store._object_project(conn, changed_object_id) != project_id:
                raise ValueError("changed object is not part of this project")
            rows = self._active_rows(conn, project_id, kinds)
            object_kinds = {
                str(row["id"]): str(row["kind"])
                for row in conn.execute(
                    "SELECT id,kind FROM witness_objects WHERE project_id=?", (project_id,)
                ).fetchall()
            }
        incoming: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            incoming.setdefault(str(row["target_object_id"]), []).append(
                {
                    "edge_id": str(row["id"]),
                    "source": str(row["source_object_id"]),
                    "kind": str(row["kind"]),
                }
            )
        queue = deque([changed_object_id])
        seen = {changed_object_id}
        traversed: list[dict[str, str]] = []
        while queue and len(seen) < max_nodes:
            target = queue.popleft()
            for edge in incoming.get(target, []):
                traversed.append({"target": target, **edge})
                source = edge["source"]
                if source not in seen:
                    seen.add(source)
                    queue.append(source)
        dependents = sorted(seen - {changed_object_id})
        claims = [object_id for object_id in dependents if object_kinds.get(object_id) == "claim"]
        return {
            "changed_object_id": changed_object_id,
            "dependent_object_ids": dependents,
            "affected_claim_ids": claims,
            "edges": traversed,
            "truncated": bool(queue),
        }


__all__ = ["RelationIndex"]
