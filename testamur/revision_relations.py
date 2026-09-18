from __future__ import annotations

import sqlite3
from typing import Any

from .revision_store import TestamurRevisionStore


class TestamurRelationRevisionStore(TestamurRevisionStore):
    """Revision store extension that snapshots relation lifecycle state.

    ``RelationIndex`` is intentionally a layer above the frozen historical Core
    and may not have created its tables when a project is first opened. The
    snapshot format therefore treats edge retractions as an optional,
    forward-compatible field. Historical storage names remain unchanged.
    """

    @staticmethod
    def _has_table(conn: sqlite3.Connection, table: str) -> bool:
        return bool(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
        )

    def _snapshot_manifest(self, conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
        manifest = super()._snapshot_manifest(conn, project_id)
        retraction_refs: dict[str, str] = {}
        if self._has_table(conn, "witness_edge_retractions"):
            rows = conn.execute(
                """SELECT * FROM witness_edge_retractions
                   WHERE project_id=? ORDER BY created_at,id""",
                (project_id,),
            ).fetchall()
            for row in rows:
                record = {
                    "id": row["id"],
                    "edge_id": row["edge_id"],
                    "project_id": row["project_id"],
                    "reason": row["reason"],
                    "actor_ref": row["actor_ref"],
                    "created_at": row["created_at"],
                }
                retraction_refs[str(row["id"])] = self._put_content(
                    conn, "edge_retraction", record
                )
        manifest["edge_retractions"] = retraction_refs
        return manifest

    @staticmethod
    def _event_derived_retractions(reconstructed: dict[str, Any]) -> list[dict[str, Any]]:
        derived: list[dict[str, Any]] = []
        seen: set[str] = set()
        for event in reconstructed.get("events", []):
            if event.get("event_kind") != "edge_retracted":
                continue
            payload = event.get("payload") or {}
            retraction_id = str(payload.get("retraction_id") or "")
            edge_id = str(event.get("subject_id") or "")
            key = retraction_id or edge_id
            if not key or key in seen:
                continue
            seen.add(key)
            derived.append(
                {
                    "id": retraction_id or None,
                    "edge_id": edge_id,
                    "project_id": event.get("project_id"),
                    "reason": str(payload.get("reason") or ""),
                    "actor_ref": None,
                    "created_at": event.get("created_at"),
                    "derived_from_event_seq": event.get("seq"),
                }
            )
        return derived

    def reconstruct_revision(self, revision_address: str) -> dict[str, Any]:
        reconstructed = super().reconstruct_revision(revision_address)
        manifest = reconstructed.get("manifest") or {}
        if "edge_retractions" in manifest:
            refs = manifest.get("edge_retractions") or {}
            with self.connect() as conn:
                retractions = [
                    self._get_content_conn(conn, ref, expected_type="edge_retraction")
                    for _, ref in sorted(refs.items())
                ]
            fidelity = "snapshot_exact"
        else:
            retractions = self._event_derived_retractions(reconstructed)
            fidelity = "event_derived"

        reconstructed["edge_retractions"] = sorted(
            retractions,
            key=lambda item: (
                str(item.get("created_at") or ""),
                str(item.get("id") or ""),
                str(item.get("edge_id") or ""),
            ),
        )
        reconstructed["retraction_fidelity"] = fidelity
        return reconstructed

    def verify_revision_integrity(self, revision_address: str) -> dict[str, Any]:
        result = super().verify_revision_integrity(revision_address)
        relation_errors: list[str] = []
        checked = 0
        try:
            with self.connect() as conn:
                state = self._state_revision_conn(conn, revision_address)
                manifest = self._get_content_conn(
                    conn, state["snapshot_address"], expected_type="snapshot"
                )
                for ref in manifest.get("edge_retractions", {}).values():
                    self._get_content_conn(conn, ref, expected_type="edge_retraction")
                    checked += 1
        except (KeyError, ValueError, sqlite3.DatabaseError) as exc:
            relation_errors.append(str(exc))
        if relation_errors:
            result["errors"] = [*result.get("errors", []), *relation_errors]
            result["ok"] = False
        result["checked_edge_retractions"] = checked
        return result


__all__ = ["TestamurRelationRevisionStore"]
