from __future__ import annotations

from typing import Any, Iterable

from . import revision_store_legacy as _legacy


class TestamurRevisionStore(_legacy.LegacyRevisionStore):
    """Revision store with a defined same-object concurrent-write boundary."""

    def revise_object(self, object_id: str, payload: dict[str, Any], *, actor_ref: str = "", parent_revision_ids: Iterable[str] | None = None) -> dict[str, Any]:
        if not isinstance(payload, dict) or not payload:
            raise ValueError("Testamur revision payload must be a non-empty object")
        now = _legacy.utc_now(); revision_id = _legacy.new_id("trv")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project_id = self._object_project(conn, object_id); previous = self._latest_revision_row(conn, object_id)
            if parent_revision_ids is None:
                parents = [str(previous["id"])]
            else:
                parents = list(dict.fromkeys(str(item) for item in parent_revision_ids))
                if not parents: raise ValueError("a non-root object revision must have at least one parent")
            for parent_id in parents:
                parent = conn.execute("SELECT object_id FROM witness_revisions WHERE id=?", (parent_id,)).fetchone()
                if not parent: raise KeyError(parent_id)
                if str(parent["object_id"]) != object_id: raise ValueError("object revision parents must belong to the same object")
            latest_no_row = conn.execute("SELECT revision_no FROM witness_revisions WHERE object_id=? ORDER BY revision_no DESC LIMIT 1", (object_id,)).fetchone()
            if latest_no_row is None: raise RuntimeError("existing Testamur object has no root revision")
            revision_no = int(latest_no_row["revision_no"]) + 1
            conn.execute("INSERT INTO witness_revisions(id,object_id,revision_no,payload_json,content_hash,actor_ref,created_at) VALUES(?,?,?,?,?,?,?)", (revision_id, object_id, revision_no, self._json(payload), _legacy.canonical_hash(payload), actor_ref, now))
            for position, parent_id in enumerate(parents):
                conn.execute("INSERT INTO witness_revision_parents(child_revision_id,parent_revision_id,position) VALUES(?,?,?)", (revision_id, parent_id, position))
            self._event(conn, project_id, "object_revised", object_id, {"revision_id": revision_id, "revision_no": revision_no, "parent_revision_ids": parents, "previous_revision_id": previous["id"]})
        return self.get_object(object_id)


__all__ = ["TestamurRevisionStore"]
