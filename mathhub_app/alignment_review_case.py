from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from audit_registry import AuditRegistry
from external_audit_ingest import ingest_manifest, validate_manifest

FORMAT = "mathhub-source-alignment-review-v1"
OBLIGATION_STATUSES = frozenset({
    "not-reviewed",
    "matched-by-inspection",
    "equivalence-obligation",
    "risk-flag",
    "mismatch",
    "discharged",
})


def _canonical_digest(payload: dict[str, Any]) -> str:
    copy = json.loads(json.dumps(payload, ensure_ascii=False))
    copy.pop("review_digest", None)
    encoded = json.dumps(copy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def validate_alignment_review(manifest: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    validate_manifest(manifest)
    if review.get("format") != FORMAT:
        raise ValueError(f"unsupported alignment review format: {review.get('format')!r}")
    if review.get("case_key") != manifest.get("case_key"):
        raise ValueError("alignment review does not belong to this manifest")

    expected_name = str(manifest.get("claim", {}).get("lean_name") or "").strip()
    claim_name = str(review.get("claim_lean_name") or "").strip()
    if not claim_name or claim_name != expected_name:
        raise ValueError("alignment review claim_lean_name must equal manifest claim.lean_name")

    source_key = str(review.get("source_statement_key") or "").strip()
    source_keys = {
        str(item.get("key") or "").strip()
        for item in manifest.get("source_statements", [])
        if isinstance(item, dict)
    }
    if not source_key or source_key not in source_keys:
        raise ValueError("alignment review source_statement_key is not registered by the manifest")

    obligations_raw = review.get("obligations")
    if not isinstance(obligations_raw, list) or not obligations_raw:
        raise ValueError("alignment review obligations must be a non-empty list")

    obligations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(obligations_raw):
        if not isinstance(raw, dict):
            raise ValueError(f"obligations[{index}] must be an object")
        oid = str(raw.get("id") or "").strip()
        status = str(raw.get("status") or "").strip().lower()
        if not oid or oid in seen:
            raise ValueError(f"duplicate/empty alignment obligation id: {oid!r}")
        if status not in OBLIGATION_STATUSES:
            raise ValueError(f"unsupported alignment obligation status: {status!r}")
        seen.add(oid)
        item = dict(raw)
        item["id"] = oid
        item["status"] = status
        obligations.append(item)

    counts = {status: 0 for status in sorted(OBLIGATION_STATUSES)}
    for item in obligations:
        counts[item["status"]] += 1

    if counts["mismatch"]:
        alignment_status = "mismatch"
        coverage_status = "issue-found"
    elif counts["risk-flag"] or counts["equivalence-obligation"] or counts["not-reviewed"]:
        alignment_status = "partially-reviewed"
        coverage_status = "partial"
    elif counts["matched-by-inspection"]:
        alignment_status = "aligned-with-notes"
        coverage_status = "partial"
    else:
        alignment_status = "aligned"
        coverage_status = "complete"

    normalized = json.loads(json.dumps(review, ensure_ascii=False))
    normalized["obligations"] = obligations
    normalized["derived"] = {
        "alignment_status": alignment_status,
        "coverage_status": coverage_status,
        "obligation_count": len(obligations),
        "status_counts": counts,
    }
    normalized["review_digest"] = _canonical_digest(normalized)
    return normalized


def _find_source_statement_id(ingest: dict[str, Any], key: str) -> str:
    matches = [
        str(item.get("source_statement_id"))
        for item in ingest.get("source_statements", [])
        if isinstance(item, dict) and str(item.get("key") or "") == key
    ]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one ingested source statement for key {key!r}")
    return matches[0]


def _latest_alignment_digest(
    audit: AuditRegistry,
    source_statement_id: str,
    claim_version_id: str,
) -> str:
    latest = audit.alignment_history(source_statement_id, claim_version_id).get("latest")
    if not isinstance(latest, dict):
        return ""
    evidence = latest.get("evidence")
    return str(evidence.get("alignment_review_digest") or "").strip() if isinstance(evidence, dict) else ""


def _latest_coverage_digest(audit: AuditRegistry, audit_case_id: str) -> str:
    latest = audit.get_audit_case(audit_case_id).get("coverage", {}).get("source-alignment")
    if not isinstance(latest, dict):
        return ""
    evidence = latest.get("evidence")
    return str(evidence.get("alignment_review_digest") or "").strip() if isinstance(evidence, dict) else ""


def _existing_mismatch_finding(
    audit: AuditRegistry,
    audit_case_id: str,
    digest: str,
    obligation_id: str,
) -> dict[str, Any] | None:
    for finding in audit.get_audit_case(audit_case_id).get("findings", []):
        if finding.get("finding_type") != "statement-mismatch":
            continue
        event = finding.get("latest_event")
        evidence = event.get("evidence") if isinstance(event, dict) else None
        if not isinstance(evidence, dict):
            continue
        if (
            evidence.get("alignment_review_digest") == digest
            and evidence.get("obligation_id") == obligation_id
        ):
            return finding
    return None


def persist_alignment_review(
    db_path: Path,
    manifest: dict[str, Any],
    review: dict[str, Any],
    *,
    ingest_fn: Callable[[Path, dict[str, Any]], dict[str, Any]] = ingest_manifest,
) -> dict[str, Any]:
    normalized = validate_alignment_review(manifest, review)
    ingest = ingest_fn(Path(db_path), manifest)
    audit_case_id = str(ingest["audit_case_id"])
    claim_version_id = str(ingest["claim_version_id"])
    source_statement_id = _find_source_statement_id(
        ingest, str(normalized["source_statement_key"])
    )
    audit = AuditRegistry(Path(db_path))
    digest = str(normalized["review_digest"])
    derived = normalized["derived"]

    alignment_created = False
    if _latest_alignment_digest(audit, source_statement_id, claim_version_id) != digest:
        alignment = audit.record_alignment(source_statement_id, claim_version_id, {
            "status": derived["alignment_status"],
            "scope": str(normalized.get("scope") or "").strip(),
            "reviewer": str(normalized.get("reviewer") or "MathHub alignment review").strip(),
            "note": (
                "Structured adversarial source-alignment review. Unresolved equivalence obligations remain explicit and prevent automatic alignment."
                if derived["alignment_status"] == "partially-reviewed"
                else "Structured adversarial source-alignment review."
            ),
            "evidence": {
                "alignment_review_digest": digest,
                "format": FORMAT,
                "derived": derived,
                "obligations": normalized["obligations"],
                "sources": normalized.get("sources") or {},
                "summary": normalized.get("summary") or {},
                "limitations": normalized.get("limitations") or [],
            },
        })
        alignment_created = True
    else:
        alignment = audit.alignment_history(source_statement_id, claim_version_id)["latest"]

    coverage_created = False
    if _latest_coverage_digest(audit, audit_case_id) != digest:
        coverage = audit.record_coverage(audit_case_id, {
            "dimension": "source-alignment",
            "status": derived["coverage_status"],
            "scope": str(normalized.get("scope") or "").strip(),
            "reviewer": str(normalized.get("reviewer") or "MathHub alignment review").strip(),
            "note": (
                "Clay-to-Lean semantic alignment remains partial because one or more obligations are unresolved."
                if derived["coverage_status"] == "partial"
                else (
                    "A confirmed mismatch is present in the Clay-to-Lean semantic alignment review."
                    if derived["coverage_status"] == "issue-found"
                    else "Every structured Clay-to-Lean alignment obligation is discharged."
                )
            ),
            "evidence": {
                "alignment_review_digest": digest,
                "source_statement_id": source_statement_id,
                "alignment_id": alignment.get("alignment_id"),
                "derived": derived,
            },
        })
        coverage_created = True
    else:
        coverage = audit.get_audit_case(audit_case_id)["coverage"]["source-alignment"]

    findings: list[dict[str, Any]] = []
    for obligation in normalized["obligations"]:
        if obligation["status"] != "mismatch":
            continue
        oid = obligation["id"]
        existing = _existing_mismatch_finding(audit, audit_case_id, digest, oid)
        if existing is not None:
            findings.append({"finding_id": existing.get("finding_id"), "created": False})
            continue
        finding = audit.create_finding(audit_case_id, {
            "finding_type": "statement-mismatch",
            "severity": "critical",
            "title": f"Confirmed Clay-to-Lean alignment mismatch: {oid}",
            "summary": str(obligation.get("analysis") or "A structured source-alignment obligation is marked as a confirmed mismatch."),
            "affected_claim_version_id": claim_version_id,
            "affected_source_statement_id": source_statement_id,
            "created_by": str(normalized.get("reviewer") or "MathHub alignment review"),
            "status": "open",
            "initial_note": "Generated from a structured Clay-to-Lean alignment review and requires independent reproduction/review.",
            "evidence": {
                "alignment_review_digest": digest,
                "obligation_id": oid,
                "obligation": obligation,
            },
        })
        findings.append({"finding_id": finding.get("finding_id"), "created": True})

    return {
        "audit_case_id": audit_case_id,
        "claim_version_id": claim_version_id,
        "source_statement_id": source_statement_id,
        "alignment_review_digest": digest,
        "alignment_id": alignment.get("alignment_id"),
        "alignment_created": alignment_created,
        "source_alignment_coverage_id": coverage.get("coverage_id"),
        "source_alignment_coverage_created": coverage_created,
        "derived": derived,
        "findings": findings,
        "independent_mathhub_proof_records": ingest.get("independent_mathhub_proof_records"),
        "independent_mathhub_build_records": ingest.get("independent_mathhub_build_records"),
        "verification_claimed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Persist a structured external mathematical source-to-Lean alignment review"
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("review", type=Path)
    parser.add_argument("--db", type=Path, default=Path(".mathub/registry.sqlite3"))
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    review = json.loads(args.review.read_text(encoding="utf-8"))
    result = persist_alignment_review(args.db, manifest, review)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if result["derived"]["coverage_status"] == "issue-found" else 0


if __name__ == "__main__":
    raise SystemExit(main())
