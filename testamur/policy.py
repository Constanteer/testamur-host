from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

POLICY_SCHEMA_VERSION = "testamur.policy.v1"
_POLICY_PREFIX = "tst:policy:"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_REGISTER_RETRIES = 8
_POLICY_TABLE = "testamur_policies"
_LEGACY_POLICY_TABLE = "testamur_policies_legacy_content_unique"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _required(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must not be empty")
    return result


def _slug(value: Any, field: str) -> str:
    result = _required(value, field)
    if not _SLUG.fullmatch(result):
        raise ValueError(f"{field} must be a lowercase stable slug")
    return result


def _dedupe(values: Iterable[str]) -> list[str]:
    """Canonicalize set-like policy lists independent of authoring order."""
    return sorted({str(value) for value in values})


def _normalize_scope_requirements(value: Any) -> dict[str, dict[str, Any]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("assurance_scope_requirements must be an object")
    result: dict[str, dict[str, Any]] = {}
    for raw_kind, raw_scope in value.items():
        kind = _slug(raw_kind, "assurance kind")
        if not isinstance(raw_scope, Mapping) or not raw_scope:
            raise ValueError("each assurance scope requirement must be a non-empty object")
        scope = dict(raw_scope)
        canonical_json(scope)
        result[kind] = scope
    return dict(sorted(result.items()))


def normalize_policy_definition(definition: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one purpose/scope policy without manufacturing truth semantics."""
    if not isinstance(definition, Mapping):
        raise ValueError("policy definition must be an object")

    required_raw = definition.get("required_assurances", [])
    dependency_raw = definition.get(
        "dependency_relation_types", definition.get("dependency_edge_kinds", [])
    )
    blocking_raw = definition.get(
        "blocking_relation_types", definition.get("blocking_edge_kinds", [])
    )
    if not isinstance(required_raw, list):
        raise ValueError("required_assurances must be a list")
    if not isinstance(dependency_raw, list) or not isinstance(blocking_raw, list):
        raise ValueError("relation type lists must be lists")

    required = _dedupe(_slug(item, "required assurance kind") for item in required_raw)
    scope_requirements = _normalize_scope_requirements(
        definition.get("assurance_scope_requirements")
    )
    unknown = sorted(set(scope_requirements) - set(required))
    if unknown:
        raise ValueError(
            "scope requirements refer to non-required assurance kinds: "
            + ", ".join(unknown)
        )

    recovery_raw = definition.get("recovery_hints", {})
    if not isinstance(recovery_raw, Mapping):
        raise ValueError("recovery_hints must be an object")
    recovery: dict[str, str] = {}
    for raw_kind, raw_text in recovery_raw.items():
        kind = _slug(raw_kind, "recovery assurance kind")
        recovery[kind] = _required(raw_text, "recovery hint")

    result = {
        "required_assurances": required,
        "assurance_scope_requirements": scope_requirements,
        "dependency_relation_types": _dedupe(
            _slug(item, "dependency relation type") for item in dependency_raw
        ),
        "blocking_relation_types": _dedupe(
            _slug(item, "blocking relation type") for item in blocking_raw
        ),
        "require_dependencies_admissible": bool(
            definition.get("require_dependencies_admissible", True)
        ),
        "allow_unruled_dependencies": bool(
            definition.get("allow_unruled_dependencies", False)
        ),
        "recovery_hints": dict(sorted(recovery.items())),
        "description": str(definition.get("description") or "").strip(),
    }
    canonical_json(result)
    return result


class PolicyStore:
    """Immutable, versioned, purpose/scope-aware Testamur policy registry.

    Rows are append-only. Registering content identical to the *current* policy is
    idempotent. Registering content different from the current policy creates the
    next version, even when that content appeared earlier in history. This permits
    an explicit A→B→A reversion without mutating or resurrecting the old A row.
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

    @staticmethod
    def _create_table(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS testamur_policies(
              policy_id TEXT PRIMARY KEY,
              policy_key TEXT NOT NULL,
              version INTEGER NOT NULL,
              scope_ref TEXT NOT NULL,
              purpose TEXT NOT NULL,
              target_kind TEXT NOT NULL,
              definition_json TEXT NOT NULL,
              content_hash TEXT NOT NULL,
              supersedes_policy_id TEXT,
              created_by TEXT,
              created_at TEXT NOT NULL,
              UNIQUE(policy_key, version)
            )
            """
        )

    @staticmethod
    def _install_aux_schema(conn: sqlite3.Connection) -> None:
        conn.execute(
            """CREATE INDEX IF NOT EXISTS testamur_policy_match_idx
               ON testamur_policies(scope_ref,purpose,target_kind,version DESC)"""
        )
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS testamur_policies_no_update
               BEFORE UPDATE ON testamur_policies BEGIN
                 SELECT RAISE(ABORT, 'Testamur policies are immutable');
               END"""
        )
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS testamur_policies_no_delete
               BEFORE DELETE ON testamur_policies BEGIN
                 SELECT RAISE(ABORT, 'Testamur policies are immutable');
               END"""
        )

    @staticmethod
    def _has_legacy_content_hash_unique(conn: sqlite3.Connection) -> bool:
        for index in conn.execute("PRAGMA index_list(testamur_policies)").fetchall():
            if not bool(index["unique"]):
                continue
            index_name = str(index["name"])
            quoted = index_name.replace("'", "''")
            columns = [
                str(row["name"])
                for row in conn.execute(f"PRAGMA index_info('{quoted}')").fetchall()
            ]
            if columns == ["policy_key", "content_hash"]:
                return True
        return False

    def _migrate_legacy_content_hash_unique(self, conn: sqlite3.Connection) -> None:
        """Remove the old history-wide content uniqueness constraint in place."""
        conn.execute("DROP TRIGGER IF EXISTS testamur_policies_no_update")
        conn.execute("DROP TRIGGER IF EXISTS testamur_policies_no_delete")
        conn.execute("DROP INDEX IF EXISTS testamur_policy_match_idx")
        conn.execute(
            f"ALTER TABLE {_POLICY_TABLE} RENAME TO {_LEGACY_POLICY_TABLE}"
        )
        self._create_table(conn)
        conn.execute(
            f"""
            INSERT INTO {_POLICY_TABLE}(
              policy_id,policy_key,version,scope_ref,purpose,target_kind,
              definition_json,content_hash,supersedes_policy_id,created_by,created_at
            )
            SELECT
              policy_id,policy_key,version,scope_ref,purpose,target_kind,
              definition_json,content_hash,supersedes_policy_id,created_by,created_at
            FROM {_LEGACY_POLICY_TABLE}
            ORDER BY rowid
            """
        )
        conn.execute(f"DROP TABLE {_LEGACY_POLICY_TABLE}")
        self._install_aux_schema(conn)

    def _init_schema(self) -> None:
        with self.connect() as conn:
            # DDL must live under an explicit write transaction; otherwise SQLite
            # may expose intermediate rename/rebuild state to another initializer.
            conn.execute("BEGIN IMMEDIATE")
            self._create_table(conn)
            if self._has_legacy_content_hash_unique(conn):
                self._migrate_legacy_content_hash_unique(conn)
            else:
                self._install_aux_schema(conn)

    @staticmethod
    def logical_key(scope_ref: str, purpose: str, target_kind: str) -> str:
        payload = {
            "scope_ref": _required(scope_ref, "scope_ref"),
            "purpose": _slug(purpose, "purpose"),
            "target_kind": _slug(target_kind, "target_kind"),
        }
        return "policy-key:" + canonical_hash(payload)

    @staticmethod
    def _serialize(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema_version": POLICY_SCHEMA_VERSION,
            "policy_id": str(row["policy_id"]),
            "policy_key": str(row["policy_key"]),
            "version": int(row["version"]),
            "scope_ref": str(row["scope_ref"]),
            "purpose": str(row["purpose"]),
            "target_kind": str(row["target_kind"]),
            "definition": json.loads(str(row["definition_json"])),
            "content_hash": str(row["content_hash"]),
            "supersedes_policy_id": row["supersedes_policy_id"],
            "created_by": row["created_by"],
            "created_at": str(row["created_at"]),
            "semantics": {
                "purpose_scoped": True,
                "scope_scoped": True,
                "immutable": True,
                "universal_truth_claim": False,
                "universal_trust_score": None,
            },
        }

    @staticmethod
    def _definition_matches(
        row: sqlite3.Row, normalized: Mapping[str, Any]
    ) -> bool:
        """Compare semantics using the current normalizer, including old rows."""
        try:
            stored = json.loads(str(row["definition_json"]))
            if not isinstance(stored, Mapping):
                return False
            return normalize_policy_definition(stored) == dict(normalized)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False

    def register(
        self,
        *,
        scope_ref: str,
        purpose: str,
        target_kind: str,
        definition: Mapping[str, Any],
        created_by: str | None = None,
    ) -> dict[str, Any]:
        scope = _required(scope_ref, "scope_ref")
        normalized_purpose = _slug(purpose, "purpose")
        normalized_kind = _slug(target_kind, "target_kind")
        normalized = normalize_policy_definition(definition)
        key = self.logical_key(scope, normalized_purpose, normalized_kind)
        content_hash = canonical_hash(normalized)
        encoded = canonical_json(normalized)

        last_error: sqlite3.IntegrityError | None = None
        for _attempt in range(_REGISTER_RETRIES):
            try:
                with self.connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    latest = conn.execute(
                        """SELECT * FROM testamur_policies
                           WHERE policy_key=? ORDER BY version DESC LIMIT 1""",
                        (key,),
                    ).fetchone()
                    if latest is not None and self._definition_matches(
                        latest, normalized
                    ):
                        return self._serialize(latest)

                    version = 1 if latest is None else int(latest["version"]) + 1
                    supersedes = None if latest is None else str(latest["policy_id"])
                    identity_core = {
                        "policy_key": key,
                        "version": version,
                        "content_hash": content_hash,
                    }
                    policy_id = _POLICY_PREFIX + canonical_hash(identity_core)
                    conn.execute(
                        """INSERT INTO testamur_policies(
                             policy_id,policy_key,version,scope_ref,purpose,target_kind,
                             definition_json,content_hash,supersedes_policy_id,created_by,created_at
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            policy_id,
                            key,
                            version,
                            scope,
                            normalized_purpose,
                            normalized_kind,
                            encoded,
                            content_hash,
                            supersedes,
                            None if created_by is None else str(created_by),
                            _utc_now(),
                        ),
                    )
                    row = conn.execute(
                        "SELECT * FROM testamur_policies WHERE policy_id=?",
                        (policy_id,),
                    ).fetchone()
                    assert row is not None
                    return self._serialize(row)
            except sqlite3.IntegrityError as exc:
                last_error = exc
                with self.connect() as conn:
                    latest = conn.execute(
                        """SELECT * FROM testamur_policies
                           WHERE policy_key=? ORDER BY version DESC LIMIT 1""",
                        (key,),
                    ).fetchone()
                if latest is not None and self._definition_matches(latest, normalized):
                    return self._serialize(latest)

        assert last_error is not None
        raise last_error

    def get(self, policy_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM testamur_policies WHERE policy_id=?", (str(policy_id),)
            ).fetchone()
        return None if row is None else self._serialize(row)

    def match(
        self, *, scope_ref: str, purpose: str, target_kind: str
    ) -> dict[str, Any] | None:
        key = self.logical_key(scope_ref, purpose, target_kind)
        with self.connect() as conn:
            row = conn.execute(
                """SELECT * FROM testamur_policies
                   WHERE policy_key=? ORDER BY version DESC LIMIT 1""",
                (key,),
            ).fetchone()
        return None if row is None else self._serialize(row)

    def history(
        self, *, scope_ref: str, purpose: str, target_kind: str
    ) -> list[dict[str, Any]]:
        key = self.logical_key(scope_ref, purpose, target_kind)
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM testamur_policies
                   WHERE policy_key=? ORDER BY version DESC""",
                (key,),
            ).fetchall()
        return [self._serialize(row) for row in rows]