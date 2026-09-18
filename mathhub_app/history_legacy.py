from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from registry import build_ref, claim_ref, proof_ref, version_ref
from trust import classify_axioms
from version_state import current_version_state


class HistoryReader:
    """Bounded read API for long-lived Claim histories.

    A Claim may accumulate many Proofs and far more Builds. Initial detail reads
    therefore include only a recent window; older Proof/Build records are exposed
    through cursor-paginated methods. Potentially large compiler logs are not
    embedded in history summaries and are fetched from the Build artifact endpoint.
    Only the newest Proof attached to the *current Claim Version* is embedded for
    the workbench; old-version proofs remain immutable history, never current proof.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _bounded(limit: int, maximum: int = 100) -> int:
        return max(1, min(int(limit), maximum))

    @staticmethod
    def _serialize_version(row: sqlite3.Row) -> dict[str, Any]:
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

    @staticmethod
    def _serialize_proof(row: sqlite3.Row, *, include_source: bool = False) -> dict[str, Any]:
        result = {
            "proof_id": proof_ref(int(row["id"])),
            "claim_id": claim_ref(int(row["claim_id"])),
            "claim_version_id": version_ref(int(row["claim_version_id"])),
            "proof_author": row["proof_author"],
            "imports": json.loads(row["imports_json"]),
            "content_hash": row["content_hash"],
            "created_at": row["created_at"],
            "proof_source_available": bool(row["proof_source"]),
        }
        if include_source:
            result["proof_source"] = row["proof_source"]
        return result

    @staticmethod
    def _serialize_build(row: sqlite3.Row, *, include_output: bool = False) -> dict[str, Any]:
        axioms = json.loads(row["axioms_json"])
        result = {
            "build_id": build_ref(int(row["id"])),
            "claim_id": claim_ref(int(row["claim_id"])),
            "proof_id": proof_ref(int(row["proof_id"])),
            "status": row["status"],
            "kernel_ok": bool(row["kernel_ok"]),
            "audit_ok": None if row["audit_ok"] is None else bool(row["audit_ok"]),
            "contains_sorry": bool(row["contains_sorry"]),
            "lean_version": row["lean_version"],
            "lake_version": row["lake_version"],
            "mathlib_revision": row["mathlib_revision"],
            "environment_hash": row["environment_hash"],
            "source_hash": row["source_hash"],
            "minimal_imports": json.loads(row["minimal_imports_json"]),
            "dependencies": json.loads(row["dependencies_json"]),
            "axioms": axioms,
            "duration_ms": int(row["duration_ms"]),
            "created_at": row["created_at"],
            "output_available": bool(row["output"]),
            **classify_axioms(axioms),
        }
        if include_output:
            result["output"] = row["output"]
        return result

    def claim_detail(
        self,
        claim_internal_id: int,
        *,
        version_limit: int = 12,
        proof_limit: int = 12,
        builds_per_proof: int = 6,
    ) -> dict[str, Any]:
        version_limit = self._bounded(version_limit, 50)
        proof_limit = self._bounded(proof_limit, 50)
        builds_per_proof = self._bounded(builds_per_proof, 30)

        with self.connect() as conn:
            identity = conn.execute(
                "SELECT * FROM claims WHERE id = ?", (claim_internal_id,)
            ).fetchone()
            if identity is None:
                raise KeyError(claim_ref(claim_internal_id))

            versions = conn.execute(
                "SELECT * FROM claim_versions WHERE claim_id = ? ORDER BY id DESC LIMIT ?",
                (claim_internal_id, version_limit + 1),
            ).fetchall()
            visible_versions = versions[:version_limit]
            if not visible_versions:
                raise KeyError(claim_ref(claim_internal_id))
            current_row = visible_versions[0]
            state = current_version_state(conn, claim_internal_id, int(current_row["id"]))

            proof_rows = conn.execute(
                "SELECT * FROM proofs WHERE claim_id = ? ORDER BY id DESC LIMIT ?",
                (claim_internal_id, proof_limit + 1),
            ).fetchall()
            visible_proofs = proof_rows[:proof_limit]
            proof_items: list[dict[str, Any]] = []
            for proof in visible_proofs:
                pid = int(proof["id"])
                build_rows = conn.execute(
                    "SELECT * FROM builds WHERE proof_id = ? ORDER BY id DESC LIMIT ?",
                    (pid, builds_per_proof + 1),
                ).fetchall()
                visible_builds = build_rows[:builds_per_proof]
                item = self._serialize_proof(
                    proof,
                    include_source=(pid == state["current_proof_id"]),
                )
                item["is_current_version"] = int(proof["claim_version_id"]) == int(current_row["id"])
                item["builds"] = [self._serialize_build(row) for row in visible_builds]
                item["build_history"] = {
                    "has_more": len(build_rows) > builds_per_proof,
                    "next_cursor": build_ref(int(visible_builds[-1]["id"]))
                        if len(build_rows) > builds_per_proof and visible_builds else None,
                }
                proof_items.append(item)

            links = conn.execute(
                """SELECT cl.*, cv.title, cv.lean_name
                   FROM claim_links cl
                   JOIN claim_versions cv ON cv.claim_id = cl.depends_on_claim_id
                   WHERE cl.claim_id = ? AND cv.version_no = (
                       SELECT MAX(v2.version_no) FROM claim_versions v2
                       WHERE v2.claim_id = cl.depends_on_claim_id
                   )
                   ORDER BY cl.id DESC LIMIT 100""",
                (claim_internal_id,),
            ).fetchall()

            current = self._serialize_version(current_row)
            return {
                "claim_id": claim_ref(claim_internal_id),
                "created_at": identity["created_at"],
                "status": state["status"],
                "latest_build_id": build_ref(state["latest_build_id"]) if state["latest_build_id"] else None,
                "verification_build_id": build_ref(state["verification_build_id"]) if state["verification_build_id"] else None,
                "current_proof_id": proof_ref(state["current_proof_id"]) if state["current_proof_id"] else None,
                "current": current,
                "versions": [self._serialize_version(row) for row in visible_versions],
                "version_history": {
                    "has_more": len(versions) > version_limit,
                    "next_cursor": version_ref(int(visible_versions[-1]["id"]))
                        if len(versions) > version_limit else None,
                },
                "proofs": proof_items,
                "proof_history": {
                    "has_more": len(proof_rows) > proof_limit,
                    "next_cursor": proof_ref(int(visible_proofs[-1]["id"]))
                        if len(proof_rows) > proof_limit and visible_proofs else None,
                },
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

    def proof_artifact(self, proof_internal_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT p.*, cv.version_no, cv.formal_declaration, cv.lean_name
                   FROM proofs p JOIN claim_versions cv ON cv.id = p.claim_version_id
                   WHERE p.id = ?""",
                (proof_internal_id,),
            ).fetchone()
            if row is None:
                raise KeyError(proof_ref(proof_internal_id))
            result = self._serialize_proof(row, include_source=True)
            result.update({
                "claim_version": int(row["version_no"]),
                "formal_declaration": row["formal_declaration"],
                "lean_name": row["lean_name"],
            })
            return result

    def build_artifact(self, build_internal_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM builds WHERE id = ?", (build_internal_id,)).fetchone()
            if row is None:
                raise KeyError(build_ref(build_internal_id))
            return self._serialize_build(row, include_output=True)

    def proof_page(
        self,
        claim_internal_id: int,
        *,
        limit: int = 20,
        before_proof_internal_id: int | None = None,
        builds_per_proof: int = 3,
    ) -> dict[str, Any]:
        limit = self._bounded(limit, 100)
        builds_per_proof = self._bounded(builds_per_proof, 20)
        with self.connect() as conn:
            current = conn.execute(
                "SELECT id FROM claim_versions WHERE claim_id = ? ORDER BY version_no DESC LIMIT 1",
                (claim_internal_id,),
            ).fetchone()
            if not current:
                raise KeyError(claim_ref(claim_internal_id))
            current_version_id = int(current["id"])
            rows = conn.execute(
                """SELECT * FROM proofs
                   WHERE claim_id = ? AND (? IS NULL OR id < ?)
                   ORDER BY id DESC LIMIT ?""",
                (claim_internal_id, before_proof_internal_id, before_proof_internal_id, limit + 1),
            ).fetchall()
            visible = rows[:limit]
            items: list[dict[str, Any]] = []
            for proof in visible:
                builds = conn.execute(
                    "SELECT * FROM builds WHERE proof_id = ? ORDER BY id DESC LIMIT ?",
                    (int(proof["id"]), builds_per_proof + 1),
                ).fetchall()
                visible_builds = builds[:builds_per_proof]
                item = self._serialize_proof(proof, include_source=False)
                item["is_current_version"] = int(proof["claim_version_id"]) == current_version_id
                item["builds"] = [self._serialize_build(row) for row in visible_builds]
                item["build_history"] = {
                    "has_more": len(builds) > builds_per_proof,
                    "next_cursor": build_ref(int(visible_builds[-1]["id"]))
                        if len(builds) > builds_per_proof and visible_builds else None,
                }
                items.append(item)
            return {
                "proofs": items,
                "has_more": len(rows) > limit,
                "next_cursor": proof_ref(int(visible[-1]["id"]))
                    if len(rows) > limit and visible else None,
                "page_size": len(items),
            }

    def build_page(
        self,
        proof_internal_id: int,
        *,
        limit: int = 30,
        before_build_internal_id: int | None = None,
    ) -> dict[str, Any]:
        limit = self._bounded(limit, 100)
        with self.connect() as conn:
            proof = conn.execute(
                "SELECT 1 FROM proofs WHERE id = ?", (proof_internal_id,)
            ).fetchone()
            if not proof:
                raise KeyError(proof_ref(proof_internal_id))
            rows = conn.execute(
                """SELECT * FROM builds
                   WHERE proof_id = ? AND (? IS NULL OR id < ?)
                   ORDER BY id DESC LIMIT ?""",
                (proof_internal_id, before_build_internal_id, before_build_internal_id, limit + 1),
            ).fetchall()
        visible = rows[:limit]
        return {
            "builds": [self._serialize_build(row) for row in visible],
            "has_more": len(rows) > limit,
            "next_cursor": build_ref(int(visible[-1]["id"]))
                if len(rows) > limit and visible else None,
            "page_size": len(visible),
        }
