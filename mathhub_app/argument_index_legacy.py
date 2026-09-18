from __future__ import annotations

import json
import sqlite3
from collections import deque
from pathlib import Path
from typing import Any

from build_selection import (
    active_argument_build_id_sql,
    active_argument_build_row,
    ensure_active_argument_build_index,
)
from dependency_index import ensure_proof_dependency_route_index
from ontology import CLAIM_KINDS, declaration_kind
from registry import build_ref, claim_ref, parse_ref, proof_ref, utc_now, version_ref
from trust import classify_axioms
from version_state import current_version_state


class ArgumentIndex:
    """Proof-specific mathematical dependency index.

    Source truth is Proof -> Claim. Claim -> Claim is only a projection.

    Statement refs are stored as formal vocabulary and never become mathematical
    edges. Proof refs may become ProofDependency records only when the Lean
    dependency audit completed successfully. Partial observations from a failed or
    timed-out audit remain provenance, but never masquerade as a complete graph.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS build_constant_refs(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  build_id INTEGER NOT NULL REFERENCES builds(id),
                  ref_kind TEXT NOT NULL CHECK(ref_kind IN('statement','proof','transitive')),
                  constant_name TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  UNIQUE(build_id,ref_kind,constant_name)
                );

                CREATE TABLE IF NOT EXISTS proof_claim_dependencies(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  proof_id INTEGER NOT NULL REFERENCES proofs(id),
                  source_build_id INTEGER NOT NULL REFERENCES builds(id),
                  target_claim_id INTEGER NOT NULL REFERENCES claims(id),
                  target_claim_version_id INTEGER NOT NULL REFERENCES claim_versions(id),
                  evidence_constant TEXT NOT NULL,
                  environment_hash TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  UNIQUE(source_build_id,target_claim_version_id,evidence_constant)
                );

                CREATE INDEX IF NOT EXISTS idx_arg_refs
                  ON build_constant_refs(build_id,ref_kind);
                CREATE INDEX IF NOT EXISTS idx_arg_target
                  ON proof_claim_dependencies(target_claim_id,target_claim_version_id);

                CREATE TRIGGER IF NOT EXISTS arg_refs_no_update
                BEFORE UPDATE ON build_constant_refs BEGIN
                  SELECT RAISE(ABORT,'constant observations are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS arg_refs_no_delete
                BEFORE DELETE ON build_constant_refs BEGIN
                  SELECT RAISE(ABORT,'constant observations are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS arg_deps_no_update
                BEFORE UPDATE ON proof_claim_dependencies BEGIN
                  SELECT RAISE(ABORT,'proof dependencies are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS arg_deps_no_delete
                BEFORE DELETE ON proof_claim_dependencies BEGIN
                  SELECT RAISE(ABORT,'proof dependencies are immutable');
                END;
                """
            )
            ensure_active_argument_build_index(conn)
            ensure_proof_dependency_route_index(conn)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @staticmethod
    def _uniq(values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        return list(dict.fromkeys(str(value) for value in values if str(value)))

    @staticmethod
    def _current_version(conn: sqlite3.Connection, claim_id: int) -> int:
        row = conn.execute(
            "SELECT id FROM claim_versions WHERE claim_id=? ORDER BY version_no DESC LIMIT 1",
            (claim_id,),
        ).fetchone()
        if not row:
            raise KeyError(claim_ref(claim_id))
        return int(row["id"])

    @staticmethod
    def _verification_build(conn: sqlite3.Connection, proof_id: int) -> sqlite3.Row | None:
        """Best proof-completeness witness, independent of dependency indexing."""
        return conn.execute(
            """SELECT * FROM builds WHERE proof_id=? ORDER BY
               CASE status WHEN 'verified' THEN 0 WHEN 'partial' THEN 1
                           WHEN 'failed' THEN 2 ELSE 3 END,
               id DESC LIMIT 1""",
            (proof_id,),
        ).fetchone()

    @staticmethod
    def _argument_build(conn: sqlite3.Connection, proof_id: int) -> sqlite3.Row | None:
        """Best Build whose dependency instrumentation completed successfully."""
        return active_argument_build_row(conn, proof_id)

    @staticmethod
    def _active_argument_build_sql(proof_alias: str = "p") -> str:
        return active_argument_build_id_sql(proof_alias)

    def record_build(
        self,
        proof: dict[str, Any],
        build: dict[str, Any],
        audit: dict[str, Any],
        environment: dict[str, Any],
    ) -> dict[str, Any]:
        proof_id = parse_ref(str(proof["proof_id"]), "PRF")
        build_id = parse_ref(str(build["build_id"]), "BLD")
        refs = {
            "statement": self._uniq(audit.get("statement_dependencies")),
            "proof": self._uniq(audit.get("proof_dependencies") or audit.get("direct_dependencies")),
            "transitive": self._uniq(audit.get("transitive_dependencies") or audit.get("dependencies")),
        }
        now = utc_now()
        environment_hash = str(environment.get("environment_hash") or "")
        with self.connect() as conn:
            for kind, names in refs.items():
                conn.executemany(
                    """INSERT OR IGNORE INTO build_constant_refs(
                       build_id,ref_kind,constant_name,created_at
                       ) VALUES(?,?,?,?)""",
                    [(build_id, kind, name, now) for name in names],
                )
            if environment_hash:
                self._reconcile(conn, environment_hash)
        return self.proof_route(proof_id, build_id)

    def _reconcile(self, conn: sqlite3.Connection, environment_hash: str) -> None:
        """Map successful proof-ref observations to proposition-bearing Claims.

        Old local databases may contain Claim-like records for `def` declarations
        from before the ontology boundary existed. We deliberately filter them out
        here so compatibility data cannot leak back into the mathematical graph.
        """
        rows = conn.execute(
            """SELECT DISTINCT
                 p.id proof_id,
                 r.build_id source_build_id,
                 fb.claim_id target_claim_id,
                 fb.claim_version_id target_version,
                 r.constant_name evidence,
                 target_cv.formal_declaration target_declaration
               FROM build_constant_refs r
               JOIN builds b ON b.id=r.build_id
               JOIN proofs p ON p.id=b.proof_id
               JOIN formal_bindings fb
                 ON fb.environment_hash=b.environment_hash
                AND fb.lean_name=r.constant_name
               JOIN claim_versions target_cv ON target_cv.id=fb.claim_version_id
               WHERE r.ref_kind='proof'
                 AND b.environment_hash=?
                 AND b.audit_ok=1
                 AND fb.claim_id!=p.claim_id""",
            (environment_hash,),
        ).fetchall()

        now = utc_now()
        values = []
        for row in rows:
            if declaration_kind(row["target_declaration"]) not in CLAIM_KINDS:
                continue
            values.append(
                (
                    int(row["proof_id"]),
                    int(row["source_build_id"]),
                    int(row["target_claim_id"]),
                    int(row["target_version"]),
                    str(row["evidence"]),
                    environment_hash,
                    now,
                )
            )
        conn.executemany(
            """INSERT OR IGNORE INTO proof_claim_dependencies(
               proof_id,source_build_id,target_claim_id,target_claim_version_id,
               evidence_constant,environment_hash,created_at
               ) VALUES(?,?,?,?,?,?,?)""",
            values,
        )

    @staticmethod
    def _raw(conn: sqlite3.Connection, build_id: int) -> dict[str, list[str]]:
        result = {"statement": [], "proof": [], "transitive": []}
        rows = conn.execute(
            """SELECT ref_kind,constant_name FROM build_constant_refs
               WHERE build_id=? ORDER BY ref_kind,constant_name""",
            (build_id,),
        )
        for row in rows:
            result[str(row["ref_kind"])].append(str(row["constant_name"]))
        return result

    @staticmethod
    def _deps(conn: sqlite3.Connection, proof_id: int, build_id: int) -> list[dict[str, Any]]:
        rows = conn.execute(
            """SELECT d.*,cv.title,cv.lean_name,cv.area,
                 (SELECT id FROM claim_versions x
                  WHERE x.claim_id=d.target_claim_id
                  ORDER BY version_no DESC LIMIT 1) current_vid
               FROM proof_claim_dependencies d
               JOIN claim_versions cv ON cv.id=d.target_claim_version_id
               WHERE d.proof_id=? AND d.source_build_id=?
               ORDER BY cv.title,d.evidence_constant""",
            (proof_id, build_id),
        ).fetchall()
        grouped: dict[tuple[int, int], dict[str, Any]] = {}
        for row in rows:
            key = (int(row["target_claim_id"]), int(row["target_claim_version_id"]))
            item = grouped.setdefault(
                key,
                {
                    "claim_id": claim_ref(key[0]),
                    "claim_version_id": version_ref(key[1]),
                    "title": row["title"],
                    "lean_name": row["lean_name"],
                    "area": row["area"],
                    "evidence_constants": [],
                    "active": key[1] == int(row["current_vid"]),
                },
            )
            item["evidence_constants"].append(str(row["evidence_constant"]))
        return list(grouped.values())

    @staticmethod
    def _verification_payload(build: sqlite3.Row | None) -> dict[str, Any] | None:
        if build is None:
            return None
        axioms = json.loads(build["axioms_json"])
        return {
            "build_id": build_ref(int(build["id"])),
            "status": build["status"],
            "kernel_ok": bool(build["kernel_ok"]),
            "audit_ok": None if build["audit_ok"] is None else bool(build["audit_ok"]),
            "lean_version": build["lean_version"],
            "mathlib_revision": build["mathlib_revision"],
            "environment_hash": build["environment_hash"],
            "axioms": axioms,
            **classify_axioms(axioms),
        }

    def proof_route(self, proof_id: int, build_id: int | None = None) -> dict[str, Any]:
        with self.connect() as conn:
            proof = conn.execute(
                """SELECT p.*,cv.version_no,cv.title claim_title,
                          cv.formal_declaration,cv.lean_name
                   FROM proofs p
                   JOIN claim_versions cv ON cv.id=p.claim_version_id
                   WHERE p.id=?""",
                (proof_id,),
            ).fetchone()
            if not proof:
                raise KeyError(proof_ref(proof_id))

            if build_id is not None:
                exact = conn.execute(
                    "SELECT * FROM builds WHERE id=? AND proof_id=?",
                    (build_id, proof_id),
                ).fetchone()
                verification_build = exact
                argument_build = exact if exact is not None and exact["audit_ok"] == 1 else None
                raw_build = exact
            else:
                verification_build = self._verification_build(conn, proof_id)
                argument_build = self._argument_build(conn, proof_id)
                raw_build = argument_build

            status = str(verification_build["status"]) if verification_build else "unbuilt"
            dependencies: list[dict[str, Any]] = []
            raw = {"statement": [], "proof": [], "transitive": []}
            argument_build_ref = None
            argument_complete = False

            if raw_build is not None:
                raw = self._raw(conn, int(raw_build["id"]))
            if argument_build is not None:
                argument_build_id = int(argument_build["id"])
                argument_build_ref = build_ref(argument_build_id)
                argument_complete = True
                dependencies = self._deps(conn, proof_id, argument_build_id)

            return {
                "proof_id": proof_ref(proof_id),
                "claim_id": claim_ref(int(proof["claim_id"])),
                "claim_version_id": version_ref(int(proof["claim_version_id"])),
                "claim_version": int(proof["version_no"]),
                "claim_title": proof["claim_title"],
                "proof_author": proof["proof_author"],
                "source_text": proof["proof_source"],
                "source_hash": proof["content_hash"],
                "imports": json.loads(proof["imports_json"]),
                "created_at": proof["created_at"],
                "formal_declaration": proof["formal_declaration"],
                "lean_name": proof["lean_name"],
                "status": status,
                "is_current_version": int(proof["claim_version_id"])
                == self._current_version(conn, int(proof["claim_id"])),
                "verification": self._verification_payload(verification_build),
                "argument_build_id": argument_build_ref,
                "argument_index_complete": argument_complete,
                "depends_on": dependencies,
                "raw_refs": raw,
            }

    def claim_argument(self, claim_id: int, *, proof_limit: int = 12) -> dict[str, Any]:
        proof_limit = max(1, min(int(proof_limit), 30))
        with self.connect() as conn:
            current = conn.execute(
                "SELECT * FROM claim_versions WHERE claim_id=? ORDER BY version_no DESC LIMIT 1",
                (claim_id,),
            ).fetchone()
            if not current:
                raise KeyError(claim_ref(claim_id))
            version_id = int(current["id"])
            state = current_version_state(conn, claim_id, version_id)
            proof_ids = [
                int(row["id"])
                for row in conn.execute(
                    """SELECT id FROM proofs
                       WHERE claim_id=? AND claim_version_id=?
                       ORDER BY id DESC LIMIT ?""",
                    (claim_id, version_id, proof_limit),
                )
            ]

        routes = [self.proof_route(proof_id) for proof_id in proof_ids]
        aggregate: dict[str, dict[str, Any]] = {}
        for route in routes:
            for dependency in route["depends_on"]:
                if not dependency["active"]:
                    continue
                item = aggregate.setdefault(
                    dependency["claim_id"],
                    {
                        "claim_id": dependency["claim_id"],
                        "title": dependency["title"],
                        "lean_name": dependency["lean_name"],
                        "area": dependency["area"],
                        "proof_ids": [],
                        "evidence_constants": [],
                    },
                )
                if route["proof_id"] not in item["proof_ids"]:
                    item["proof_ids"].append(route["proof_id"])
                for evidence in dependency["evidence_constants"]:
                    if evidence not in item["evidence_constants"]:
                        item["evidence_constants"].append(evidence)

        with self.connect() as conn:
            used_by = self._used_by(conn, claim_id, version_id)

        statement_refs: list[str] = []
        for route in routes:
            if route["argument_index_complete"]:
                statement_refs = route["raw_refs"]["statement"]
                break

        return {
            "claim": {
                "claim_id": claim_ref(claim_id),
                "title": current["title"],
                "statement": current["statement"],
                "latex": current["latex"],
                "area": current["area"],
                "claim_author": current["claim_author"],
                "formal_declaration": current["formal_declaration"],
                "lean_name": current["lean_name"],
                "status": state["status"],
            },
            "proofs": routes,
            "depends_on": list(aggregate.values()),
            "used_by": used_by,
            "statement_refs": statement_refs,
            "provenance": {
                "claim_version_id": version_ref(version_id),
                "verification_build_id": build_ref(state["verification_build_id"])
                if state["verification_build_id"]
                else None,
            },
        }

    def _used_by(
        self, conn: sqlite3.Connection, claim_id: int, version_id: int
    ) -> list[dict[str, Any]]:
        active_build = self._active_argument_build_sql("p")
        rows = conn.execute(
            f"""SELECT d.*,p.claim_id source_id,cv.title,cv.lean_name,cv.area
                FROM proof_claim_dependencies d
                JOIN proofs p ON p.id=d.proof_id
                JOIN claim_versions cv ON cv.claim_id=p.claim_id
                WHERE d.target_claim_id=?
                  AND d.target_claim_version_id=?
                  AND p.claim_version_id=(
                    SELECT id FROM claim_versions x
                    WHERE x.claim_id=p.claim_id ORDER BY version_no DESC LIMIT 1
                  )
                  AND d.source_build_id={active_build}
                  AND cv.version_no=(
                    SELECT MAX(version_no) FROM claim_versions x
                    WHERE x.claim_id=p.claim_id
                  )
                ORDER BY d.id DESC LIMIT 640""",
            (claim_id, version_id),
        ).fetchall()

        grouped: dict[int, dict[str, Any]] = {}
        for row in rows:
            source_id = int(row["source_id"])
            item = grouped.setdefault(
                source_id,
                {
                    "claim_id": claim_ref(source_id),
                    "title": row["title"],
                    "lean_name": row["lean_name"],
                    "area": row["area"],
                    "proof_ids": [],
                    "evidence_constants": [],
                },
            )
            proof = proof_ref(int(row["proof_id"]))
            evidence = str(row["evidence_constant"])
            if proof not in item["proof_ids"]:
                item["proof_ids"].append(proof)
            if evidence not in item["evidence_constants"]:
                item["evidence_constants"].append(evidence)
            if len(grouped) >= 80:
                break
        return list(grouped.values())

    def _pairs(self, conn: sqlite3.Connection, claim_id: int) -> list[tuple[int, int]]:
        active_build = self._active_argument_build_sql("p")
        return [
            (int(row["source_id"]), int(row["target_claim_id"]))
            for row in conn.execute(
                f"""SELECT DISTINCT p.claim_id source_id,d.target_claim_id
                    FROM proof_claim_dependencies d
                    JOIN proofs p ON p.id=d.proof_id
                    WHERE (p.claim_id=? OR d.target_claim_id=?)
                      AND p.claim_version_id=(
                        SELECT id FROM claim_versions x
                        WHERE x.claim_id=p.claim_id ORDER BY version_no DESC LIMIT 1
                      )
                      AND d.target_claim_version_id=(
                        SELECT id FROM claim_versions y
                        WHERE y.claim_id=d.target_claim_id ORDER BY version_no DESC LIMIT 1
                      )
                      AND d.source_build_id={active_build}""",
                (claim_id, claim_id),
            )
        ]

    def neighborhood(
        self,
        *,
        root_internal_id: int | None = None,
        depth: int = 1,
        limit: int = 80,
        relation_mode: str = "uses",
    ) -> dict[str, Any]:
        if relation_mode not in {"uses", "all"}:
            raise ValueError("argument graph currently exposes proof-derived uses edges only")
        depth = max(0, min(int(depth), 4))
        limit = max(1, min(int(limit), 200))

        with self.connect() as conn:
            if root_internal_id is None:
                ids = [
                    int(row["id"])
                    for row in conn.execute(
                        "SELECT id FROM claims ORDER BY id DESC LIMIT ?", (limit,)
                    )
                ]
                truncated = False
                mode = "recent"
            else:
                if not conn.execute(
                    "SELECT 1 FROM claims WHERE id=?", (root_internal_id,)
                ).fetchone():
                    raise KeyError(claim_ref(root_internal_id))
                seen = {root_internal_id}
                ids = [root_internal_id]
                queue: deque[tuple[int, int]] = deque([(root_internal_id, 0)])
                truncated = False
                while queue:
                    current, level = queue.popleft()
                    if level >= depth:
                        continue
                    for source, target in self._pairs(conn, current):
                        neighbor = target if source == current else source
                        if neighbor in seen:
                            continue
                        if len(ids) >= limit:
                            truncated = True
                            queue.clear()
                            break
                        seen.add(neighbor)
                        ids.append(neighbor)
                        queue.append((neighbor, level + 1))
                mode = "neighborhood"

            nodes = self._nodes(conn, ids)
            edges = self._edges(conn, ids)

        return {
            "mode": mode,
            "root": claim_ref(root_internal_id) if root_internal_id else None,
            "depth": depth,
            "node_limit": limit,
            "truncated": truncated,
            "nodes": nodes,
            "edges": edges,
        }

    @staticmethod
    def _nodes(conn: sqlite3.Connection, ids: list[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"""SELECT c.id cid,cv.id vid,cv.title,cv.lean_name,cv.area
                FROM claims c
                JOIN claim_versions cv ON cv.claim_id=c.id
                WHERE c.id IN({placeholders})
                  AND cv.version_no=(
                    SELECT MAX(version_no) FROM claim_versions x WHERE x.claim_id=c.id
                  )""",
            tuple(ids),
        ).fetchall()
        by_id: dict[int, dict[str, Any]] = {}
        for row in rows:
            claim_id = int(row["cid"])
            state = current_version_state(conn, claim_id, int(row["vid"]))
            by_id[claim_id] = {
                "id": claim_ref(claim_id),
                "title": row["title"],
                "lean_name": row["lean_name"],
                "area": row["area"],
                "status": state["status"],
            }
        return [by_id[claim_id] for claim_id in ids if claim_id in by_id]

    def _edges(self, conn: sqlite3.Connection, ids: list[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        active_build = self._active_argument_build_sql("p")
        rows = conn.execute(
            f"""SELECT
                  p.claim_id source_id,
                  d.target_claim_id,
                  COUNT(DISTINCT p.id) proof_count,
                  GROUP_CONCAT(DISTINCT p.id) proof_ids
                FROM proof_claim_dependencies d
                JOIN proofs p ON p.id=d.proof_id
                WHERE p.claim_id IN({placeholders})
                  AND d.target_claim_id IN({placeholders})
                  AND p.claim_version_id=(
                    SELECT id FROM claim_versions x
                    WHERE x.claim_id=p.claim_id ORDER BY version_no DESC LIMIT 1
                  )
                  AND d.target_claim_version_id=(
                    SELECT id FROM claim_versions y
                    WHERE y.claim_id=d.target_claim_id ORDER BY version_no DESC LIMIT 1
                  )
                  AND d.source_build_id={active_build}
                GROUP BY p.claim_id,d.target_claim_id""",
            (*ids, *ids),
        ).fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            proof_ids = [
                proof_ref(int(value))
                for value in str(row["proof_ids"] or "").split(",")
                if value
            ]
            result.append(
                {
                    # Public direction: dependent Claim -> dependency Claim.
                    "from": claim_ref(int(row["source_id"])),
                    "to": claim_ref(int(row["target_claim_id"])),
                    "relation": "uses",
                    "evidence_kind": "proof-dependency",
                    "proof_count": int(row["proof_count"]),
                    "proof_ids": proof_ids,
                    "proof_id": proof_ids[0] if proof_ids else None,
                }
            )
        return result
