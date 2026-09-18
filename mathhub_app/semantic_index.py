from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from registry import build_ref, claim_ref, parse_ref, proof_ref, utc_now, version_ref


SEMANTIC_RELATIONS = {
    "uses-directly",
    "uses-transitively",
    "equivalent-to",
    "specializes",
    "generalizes",
    "alias-of",
}


class SemanticIndex:
    """Second-layer index over the append-only registry.

    The registry stores artifacts. This index stores what Lean says those artifacts
    mean *structurally*: environment-scoped formal fingerprints, direct/transitive
    constant dependencies, and Claim→Claim semantic edges backed by those facts.

    A fingerprint is deliberately scoped to an exact environment. Identical hashes
    in different Lean/mathlib environments are not silently treated as identity.
    Cross-environment equivalence must be asserted/proved separately.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS formal_bindings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id INTEGER NOT NULL REFERENCES claims(id),
            claim_version_id INTEGER NOT NULL REFERENCES claim_versions(id),
            source_build_id INTEGER REFERENCES builds(id),
            lean_name TEXT NOT NULL,
            environment_hash TEXT NOT NULL,
            type_hash TEXT NOT NULL,
            formal_fingerprint TEXT NOT NULL,
            origin TEXT NOT NULL DEFAULT 'local-build',
            created_at TEXT NOT NULL,
            UNIQUE(claim_version_id, source_build_id, lean_name, environment_hash, type_hash)
        );

        CREATE TABLE IF NOT EXISTS build_constant_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            build_id INTEGER NOT NULL REFERENCES builds(id),
            root_name TEXT NOT NULL,
            dependency_name TEXT NOT NULL,
            depth_kind TEXT NOT NULL CHECK(depth_kind IN ('direct', 'transitive')),
            created_at TEXT NOT NULL,
            UNIQUE(build_id, dependency_name, depth_kind)
        );

        CREATE TABLE IF NOT EXISTS semantic_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id INTEGER NOT NULL REFERENCES claims(id),
            related_claim_id INTEGER NOT NULL REFERENCES claims(id),
            relation TEXT NOT NULL,
            evidence_kind TEXT NOT NULL,
            source_build_id INTEGER REFERENCES builds(id),
            author TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(claim_id, related_claim_id, relation, evidence_kind, source_build_id)
        );

        CREATE INDEX IF NOT EXISTS idx_binding_name_env
          ON formal_bindings(lean_name, environment_hash);
        CREATE INDEX IF NOT EXISTS idx_binding_fp
          ON formal_bindings(formal_fingerprint, environment_hash);
        CREATE INDEX IF NOT EXISTS idx_constant_build
          ON build_constant_edges(build_id, depth_kind);
        CREATE INDEX IF NOT EXISTS idx_semantic_claim
          ON semantic_relations(claim_id, relation);

        CREATE TRIGGER IF NOT EXISTS formal_bindings_no_update
        BEFORE UPDATE ON formal_bindings BEGIN
            SELECT RAISE(ABORT, 'formal bindings are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS formal_bindings_no_delete
        BEFORE DELETE ON formal_bindings BEGIN
            SELECT RAISE(ABORT, 'formal bindings are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS constant_edges_no_update
        BEFORE UPDATE ON build_constant_edges BEGIN
            SELECT RAISE(ABORT, 'constant dependency records are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS constant_edges_no_delete
        BEFORE DELETE ON build_constant_edges BEGIN
            SELECT RAISE(ABORT, 'constant dependency records are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS semantic_relations_no_update
        BEFORE UPDATE ON semantic_relations BEGIN
            SELECT RAISE(ABORT, 'semantic relations are immutable');
        END;
        """
        with self.connect() as conn:
            conn.executescript(schema)

    def record_build_semantics(
        self,
        proof: dict[str, Any],
        build: dict[str, Any],
        audit: dict[str, Any],
        environment: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist identity/dependency facts emitted by one immutable Build."""
        cid = parse_ref(str(proof["claim_id"]), "CLM")
        vid = parse_ref(str(proof["claim_version_id"]), "VER")
        bid = parse_ref(str(build["build_id"]), "BLD")
        lean_name = str(proof["lean_name"])
        env_hash = str(environment.get("environment_hash") or "")
        type_hash = str(audit.get("type_hash") or "")
        fingerprint = str(audit.get("formal_fingerprint") or "")
        direct = list(dict.fromkeys(audit.get("direct_dependencies") or []))
        transitive = list(dict.fromkeys(audit.get("transitive_dependencies") or audit.get("dependencies") or []))
        now = utc_now()

        with self.connect() as conn:
            if type_hash and fingerprint and env_hash:
                conn.execute(
                    """INSERT OR IGNORE INTO formal_bindings(
                        claim_id, claim_version_id, source_build_id, lean_name,
                        environment_hash, type_hash, formal_fingerprint, origin, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'local-build', ?)""",
                    (cid, vid, bid, lean_name, env_hash, type_hash, fingerprint, now),
                )

            for dep in direct:
                if dep != lean_name:
                    conn.execute(
                        """INSERT OR IGNORE INTO build_constant_edges(
                            build_id, root_name, dependency_name, depth_kind, created_at
                        ) VALUES (?, ?, ?, 'direct', ?)""",
                        (bid, lean_name, dep, now),
                    )
            for dep in transitive:
                if dep != lean_name:
                    conn.execute(
                        """INSERT OR IGNORE INTO build_constant_edges(
                            build_id, root_name, dependency_name, depth_kind, created_at
                        ) VALUES (?, ?, ?, 'transitive', ?)""",
                        (bid, lean_name, dep, now),
                    )

            self._derive_claim_relations(conn, cid, bid, env_hash, direct, transitive)

        return self.build_semantics(build["build_id"])

    def _derive_claim_relations(
        self,
        conn: sqlite3.Connection,
        claim_id: int,
        build_id: int,
        environment_hash: str,
        direct: list[str],
        transitive: list[str],
    ) -> None:
        """Map Lean constant names to *built* Claims in the same environment.

        A naked theorem-name match is not enough. The target Claim must have a
        formal binding produced in this exact dependency environment.
        """
        direct_set = set(direct)
        transitive_set = set(transitive)
        candidates = direct_set | transitive_set
        if not candidates or not environment_hash:
            return
        placeholders = ",".join("?" for _ in candidates)
        rows = conn.execute(
            f"""SELECT DISTINCT claim_id, lean_name
                FROM formal_bindings
                WHERE environment_hash = ? AND lean_name IN ({placeholders})""",
            (environment_hash, *sorted(candidates)),
        ).fetchall()
        now = utc_now()
        for row in rows:
            dep_claim_id = int(row["claim_id"])
            dep_name = str(row["lean_name"])
            if dep_claim_id == claim_id:
                continue
            relation = "uses-directly" if dep_name in direct_set else "uses-transitively"
            conn.execute(
                """INSERT OR IGNORE INTO semantic_relations(
                    claim_id, related_claim_id, relation, evidence_kind,
                    source_build_id, author, note, created_at
                ) VALUES (?, ?, ?, 'lean-dependency', ?, '', '', ?)""",
                (claim_id, dep_claim_id, relation, build_id, now),
            )

    def add_relation(
        self,
        claim_id: str,
        related_claim_id: str,
        relation: str,
        author: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        relation = relation.strip().lower()
        if relation not in SEMANTIC_RELATIONS - {"uses-directly", "uses-transitively"}:
            raise ValueError(f"unsupported manual semantic relation: {relation}")
        cid = parse_ref(claim_id, "CLM")
        rid = parse_ref(related_claim_id, "CLM")
        if cid == rid:
            raise ValueError("a Claim cannot have a manual semantic relation to itself")
        with self.connect() as conn:
            for value in (cid, rid):
                if conn.execute("SELECT 1 FROM claims WHERE id = ?", (value,)).fetchone() is None:
                    raise KeyError(claim_ref(value))
            cur = conn.execute(
                """INSERT INTO semantic_relations(
                    claim_id, related_claim_id, relation, evidence_kind,
                    source_build_id, author, note, created_at
                ) VALUES (?, ?, ?, 'manual', NULL, ?, ?, ?)""",
                (cid, rid, relation, author.strip(), note.strip(), utc_now()),
            )
            sid = int(cur.lastrowid)
        return {"relation_id": f"REL-{sid:06d}", "claim_id": claim_ref(cid),
                "related_claim_id": claim_ref(rid), "relation": relation,
                "evidence_kind": "manual"}

    def build_semantics(self, build_id: str) -> dict[str, Any]:
        bid = parse_ref(build_id, "BLD")
        with self.connect() as conn:
            binding = conn.execute(
                "SELECT * FROM formal_bindings WHERE source_build_id = ? ORDER BY id DESC LIMIT 1",
                (bid,),
            ).fetchone()
            direct = conn.execute(
                """SELECT dependency_name FROM build_constant_edges
                   WHERE build_id = ? AND depth_kind = 'direct' ORDER BY dependency_name""",
                (bid,),
            ).fetchall()
            transitive = conn.execute(
                """SELECT dependency_name FROM build_constant_edges
                   WHERE build_id = ? AND depth_kind = 'transitive' ORDER BY dependency_name""",
                (bid,),
            ).fetchall()
            return {
                "build_id": build_ref(bid),
                "formal_binding": None if binding is None else self._binding(binding),
                "direct_dependencies": [r["dependency_name"] for r in direct],
                "transitive_dependencies": [r["dependency_name"] for r in transitive],
            }

    def claim_semantics(self, claim_id: str) -> dict[str, Any]:
        cid = parse_ref(claim_id, "CLM")
        with self.connect() as conn:
            if conn.execute("SELECT 1 FROM claims WHERE id = ?", (cid,)).fetchone() is None:
                raise KeyError(claim_id)
            bindings = conn.execute(
                "SELECT * FROM formal_bindings WHERE claim_id = ? ORDER BY id DESC", (cid,)
            ).fetchall()
            outgoing = self._relation_rows(conn, "sr.claim_id = ?", cid)
            incoming = self._relation_rows(conn, "sr.related_claim_id = ?", cid)

            matches: list[dict[str, Any]] = []
            seen: set[tuple[int, str, str]] = set()
            for binding in bindings:
                rows = conn.execute(
                    """SELECT fb.*, cv.title
                       FROM formal_bindings fb
                       JOIN claim_versions cv ON cv.id = fb.claim_version_id
                       WHERE fb.formal_fingerprint = ? AND fb.environment_hash = ?
                         AND fb.claim_id != ?""",
                    (binding["formal_fingerprint"], binding["environment_hash"], cid),
                ).fetchall()
                for row in rows:
                    key = (int(row["claim_id"]), str(row["formal_fingerprint"]), str(row["environment_hash"]))
                    if key in seen:
                        continue
                    seen.add(key)
                    matches.append({
                        "claim_id": claim_ref(int(row["claim_id"])),
                        "claim_version_id": version_ref(int(row["claim_version_id"])),
                        "title": row["title"],
                        "lean_name": row["lean_name"],
                        "formal_fingerprint": row["formal_fingerprint"],
                        "environment_hash": row["environment_hash"],
                    })
            return {
                "claim_id": claim_ref(cid),
                "bindings": [self._binding(r) for r in bindings],
                "outgoing": outgoing,
                "incoming": incoming,
                "same_formal_type": matches,
            }

    def _relation_rows(self, conn: sqlite3.Connection, where: str, cid: int) -> list[dict[str, Any]]:
        rows = conn.execute(
            f"""SELECT sr.*,
                       src.title AS source_title,
                       dst.title AS target_title
                FROM semantic_relations sr
                JOIN claim_versions src ON src.claim_id = sr.claim_id
                JOIN claim_versions dst ON dst.claim_id = sr.related_claim_id
                WHERE {where}
                  AND src.version_no = (SELECT MAX(v.version_no) FROM claim_versions v WHERE v.claim_id = sr.claim_id)
                  AND dst.version_no = (SELECT MAX(v.version_no) FROM claim_versions v WHERE v.claim_id = sr.related_claim_id)
                ORDER BY sr.id DESC""",
            (cid,),
        ).fetchall()
        return [self._relation(r) for r in rows]

    def _binding(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "binding_id": f"FML-{int(row['id']):06d}",
            "claim_id": claim_ref(int(row["claim_id"])),
            "claim_version_id": version_ref(int(row["claim_version_id"])),
            "source_build_id": build_ref(int(row["source_build_id"])) if row["source_build_id"] else None,
            "lean_name": row["lean_name"],
            "environment_hash": row["environment_hash"],
            "type_hash": row["type_hash"],
            "formal_fingerprint": row["formal_fingerprint"],
            "origin": row["origin"],
            "created_at": row["created_at"],
        }

    def _relation(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "relation_id": f"REL-{int(row['id']):06d}",
            "claim_id": claim_ref(int(row["claim_id"])),
            "claim_title": row["source_title"],
            "related_claim_id": claim_ref(int(row["related_claim_id"])),
            "related_claim_title": row["target_title"],
            "relation": row["relation"],
            "evidence_kind": row["evidence_kind"],
            "source_build_id": build_ref(int(row["source_build_id"])) if row["source_build_id"] else None,
            "author": row["author"],
            "note": row["note"],
            "created_at": row["created_at"],
        }

    def graph(self, registry: Any) -> dict[str, Any]:
        claims = registry.list_claims()
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT sr.* FROM semantic_relations sr
                   WHERE sr.id IN (
                     SELECT MAX(x.id) FROM semantic_relations x
                     GROUP BY x.claim_id, x.related_claim_id, x.relation, x.evidence_kind
                   ) ORDER BY sr.id"""
            ).fetchall()
            edges = []
            for row in rows:
                edges.append({
                    "from": claim_ref(int(row["related_claim_id"])),
                    "to": claim_ref(int(row["claim_id"])),
                    "relation": row["relation"],
                    "evidence_kind": row["evidence_kind"],
                    "source_build_id": build_ref(int(row["source_build_id"])) if row["source_build_id"] else None,
                })
        return {
            "nodes": [{
                "id": c["claim_id"], "title": c["title"], "status": c["status"],
                "lean_name": c["lean_name"], "area": c.get("area", "")
            } for c in claims],
            "edges": edges,
        }
