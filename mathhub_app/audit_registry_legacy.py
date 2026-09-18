from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from registry import canonical_hash, parse_ref, utc_now, version_ref, proof_ref, build_ref


ALIGNMENT_STATUSES = frozenset({
    "unreviewed",
    "partially-reviewed",
    "aligned",
    "aligned-with-notes",
    "disputed",
    "mismatch",
})

COVERAGE_DIMENSIONS = frozenset({
    "formal-source-provenance",
    "source-alignment",
    "semantic-definitions",
    "proof-dependencies",
    "axiom-closure",
    "build-reproducibility",
    "supply-chain",
    "independent-checker",
})

COVERAGE_STATUSES = frozenset({
    "not-examined",
    "partial",
    "complete",
    "issue-found",
})

FINDING_TYPES = frozenset({
    "statement-mismatch",
    "definition-strengthening",
    "definition-weakening",
    "quantifier-mismatch",
    "dependency-gap",
    "unexpected-axiom",
    "provenance-gap",
    "reproducibility-failure",
    "checker-disagreement",
    "supply-chain-risk",
    "documentation-mismatch",
    "tooling-bug",
})

FINDING_SEVERITIES = frozenset({"note", "low", "medium", "high", "critical"})
FINDING_STATUSES = frozenset({
    "open",
    "investigating",
    "confirmed",
    "rejected",
    "resolved",
    "superseded",
})

AUDIT_TARGET_KINDS = frozenset({
    "claim-version",
    "proof",
    "build",
    "source-statement",
    "formal-source",
})


def source_ref(value: int) -> str:
    return f"SRC-{value:06d}"


def statement_ref(value: int) -> str:
    return f"STM-{value:06d}"


def alignment_ref(value: int) -> str:
    return f"ALN-{value:06d}"


def audit_ref(value: int) -> str:
    return f"AUD-{value:06d}"


def target_ref(value: int) -> str:
    return f"TGT-{value:06d}"


def coverage_ref(value: int) -> str:
    return f"COV-{value:06d}"


def finding_ref(value: int) -> str:
    return f"FND-{value:06d}"


def finding_event_ref(value: int) -> str:
    return f"FEV-{value:06d}"


def _json(value: Any) -> str:
    if value is None:
        value = {}
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_json(value: str | None) -> Any:
    if not value:
        return {}
    return json.loads(value)


