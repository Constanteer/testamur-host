from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .runtime_protocol import canonical_json

from .blob_store import TestamurBlobStore, normalize_sha256_digest
from .source_access import TestamurSourceAccessStore
from .source_store import TestamurSourceStore

RETENTION_ACTIONS = frozenset({"retain", "release"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _required(value: str, *, field: str) -> str:
    raw = str(value).strip()
    if not raw:
        raise ValueError(f"{field} must not be empty")
    return raw


class TestamurBlobRetentionStore:
    """Reference-aware raw-byte retention and purge coordination.

    Durable byte references are derived from immutable SourceRevision rows rather
    than copied into a second ownership table. Per-Source retention state is an
    append-only sequence of ``retain`` / ``release`` control events for each
    digest. No event means retain (fail closed).

    A release only becomes effective when the Source's *current* access policy
    still says ``purge_on_request``. Missing policy or a current ``retain``
    policy is itself an active retention blocker. This closes the transaction
    window between changing policy and appending the corresponding retain event.

    In-flight claims close the window between CAS materialization and
    Snapshot/Revision persistence. Purge holds a SQLite ``BEGIN IMMEDIATE`` lock
    while checking durable refs, checking claims and unlinking the CAS object.
    A retention-aware writer must insert its claim first, so it cannot race into
    the final check/delete critical section.
    """

    def __init__(self, database_path: str | Path, blob_root: str | Path) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.sources = TestamurSourceStore(self.path)
        self.access = TestamurSourceAccessStore(self.path)
        self.blobs = TestamurBlobStore(blob_root)
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

    def _locked_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")
        return conn

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS testamur_blob_ingest_claims(
                  claim_id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  content_hash TEXT NOT NULL,
                  purpose TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS testamur_blob_ingest_claim_digest_idx
                  ON testamur_blob_ingest_claims(content_hash, created_at, claim_id);

                CREATE TABLE IF NOT EXISTS testamur_blob_retention_events(
                  event_id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  content_hash TEXT NOT NULL,
                  action TEXT NOT NULL CHECK(action IN ('retain','release')),
                  owner_subject TEXT NOT NULL,
                  policy_revision_id TEXT,
                  recorded_at TEXT NOT NULL,
                  record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS testamur_blob_retention_event_ref_idx
                  ON testamur_blob_retention_events(source_id, content_hash);
                CREATE INDEX IF NOT EXISTS testamur_blob_retention_event_digest_idx
                  ON testamur_blob_retention_events(content_hash);

                CREATE TRIGGER IF NOT EXISTS testamur_blob_retention_events_no_update
                BEFORE UPDATE ON testamur_blob_retention_events BEGIN
                  SELECT RAISE(ABORT, 'Testamur blob retention events are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS testamur_blob_retention_events_no_delete
                BEFORE DELETE ON testamur_blob_retention_events BEGIN
                  SELECT RAISE(ABORT, 'Testamur blob retention events are append-only');
                END;
                """
            )
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(testamur_blob_retention_events)").fetchall()
            }
            if "policy_revision_id" not in columns:
                # Pre-clean-forward local-alpha rows fail closed: NULL never
                # authorizes a release in a later policy epoch.
                conn.execute(
                    "ALTER TABLE testamur_blob_retention_events ADD COLUMN policy_revision_id TEXT"
                )

    def claim(
        self,
        source_id: str,
        content_hash: str,
        *,
        purpose: str,
    ) -> dict[str, Any]:
        source = _required(source_id, field="source_id")
        digest = normalize_sha256_digest(content_hash)
        resolved_purpose = _required(purpose, field="purpose")
        claim_id = "blob-claim:" + uuid.uuid4().hex
        created_at = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO testamur_blob_ingest_claims(
                     claim_id,source_id,content_hash,purpose,created_at
                   ) VALUES(?,?,?,?,?)""",
                (claim_id, source, digest, resolved_purpose, created_at),
            )
        return {
            "claim_id": claim_id,
            "source_id": source,
            "content_hash": digest,
            "purpose": resolved_purpose,
            "created_at": created_at,
            "operational": True,
        }

    def release_claim(self, claim_id: str) -> bool:
        resolved = _required(claim_id, field="claim_id")
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM testamur_blob_ingest_claims WHERE claim_id=?",
                (resolved,),
            )
        return int(cursor.rowcount or 0) > 0

    @staticmethod
    def _claim_count_conn(conn: sqlite3.Connection, digest: str) -> int:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM testamur_blob_ingest_claims WHERE content_hash=?",
            (digest,),
        ).fetchone()
        return 0 if row is None else int(row["n"])

    def active_claim_count(self, content_hash: str) -> int:
        digest = normalize_sha256_digest(content_hash)
        with self.connect() as conn:
            return self._claim_count_conn(conn, digest)

    @staticmethod
    def _latest_event_conn(
        conn: sqlite3.Connection,
        source_id: str,
        digest: str,
    ) -> dict[str, Any] | None:
        row = conn.execute(
            """SELECT record_json FROM testamur_blob_retention_events
               WHERE source_id=? AND content_hash=?
               ORDER BY rowid DESC LIMIT 1""",
            (source_id, digest),
        ).fetchone()
        return None if row is None else json.loads(str(row["record_json"]))

    def latest_event(self, source_id: str, content_hash: str) -> dict[str, Any] | None:
        digest = normalize_sha256_digest(content_hash)
        with self.connect() as conn:
            return self._latest_event_conn(conn, str(source_id), digest)

    @staticmethod
    def _latest_policy_conn(conn: sqlite3.Connection, source_id: str) -> dict[str, Any] | None:
        row = conn.execute(
            """SELECT record_json
               FROM testamur_source_access_policy_revisions
               WHERE source_id=?
               ORDER BY rowid DESC LIMIT 1""",
            (source_id,),
        ).fetchone()
        return None if row is None else json.loads(str(row["record_json"]))

    @staticmethod
    def _authorize_owner(policy: dict[str, Any] | None, owner: str) -> None:
        if policy is None:
            raise PermissionError("source has no access policy; raw-byte retention fails closed")
        if policy.get("owner_subject") != owner:
            raise PermissionError("owner subject is not authorized for source retention")

    @staticmethod
    def _new_event_payload(
        source: str,
        digest: str,
        owner: str,
        action: str,
        policy_revision_id: str,
    ) -> dict[str, Any]:
        if action not in RETENTION_ACTIONS:
            raise ValueError("retention action must be retain or release")
        return {
            "event_id": "blob-retention:" + uuid.uuid4().hex,
            "source_id": source,
            "content_hash": digest,
            "action": action,
            "owner_subject": owner,
            "policy_revision_id": _required(policy_revision_id, field="policy_revision_id"),
            "recorded_at": _utc_now(),
            "semantics": {
                "raw_bytes_only": True,
                "source_evidence_deleted": False,
                "digest_identity_deleted": False,
                "append_order_authoritative": True,
                "publication_rights_implied": False,
            },
        }

    @staticmethod
    def _insert_event_conn(
        conn: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        conn.execute(
            """INSERT INTO testamur_blob_retention_events(
                 event_id,source_id,content_hash,action,owner_subject,policy_revision_id,recorded_at,record_json
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                payload["event_id"],
                payload["source_id"],
                payload["content_hash"],
                payload["action"],
                payload["owner_subject"],
                payload["policy_revision_id"],
                payload["recorded_at"],
                canonical_json(payload),
            ),
        )
        return payload

    def record_event(
        self,
        source_id: str,
        content_hash: str,
        *,
        owner_subject: str,
        action: str,
    ) -> dict[str, Any]:
        source = _required(source_id, field="source_id")
        digest = normalize_sha256_digest(content_hash)
        owner = _required(owner_subject, field="owner_subject")
        resolved_action = str(action).strip().lower()
        if resolved_action not in RETENTION_ACTIONS:
            raise ValueError("retention action must be retain or release")
        conn = self._locked_connection()
        try:
            policy = self._latest_policy_conn(conn, source)
            self._authorize_owner(policy, owner)
            if resolved_action == "release" and policy.get("raw_bytes_policy") != "purge_on_request":
                raise PermissionError("source raw-byte policy does not permit release")
            policy_revision_id = str(policy["policy_revision_id"])
            latest = self._latest_event_conn(conn, source, digest)
            if (
                latest is not None
                and latest.get("action") == resolved_action
                and latest.get("owner_subject") == owner
                and latest.get("policy_revision_id") == policy_revision_id
            ):
                conn.commit()
                return latest
            payload = self._new_event_payload(
                source, digest, owner, resolved_action, policy_revision_id
            )
            self._insert_event_conn(conn, payload)
            conn.commit()
            return payload
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def source_digests(self, source_id: str) -> list[str]:
        source = _required(source_id, field="source_id")
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT DISTINCT content_hash
                   FROM testamur_source_revisions
                   WHERE source_id=?
                   ORDER BY content_hash""",
                (source,),
            ).fetchall()
        return [normalize_sha256_digest(str(row["content_hash"])) for row in rows]

    @staticmethod
    def _active_reference_count_conn(conn: sqlite3.Connection, digest: str) -> int:
        row = conn.execute(
            """SELECT COUNT(DISTINCT r.source_id) AS n
               FROM testamur_source_revisions AS r
               LEFT JOIN testamur_blob_retention_events AS e
                 ON e.rowid = (
                   SELECT e2.rowid
                   FROM testamur_blob_retention_events AS e2
                   WHERE e2.source_id=r.source_id AND e2.content_hash=r.content_hash
                   ORDER BY e2.rowid DESC LIMIT 1
                 )
               LEFT JOIN testamur_source_access_policy_revisions AS p
                 ON p.rowid = (
                   SELECT p2.rowid
                   FROM testamur_source_access_policy_revisions AS p2
                   WHERE p2.source_id=r.source_id
                   ORDER BY p2.rowid DESC LIMIT 1
                 )
               WHERE r.content_hash=?
                 AND (
                   e.event_id IS NULL
                   OR e.action='retain'
                   OR p.policy_revision_id IS NULL
                   OR e.policy_revision_id IS NULL
                   OR e.policy_revision_id!=p.policy_revision_id
                   OR p.raw_bytes_policy!='purge_on_request'
                 )""",
            (digest,),
        ).fetchone()
        return 0 if row is None else int(row["n"])

    def retention_status(self, content_hash: str) -> dict[str, Any]:
        digest = normalize_sha256_digest(content_hash)
        with self.connect() as conn:
            references = self._active_reference_count_conn(conn, digest)
            claims = self._claim_count_conn(conn, digest)
        return {
            "content_hash": digest,
            "active_reference_count": references,
            "active_ingest_claim_count": claims,
            "purge_blocked": bool(references or claims),
            "blob_present": self.blobs.has(digest),
        }

    def retain_source(
        self,
        source_id: str,
        *,
        owner_subject: str,
    ) -> dict[str, Any]:
        source = _required(source_id, field="source_id")
        owner = _required(owner_subject, field="owner_subject")
        if self.sources.get_source(source) is None:
            raise KeyError(source)
        policy = self.access.latest_policy(source)
        self._authorize_owner(policy, owner)
        digests = self.source_digests(source)
        events = [
            self.record_event(source, digest, owner_subject=owner, action="retain")
            for digest in digests
        ]
        return {
            "source_id": source,
            "retained_digest_count": len(digests),
            "events": events,
            "semantics": {
                "bytes_restored_if_absent": False,
                "future_purge_blocked_by_source": True,
            },
        }

    def _purge_digest(
        self,
        source: str,
        digest: str,
        owner: str,
    ) -> dict[str, Any]:
        conn = self._locked_connection()
        try:
            policy = self._latest_policy_conn(conn, source)
            self._authorize_owner(policy, owner)
            if policy.get("raw_bytes_policy") != "purge_on_request":
                raise PermissionError("source raw-byte policy does not permit purge on request")

            policy_revision_id = str(policy["policy_revision_id"])
            latest = self._latest_event_conn(conn, source, digest)
            if (
                latest is None
                or latest.get("action") != "release"
                or latest.get("policy_revision_id") != policy_revision_id
            ):
                latest = self._new_event_payload(
                    source, digest, owner, "release", policy_revision_id
                )
                self._insert_event_conn(conn, latest)
            elif latest.get("owner_subject") != owner:
                raise PermissionError("latest retention event belongs to a different owner subject")

            references = self._active_reference_count_conn(conn, digest)
            claims = self._claim_count_conn(conn, digest)
            if references or claims:
                conn.commit()
                return {
                    "content_hash": digest,
                    "retention_event_id": latest["event_id"],
                    "outcome": "retained_shared_or_inflight",
                    "active_reference_count": references,
                    "active_ingest_claim_count": claims,
                }

            deleted = self.blobs.delete_verified(digest)
            conn.commit()
            return {
                "content_hash": digest,
                "retention_event_id": latest["event_id"],
                "outcome": "deleted" if deleted else "already_absent",
                "active_reference_count": 0,
                "active_ingest_claim_count": 0,
            }
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def purge_source(
        self,
        source_id: str,
        *,
        owner_subject: str,
    ) -> dict[str, Any]:
        source = _required(source_id, field="source_id")
        owner = _required(owner_subject, field="owner_subject")
        if self.sources.get_source(source) is None:
            raise KeyError(source)
        policy = self.access.latest_policy(source)
        self._authorize_owner(policy, owner)
        if policy.get("raw_bytes_policy") != "purge_on_request":
            raise PermissionError("source raw-byte policy does not permit purge on request")

        digests = self.source_digests(source)
        results = [self._purge_digest(source, digest, owner) for digest in digests]
        return {
            "source_id": source,
            "released_digest_count": len(digests),
            "digests": results,
            "semantics": {
                "source_identity_preserved": True,
                "revision_identity_preserved": True,
                "snapshot_history_preserved": True,
                "private_metadata_preserved": True,
                "shared_or_inflight_bytes_are_not_deleted": True,
                "release_can_be_followed_by_explicit_retain": True,
            },
        }
