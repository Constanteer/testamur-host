from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .runtime_protocol import canonical_json


SOURCE_POLICY_PREFIX = "tst:source-policy:"
VISIBILITIES = frozenset({"private", "public"})
RAW_BYTE_POLICIES = frozenset({"retain", "purge_on_request"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_nonempty(value: str, *, field: str) -> str:
    raw = str(value).strip()
    if not raw:
        raise ValueError(f"{field} must not be empty")
    return raw


class TestamurSourceAccessStore:
    """Append-only access-policy history for durable Sources.

    Source content identity and access control are deliberately separate.
    Changing a Source from private to public must not mint a new Source or
    Revision identity.

    The absence of a policy is fail-closed: no anonymous/public read is
    authorized. Hosted deployments must authenticate ``viewer_subject`` before
    using owner access. This store does not itself implement authentication.

    Policy authority follows append order, not caller-supplied ``recorded_at``.
    ``recorded_at`` remains evidence metadata and may be backfilled, but a later
    appended revoke/private policy must always supersede an earlier row.
    """

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
        schema = """
        CREATE TABLE IF NOT EXISTS testamur_source_access_policy_revisions(
          policy_revision_id TEXT PRIMARY KEY,
          source_id TEXT NOT NULL,
          owner_subject TEXT NOT NULL,
          visibility TEXT NOT NULL,
          raw_bytes_policy TEXT NOT NULL,
          allow_external_processing INTEGER NOT NULL,
          recorded_at TEXT NOT NULL,
          record_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS testamur_source_access_policy_source_idx
          ON testamur_source_access_policy_revisions(
            source_id, recorded_at DESC, policy_revision_id DESC
          );

        CREATE TRIGGER IF NOT EXISTS testamur_source_access_policy_no_update
        BEFORE UPDATE ON testamur_source_access_policy_revisions BEGIN
          SELECT RAISE(ABORT, 'Testamur source access policies are append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS testamur_source_access_policy_no_delete
        BEFORE DELETE ON testamur_source_access_policy_revisions BEGIN
          SELECT RAISE(ABORT, 'Testamur source access policies are append-only');
        END;
        """
        with self.connect() as conn:
            conn.executescript(schema)

    def record_policy(
        self,
        source_id: str,
        *,
        owner_subject: str,
        visibility: str = "private",
        raw_bytes_policy: str = "retain",
        allow_external_processing: bool = False,
        recorded_at: str | None = None,
    ) -> dict[str, Any]:
        resolved_source_id = _require_nonempty(source_id, field="source_id")
        resolved_owner = _require_nonempty(owner_subject, field="owner_subject")
        resolved_visibility = str(visibility).strip().lower()
        if resolved_visibility not in VISIBILITIES:
            raise ValueError("visibility must be private or public")
        resolved_raw_policy = str(raw_bytes_policy).strip().lower()
        if resolved_raw_policy not in RAW_BYTE_POLICIES:
            raise ValueError("raw_bytes_policy must be retain or purge_on_request")
        if not isinstance(allow_external_processing, bool):
            raise ValueError("allow_external_processing must be a boolean")
        timestamp = str(recorded_at or _utc_now())
        policy_revision_id = SOURCE_POLICY_PREFIX + uuid.uuid4().hex
        payload = {
            "policy_revision_id": policy_revision_id,
            "source_id": resolved_source_id,
            "owner_subject": resolved_owner,
            "visibility": resolved_visibility,
            "raw_bytes_policy": resolved_raw_policy,
            "allow_external_processing": bool(allow_external_processing),
            "recorded_at": timestamp,
            "semantics": {
                "access_policy_separate_from_source_identity": True,
                "absence_of_policy_is_private": True,
                "policy_precedence_is_append_order": True,
                "publication_rights_implied": False,
                "authentication_implemented_here": False,
            },
        }
        payload_json = canonical_json(payload)
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO testamur_source_access_policy_revisions(
                     policy_revision_id,source_id,owner_subject,visibility,
                     raw_bytes_policy,allow_external_processing,recorded_at,record_json
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    policy_revision_id,
                    resolved_source_id,
                    resolved_owner,
                    resolved_visibility,
                    resolved_raw_policy,
                    1 if allow_external_processing else 0,
                    timestamp,
                    payload_json,
                ),
            )
        return payload

    def latest_policy(self, source_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT record_json
                   FROM testamur_source_access_policy_revisions
                   WHERE source_id=?
                   ORDER BY rowid DESC
                   LIMIT 1""",
                (str(source_id),),
            ).fetchone()
        return None if row is None else json.loads(str(row["record_json"]))

    def can_read(self, source_id: str, *, viewer_subject: str | None = None) -> bool:
        policy = self.latest_policy(source_id)
        if policy is None:
            return False
        if policy.get("visibility") == "public":
            return True
        viewer = None if viewer_subject is None else str(viewer_subject).strip()
        return bool(viewer) and viewer == policy.get("owner_subject")

    def can_process_externally(
        self,
        source_id: str,
        *,
        viewer_subject: str | None = None,
    ) -> bool:
        policy = self.latest_policy(source_id)
        if policy is None:
            return False
        return self.can_read(source_id, viewer_subject=viewer_subject) and bool(
            policy.get("allow_external_processing")
        )