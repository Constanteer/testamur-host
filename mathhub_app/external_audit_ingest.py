from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from audit_registry import AuditRegistry
from registry import Registry, claim_ref, version_ref

SCHEMA = "mathhub-external-audit-manifest-v1"


def _canon(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _required_mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def _required_text(parent: dict[str, Any], key: str) -> str:
    value = str(parent.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema") != SCHEMA:
        raise ValueError(f"unsupported manifest schema: {manifest.get('schema')!r}")
    _required_text(manifest, "case_key")
    claim = _required_mapping(manifest, "claim")
    for key in ("title", "statement", "formal_declaration", "lean_name"):
        _required_text(claim, key)
    source = _required_mapping(manifest, "formal_source")
    for key in ("provider", "revision", "file_path", "lean_name", "source_hash"):
        _required_text(source, key)
    audit_case = _required_mapping(manifest, "audit_case")
    _required_text(audit_case, "title")
    statements = manifest.get("source_statements", [])
    if not isinstance(statements, list):
        raise ValueError("source_statements must be a list")
    for i, item in enumerate(statements):
        if not isinstance(item, dict):
            raise ValueError(f"source_statements[{i}] must be an object")
        for key in ("source_kind", "title", "statement_text"):
            _required_text(item, key)
    coverage = audit_case.get("coverage", [])
    if not isinstance(coverage, list):
        raise ValueError("audit_case.coverage must be a list")


class ExternalAuditIngestor:
    """Idempotently bootstrap an external, commit-pinned result into MathHub.

    Importing publisher metadata is deliberately not verification. This class creates
    no Proof or Build records. It only registers the claimed statement, immutable
    provenance, reference statements, an AuditCase, and conservative bootstrap
    coverage. Independent MathHub builds/checks must be added by later workflows.
    """

    def __init__(self, db_path: Path) -> None:
        self.path = Path(db_path)
        # Registry must initialize the core tables before AuditRegistry adds FKs.
        self.registry = Registry(self.path)
        self.audit = AuditRegistry(self.path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @staticmethod
    def _claim_payload(raw: dict[str, Any]) -> dict[str, str]:
        return {
            "title": str(raw.get("title") or "").strip(),
            "statement": str(raw.get("statement") or "").strip(),
            "latex": str(raw.get("latex") or "").strip(),
            "area": str(raw.get("area") or "").strip(),
            "claim_author": str(raw.get("claim_author") or "").strip(),
            "formal_declaration": str(raw.get("formal_declaration") or "").strip(),
            "lean_name": str(raw.get("lean_name") or "").strip(),
        }

    def _find_claim_version(self, payload: dict[str, str]) -> sqlite3.Row | None:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT cv.* FROM claim_versions cv
                   WHERE cv.lean_name=? AND cv.formal_declaration=? AND cv.statement=?
                     AND cv.title=? AND cv.latex=? AND cv.area=? AND cv.claim_author=?
                   ORDER BY cv.id""",
                (
                    payload["lean_name"], payload["formal_declaration"], payload["statement"],
                    payload["title"], payload["latex"], payload["area"], payload["claim_author"],
                ),
            ).fetchall()
        if len(rows) > 1:
            raise ValueError("ambiguous exact Claim revision: duplicate registry rows")
        return rows[0] if rows else None

    def _ensure_claim(self, raw: dict[str, Any]) -> tuple[str, str, bool]:
        payload = self._claim_payload(raw)
        row = self._find_claim_version(payload)
        created = False
        if row is None:
            self.registry.create_claim(payload)
            row = self._find_claim_version(payload)
            if row is None:
                raise RuntimeError("Claim creation did not produce an exact revision")
            created = True
        return claim_ref(int(row["claim_id"])), version_ref(int(row["id"])), created

    def _ensure_formal_source(
        self, claim_version_id: str, raw: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        metadata_json = _canon(raw.get("metadata"))
        with self.connect() as conn:
            row = conn.execute(
                """SELECT * FROM formal_sources
                   WHERE claim_version_id=? AND provider=? AND repository=? AND revision=?
                     AND package=? AND package_version=? AND file_path=? AND lean_name=?
                     AND environment_hash=? AND source_hash=? AND metadata_json=?
                   ORDER BY id LIMIT 1""",
                (
                    int(claim_version_id.split("-", 1)[1]),
                    str(raw.get("provider") or "").strip(),
                    str(raw.get("repository") or "").strip(),
                    str(raw.get("revision") or "").strip(),
                    str(raw.get("package") or "").strip(),
                    str(raw.get("package_version") or "").strip(),
                    str(raw.get("file_path") or "").strip(),
                    str(raw.get("lean_name") or "").strip(),
                    str(raw.get("environment_hash") or "").strip(),
                    str(raw.get("source_hash") or "").strip(),
                    metadata_json,
                ),
            ).fetchone()
        if row is not None:
            return self.audit.get_formal_source(f"SRC-{int(row['id']):06d}"), False
        payload = dict(raw)
        payload["claim_version_id"] = claim_version_id
        return self.audit.create_formal_source(payload), True

    def _ensure_source_statement(self, raw: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        # AuditRegistry computes this content hash from the six source identity fields;
        # compare those directly so importer semantics stay independent of private helpers.
        fields = (
            str(raw.get("source_kind") or "").strip(),
            str(raw.get("title") or "").strip(),
            str(raw.get("statement_text") or "").strip(),
            str(raw.get("source_uri") or "").strip(),
            str(raw.get("locator") or "").strip(),
            str(raw.get("source_revision") or "").strip(),
            _canon(raw.get("metadata")),
        )
        with self.connect() as conn:
            row = conn.execute(
                """SELECT * FROM source_statements
                   WHERE source_kind=? AND title=? AND statement_text=? AND source_uri=?
                     AND locator=? AND source_revision=? AND metadata_json=?
                   ORDER BY id LIMIT 1""",
                fields,
            ).fetchone()
        if row is not None:
            return self.audit.get_source_statement(f"STM-{int(row['id']):06d}"), False
        return self.audit.create_source_statement(raw), True

    def _ensure_bootstrap_alignment(
        self, statement_id: str, claim_version_id: str, raw: dict[str, Any] | None
    ) -> tuple[dict[str, Any] | None, bool]:
        if not raw:
            return None, False
        history = self.audit.alignment_history(statement_id, claim_version_id)
        if history["latest"] is not None:
            # Never overwrite or append behind later human review on re-ingest.
            return history["latest"], False
        return self.audit.record_alignment(statement_id, claim_version_id, raw), True

    def _ensure_audit_case(self, claim_version_id: str, raw: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        vid = int(claim_version_id.split("-", 1)[1])
        title = str(raw.get("title") or "").strip()
        with self.connect() as conn:
            row = conn.execute(
                """SELECT id FROM audit_cases
                   WHERE headline_claim_version_id=? AND title=? ORDER BY id LIMIT 1""",
                (vid, title),
            ).fetchone()
        if row is not None:
            return self.audit.get_audit_case(f"AUD-{int(row['id']):06d}"), False
        payload = dict(raw)
        payload.pop("coverage", None)
        payload["headline_claim_version_id"] = claim_version_id
        return self.audit.create_audit_case(payload), True

    def _ensure_target(
        self, audit_case_id: str, target_kind: str, field: str, value: str, note: str
    ) -> bool:
        aid = int(audit_case_id.split("-", 1)[1])
        column = {
            "claim-version": "claim_version_id",
            "proof": "proof_id",
            "build": "build_id",
            "source-statement": "source_statement_id",
            "formal-source": "formal_source_id",
        }[target_kind]
        numeric = int(value.split("-", 1)[1])
        with self.connect() as conn:
            exists = conn.execute(
                f"SELECT 1 FROM audit_targets WHERE audit_case_id=? AND target_kind=? AND {column}=? LIMIT 1",
                (aid, target_kind, numeric),
            ).fetchone()
        if exists:
            return False
        self.audit.add_audit_target(audit_case_id, {
            "target_kind": target_kind,
            field: value,
            "note": note,
        })
        return True

    def _ensure_bootstrap_coverage(self, audit_case_id: str, raw: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        aid = int(audit_case_id.split("-", 1)[1])
        dimension = str(raw.get("dimension") or "").strip().lower()
        with self.connect() as conn:
            row = conn.execute(
                """SELECT * FROM audit_coverage_records
                   WHERE audit_case_id=? AND dimension=? ORDER BY id DESC LIMIT 1""",
                (aid, dimension),
            ).fetchone()
        if row is not None:
            case = self.audit.get_audit_case(audit_case_id)
            latest = case.get("coverage", {}).get(dimension)
            return latest or {"dimension": dimension, "status": row["status"]}, False
        return self.audit.record_coverage(audit_case_id, raw), True

    def ingest(self, manifest: dict[str, Any]) -> dict[str, Any]:
        validate_manifest(manifest)
        claim_id, claim_version_id, claim_created = self._ensure_claim(manifest["claim"])
        formal_source, formal_source_created = self._ensure_formal_source(
            claim_version_id, manifest["formal_source"]
        )
        audit_case, audit_case_created = self._ensure_audit_case(
            claim_version_id, manifest["audit_case"]
        )
        audit_case_id = str(audit_case["audit_case_id"])

        target_created = self._ensure_target(
            audit_case_id, "formal-source", "formal_source_id",
            str(formal_source["formal_source_id"]), "commit-pinned formal source",
        )

        statements_out: list[dict[str, Any]] = []
        for raw_statement in manifest.get("source_statements", []):
            statement_payload = {k: v for k, v in raw_statement.items() if k != "alignment" and k != "key"}
            statement, statement_created = self._ensure_source_statement(statement_payload)
            statement_id = str(statement["source_statement_id"])
            alignment, alignment_created = self._ensure_bootstrap_alignment(
                statement_id, claim_version_id, raw_statement.get("alignment")
            )
            statement_target_created = self._ensure_target(
                audit_case_id, "source-statement", "source_statement_id", statement_id,
                f"reference statement: {raw_statement.get('key') or statement.get('title')}",
            )
            statements_out.append({
                "key": raw_statement.get("key"),
                "source_statement_id": statement_id,
                "created": statement_created,
                "alignment_id": None if alignment is None else alignment.get("alignment_id"),
                "alignment_created": alignment_created,
                "target_created": statement_target_created,
            })

        coverage_out: list[dict[str, Any]] = []
        for raw in manifest["audit_case"].get("coverage", []):
            coverage, created = self._ensure_bootstrap_coverage(audit_case_id, raw)
            coverage_out.append({
                "dimension": raw.get("dimension"),
                "status": coverage.get("status"),
                "created": created,
            })

        with self.connect() as conn:
            proof_count = conn.execute(
                "SELECT COUNT(*) FROM proofs WHERE claim_version_id=?",
                (int(claim_version_id.split("-", 1)[1]),),
            ).fetchone()[0]
            build_count = conn.execute(
                """SELECT COUNT(*) FROM builds b JOIN proofs p ON p.id=b.proof_id
                   WHERE p.claim_version_id=?""",
                (int(claim_version_id.split("-", 1)[1]),),
            ).fetchone()[0]

        return {
            "schema": SCHEMA,
            "case_key": manifest["case_key"],
            "claim_id": claim_id,
            "claim_version_id": claim_version_id,
            "claim_created": claim_created,
            "formal_source_id": formal_source["formal_source_id"],
            "formal_source_created": formal_source_created,
            "audit_case_id": audit_case_id,
            "audit_case_created": audit_case_created,
            "formal_source_target_created": target_created,
            "source_statements": statements_out,
            "coverage": coverage_out,
            "independent_mathhub_proof_records": int(proof_count),
            "independent_mathhub_build_records": int(build_count),
            "verification_claimed_by_ingest": False,
        }


def ingest_manifest(db_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    return ExternalAuditIngestor(db_path).ingest(manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap a commit-pinned external result into a MathHub AuditCase")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--db", type=Path, default=Path(".mathub/registry.sqlite3"))
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    result = ingest_manifest(args.db, manifest)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
