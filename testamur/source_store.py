from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from .runtime_protocol import canonical_hash, canonical_json

SOURCE_PREFIX = "tst:source:"
REVISION_PREFIX = "tst:revision:"
SNAPSHOT_PREFIX = "tst:snapshot:"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _require_nonempty(value: str, *, field: str) -> str:
    raw = str(value).strip()
    if not raw:
        raise ValueError(f"{field} must not be empty")
    return raw


def _validate_sha256_digest(value: str) -> str:
    raw = _require_nonempty(value, field="content_hash")
    if not raw.startswith("sha256:"):
        raise ValueError("content_hash must use sha256:<64 lowercase-or-uppercase hex>")
    hex_value = raw[7:]
    if len(hex_value) != 64:
        raise ValueError("content_hash must contain exactly 64 SHA-256 hex characters")
    try:
        int(hex_value, 16)
    except ValueError as exc:
        raise ValueError("content_hash contains non-hexadecimal characters") from exc
    return "sha256:" + hex_value.lower()


class TestamurSourceStore:
    """Append-only Source -> Revision -> Snapshot identity store."""

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS testamur_sources(source_id TEXT PRIMARY KEY,initial_locator TEXT NOT NULL UNIQUE,record_json TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS testamur_source_revisions(revision_id TEXT PRIMARY KEY,source_id TEXT NOT NULL REFERENCES testamur_sources(source_id),content_hash TEXT NOT NULL,record_json TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(source_id,content_hash));
            CREATE TABLE IF NOT EXISTS testamur_source_snapshots(snapshot_id TEXT PRIMARY KEY,source_id TEXT NOT NULL REFERENCES testamur_sources(source_id),revision_id TEXT REFERENCES testamur_source_revisions(revision_id),locator TEXT NOT NULL,observed_at TEXT NOT NULL,recorded_at TEXT NOT NULL,status TEXT NOT NULL,content_hash TEXT,record_json TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS testamur_source_revisions_source_idx ON testamur_source_revisions(source_id,created_at);
            CREATE INDEX IF NOT EXISTS testamur_source_snapshots_source_recorded_idx ON testamur_source_snapshots(source_id,recorded_at DESC,snapshot_id DESC);
            CREATE INDEX IF NOT EXISTS testamur_source_snapshots_revision_idx ON testamur_source_snapshots(revision_id,recorded_at DESC,snapshot_id DESC);
            CREATE TRIGGER IF NOT EXISTS testamur_sources_no_update BEFORE UPDATE ON testamur_sources BEGIN SELECT RAISE(ABORT,'Testamur sources are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS testamur_sources_no_delete BEFORE DELETE ON testamur_sources BEGIN SELECT RAISE(ABORT,'Testamur sources are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS testamur_source_revisions_no_update BEFORE UPDATE ON testamur_source_revisions BEGIN SELECT RAISE(ABORT,'Testamur source revisions are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS testamur_source_revisions_no_delete BEFORE DELETE ON testamur_source_revisions BEGIN SELECT RAISE(ABORT,'Testamur source revisions are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS testamur_source_snapshots_no_update BEFORE UPDATE ON testamur_source_snapshots BEGIN SELECT RAISE(ABORT,'Testamur source snapshots are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS testamur_source_snapshots_no_delete BEFORE DELETE ON testamur_source_snapshots BEGIN SELECT RAISE(ABORT,'Testamur source snapshots are immutable'); END;
            """)

    def get_or_create_source(self, locator: str, *, source_id: str | None = None) -> dict[str, Any]:
        initial_locator = _require_nonempty(locator, field="locator")
        resolved_id = SOURCE_PREFIX + canonical_hash({"initial_locator": initial_locator}) if source_id is None else _require_nonempty(source_id, field="source_id")
        if not resolved_id.startswith(SOURCE_PREFIX) or resolved_id == SOURCE_PREFIX:
            raise ValueError(f"invalid Testamur source id: {resolved_id}")
        payload = {"source_id": resolved_id, "initial_locator": initial_locator, "identity_semantics": {"persistent_identity": True, "locator_equivalence_inferred": False, "default_id_derived_from_exact_initial_locator": source_id is None}}
        payload_json = canonical_json(payload)
        with self.connect() as conn:
            row = conn.execute("SELECT source_id,record_json FROM testamur_sources WHERE initial_locator=?", (initial_locator,)).fetchone()
            if row is not None:
                if source_id is not None and str(row["source_id"]) != resolved_id:
                    raise ValueError("locator is already bound to a different source id")
                return json.loads(str(row["record_json"]))
            row = conn.execute("SELECT record_json FROM testamur_sources WHERE source_id=?", (resolved_id,)).fetchone()
            if row is not None:
                existing = json.loads(str(row["record_json"]))
                if existing != payload:
                    raise ValueError("source id is already bound to different identity content")
                return existing
            conn.execute("INSERT INTO testamur_sources VALUES(?,?,?,?)", (resolved_id, initial_locator, payload_json, _utc_now()))
        return payload

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT record_json FROM testamur_sources WHERE source_id=?", (str(source_id),)).fetchone()
        return None if row is None else json.loads(str(row["record_json"]))

    def source_for_locator(self, locator: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT record_json FROM testamur_sources WHERE initial_locator=?", (str(locator),)).fetchone()
        return None if row is None else json.loads(str(row["record_json"]))

    def _ensure_revision(self, *, source_id: str, content_hash: str, recorded_at: str) -> dict[str, Any]:
        digest = _validate_sha256_digest(content_hash)
        revision_id = REVISION_PREFIX + canonical_hash({"source_id": source_id, "content_hash": digest})
        payload = {"revision_id": revision_id, "source_id": source_id, "content_hash": digest, "identity_semantics": {"exact_content_identity": True, "digest_algorithm": "sha256", "scoped_to_source": True, "raw_bytes_persisted_here": False}}
        encoded = canonical_json(payload)
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO testamur_source_revisions VALUES(?,?,?,?,?)", (revision_id, source_id, digest, encoded, recorded_at))
            row = conn.execute("SELECT record_json FROM testamur_source_revisions WHERE revision_id=?", (revision_id,)).fetchone()
            if row is None or str(row["record_json"]) != encoded:
                raise ValueError("source revision identity is bound to different content")
        return payload

    def record_snapshot(self, source_id: str, *, locator: str | None = None, content: bytes | None = None, content_hash: str | None = None, observed_at: str | None = None, status: str | None = None, retrieval_metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        source = self.get_source(source_id)
        if source is None:
            raise KeyError(source_id)
        snapshot_locator = _require_nonempty(locator or str(source["initial_locator"]), field="locator")
        recorded_at = _utc_now()
        observation_time = str(observed_at or recorded_at)
        computed = None if content is None else _sha256_bytes(bytes(content))
        declared = None if content_hash is None else _validate_sha256_digest(content_hash)
        if computed is not None and declared is not None and computed != declared:
            raise ValueError("declared content_hash does not match provided bytes")
        digest = computed or declared
        digest_basis = "computed_from_exact_bytes" if computed is not None else "declared_digest" if declared is not None else "not_available"
        resolved_status = str(status or ("captured" if digest is not None else "metadata_only"))
        if resolved_status == "captured" and digest is None:
            raise ValueError("captured source snapshot requires an exact content digest")
        revision = None if digest is None else self._ensure_revision(source_id=source_id, content_hash=digest, recorded_at=recorded_at)
        core = {"source_id": source_id, "revision_id": None if revision is None else revision["revision_id"], "locator": snapshot_locator, "observed_at": observation_time, "recorded_at": recorded_at, "status": resolved_status, "content_hash": digest, "digest_basis": digest_basis, "byte_size": None if content is None else len(content), "retrieval_metadata": dict(retrieval_metadata or {})}
        snapshot_id = SNAPSHOT_PREFIX + canonical_hash(core)
        payload = {"snapshot_id": snapshot_id, **core, "semantics": {"immutable_observation": True, "snapshot_is_revision_identity": False, "same_revision_may_have_multiple_snapshots": True, "raw_bytes_persisted_here": False, "recorded_at_is_local_persistence_time": True, "observed_at_is_caller_supplied_or_recorded_at": True}}
        encoded = canonical_json(payload)
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO testamur_source_snapshots VALUES(?,?,?,?,?,?,?,?,?)", (snapshot_id, source_id, payload["revision_id"], snapshot_locator, observation_time, recorded_at, resolved_status, digest, encoded))
            row = conn.execute("SELECT record_json FROM testamur_source_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
            if row is None or str(row["record_json"]) != encoded:
                raise ValueError("snapshot identity is already bound to different content")
        return payload

    def get_revision(self, revision_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT record_json FROM testamur_source_revisions WHERE revision_id=?", (str(revision_id),)).fetchone()
        return None if row is None else json.loads(str(row["record_json"]))

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT record_json FROM testamur_source_snapshots WHERE snapshot_id=?", (str(snapshot_id),)).fetchone()
        return None if row is None else json.loads(str(row["record_json"]))

    def snapshots_for_revision(self, revision_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        if self.get_revision(revision_id) is None:
            raise KeyError(revision_id)
        with self.connect() as conn:
            rows = conn.execute("SELECT record_json FROM testamur_source_snapshots WHERE revision_id=? ORDER BY recorded_at DESC,snapshot_id DESC LIMIT ?", (revision_id, max(1, min(int(limit), 500)))).fetchall()
        return [json.loads(str(row["record_json"])) for row in rows]

    def history(self, source_id: str, *, limit: int = 50, before: tuple[str, str] | None = None) -> list[dict[str, Any]]:
        if self.get_source(source_id) is None:
            raise KeyError(source_id)
        bounded = max(1, min(int(limit), 500))
        with self.connect() as conn:
            if before is None:
                rows = conn.execute("SELECT record_json FROM testamur_source_snapshots WHERE source_id=? ORDER BY recorded_at DESC,snapshot_id DESC LIMIT ?", (source_id, bounded)).fetchall()
            else:
                t, sid = before
                rows = conn.execute("SELECT record_json FROM testamur_source_snapshots WHERE source_id=? AND (recorded_at < ? OR (recorded_at=? AND snapshot_id < ?)) ORDER BY recorded_at DESC,snapshot_id DESC LIMIT ?", (source_id, str(t), str(t), str(sid), bounded)).fetchall()
        return [json.loads(str(row["record_json"])) for row in rows]

    def latest_recorded_snapshot(self, source_id: str) -> dict[str, Any] | None:
        values = self.history(source_id, limit=1)
        return values[0] if values else None

    def stats(self) -> dict[str, int]:
        with self.connect() as conn:
            sources = int(conn.execute("SELECT COUNT(*) FROM testamur_sources").fetchone()[0])
            revisions = int(conn.execute("SELECT COUNT(*) FROM testamur_source_revisions").fetchone()[0])
            snapshots = int(conn.execute("SELECT COUNT(*) FROM testamur_source_snapshots").fetchone()[0])
            metadata_only = int(conn.execute("SELECT COUNT(*) FROM testamur_source_snapshots WHERE revision_id IS NULL").fetchone()[0])
        return {"sources": sources, "revisions": revisions, "snapshots": snapshots, "snapshots_without_revision": metadata_only}

    def compare_snapshots(self, left_id: str, right_id: str) -> dict[str, Any]:
        left, right = self.get_snapshot(left_id), self.get_snapshot(right_id)
        if left is None:
            raise KeyError(left_id)
        if right is None:
            raise KeyError(right_id)
        if left["source_id"] != right["source_id"]:
            raise ValueError("mechanical source comparison requires snapshots from the same source")
        lh, rh = left.get("content_hash"), right.get("content_hash")
        assessable = lh is not None and rh is not None
        return {"source_id": left["source_id"], "left_snapshot_id": left_id, "right_snapshot_id": right_id, "left_revision_id": left.get("revision_id"), "right_revision_id": right.get("revision_id"), "left_content_hash": lh, "right_content_hash": rh, "content_identity_assessable": assessable, "content_changed": None if not assessable else lh != rh, "same_revision": None if left.get("revision_id") is None or right.get("revision_id") is None else left.get("revision_id") == right.get("revision_id"), "semantics": {"mechanical_identity_comparison_only": True, "semantic_change_inferred": False, "raw_diff_available_from_this_store": False}}