class AuditRegistry:
    """Append-only evidence/workflow registry for adversarial mathematical audits.

    This module intentionally does not change MathHub's mathematical ontology.
    Claim, Proof and ProofDependency remain authoritative mathematical objects.
    The tables here record provenance, review scope, audit coverage and findings.

    The registry is designed to share the same SQLite database as ``Registry`` so
    foreign keys point to exact Claim revisions, Proofs and Builds. Instantiate the
    core ``Registry`` first when creating a new database.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS formal_sources(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          claim_version_id INTEGER NOT NULL REFERENCES claim_versions(id),
          provider TEXT NOT NULL,
          repository TEXT NOT NULL DEFAULT '',
          revision TEXT NOT NULL DEFAULT '',
          package TEXT NOT NULL DEFAULT '',
          package_version TEXT NOT NULL DEFAULT '',
          file_path TEXT NOT NULL DEFAULT '',
          lean_name TEXT NOT NULL,
          environment_hash TEXT NOT NULL DEFAULT '',
          source_hash TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS source_statements(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          source_kind TEXT NOT NULL,
          title TEXT NOT NULL,
          statement_text TEXT NOT NULL,
          source_uri TEXT NOT NULL DEFAULT '',
          locator TEXT NOT NULL DEFAULT '',
          source_revision TEXT NOT NULL DEFAULT '',
          content_hash TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS alignment_reviews(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          source_statement_id INTEGER NOT NULL REFERENCES source_statements(id),
          claim_version_id INTEGER NOT NULL REFERENCES claim_versions(id),
          status TEXT NOT NULL,
          scope TEXT NOT NULL DEFAULT '',
          reviewer TEXT NOT NULL DEFAULT '',
          note TEXT NOT NULL DEFAULT '',
          evidence_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          CHECK(status IN (
            'unreviewed','partially-reviewed','aligned','aligned-with-notes',
            'disputed','mismatch'
          ))
        );

        CREATE TABLE IF NOT EXISTS audit_cases(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          title TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '',
          headline_claim_version_id INTEGER NOT NULL REFERENCES claim_versions(id),
          created_by TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_targets(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          audit_case_id INTEGER NOT NULL REFERENCES audit_cases(id),
          target_kind TEXT NOT NULL,
          claim_version_id INTEGER REFERENCES claim_versions(id),
          proof_id INTEGER REFERENCES proofs(id),
          build_id INTEGER REFERENCES builds(id),
          source_statement_id INTEGER REFERENCES source_statements(id),
          formal_source_id INTEGER REFERENCES formal_sources(id),
          note TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          CHECK(target_kind IN (
            'claim-version','proof','build','source-statement','formal-source'
          )),
          CHECK(
            (target_kind='claim-version' AND claim_version_id IS NOT NULL AND proof_id IS NULL AND build_id IS NULL AND source_statement_id IS NULL AND formal_source_id IS NULL)
            OR
            (target_kind='proof' AND claim_version_id IS NULL AND proof_id IS NOT NULL AND build_id IS NULL AND source_statement_id IS NULL AND formal_source_id IS NULL)
            OR
            (target_kind='build' AND claim_version_id IS NULL AND proof_id IS NULL AND build_id IS NOT NULL AND source_statement_id IS NULL AND formal_source_id IS NULL)
            OR
            (target_kind='source-statement' AND claim_version_id IS NULL AND proof_id IS NULL AND build_id IS NULL AND source_statement_id IS NOT NULL AND formal_source_id IS NULL)
            OR
            (target_kind='formal-source' AND claim_version_id IS NULL AND proof_id IS NULL AND build_id IS NULL AND source_statement_id IS NULL AND formal_source_id IS NOT NULL)
          )
        );

        CREATE TABLE IF NOT EXISTS audit_coverage_records(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          audit_case_id INTEGER NOT NULL REFERENCES audit_cases(id),
          dimension TEXT NOT NULL,
          status TEXT NOT NULL,
          scope TEXT NOT NULL DEFAULT '',
          reviewer TEXT NOT NULL DEFAULT '',
          note TEXT NOT NULL DEFAULT '',
          evidence_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          CHECK(dimension IN (
            'formal-source-provenance','source-alignment','semantic-definitions',
            'proof-dependencies','axiom-closure','build-reproducibility',
            'supply-chain','independent-checker'
          )),
          CHECK(status IN ('not-examined','partial','complete','issue-found'))
        );

        CREATE TABLE IF NOT EXISTS findings(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          audit_case_id INTEGER NOT NULL REFERENCES audit_cases(id),
          finding_type TEXT NOT NULL,
          severity TEXT NOT NULL,
          title TEXT NOT NULL,
          summary TEXT NOT NULL,
          affected_claim_version_id INTEGER REFERENCES claim_versions(id),
          affected_proof_id INTEGER REFERENCES proofs(id),
          affected_build_id INTEGER REFERENCES builds(id),
          affected_source_statement_id INTEGER REFERENCES source_statements(id),
          affected_formal_source_id INTEGER REFERENCES formal_sources(id),
          created_by TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          CHECK(finding_type IN (
            'statement-mismatch','definition-strengthening','definition-weakening',
            'quantifier-mismatch','dependency-gap','unexpected-axiom',
            'provenance-gap','reproducibility-failure','checker-disagreement',
            'supply-chain-risk','documentation-mismatch','tooling-bug'
          )),
          CHECK(severity IN ('note','low','medium','high','critical'))
        );

        CREATE TABLE IF NOT EXISTS finding_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          finding_id INTEGER NOT NULL REFERENCES findings(id),
          status TEXT NOT NULL,
          author TEXT NOT NULL DEFAULT '',
          note TEXT NOT NULL DEFAULT '',
          evidence_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          CHECK(status IN (
            'open','investigating','confirmed','rejected','resolved','superseded'
          ))
        );

        CREATE INDEX IF NOT EXISTS idx_formal_source_claim
          ON formal_sources(claim_version_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_alignment_pair
          ON alignment_reviews(source_statement_id, claim_version_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_headline
          ON audit_cases(headline_claim_version_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_targets
          ON audit_targets(audit_case_id, target_kind, id);
        CREATE INDEX IF NOT EXISTS idx_coverage_case
          ON audit_coverage_records(audit_case_id, dimension, id DESC);
        CREATE INDEX IF NOT EXISTS idx_findings_case
          ON findings(audit_case_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_finding_events
          ON finding_events(finding_id, id DESC);

        CREATE TRIGGER IF NOT EXISTS formal_sources_no_update
        BEFORE UPDATE ON formal_sources BEGIN
          SELECT RAISE(ABORT, 'formal sources are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS formal_sources_no_delete
        BEFORE DELETE ON formal_sources BEGIN
          SELECT RAISE(ABORT, 'formal sources are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS source_statements_no_update
        BEFORE UPDATE ON source_statements BEGIN
          SELECT RAISE(ABORT, 'source statements are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS source_statements_no_delete
        BEFORE DELETE ON source_statements BEGIN
          SELECT RAISE(ABORT, 'source statements are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS alignment_reviews_no_update
        BEFORE UPDATE ON alignment_reviews BEGIN
          SELECT RAISE(ABORT, 'alignment reviews are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS alignment_reviews_no_delete
        BEFORE DELETE ON alignment_reviews BEGIN
          SELECT RAISE(ABORT, 'alignment reviews are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS audit_cases_no_update
        BEFORE UPDATE ON audit_cases BEGIN
          SELECT RAISE(ABORT, 'audit cases are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS audit_cases_no_delete
        BEFORE DELETE ON audit_cases BEGIN
          SELECT RAISE(ABORT, 'audit cases are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS audit_targets_no_update
        BEFORE UPDATE ON audit_targets BEGIN
          SELECT RAISE(ABORT, 'audit targets are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS audit_targets_no_delete
        BEFORE DELETE ON audit_targets BEGIN
          SELECT RAISE(ABORT, 'audit targets are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS audit_coverage_no_update
        BEFORE UPDATE ON audit_coverage_records BEGIN
          SELECT RAISE(ABORT, 'audit coverage records are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS audit_coverage_no_delete
        BEFORE DELETE ON audit_coverage_records BEGIN
          SELECT RAISE(ABORT, 'audit coverage records are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS findings_no_update
        BEFORE UPDATE ON findings BEGIN
          SELECT RAISE(ABORT, 'findings are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS findings_no_delete
        BEFORE DELETE ON findings BEGIN
          SELECT RAISE(ABORT, 'findings are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS finding_events_no_update
        BEFORE UPDATE ON finding_events BEGIN
          SELECT RAISE(ABORT, 'finding events are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS finding_events_no_delete
        BEFORE DELETE ON finding_events BEGIN
          SELECT RAISE(ABORT, 'finding events are immutable');
        END;
        """
        with self.connect() as conn:
            conn.executescript(schema)

    @staticmethod
    def _require(conn: sqlite3.Connection, table: str, value: int, label: str) -> None:
        allowed = {
            "claim_versions", "proofs", "builds", "formal_sources",
            "source_statements", "audit_cases", "findings",
        }
        if table not in allowed:
            raise ValueError(f"unsupported reference table: {table}")
        row = conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (value,)).fetchone()
        if row is None:
            raise KeyError(label)

    def create_formal_source(self, payload: dict[str, Any]) -> dict[str, Any]:
        claim_version_id = parse_ref(str(payload.get("claim_version_id") or ""), "VER")
        provider = str(payload.get("provider") or "").strip()
        lean_name = str(payload.get("lean_name") or "").strip()
        source_hash = str(payload.get("source_hash") or "").strip()
        if not provider or not lean_name or not source_hash:
            raise ValueError("provider, lean_name, and source_hash are required")
        now = utc_now()
        with self.connect() as conn:
            self._require(conn, "claim_versions", claim_version_id, version_ref(claim_version_id))
            cur = conn.execute(
                """INSERT INTO formal_sources(
                   claim_version_id,provider,repository,revision,package,package_version,
                   file_path,lean_name,environment_hash,source_hash,metadata_json,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    claim_version_id,
                    provider,
                    str(payload.get("repository") or "").strip(),
                    str(payload.get("revision") or "").strip(),
                    str(payload.get("package") or "").strip(),
                    str(payload.get("package_version") or "").strip(),
                    str(payload.get("file_path") or "").strip(),
                    lean_name,
                    str(payload.get("environment_hash") or "").strip(),
                    source_hash,
                    _json(payload.get("metadata")),
                    now,
                ),
            )
            sid = int(cur.lastrowid)
        return self.get_formal_source(source_ref(sid))

    def get_formal_source(self, source_id: str) -> dict[str, Any]:
        sid = parse_ref(source_id, "SRC")
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM formal_sources WHERE id=?", (sid,)).fetchone()
            if row is None:
                raise KeyError(source_id)
            return self._formal_source(row)

    def create_source_statement(self, payload: dict[str, Any]) -> dict[str, Any]:
        source_kind = str(payload.get("source_kind") or "").strip()
        title = str(payload.get("title") or "").strip()
        statement_text = str(payload.get("statement_text") or payload.get("statement") or "").strip()
        if not source_kind or not title or not statement_text:
            raise ValueError("source_kind, title, and statement_text are required")
        statement_payload = {
            "source_kind": source_kind,
            "title": title,
            "statement_text": statement_text,
            "source_uri": str(payload.get("source_uri") or "").strip(),
            "locator": str(payload.get("locator") or "").strip(),
            "source_revision": str(payload.get("source_revision") or "").strip(),
        }
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT INTO source_statements(
                   source_kind,title,statement_text,source_uri,locator,source_revision,
                   content_hash,metadata_json,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    statement_payload["source_kind"], statement_payload["title"],
                    statement_payload["statement_text"], statement_payload["source_uri"],
                    statement_payload["locator"], statement_payload["source_revision"],
                    canonical_hash(statement_payload), _json(payload.get("metadata")), utc_now(),
                ),
            )
            sid = int(cur.lastrowid)
        return self.get_source_statement(statement_ref(sid))

    def get_source_statement(self, source_statement_id: str) -> dict[str, Any]:
        sid = parse_ref(source_statement_id, "STM")
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM source_statements WHERE id=?", (sid,)).fetchone()
            if row is None:
                raise KeyError(source_statement_id)
            return self._source_statement(row)

    def record_alignment(
        self,
        source_statement_id: str,
        claim_version_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        sid = parse_ref(source_statement_id, "STM")
        vid = parse_ref(claim_version_id, "VER")
        status = str(payload.get("status") or "").strip().lower()
        if status not in ALIGNMENT_STATUSES:
            raise ValueError(f"unsupported alignment status: {status!r}")
        with self.connect() as conn:
            self._require(conn, "source_statements", sid, source_statement_id)
            self._require(conn, "claim_versions", vid, claim_version_id)
            cur = conn.execute(
                """INSERT INTO alignment_reviews(
                   source_statement_id,claim_version_id,status,scope,reviewer,note,
                   evidence_json,created_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    sid, vid, status,
                    str(payload.get("scope") or "").strip(),
                    str(payload.get("reviewer") or "").strip(),
                    str(payload.get("note") or "").strip(),
                    _json(payload.get("evidence")), utc_now(),
                ),
            )
            aid = int(cur.lastrowid)
            row = conn.execute("SELECT * FROM alignment_reviews WHERE id=?", (aid,)).fetchone()
        assert row is not None
        return self._alignment(row)

    def alignment_history(self, source_statement_id: str, claim_version_id: str) -> dict[str, Any]:
        sid = parse_ref(source_statement_id, "STM")
        vid = parse_ref(claim_version_id, "VER")
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM alignment_reviews
                   WHERE source_statement_id=? AND claim_version_id=?
                   ORDER BY id DESC""",
                (sid, vid),
            ).fetchall()
        items = [self._alignment(row) for row in rows]
        return {
            "source_statement_id": source_statement_id,
            "claim_version_id": claim_version_id,
            "latest": items[0] if items else None,
            "history": items,
        }

    def create_audit_case(self, payload: dict[str, Any]) -> dict[str, Any]:
        title = str(payload.get("title") or "").strip()
        vid = parse_ref(str(payload.get("headline_claim_version_id") or ""), "VER")
        if not title:
            raise ValueError("title is required")
        with self.connect() as conn:
            self._require(conn, "claim_versions", vid, version_ref(vid))
            cur = conn.execute(
                """INSERT INTO audit_cases(
                   title,description,headline_claim_version_id,created_by,created_at
                   ) VALUES(?,?,?,?,?)""",
                (
                    title,
                    str(payload.get("description") or "").strip(),
                    vid,
                    str(payload.get("created_by") or "").strip(),
                    utc_now(),
                ),
            )
            aid = int(cur.lastrowid)
        # The headline Claim revision is also an explicit target for uniform reads.
        self.add_audit_target(audit_ref(aid), {
            "target_kind": "claim-version",
            "claim_version_id": version_ref(vid),
            "note": "headline claim revision",
        })
        return self.get_audit_case(audit_ref(aid))

    def add_audit_target(self, audit_case_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        aid = parse_ref(audit_case_id, "AUD")
        kind = str(payload.get("target_kind") or "").strip().lower()
        if kind not in AUDIT_TARGET_KINDS:
            raise ValueError(f"unsupported audit target kind: {kind!r}")
        columns: dict[str, int | None] = {
            "claim_version_id": None,
            "proof_id": None,
            "build_id": None,
            "source_statement_id": None,
            "formal_source_id": None,
        }
        mapping = {
            "claim-version": ("claim_version_id", "VER", "claim_versions"),
            "proof": ("proof_id", "PRF", "proofs"),
            "build": ("build_id", "BLD", "builds"),
            "source-statement": ("source_statement_id", "STM", "source_statements"),
            "formal-source": ("formal_source_id", "SRC", "formal_sources"),
        }
        field, prefix, table = mapping[kind]
        raw = str(payload.get(field) or "")
        value = parse_ref(raw, prefix)
        columns[field] = value
        with self.connect() as conn:
            self._require(conn, "audit_cases", aid, audit_case_id)
            self._require(conn, table, value, raw)
            cur = conn.execute(
                """INSERT INTO audit_targets(
                   audit_case_id,target_kind,claim_version_id,proof_id,build_id,
                   source_statement_id,formal_source_id,note,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    aid, kind, columns["claim_version_id"], columns["proof_id"],
                    columns["build_id"], columns["source_statement_id"],
                    columns["formal_source_id"], str(payload.get("note") or "").strip(),
                    utc_now(),
                ),
            )
            tid = int(cur.lastrowid)
            row = conn.execute("SELECT * FROM audit_targets WHERE id=?", (tid,)).fetchone()
        assert row is not None
        return self._audit_target(row)

    def record_coverage(self, audit_case_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        aid = parse_ref(audit_case_id, "AUD")
        dimension = str(payload.get("dimension") or "").strip().lower()
        status = str(payload.get("status") or "").strip().lower()
        if dimension not in COVERAGE_DIMENSIONS:
            raise ValueError(f"unsupported coverage dimension: {dimension!r}")
        if status not in COVERAGE_STATUSES:
            raise ValueError(f"unsupported coverage status: {status!r}")
        with self.connect() as conn:
            self._require(conn, "audit_cases", aid, audit_case_id)
            cur = conn.execute(
                """INSERT INTO audit_coverage_records(
                   audit_case_id,dimension,status,scope,reviewer,note,evidence_json,created_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    aid, dimension, status,
                    str(payload.get("scope") or "").strip(),
                    str(payload.get("reviewer") or "").strip(),
                    str(payload.get("note") or "").strip(),
                    _json(payload.get("evidence")), utc_now(),
                ),
            )
            cid = int(cur.lastrowid)
            row = conn.execute("SELECT * FROM audit_coverage_records WHERE id=?", (cid,)).fetchone()
        assert row is not None
        return self._coverage(row)

    def create_finding(self, audit_case_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        aid = parse_ref(audit_case_id, "AUD")
        finding_type = str(payload.get("finding_type") or "").strip().lower()
        severity = str(payload.get("severity") or "").strip().lower()
        title = str(payload.get("title") or "").strip()
        summary = str(payload.get("summary") or "").strip()
        if finding_type not in FINDING_TYPES:
            raise ValueError(f"unsupported finding type: {finding_type!r}")
        if severity not in FINDING_SEVERITIES:
            raise ValueError(f"unsupported finding severity: {severity!r}")
        if not title or not summary:
            raise ValueError("title and summary are required")

        refs = self._finding_refs(payload)
        initial_status = str(payload.get("status") or "open").strip().lower()
        if initial_status not in FINDING_STATUSES:
            raise ValueError(f"unsupported finding status: {initial_status!r}")

        with self.connect() as conn:
            self._require(conn, "audit_cases", aid, audit_case_id)
            self._validate_finding_refs(conn, refs)
            cur = conn.execute(
                """INSERT INTO findings(
                   audit_case_id,finding_type,severity,title,summary,
                   affected_claim_version_id,affected_proof_id,affected_build_id,
                   affected_source_statement_id,affected_formal_source_id,
                   created_by,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    aid, finding_type, severity, title, summary,
                    refs["claim_version_id"], refs["proof_id"], refs["build_id"],
                    refs["source_statement_id"], refs["formal_source_id"],
                    str(payload.get("created_by") or "").strip(), utc_now(),
                ),
            )
            fid = int(cur.lastrowid)
            conn.execute(
                """INSERT INTO finding_events(
                   finding_id,status,author,note,evidence_json,created_at
                   ) VALUES(?,?,?,?,?,?)""",
                (
                    fid, initial_status,
                    str(payload.get("created_by") or "").strip(),
                    str(payload.get("initial_note") or "").strip(),
                    _json(payload.get("evidence")), utc_now(),
                ),
            )
        return self.get_finding(finding_ref(fid))

    def record_finding_event(self, finding_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        fid = parse_ref(finding_id, "FND")
        status = str(payload.get("status") or "").strip().lower()
        if status not in FINDING_STATUSES:
            raise ValueError(f"unsupported finding status: {status!r}")
        with self.connect() as conn:
            self._require(conn, "findings", fid, finding_id)
            cur = conn.execute(
                """INSERT INTO finding_events(
                   finding_id,status,author,note,evidence_json,created_at
                   ) VALUES(?,?,?,?,?,?)""",
                (
                    fid, status,
                    str(payload.get("author") or "").strip(),
                    str(payload.get("note") or "").strip(),
                    _json(payload.get("evidence")), utc_now(),
                ),
            )
            eid = int(cur.lastrowid)
            row = conn.execute("SELECT * FROM finding_events WHERE id=?", (eid,)).fetchone()
        assert row is not None
        return self._finding_event(row)

    def get_finding(self, finding_id: str) -> dict[str, Any]:
        fid = parse_ref(finding_id, "FND")
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM findings WHERE id=?", (fid,)).fetchone()
            if row is None:
                raise KeyError(finding_id)
            events = conn.execute(
                "SELECT * FROM finding_events WHERE finding_id=? ORDER BY id DESC", (fid,)
            ).fetchall()
        result = self._finding(row)
        history = [self._finding_event(event) for event in events]
        result["status"] = history[0]["status"] if history else None
        result["latest_event"] = history[0] if history else None
        result["events"] = history
        return result

    def get_audit_case(self, audit_case_id: str) -> dict[str, Any]:
        aid = parse_ref(audit_case_id, "AUD")
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM audit_cases WHERE id=?", (aid,)).fetchone()
            if row is None:
                raise KeyError(audit_case_id)
            targets = conn.execute(
                "SELECT * FROM audit_targets WHERE audit_case_id=? ORDER BY id", (aid,)
            ).fetchall()
            coverage_rows = conn.execute(
                "SELECT * FROM audit_coverage_records WHERE audit_case_id=? ORDER BY id DESC", (aid,)
            ).fetchall()
            finding_rows = conn.execute(
                "SELECT id FROM findings WHERE audit_case_id=? ORDER BY id DESC", (aid,)
            ).fetchall()

        result = self._audit_case(row)
        result["targets"] = [self._audit_target(target) for target in targets]
        coverage = [self._coverage(item) for item in coverage_rows]
        latest: dict[str, dict[str, Any]] = {}
        for item in coverage:
            latest.setdefault(item["dimension"], item)
        result["coverage"] = latest
        result["coverage_history"] = coverage
        result["findings"] = [self.get_finding(finding_ref(int(item["id"]))) for item in finding_rows]
        return result

    @staticmethod
    def _finding_refs(payload: dict[str, Any]) -> dict[str, int | None]:
        specs = {
            "claim_version_id": "VER",
            "proof_id": "PRF",
            "build_id": "BLD",
            "source_statement_id": "STM",
            "formal_source_id": "SRC",
        }
        result: dict[str, int | None] = {}
        for field, prefix in specs.items():
            value = str(payload.get(f"affected_{field}") or payload.get(field) or "").strip()
            result[field] = parse_ref(value, prefix) if value else None
        return result

    def _validate_finding_refs(self, conn: sqlite3.Connection, refs: dict[str, int | None]) -> None:
        specs = {
            "claim_version_id": ("claim_versions", "VER"),
            "proof_id": ("proofs", "PRF"),
            "build_id": ("builds", "BLD"),
            "source_statement_id": ("source_statements", "STM"),
            "formal_source_id": ("formal_sources", "SRC"),
        }
        for field, value in refs.items():
            if value is None:
                continue
            table, prefix = specs[field]
            self._require(conn, table, value, f"{prefix}-{value:06d}")

    @staticmethod
    def _formal_source(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "formal_source_id": source_ref(int(row["id"])),
            "claim_version_id": version_ref(int(row["claim_version_id"])),
            "provider": row["provider"],
            "repository": row["repository"],
            "revision": row["revision"],
            "package": row["package"],
            "package_version": row["package_version"],
            "file_path": row["file_path"],
            "lean_name": row["lean_name"],
            "environment_hash": row["environment_hash"],
            "source_hash": row["source_hash"],
            "metadata": _load_json(row["metadata_json"]),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _source_statement(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "source_statement_id": statement_ref(int(row["id"])),
            "source_kind": row["source_kind"],
            "title": row["title"],
            "statement_text": row["statement_text"],
            "source_uri": row["source_uri"],
            "locator": row["locator"],
            "source_revision": row["source_revision"],
            "content_hash": row["content_hash"],
            "metadata": _load_json(row["metadata_json"]),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _alignment(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "alignment_id": alignment_ref(int(row["id"])),
            "source_statement_id": statement_ref(int(row["source_statement_id"])),
            "claim_version_id": version_ref(int(row["claim_version_id"])),
            "status": row["status"],
            "scope": row["scope"],
            "reviewer": row["reviewer"],
            "note": row["note"],
            "evidence": _load_json(row["evidence_json"]),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _audit_case(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "audit_case_id": audit_ref(int(row["id"])),
            "title": row["title"],
            "description": row["description"],
            "headline_claim_version_id": version_ref(int(row["headline_claim_version_id"])),
            "created_by": row["created_by"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _audit_target(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "audit_target_id": target_ref(int(row["id"])),
            "audit_case_id": audit_ref(int(row["audit_case_id"])),
            "target_kind": row["target_kind"],
            "claim_version_id": version_ref(int(row["claim_version_id"])) if row["claim_version_id"] else None,
            "proof_id": proof_ref(int(row["proof_id"])) if row["proof_id"] else None,
            "build_id": build_ref(int(row["build_id"])) if row["build_id"] else None,
            "source_statement_id": statement_ref(int(row["source_statement_id"])) if row["source_statement_id"] else None,
            "formal_source_id": source_ref(int(row["formal_source_id"])) if row["formal_source_id"] else None,
            "note": row["note"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _coverage(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "coverage_id": coverage_ref(int(row["id"])),
            "audit_case_id": audit_ref(int(row["audit_case_id"])),
            "dimension": row["dimension"],
            "status": row["status"],
            "scope": row["scope"],
            "reviewer": row["reviewer"],
            "note": row["note"],
            "evidence": _load_json(row["evidence_json"]),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _finding(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "finding_id": finding_ref(int(row["id"])),
            "audit_case_id": audit_ref(int(row["audit_case_id"])),
            "finding_type": row["finding_type"],
            "severity": row["severity"],
            "title": row["title"],
            "summary": row["summary"],
            "affected_claim_version_id": version_ref(int(row["affected_claim_version_id"])) if row["affected_claim_version_id"] else None,
            "affected_proof_id": proof_ref(int(row["affected_proof_id"])) if row["affected_proof_id"] else None,
            "affected_build_id": build_ref(int(row["affected_build_id"])) if row["affected_build_id"] else None,
            "affected_source_statement_id": statement_ref(int(row["affected_source_statement_id"])) if row["affected_source_statement_id"] else None,
            "affected_formal_source_id": source_ref(int(row["affected_formal_source_id"])) if row["affected_formal_source_id"] else None,
            "created_by": row["created_by"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _finding_event(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "finding_event_id": finding_event_ref(int(row["id"])),
            "finding_id": finding_ref(int(row["finding_id"])),
            "status": row["status"],
            "author": row["author"],
            "note": row["note"],
            "evidence": _load_json(row["evidence_json"]),
            "created_at": row["created_at"],
        }
