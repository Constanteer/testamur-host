from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from alignment_review_case import persist_alignment_review
from external_audit_ingest import ingest_manifest, validate_manifest
from proof_audit_case import run_case_proof_audit
from reference_audit_case import run_case_reference_audit
from semantic_audit_case import run_case_semantic_audit

FORMAT = "mathhub-audit-case-run-v1"


def _load_review(value: Path | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return json.loads(Path(value).read_text(encoding="utf-8"))


def run_audit_case(
    manifest: dict[str, Any],
    checkout: Path,
    *,
    db_path: Path,
    alignment_reviews: list[Path | dict[str, Any]] | None = None,
    ingest_fn: Callable[[Path, dict[str, Any]], dict[str, Any]] = ingest_manifest,
    reference_fn: Callable[..., dict[str, Any]] = run_case_reference_audit,
    semantic_fn: Callable[..., dict[str, Any]] = run_case_semantic_audit,
    proof_fn: Callable[..., dict[str, Any]] = run_case_proof_audit,
    alignment_fn: Callable[..., dict[str, Any]] = persist_alignment_review,
) -> dict[str, Any]:
    """Run configured audit dimensions without collapsing them into one trust score.

    The orchestrator is coordination only. Each sub-run remains independently
    content-addressed and persists its own append-only evidence. In particular,
    successful completion never creates an external Proof/Build record and never
    claims the mathematical result has been independently verified.
    """
    validate_manifest(manifest)
    checkout = Path(checkout)
    db_path = Path(db_path)

    bootstrap = ingest_fn(db_path, manifest)
    dimensions: dict[str, Any] = {}

    if isinstance(manifest.get("reference_probe"), dict):
        dimensions["reference"] = reference_fn(
            checkout, manifest, db_path=db_path
        )

    if isinstance(manifest.get("semantic_probe"), dict):
        dimensions["semantic"] = semantic_fn(
            checkout, manifest, db_path=db_path
        )

    if isinstance(manifest.get("proof_probe"), dict):
        dimensions["proof"] = proof_fn(
            checkout, manifest, db_path=db_path
        )

    review_results: list[dict[str, Any]] = []
    for review_value in alignment_reviews or []:
        review = _load_review(review_value)
        review_results.append(alignment_fn(db_path, manifest, review))
    if review_results:
        dimensions["alignment_reviews"] = review_results

    issue_signals: list[dict[str, str]] = []

    reference = dimensions.get("reference")
    if isinstance(reference, dict):
        summary = (reference.get("reference_audit") or {}).get("summary") or {}
        if summary.get("forbidden_import_reachable"):
            issue_signals.append({
                "dimension": "reference",
                "signal": "forbidden-reference-import",
            })
        for key in summary.get("parity_mismatch_groups") or []:
            issue_signals.append({
                "dimension": "reference",
                "signal": f"parity-mismatch:{key}",
            })

    proof = dimensions.get("proof")
    if isinstance(proof, dict):
        closure = (proof.get("proof_audit") or {}).get("axiom_closure") or {}
        for axiom in closure.get("unexpected_axioms") or []:
            issue_signals.append({
                "dimension": "axiom-closure",
                "signal": f"unexpected-axiom:{axiom}",
            })

    for item in review_results:
        if (item.get("derived") or {}).get("coverage_status") == "issue-found":
            issue_signals.append({
                "dimension": "source-alignment",
                "signal": "confirmed-alignment-mismatch",
            })

    return {
        "format": FORMAT,
        "case_key": manifest["case_key"],
        "audit_case_id": bootstrap.get("audit_case_id"),
        "bootstrap": bootstrap,
        "dimensions": dimensions,
        "issue_signals": issue_signals,
        "has_issue_signal": bool(issue_signals),
        "verification_claimed": False,
        "clay_semantic_equivalence_claimed": False,
        "overall_trust_score": None,
        "interpretation": (
            "All configured audit workflows completed. The absence of an issue signal is not a proof of correctness; inspect per-dimension coverage and unresolved obligations."
            if not issue_signals else
            "One or more configured audit workflows produced an adversarial issue signal. Inspect the corresponding immutable evidence/finding before drawing a mathematical conclusion."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run all configured MathHub AuditCase evidence workflows without synthesizing a trust score"
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--db", type=Path, default=Path(".mathub/registry.sqlite3"))
    parser.add_argument(
        "--alignment-review",
        dest="alignment_reviews",
        action="append",
        type=Path,
        default=[],
        help="structured source-alignment review JSON; repeatable",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    result = run_audit_case(
        manifest,
        args.checkout,
        db_path=args.db,
        alignment_reviews=args.alignment_reviews,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 1 if result["has_issue_signal"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
