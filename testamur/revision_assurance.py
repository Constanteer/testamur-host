from __future__ import annotations

import sqlite3
from typing import Any

from .revision_relations import TestamurRelationRevisionStore

ASSURANCE_SNAPSHOT_VERSION = "witness-assurance-snapshot-v0.1"


class TestamurVersionedStore(TestamurRelationRevisionStore):
    """Snapshot optional assurance history without expanding the frozen Core.

    Assurance is extension infrastructure, not a Core entity/relation. When the
    append-only historical assurance table exists, later project snapshots commit
    its immutable rows into CAS under ``manifest.extensions.assurance``.
    Snapshots created before that table existed remain valid and explicitly
    reconstruct with ``availability=not_snapshot_backed``.
    """

    def _snapshot_manifest(self, conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
        manifest = super()._snapshot_manifest(conn, project_id)
        if not self._has_table(conn, "witness_assurance_runs"):
            return manifest

        run_refs: dict[str, str] = {}
        rows = conn.execute(
            """SELECT rowid AS assurance_sequence,* FROM witness_assurance_runs
               WHERE project_id=? ORDER BY rowid""",
            (project_id,),
        ).fetchall()
        for row in rows:
            record = {
                "sequence": int(row["assurance_sequence"]),
                "id": row["id"],
                "project_id": row["project_id"],
                "target_object_id": row["target_object_id"],
                "producer_object_id": row["producer_object_id"],
                "assurance_kind": row["assurance_kind"],
                "status": row["status"],
                "scope": self._load(row["scope_json"]),
                "assumption_object_ids": self._load(row["assumption_object_ids_json"]),
                "input_revisions": self._load(row["input_revisions_json"]),
                "environment": self._load(row["environment_json"]),
                "result": self._load(row["result_json"]),
                "artifact_ids": self._load(row["artifact_ids_json"]),
                "actor_ref": row["actor_ref"],
                "created_at": row["created_at"],
            }
            run_refs[str(row["id"])] = self._put_content(conn, "assurance_run", record)

        extensions = dict(manifest.get("extensions") or {})
        extensions["assurance"] = {
            "snapshot_version": ASSURANCE_SNAPSHOT_VERSION,
            "runs": run_refs,
        }
        manifest["extensions"] = extensions
        return manifest

    def reconstruct_revision(self, revision_address: str) -> dict[str, Any]:
        reconstructed = super().reconstruct_revision(revision_address)

        # Present each historical object with the payload of its latest revision
        # captured by this immutable snapshot. The legacy CAS intentionally stores
        # object identity and revision payloads separately; the Testamur facade
        # joins them at reconstruction time without rewriting durable hashes.
        latest_by_object: dict[str, dict[str, Any]] = {}
        for revision in reconstructed.get("revisions") or []:
            object_id = str(revision.get("object_id") or "")
            if not object_id:
                continue
            previous = latest_by_object.get(object_id)
            if previous is None or int(revision.get("revision_no") or 0) > int(
                previous.get("revision_no") or 0
            ):
                latest_by_object[object_id] = revision
        objects: list[dict[str, Any]] = []
        for raw_object in reconstructed.get("objects") or []:
            item = dict(raw_object)
            latest = latest_by_object.get(str(item.get("id") or ""))
            if latest is not None:
                item["payload"] = latest.get("payload")
                item["revision_id"] = latest.get("id")
                item["revision_no"] = latest.get("revision_no")
            objects.append(item)
        reconstructed["objects"] = objects

        manifest = reconstructed.get("manifest") or {}
        extension_manifest = (manifest.get("extensions") or {}).get("assurance")

        extensions = dict(reconstructed.get("extensions") or {})
        if not isinstance(extension_manifest, dict):
            extensions["assurance"] = {
                "availability": "not_snapshot_backed",
                "snapshot_version": None,
                "runs": [],
            }
            reconstructed["extensions"] = extensions
            return reconstructed

        refs = extension_manifest.get("runs") or {}
        with self.connect() as conn:
            runs = [
                self._get_content_conn(conn, ref, expected_type="assurance_run")
                for _, ref in sorted(refs.items())
            ]
        runs.sort(
            key=lambda item: (
                int(item.get("sequence") or 0),
                str(item.get("id") or ""),
            )
        )
        extensions["assurance"] = {
            "availability": "snapshot_exact",
            "snapshot_version": extension_manifest.get("snapshot_version"),
            "runs": runs,
        }
        reconstructed["extensions"] = extensions
        return reconstructed

    def verify_revision_integrity(self, revision_address: str) -> dict[str, Any]:
        result = super().verify_revision_integrity(revision_address)
        errors: list[str] = []
        checked = 0
        try:
            with self.connect() as conn:
                state = self._state_revision_conn(conn, revision_address)
                manifest = self._get_content_conn(
                    conn, state["snapshot_address"], expected_type="snapshot"
                )
                extension_manifest = (manifest.get("extensions") or {}).get("assurance")
                if isinstance(extension_manifest, dict):
                    for ref in (extension_manifest.get("runs") or {}).values():
                        self._get_content_conn(conn, ref, expected_type="assurance_run")
                        checked += 1
        except (KeyError, ValueError, sqlite3.DatabaseError) as exc:
            errors.append(str(exc))

        if errors:
            result["errors"] = [*result.get("errors", []), *errors]
            result["ok"] = False
        result["checked_assurance_runs"] = checked
        result["assurance_snapshot_extension"] = ASSURANCE_SNAPSHOT_VERSION
        return result


__all__ = ["ASSURANCE_SNAPSHOT_VERSION", "TestamurVersionedStore"]
