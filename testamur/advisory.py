from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from .component_identity import canonical_extension_id, canonical_json


class AdverseEventClass(StrEnum):
    VULNERABILITY_ADVISORY = "VULNERABILITY_ADVISORY"
    KNOWN_EXPLOITED_VULNERABILITY = "KNOWN_EXPLOITED_VULNERABILITY"
    RETRACTION = "RETRACTION"
    DATA_CORRECTION = "DATA_CORRECTION"
    CALIBRATION_INVALIDATION = "CALIBRATION_INVALIDATION"
    LICENSE_OR_POLICY_CHANGE = "LICENSE_OR_POLICY_CHANGE"
    COMPROMISED_RELEASE = "COMPROMISED_RELEASE"
    WITHDRAWAL = "WITHDRAWAL"
    ERRATUM = "ERRATUM"
    FAILED_REPRODUCTION = "FAILED_REPRODUCTION"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _required(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    return text


def _json_mapping(value: Mapping[str, Any] | None, *, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    result = dict(value)
    canonical_json(result)
    return result


def _refs(values: Iterable[str] | None, *, field: str) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)):
        raise ValueError(
            f"{field} must be an iterable of reference strings, not a scalar string"
        )
    result: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError(f"{field} entries must be strings")
        result.add(_required(value, field=field))
    return sorted(result)


