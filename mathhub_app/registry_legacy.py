from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_ref(value: str, prefix: str) -> int:
    match = re.fullmatch(rf"{re.escape(prefix)}-(\d+)", value.strip(), re.IGNORECASE)
    if not match:
        raise ValueError(f"invalid {prefix} identifier: {value!r}")
    return int(match.group(1))


def claim_ref(value: int) -> str:
    return f"CLM-{value:06d}"


def version_ref(value: int) -> str:
    return f"VER-{value:06d}"


def proof_ref(value: int) -> str:
    return f"PRF-{value:06d}"


def build_ref(value: int) -> str:
    return f"BLD-{value:06d}"


class Registry:
    """Append-only artifact registry.

    Mathematical dependency truth lives in ArgumentIndex. `claim_links` remains in
    the schema only so old local databases can still be read; new Builds never
    populate it.
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
        CREATE TABLE IF NOT EXISTS claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS claim_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id INTEGER NOT NULL REFERENCES claims(id),
            version_no INTEGER NOT NULL,
            title TEXT NOT NULL,
            statement TEXT NOT NULL,
            latex TEXT NOT NULL DEFAULT '',
            area TEXT NOT NULL DEFAULT '',
            claim_author TEXT NOT NULL DEFAULT '',
            formal_declaration TEXT NOT NULL,
            lean_name TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(claim_id, version_no)
        );

        CREATE TABLE IF NOT EXISTS proofs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id INTEGER NOT NULL REFERENCES claims(id),
            claim_version_id INTEGER NOT NULL REFERENCES claim_versions(id),
            proof_author TEXT NOT NULL DEFAULT '',
            imports_json TEXT NOT NULL,
            proof_source TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS builds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id INTEGER NOT NULL REFERENCES claims(id),
            proof_id INTEGER NOT NULL REFERENCES proofs(id),
            status TEXT NOT NULL,
            kernel_ok INTEGER NOT NULL,
            audit_ok INTEGER,
            contains_sorry INTEGER NOT NULL,
            uses_trust_compiler INTEGER NOT NULL,
            lean_version TEXT,
            lake_version TEXT,
            mathlib_revision TEXT,
            environment_hash TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            minimal_imports_json TEXT NOT NULL,
            dependencies_json TEXT NOT NULL,
            axioms_json TEXT NOT NULL,
            output TEXT NOT NULL,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        -- Compatibility-only table from the first registry prototype. New Builds
        -- do not write here; ArgumentIndex is the sole dependency source of truth.
        CREATE TABLE IF NOT EXISTS claim_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id INTEGER NOT NULL REFERENCES claims(id),
            depends_on_claim_id INTEGER NOT NULL REFERENCES claims(id),
            relation TEXT NOT NULL,
            source_build_id INTEGER NOT NULL REFERENCES builds(id),
            created_at TEXT NOT NULL,
            UNIQUE(claim_id, depends_on_claim_id, relation, source_build_id)
        );

        CREATE INDEX IF NOT EXISTS idx_versions_claim ON claim_versions(claim_id, version_no DESC);
        CREATE INDEX IF NOT EXISTS idx_proofs_claim ON proofs(claim_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_builds_proof ON builds(proof_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_links_claim ON claim_links(claim_id, source_build_id);

        CREATE TRIGGER IF NOT EXISTS claim_versions_no_update
        BEFORE UPDATE ON claim_versions BEGIN
            SELECT RAISE(ABORT, 'claim versions are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS claim_versions_no_delete
        BEFORE DELETE ON claim_versions BEGIN
            SELECT RAISE(ABORT, 'claim versions are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS proofs_no_update
        BEFORE UPDATE ON proofs BEGIN
            SELECT RAISE(ABORT, 'proof records are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS proofs_no_delete
        BEFORE DELETE ON proofs BEGIN
            SELECT RAISE(ABORT, 'proof records are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS builds_no_update
        BEFORE UPDATE ON builds BEGIN
            SELECT RAISE(ABORT, 'build records are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS builds_no_delete
        BEFORE DELETE ON builds BEGIN
            SELECT RAISE(ABORT, 'build records are immutable');
        END;
        """
        with self.connect() as conn:
            conn.executescript(schema)

    def _latest_version_row(self, conn: sqlite3.Connection, claim_id: int) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM claim_versions WHERE claim_id = ? ORDER BY version_no DESC LIMIT 1",
            (claim_id,),
        ).fetchone()
        if row is None:
            raise KeyError(claim_ref(claim_id))
        return row

    def _status_for_claim(self, conn: sqlite3.Connection, claim_id: int) -> tuple[str, sqlite3.Row | None]:
        build = conn.execute(
            "SELECT * FROM builds WHERE claim_id = ? ORDER BY id DESC LIMIT 1", (claim_id,)
        ).fetchone()
        if build is not None:
            return str(build["status"]), build
        proof = conn.execute("SELECT id FROM proofs WHERE claim_id = ? LIMIT 1", (claim_id,)).fetchone()
        return ("partial" if proof else "open"), None

    def _serialize_version(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "version_id": version_ref(int(row["id"])),
            "version": int(row["version_no"]),
            "title": row["title"],
            "statement": row["statement"],
            "latex": row["latex"],
            "area": row["area"],
            "claim_author": row["claim_author"],
            "formal_declaration": row["formal_declaration"],
            "lean_name": row["lean_name"],
            "content_hash": row["content_hash"],
            "created_at": row["created_at"],
        }

    def create_claim(self, payload: dict[str, Any]) -> dict[str, Any]:
        title = str(payload.get("title") or "").strip()
        statement = str(payload.get("statement") or "").strip()
        declaration = str(payload.get("formal_declaration") or payload.get("declaration") or "").strip()
        lean_name = str(payload.get("lean_name") or "").strip()
        if not title or not statement or not declaration or not lean_name:
            raise ValueError("title, statement, formal_declaration, and lean_name are required")
        if not re.fullmatch(r"[^\s]+", lean_name):
            raise ValueError("lean_name must be a Lean declaration identifier")

        now = utc_now()
        version_payload = {
            "title": title,
            "statement": statement,
            "latex": str(payload.get("latex") or "").strip(),
            "area": str(payload.get("area") or "").strip(),
            "claim_author": str(payload.get("claim_author") or "").strip(),
            "formal_declaration": declaration,
            "lean_name": lean_name,
        }
        with self.connect() as conn:
            cur = conn.execute("INSERT INTO claims(created_at) VALUES (?)", (now,))
            cid = int(cur.lastrowid)
            vcur = conn.execute(
                """INSERT INTO claim_versions(
                    claim_id, version_no, title, statement, latex, area, claim_author,
                    formal_declaration, lean_name, content_hash, created_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cid,
                    version_payload["title"],
                    version_payload["statement"],
                    version_payload["latex"],
                    version_payload["area"],
                    version_payload["claim_author"],
                    version_payload["formal_declaration"],
                    version_payload["lean_name"],
                    canonical_hash(version_payload),
                    now,
                ),
            )
            vid = int(vcur.lastrowid)
            proof_id: int | None = None
            proof_source = str(payload.get("proof_source") or payload.get("proof") or "").strip()
            if proof_source:
                proof_id = self._insert_proof(
                    conn,
                    cid,
                    vid,
                    str(payload.get("proof_author") or "").strip(),
                    payload.get("imports") or ["Mathlib"],
                    proof_source,
                )
        result = self.get_claim(claim_ref(cid))
        result["created_proof_id"] = proof_ref(proof_id) if proof_id else None
        return result

    def create_version(self, claim_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        cid = parse_ref(claim_id, "CLM")
        now = utc_now()
        with self.connect() as conn:
            prev = self._latest_version_row(conn, cid)
            version_no = int(prev["version_no"]) + 1
            data = {
                "title": str(payload.get("title", prev["title"])).strip(),
                "statement": str(payload.get("statement", prev["statement"])).strip(),
                "latex": str(payload.get("latex", prev["latex"])).strip(),
                "area": str(payload.get("area", prev["area"])).strip(),
                "claim_author": str(payload.get("claim_author", prev["claim_author"])).strip(),
                "formal_declaration": str(payload.get("formal_declaration", prev["formal_declaration"])).strip(),
                "lean_name": str(payload.get("lean_name", prev["lean_name"])).strip(),
            }
            cur = conn.execute(
                """INSERT INTO claim_versions(
                    claim_id, version_no, title, statement, latex, area, claim_author,
                    formal_declaration, lean_name, content_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cid, version_no, data["title"], data["statement"], data["latex"], data["area"],
                    data["claim_author"], data["formal_declaration"], data["lean_name"], canonical_hash(data), now,
                ),
            )
            vid = int(cur.lastrowid)
        return {"claim_id": claim_ref(cid), "version_id": version_ref(vid), "version": version_no}

    def _insert_proof(
        self,
        conn: sqlite3.Connection,
        claim_id: int,
        version_id: int,
        proof_author: str,
        imports: Any,
        proof_source: str,
    ) -> int:
        if not isinstance(imports, list) or not all(isinstance(x, str) for x in imports):
            raise ValueError("imports must be a list of module names")
        imports = [x.strip() for x in imports if x.strip()] or ["Mathlib"]
        proof_payload = {
            "claim_id": claim_id,
            "claim_version_id": version_id,
            "proof_author": proof_author,
            "imports": imports,
            "proof_source": proof_source,
        }
        cur = conn.execute(
            """INSERT INTO proofs(
                claim_id, claim_version_id, proof_author, imports_json, proof_source, content_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                claim_id, version_id, proof_author, json.dumps(imports), proof_source,
                canonical_hash(proof_payload), utc_now(),
            ),
        )
        return int(cur.lastrowid)

    def create_proof(self, claim_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        cid = parse_ref(claim_id, "CLM")
        proof_source = str(payload.get("proof_source") or payload.get("proof") or "").strip()
        if not proof_source:
            raise ValueError("proof_source is required")
        with self.connect() as conn:
            version = self._latest_version_row(conn, cid)
            pid = self._insert_proof(
                conn,
                cid,
                int(version["id"]),
                str(payload.get("proof_author") or "").strip(),
                payload.get("imports") or ["Mathlib"],
                proof_source,
            )
        return self.get_proof(proof_ref(pid))

    def get_proof(self, proof_id: str) -> dict[str, Any]:
        pid = parse_ref(proof_id, "PRF")
        with self.connect() as conn:
            row = conn.execute(
                """SELECT p.*, cv.formal_declaration, cv.lean_name, cv.version_no
                   FROM proofs p JOIN claim_versions cv ON cv.id = p.claim_version_id
                   WHERE p.id = ?""",
                (pid,),
            ).fetchone()
            if row is None:
                raise KeyError(proof_id)
            return {
                "proof_id": proof_ref(pid),
                "claim_id": claim_ref(int(row["claim_id"])),
                "claim_version_id": version_ref(int(row["claim_version_id"])),
                "claim_version": int(row["version_no"]),
                "proof_author": row["proof_author"],
                "imports": json.loads(row["imports_json"]),
                "proof_source": row["proof_source"],
                "content_hash": row["content_hash"],
                "formal_declaration": row["formal_declaration"],
                "lean_name": row["lean_name"],
                "created_at": row["created_at"],
            }

    def store_build(
        self,
        proof_id: str,
        audit: dict[str, Any],
        environment: dict[str, Any],
    ) -> dict[str, Any]:
        pid = parse_ref(proof_id, "PRF")
        with self.connect() as conn:
            proof = conn.execute("SELECT * FROM proofs WHERE id = ?", (pid,)).fetchone()
            if proof is None:
                raise KeyError(proof_id)
            cid = int(proof["claim_id"])
            kernel_ok = bool(audit.get("ok"))
            contains_sorry = bool(audit.get("contains_sorry"))
            status = "failed" if not kernel_ok else ("partial" if contains_sorry else "verified")
            cur = conn.execute(
                """INSERT INTO builds(
                    claim_id, proof_id, status, kernel_ok, audit_ok, contains_sorry, uses_trust_compiler,
                    lean_version, lake_version, mathlib_revision, environment_hash, source_hash,
                    minimal_imports_json, dependencies_json, axioms_json, output, duration_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cid,
                    pid,
                    status,
                    1 if kernel_ok else 0,
                    None if audit.get("audit_ok") is None else (1 if audit.get("audit_ok") else 0),
                    1 if contains_sorry else 0,
                    1 if audit.get("uses_trust_compiler") else 0,
                    environment.get("lean_version"),
                    environment.get("lake_version"),
                    environment.get("mathlib_revision"),
                    str(environment.get("environment_hash") or ""),
                    str(audit.get("source_hash") or ""),
                    json.dumps(audit.get("minimal_imports") or []),
                    json.dumps(audit.get("dependencies") or []),
                    json.dumps(audit.get("axioms") or []),
                    str(audit.get("output") or ""),
                    int(audit.get("duration_ms") or 0),
                    utc_now(),
                ),
            )
            bid = int(cur.lastrowid)
        return self.get_build(build_ref(bid))

    def get_build(self, build_id: str) -> dict[str, Any]:
        bid = parse_ref(build_id, "BLD")
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM builds WHERE id = ?", (bid,)).fetchone()
            if row is None:
                raise KeyError(build_id)
            return self._serialize_build(row)

    def _serialize_build(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "build_id": build_ref(int(row["id"])),
            "claim_id": claim_ref(int(row["claim_id"])),
            "proof_id": proof_ref(int(row["proof_id"])),
            "status": row["status"],
            "kernel_ok": bool(row["kernel_ok"]),
            "audit_ok": None if row["audit_ok"] is None else bool(row["audit_ok"]),
            "contains_sorry": bool(row["contains_sorry"]),
            "uses_trust_compiler": bool(row["uses_trust_compiler"]),
            "lean_version": row["lean_version"],
            "lake_version": row["lake_version"],
            "mathlib_revision": row["mathlib_revision"],
            "environment_hash": row["environment_hash"],
            "source_hash": row["source_hash"],
            "minimal_imports": json.loads(row["minimal_imports_json"]),
            "dependencies": json.loads(row["dependencies_json"]),
            "axioms": json.loads(row["axioms_json"]),
            "output": row["output"],
            "duration_ms": int(row["duration_ms"]),
            "created_at": row["created_at"],
        }

    def list_claims(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT c.id AS claim_id, c.created_at AS claim_created_at, cv.*
                   FROM claims c
                   JOIN claim_versions cv ON cv.claim_id = c.id
                   WHERE cv.version_no = (
                       SELECT MAX(v2.version_no) FROM claim_versions v2 WHERE v2.claim_id = c.id
                   )
                   ORDER BY c.id DESC"""
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                cid = int(row["claim_id"])
                status, latest = self._status_for_claim(conn, cid)
                item = self._serialize_version(row)
                item.update(
                    {
                        "claim_id": claim_ref(cid),
                        "status": status,
                        "created_at": row["claim_created_at"],
                        "latest_build_id": build_ref(int(latest["id"])) if latest else None,
                    }
                )
                result.append(item)
            return result

    def get_claim(self, claim_id: str) -> dict[str, Any]:
        cid = parse_ref(claim_id, "CLM")
        with self.connect() as conn:
            identity = conn.execute("SELECT * FROM claims WHERE id = ?", (cid,)).fetchone()
            if identity is None:
                raise KeyError(claim_id)
            versions = conn.execute(
                "SELECT * FROM claim_versions WHERE claim_id = ? ORDER BY version_no DESC", (cid,)
            ).fetchall()
            proofs = conn.execute(
                "SELECT * FROM proofs WHERE claim_id = ? ORDER BY id DESC", (cid,)
            ).fetchall()
            status, latest = self._status_for_claim(conn, cid)
            proof_items: list[dict[str, Any]] = []
            for proof in proofs:
                builds = conn.execute(
                    "SELECT * FROM builds WHERE proof_id = ? ORDER BY id DESC", (proof["id"],)
                ).fetchall()
                proof_items.append(
                    {
                        "proof_id": proof_ref(int(proof["id"])),
                        "claim_version_id": version_ref(int(proof["claim_version_id"])),
                        "proof_author": proof["proof_author"],
                        "imports": json.loads(proof["imports_json"]),
                        "proof_source": proof["proof_source"],
                        "content_hash": proof["content_hash"],
                        "created_at": proof["created_at"],
                        "builds": [self._serialize_build(b) for b in builds],
                    }
                )
            # Compatibility read only. New Builds never add rows to claim_links.
            links = conn.execute(
                """SELECT cl.*, cv.title, cv.lean_name
                   FROM claim_links cl
                   JOIN claim_versions cv ON cv.claim_id = cl.depends_on_claim_id
                   WHERE cl.claim_id = ? AND cv.version_no = (
                       SELECT MAX(v2.version_no) FROM claim_versions v2 WHERE v2.claim_id = cl.depends_on_claim_id
                   )
                   ORDER BY cl.id DESC""",
                (cid,),
            ).fetchall()
            return {
                "claim_id": claim_ref(cid),
                "created_at": identity["created_at"],
                "status": status,
                "latest_build_id": build_ref(int(latest["id"])) if latest else None,
                "current": self._serialize_version(versions[0]),
                "versions": [self._serialize_version(v) for v in versions],
                "proofs": proof_items,
                "dependencies": [
                    {
                        "claim_id": claim_ref(int(link["depends_on_claim_id"])),
                        "title": link["title"],
                        "lean_name": link["lean_name"],
                        "relation": link["relation"],
                        "source_build_id": build_ref(int(link["source_build_id"])),
                    }
                    for link in links
                ],
            }

    def graph(self) -> dict[str, Any]:
        """Legacy compatibility graph over claim_links.

        The public API does not use this method; ArgumentIndex.neighborhood is the
        active mathematical graph.
        """
        claims = self.list_claims()
        with self.connect() as conn:
            edges = conn.execute(
                """SELECT DISTINCT claim_id, depends_on_claim_id, relation
                   FROM claim_links ORDER BY claim_id, depends_on_claim_id"""
            ).fetchall()
        return {
            "nodes": [
                {
                    "id": c["claim_id"],
                    "title": c["title"],
                    "status": c["status"],
                    "lean_name": c["lean_name"],
                }
                for c in claims
            ],
            "edges": [
                {
                    "from": claim_ref(int(e["depends_on_claim_id"])),
                    "to": claim_ref(int(e["claim_id"])),
                    "relation": e["relation"],
                }
                for e in edges
            ],
        }
