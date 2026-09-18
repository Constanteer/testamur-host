from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from argument_index import ArgumentIndex
from registry import build_ref, canonical_hash, parse_ref, proof_ref, version_ref
from trust import classify_axioms


SBOM_FORMAT = "mathhub-proof-sbom-v1"


class ProofSBOMBuilder:
    """Build a deterministic proof software/mathematics bill of materials.

    The SBOM is a read model over existing immutable evidence. It does not perform
    verification itself and must not be interpreted as a validity certificate.
    Missing provenance is represented explicitly in ``limitations`` rather than
    filled with guesses.
    """

    def __init__(self, path: Path, argument_index: ArgumentIndex | None = None) -> None:
        self.path = Path(path)
        self.argument = argument_index or ArgumentIndex(self.path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None

    def build(self, proof_id: str, build_id: str | None = None) -> dict[str, Any]:
        pid = parse_ref(proof_id, "PRF")
        exact_bid = parse_ref(build_id, "BLD") if build_id else None
        route = self.argument.proof_route(pid, exact_bid)

        verification_ref = None
        if route.get("verification"):
            verification_ref = route["verification"].get("build_id")
        selected_build_ref = build_id or verification_ref

        with self.connect() as conn:
            proof_row = conn.execute(
                """SELECT p.*,cv.title claim_title,cv.statement,cv.formal_declaration,
                          cv.lean_name,cv.content_hash claim_version_hash
                   FROM proofs p
                   JOIN claim_versions cv ON cv.id=p.claim_version_id
                   WHERE p.id=?""",
                (pid,),
            ).fetchone()
            if proof_row is None:
                raise KeyError(proof_id)

            build_row = None
            if selected_build_ref:
                bid = parse_ref(str(selected_build_ref), "BLD")
                build_row = conn.execute(
                    "SELECT * FROM builds WHERE id=? AND proof_id=?", (bid, pid)
                ).fetchone()
                if build_row is None:
                    raise KeyError(str(selected_build_ref))

            formal_sources = self._formal_sources(conn, int(proof_row["claim_version_id"]))
            alignments = self._alignment_snapshot(conn, int(proof_row["claim_version_id"]))

        limitations: list[str] = []
        if build_row is None:
            limitations.append("no verification Build is recorded for this Proof")
        if not route.get("argument_index_complete"):
            limitations.append("proof dependency instrumentation is incomplete or unavailable")
        if not formal_sources:
            limitations.append("no exact FormalSource record is attached to this Claim revision")
        if not alignments:
            limitations.append("no source-statement alignment review is recorded for this Claim revision")

        build_payload = self._build_payload(build_row)
        if build_row is not None:
            limitations.extend([
                "checker binary hash is not recorded by the current Build schema",
                "cache/artifact provenance is not recorded by the current Build schema",
                "host compiler/runtime provenance is not recorded by the current Build schema",
            ])

        document: dict[str, Any] = {
            "format": SBOM_FORMAT,
            "proof": {
                "proof_id": proof_ref(pid),
                "claim_id": route["claim_id"],
                "claim_version_id": version_ref(int(proof_row["claim_version_id"])),
                "claim_title": proof_row["claim_title"],
                "claim_version_hash": proof_row["claim_version_hash"],
                "formal_declaration": proof_row["formal_declaration"],
                "lean_name": proof_row["lean_name"],
                "proof_author": proof_row["proof_author"],
                "proof_source_hash": proof_row["content_hash"],
                "imports": json.loads(proof_row["imports_json"]),
            },
            "witnesses": {
                "verification_build_id": verification_ref,
                "argument_build_id": route.get("argument_build_id"),
                "argument_index_complete": bool(route.get("argument_index_complete")),
            },
            "build": build_payload,
            "mathematical_dependencies": route.get("depends_on") or [],
            "raw_constant_observations": route.get("raw_refs") or {
                "statement": [], "proof": [], "transitive": []
            },
            "formal_sources": formal_sources,
            "source_alignment": alignments,
            "artifact_provenance": {
                "checker_binary_hash": None,
                "cache_provenance": None,
                "host_compiler": None,
                "host_runtime": None,
            },
            "limitations": limitations,
        }
        # Hash the canonical document without a self-referential hash field.
        document["sbom_hash"] = canonical_hash(document)
        return document

    @staticmethod
    def to_json(document: dict[str, Any], *, pretty: bool = True) -> str:
        return json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
        )

    @staticmethod
    def _build_payload(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        axioms = json.loads(row["axioms_json"])
        return {
            "build_id": build_ref(int(row["id"])),
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
            "recorded_transitive_dependencies": json.loads(row["dependencies_json"]),
            "axioms": axioms,
            "trust": classify_axioms(axioms),
            "duration_ms": int(row["duration_ms"]),
            "created_at": row["created_at"],
        }

    def _formal_sources(self, conn: sqlite3.Connection, claim_version_id: int) -> list[dict[str, Any]]:
        if not self._table_exists(conn, "formal_sources"):
            return []
        rows = conn.execute(
            "SELECT * FROM formal_sources WHERE claim_version_id=? ORDER BY id",
            (claim_version_id,),
        ).fetchall()
        return [
            {
                "formal_source_id": f"SRC-{int(row['id']):06d}",
                "provider": row["provider"],
                "repository": row["repository"],
                "revision": row["revision"],
                "package": row["package"],
                "package_version": row["package_version"],
                "file_path": row["file_path"],
                "lean_name": row["lean_name"],
                "environment_hash": row["environment_hash"],
                "source_hash": row["source_hash"],
                "metadata": json.loads(row["metadata_json"]),
            }
            for row in rows
        ]

    def _alignment_snapshot(self, conn: sqlite3.Connection, claim_version_id: int) -> list[dict[str, Any]]:
        if not (
            self._table_exists(conn, "alignment_reviews")
            and self._table_exists(conn, "source_statements")
        ):
            return []
        rows = conn.execute(
            """SELECT ar.*,ss.title source_title,ss.source_kind,ss.content_hash source_content_hash,
                      ss.source_uri,ss.locator
               FROM alignment_reviews ar
               JOIN source_statements ss ON ss.id=ar.source_statement_id
               WHERE ar.claim_version_id=?
                 AND ar.id IN (
                   SELECT MAX(x.id) FROM alignment_reviews x
                   WHERE x.claim_version_id=?
                   GROUP BY x.source_statement_id
                 )
               ORDER BY ar.source_statement_id""",
            (claim_version_id, claim_version_id),
        ).fetchall()
        return [
            {
                "alignment_id": f"ALN-{int(row['id']):06d}",
                "source_statement_id": f"STM-{int(row['source_statement_id']):06d}",
                "source_title": row["source_title"],
                "source_kind": row["source_kind"],
                "source_content_hash": row["source_content_hash"],
                "source_uri": row["source_uri"],
                "locator": row["locator"],
                "status": row["status"],
                "scope": row["scope"],
                "reviewer": row["reviewer"],
                "note": row["note"],
                "evidence": json.loads(row["evidence_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