class TestamurAdvisoryStore:
    """Append-only provider-neutral adverse-event/advisory store.

    External CVE/KEV/package metadata is preserved as source material. This store
    never turns external labels or version matches into a Testamur affectedness
    verdict; affectedness is recorded separately against an immutable event revision.
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
        CREATE TABLE IF NOT EXISTS testamur_advisories(
          event_id TEXT PRIMARY KEY,
          provider TEXT NOT NULL,
          external_id TEXT NOT NULL,
          event_class TEXT NOT NULL,
          event_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          UNIQUE(provider, external_id)
        );

        CREATE TABLE IF NOT EXISTS testamur_advisory_revisions(
          event_revision_id TEXT PRIMARY KEY,
          event_id TEXT NOT NULL REFERENCES testamur_advisories(event_id),
          revision_json TEXT NOT NULL,
          recorded_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS testamur_advisory_revisions_event_idx
          ON testamur_advisory_revisions(
            event_id, recorded_at DESC, event_revision_id DESC
          );

        CREATE TRIGGER IF NOT EXISTS testamur_advisories_no_update
        BEFORE UPDATE ON testamur_advisories BEGIN
          SELECT RAISE(ABORT, 'Testamur advisory identities are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS testamur_advisories_no_delete
        BEFORE DELETE ON testamur_advisories BEGIN
          SELECT RAISE(ABORT, 'Testamur advisory identities are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS testamur_advisory_revisions_no_update
        BEFORE UPDATE ON testamur_advisory_revisions BEGIN
          SELECT RAISE(ABORT, 'Testamur advisory revisions are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS testamur_advisory_revisions_no_delete
        BEFORE DELETE ON testamur_advisory_revisions BEGIN
          SELECT RAISE(ABORT, 'Testamur advisory revisions are immutable');
        END;
        """
        with self.connect() as conn:
            conn.executescript(schema)

    def record_adverse_event(
        self,
        *,
        provider: str,
        external_id: str,
        event_class: str | AdverseEventClass,
        upstream_refs: Iterable[str] | None = None,
        upstream_identity: Mapping[str, Any] | None = None,
        known_affected: Mapping[str, Any] | None = None,
        known_unaffected: Mapping[str, Any] | None = None,
        condition: Mapping[str, Any] | None = None,
        affected_component_or_region: Mapping[str, Any] | None = None,
        source_refs: Iterable[str] | None = None,
        issued_at: str | None = None,
        severity: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        provider_value = _required(provider, field="provider")
        external_value = _required(external_id, field="external_id")
        try:
            class_value = AdverseEventClass(str(event_class)).value
        except ValueError as exc:
            allowed = ", ".join(item.value for item in AdverseEventClass)
            raise ValueError(
                f"unsupported adverse event class; expected one of: {allowed}"
            ) from exc

        event_identity = {
            "provider": provider_value,
            "external_id": external_value,
        }
        event_id = canonical_extension_id("advisory", event_identity)
        created_at = _utc_now()
        event = {
            "event_id": event_id,
            **event_identity,
            "event_class": class_value,
            "created_at": created_at,
            "semantics": {
                "external_label_is_source_fact": True,
                "event_identity_implies_local_affectedness": False,
                "identity_fields_are_type_stable_strings": True,
                "canonical_extension_identity": True,
            },
        }

        revision_core = {
            "event_id": event_id,
            "event_class": class_value,
            "upstream_refs": _refs(upstream_refs, field="upstream_refs"),
            "upstream_identity": _json_mapping(
                upstream_identity, field="upstream_identity"
            ),
            "known_affected": _json_mapping(
                known_affected, field="known_affected"
            ),
            "known_unaffected": _json_mapping(
                known_unaffected, field="known_unaffected"
            ),
            "condition": _json_mapping(condition, field="condition"),
            "affected_component_or_region": _json_mapping(
                affected_component_or_region, field="affected_component_or_region"
            ),
            "source_refs": _refs(source_refs, field="source_refs"),
            "issued_at": (
                None
                if issued_at is None
                else _required(issued_at, field="issued_at")
            ),
            "severity": _json_mapping(severity, field="severity"),
            "metadata": _json_mapping(metadata, field="metadata"),
        }
        if not revision_core["upstream_refs"] and not revision_core["upstream_identity"]:
            raise ValueError("adverse event requires upstream_refs or upstream_identity")
        if not revision_core["source_refs"]:
            raise ValueError("adverse event revision requires at least one source_ref")

        event_identity = {
            "event_id": event_id,
            "revision": revision_core,
        }
        event_revision_id = canonical_extension_id("advisory", event_identity)
        recorded_at = _utc_now()
        revision = {
            "event_revision_id": event_revision_id,
            **revision_core,
            "recorded_at": recorded_at,
            "semantics": {
                "immutable_event_revision": True,
                "version_or_name_match_alone_proves_affectedness": False,
                "affectedness_is_separate_object": True,
                "identity_fields_are_type_stable_strings": True,
                "canonical_extension_identity": True,
            },
        }

        with self.connect() as conn:
            existing_event = conn.execute(
                "SELECT event_json,event_class FROM testamur_advisories WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if existing_event is None:
                conn.execute(
                    """INSERT INTO testamur_advisories(
                         event_id,provider,external_id,event_class,event_json,created_at
                       ) VALUES(?,?,?,?,?,?)""",
                    (
                        event_id,
                        provider_value,
                        external_value,
                        class_value,
                        canonical_json(event),
                        created_at,
                    ),
                )
            elif str(existing_event["event_class"]) != class_value:
                raise ValueError(
                    "provider/external_id already exists with another event_class"
                )

            existing_revision = conn.execute(
                "SELECT revision_json FROM testamur_advisory_revisions WHERE event_revision_id=?",
                (event_revision_id,),
            ).fetchone()
            if existing_revision is not None:
                return json.loads(str(existing_revision["revision_json"]))
            conn.execute(
                """INSERT INTO testamur_advisory_revisions(
                     event_revision_id,event_id,revision_json,recorded_at
                   ) VALUES(?,?,?,?)""",
                (
                    event_revision_id,
                    event_id,
                    canonical_json(revision),
                    recorded_at,
                ),
            )
        return revision

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT event_json FROM testamur_advisories WHERE event_id=?",
                (str(event_id),),
            ).fetchone()
        return None if row is None else json.loads(str(row["event_json"]))

    def get_revision(self, event_revision_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT revision_json FROM testamur_advisory_revisions WHERE event_revision_id=?",
                (str(event_revision_id),),
            ).fetchone()
        return None if row is None else json.loads(str(row["revision_json"]))

    def revisions(self, event_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 1000))
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT revision_json FROM testamur_advisory_revisions
                   WHERE event_id=? ORDER BY rowid DESC LIMIT ?""",
                (str(event_id), bounded),
            ).fetchall()
        return [json.loads(str(row["revision_json"])) for row in rows]

    def latest_revision(self, event_id: str) -> dict[str, Any] | None:
        values = self.revisions(event_id, limit=1)
        return None if not values else values[0]
