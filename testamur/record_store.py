from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from .runtime_protocol import canonical_hash, canonical_json


RECORD_PREFIX = "tst:record:"
RECORD_REVISION_PREFIX = "tst:record-revision:"
RELATION_PREFIX = "tst:relation:"

RELATION_TYPES = frozenset(
    {
        "supports",
        "depends-on",
        "contradicts",
        "supersedes",
        "cites",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _required(value: Any, *, field: str) -> str:
    raw = str(value).strip()
    if not raw:
        raise ValueError(f"{field} must not be empty")
    return raw


def _normalize_basis(basis: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in basis or ():
        if not isinstance(raw, Mapping):
            raise ValueError("basis entries must be mappings")
        item = dict(raw)
        item["kind"] = _required(item.get("kind"), field="basis.kind")
        item["ref"] = _required(item.get("ref"), field="basis.ref")
        canonical_json(item)
        result.append(item)
    return result


class TestamurRecordStore:
    """Append-only persistent Record / RecordRevision / Relation store."""

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
        CREATE TABLE IF NOT EXISTS testamur_records(
          record_id TEXT PRIMARY KEY,
          record_kind TEXT NOT NULL,
          record_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS testamur_record_revisions(
          revision_id TEXT PRIMARY KEY,
          record_id TEXT NOT NULL REFERENCES testamur_records(record_id),
          ordinal INTEGER NOT NULL,
          parent_revision_id TEXT REFERENCES testamur_record_revisions(revision_id),
          statement TEXT NOT NULL,
          record_json TEXT NOT NULL,
          recorded_at TEXT NOT NULL,
          UNIQUE(record_id, ordinal)
        );
        CREATE INDEX IF NOT EXISTS testamur_record_revisions_record_idx ON testamur_record_revisions(record_id, ordinal DESC);
        CREATE TABLE IF NOT EXISTS testamur_relations(
          relation_id TEXT PRIMARY KEY,
          relation_type TEXT NOT NULL,
          from_ref TEXT NOT NULL,
          to_ref TEXT NOT NULL,
          record_json TEXT NOT NULL,
          recorded_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS testamur_relations_from_idx ON testamur_relations(from_ref, recorded_at DESC, relation_id DESC);
        CREATE INDEX IF NOT EXISTS testamur_relations_to_idx ON testamur_relations(to_ref, recorded_at DESC, relation_id DESC);
        CREATE INDEX IF NOT EXISTS testamur_relations_type_idx ON testamur_relations(relation_type, recorded_at DESC);
        CREATE TRIGGER IF NOT EXISTS testamur_records_no_update BEFORE UPDATE ON testamur_records BEGIN SELECT RAISE(ABORT, 'Testamur records are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS testamur_records_no_delete BEFORE DELETE ON testamur_records BEGIN SELECT RAISE(ABORT, 'Testamur records are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS testamur_record_revisions_no_update BEFORE UPDATE ON testamur_record_revisions BEGIN SELECT RAISE(ABORT, 'Testamur record revisions are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS testamur_record_revisions_no_delete BEFORE DELETE ON testamur_record_revisions BEGIN SELECT RAISE(ABORT, 'Testamur record revisions are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS testamur_relations_no_update BEFORE UPDATE ON testamur_relations BEGIN SELECT RAISE(ABORT, 'Testamur relations are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS testamur_relations_no_delete BEFORE DELETE ON testamur_relations BEGIN SELECT RAISE(ABORT, 'Testamur relations are immutable'); END;
        """
        with self.connect() as conn:
            conn.executescript(schema)

    @staticmethod
    def _record_id(value: str | None) -> str:
        if value is None:
            return RECORD_PREFIX + uuid.uuid4().hex
        resolved = _required(value, field="record_id")
        if not resolved.startswith(RECORD_PREFIX) or resolved == RECORD_PREFIX:
            raise ValueError(f"invalid Testamur record id: {resolved}")
        return resolved

    @staticmethod
    def _revision_payload(*, record_id: str, ordinal: int, parent_revision_id: str | None, statement: str, title: str | None, basis: list[dict[str, Any]], created_by: str | None, recorded_at: str) -> dict[str, Any]:
        core = {"record_id": record_id, "ordinal": int(ordinal), "parent_revision_id": parent_revision_id, "statement": statement, "title": title, "basis": basis, "created_by": created_by, "recorded_at": recorded_at}
        revision_id = RECORD_REVISION_PREFIX + canonical_hash(core)
        return {"revision_id": revision_id, **core, "semantics": {"immutable_revision": True, "statement_is_declared_normalized_record_content": True, "statement_is_source_bytes": False, "basis_entries_have_equal_epistemic_force": False, "truth_implied_by_recording": False}}

    def create_record(self, *, record_kind: str, statement: str, basis: Iterable[Mapping[str, Any]] | None = None, title: str | None = None, created_by: str | None = None, record_id: str | None = None) -> dict[str, Any]:
        kind = _required(record_kind, field="record_kind")
        normalized_statement = _required(statement, field="statement")
        normalized_basis = _normalize_basis(basis)
        resolved_id = self._record_id(record_id)
        created_at = _utc_now()
        record = {"record_id": resolved_id, "record_kind": kind, "created_at": created_at, "identity_semantics": {"persistent_identity": True, "statement_changes_create_new_revision": True, "privacy_changes_identity_semantics": False}}
        revision = self._revision_payload(record_id=resolved_id, ordinal=1, parent_revision_id=None, statement=normalized_statement, title=None if title is None else str(title), basis=normalized_basis, created_by=None if created_by is None else str(created_by), recorded_at=created_at)
        record_json = canonical_json(record); revision_json = canonical_json(revision)
        with self.connect() as conn:
            if conn.execute("SELECT record_json FROM testamur_records WHERE record_id=?", (resolved_id,)).fetchone() is not None: raise ValueError("record id already exists")
            conn.execute("INSERT INTO testamur_records(record_id,record_kind,record_json,created_at) VALUES(?,?,?,?)", (resolved_id, kind, record_json, created_at))
            conn.execute("INSERT INTO testamur_record_revisions(revision_id,record_id,ordinal,parent_revision_id,statement,record_json,recorded_at) VALUES(?,?,?,?,?,?,?)", (revision["revision_id"], resolved_id, 1, None, normalized_statement, revision_json, created_at))
        return {"record": record, "revision": revision}

    def get_record(self, record_id: str) -> dict[str, Any] | None:
        with self.connect() as conn: row = conn.execute("SELECT record_json FROM testamur_records WHERE record_id=?", (str(record_id),)).fetchone()
        return None if row is None else json.loads(str(row["record_json"]))

    def get_revision(self, revision_id: str) -> dict[str, Any] | None:
        with self.connect() as conn: row = conn.execute("SELECT record_json FROM testamur_record_revisions WHERE revision_id=?", (str(revision_id),)).fetchone()
        return None if row is None else json.loads(str(row["record_json"]))

    def latest_revision(self, record_id: str) -> dict[str, Any] | None:
        if self.get_record(record_id) is None: raise KeyError(record_id)
        with self.connect() as conn: row = conn.execute("SELECT record_json FROM testamur_record_revisions WHERE record_id=? ORDER BY ordinal DESC LIMIT 1", (record_id,)).fetchone()
        return None if row is None else json.loads(str(row["record_json"]))

    def append_revision(self, record_id: str, *, expected_parent_revision_id: str, statement: str | None = None, basis: Iterable[Mapping[str, Any]] | None = None, title: str | None = None, created_by: str | None = None) -> dict[str, Any]:
        if self.get_record(record_id) is None: raise KeyError(record_id)
        parent = self.latest_revision(record_id)
        if parent is None: raise RuntimeError("record has no current revision")
        expected = _required(expected_parent_revision_id, field="expected_parent_revision_id")
        if parent["revision_id"] != expected: raise ValueError("record head changed; expected_parent_revision_id is stale")
        next_statement = str(parent["statement"]) if statement is None else _required(statement, field="statement")
        next_basis = [dict(item) for item in parent.get("basis") or []] if basis is None else _normalize_basis(basis)
        next_title = parent.get("title") if title is None else str(title)
        recorded_at = _utc_now(); ordinal = int(parent["ordinal"]) + 1
        revision = self._revision_payload(record_id=record_id, ordinal=ordinal, parent_revision_id=parent["revision_id"], statement=next_statement, title=next_title, basis=next_basis, created_by=None if created_by is None else str(created_by), recorded_at=recorded_at)
        revision_json = canonical_json(revision)
        with self.connect() as conn:
            live = conn.execute("SELECT revision_id,ordinal FROM testamur_record_revisions WHERE record_id=? ORDER BY ordinal DESC LIMIT 1", (record_id,)).fetchone()
            if live is None or str(live["revision_id"]) != expected: raise ValueError("record head changed during append; retry from latest revision")
            conn.execute("INSERT INTO testamur_record_revisions(revision_id,record_id,ordinal,parent_revision_id,statement,record_json,recorded_at) VALUES(?,?,?,?,?,?,?)", (revision["revision_id"], record_id, ordinal, expected, next_statement, revision_json, recorded_at))
        return revision

    def history(self, record_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        if self.get_record(record_id) is None: raise KeyError(record_id)
        bounded = max(1, min(int(limit), 500))
        with self.connect() as conn: rows = conn.execute("SELECT record_json FROM testamur_record_revisions WHERE record_id=? ORDER BY ordinal DESC LIMIT ?", (record_id, bounded)).fetchall()
        return [json.loads(str(row["record_json"])) for row in rows]

    def compare_revisions(self, left_id: str, right_id: str) -> dict[str, Any]:
        left = self.get_revision(left_id); right = self.get_revision(right_id)
        if left is None: raise KeyError(left_id)
        if right is None: raise KeyError(right_id)
        if left["record_id"] != right["record_id"]: raise ValueError("record revision comparison requires one persistent record")
        return {"record_id": left["record_id"], "left_revision_id": left_id, "right_revision_id": right_id, "statement_changed": left.get("statement") != right.get("statement"), "title_changed": left.get("title") != right.get("title"), "basis_changed": left.get("basis") != right.get("basis"), "semantics": {"mechanical_comparison_only": True, "semantic_equivalence_inferred": False, "truth_change_inferred": False}}

    def create_relation(self, relation_type: str, *, from_ref: str, to_ref: str, basis: Iterable[Mapping[str, Any]] | None = None, created_by: str | None = None) -> dict[str, Any]:
        relation = _required(relation_type, field="relation_type")
        if relation not in RELATION_TYPES: raise ValueError(f"unsupported relation type {relation!r}; expected one of: {', '.join(sorted(RELATION_TYPES))}")
        source = _required(from_ref, field="from_ref"); target = _required(to_ref, field="to_ref")
        if source == target: raise ValueError("relation endpoints must be distinct")
        normalized_basis = _normalize_basis(basis); recorded_at = _utc_now()
        core = {"relation_type": relation, "from_ref": source, "to_ref": target, "basis": normalized_basis, "created_by": None if created_by is None else str(created_by), "recorded_at": recorded_at}
        relation_id = RELATION_PREFIX + canonical_hash(core)
        payload = {"relation_id": relation_id, **core, "semantics": {"first_class_immutable_relation": True, "relation_implies_truth": False, "relation_implies_causality": False, "created_by_is_declared_provenance": True}}
        with self.connect() as conn: conn.execute("INSERT INTO testamur_relations(relation_id,relation_type,from_ref,to_ref,record_json,recorded_at) VALUES(?,?,?,?,?,?)", (relation_id, relation, source, target, canonical_json(payload), recorded_at))
        return payload

    def get_relation(self, relation_id: str) -> dict[str, Any] | None:
        with self.connect() as conn: row = conn.execute("SELECT record_json FROM testamur_relations WHERE relation_id=?", (str(relation_id),)).fetchone()
        return None if row is None else json.loads(str(row["record_json"]))

    def relations_for(self, ref: str, *, direction: str = "both", relation_types: Iterable[str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        target = _required(ref, field="ref")
        if direction not in {"incoming", "outgoing", "both"}: raise ValueError("direction must be incoming, outgoing, or both")
        selected_types = None
        if relation_types is not None:
            selected_types = {_required(value, field="relation_type") for value in relation_types}; unknown = selected_types - RELATION_TYPES
            if unknown: raise ValueError(f"unsupported relation types: {', '.join(sorted(unknown))}")
        bounded = max(1, min(int(limit), 500)); clauses: list[str] = []; params: list[Any] = []
        if direction in {"outgoing", "both"}: clauses.append("from_ref=?"); params.append(target)
        if direction in {"incoming", "both"}: clauses.append("to_ref=?"); params.append(target)
        where = "(" + " OR ".join(clauses) + ")"
        if selected_types:
            placeholders = ",".join("?" for _ in selected_types); where += f" AND relation_type IN ({placeholders})"; params.extend(sorted(selected_types))
        params.append(bounded)
        with self.connect() as conn: rows = conn.execute(f"SELECT record_json FROM testamur_relations WHERE {where} ORDER BY recorded_at DESC, relation_id DESC LIMIT ?", params).fetchall()
        return [json.loads(str(row["record_json"])) for row in rows]

    def stats(self) -> dict[str, int]:
        with self.connect() as conn:
            records = int(conn.execute("SELECT COUNT(*) FROM testamur_records").fetchone()[0]); revisions = int(conn.execute("SELECT COUNT(*) FROM testamur_record_revisions").fetchone()[0]); relations = int(conn.execute("SELECT COUNT(*) FROM testamur_relations").fetchone()[0])
        return {"records": records, "record_revisions": revisions, "relations": relations}
