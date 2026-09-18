from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from audit_registry import AuditRegistry
from external_audit_ingest import ingest_manifest, validate_manifest
from external_proof_audit import build_external_proof_audit

FORMAT = "mathhub-proof-audit-case-run-v1"


def _required_text(parent: dict[str, Any], key: str) -> str:
    value = str(parent.get(key) or "").strip()
    if not value:
        raise ValueError(f"proof_probe.{key} is required")
    return value


def proof_probe_config(manifest: dict[str, Any]) -> dict[str, Any]:
    raw = manifest.get("proof_probe")
    if not isinstance(raw, dict):
        raise ValueError("manifest.proof_probe must be an object")

    imports_raw = raw.get("imports")
    if not isinstance(imports_raw, list):
        raise ValueError("proof_probe.imports must be a list")
    imports = list(dict.fromkeys(str(x).strip() for x in imports_raw if str(x).strip()))
    if not imports:
        raise ValueError("proof_probe.imports must contain at least one module")

    root = _required_text(raw, "root")
    claim_name = str(manifest.get("claim", {}).get("lean_name") or "").strip()
    source_name = str(manifest.get("formal_source", {}).get("lean_name") or "").strip()
    if root != claim_name or root != source_name:
        raise ValueError("proof_probe.root must equal both claim.lean_name and formal_source.lean_name")

    prefixes_raw = raw.get("expand_prefixes", [])
    if not isinstance(prefixes_raw, list):
        raise ValueError("proof_probe.expand_prefixes must be a list")
    prefixes = list(dict.fromkeys(str(x).strip() for x in prefixes_raw if str(x).strip()))

    permitted_raw = raw.get("permitted_axioms")
    if not isinstance(permitted_raw, list):
        raise ValueError("proof_probe.permitted_axioms must be a list")
    permitted = sorted(dict.fromkeys(str(x).strip() for x in permitted_raw if str(x).strip()))

    publisher_raw = manifest.get("formal_source", {}).get("metadata", {}).get(
        "publisher_declared_axioms"
    )
    publisher_axioms = None
    if isinstance(publisher_raw, list):
        publisher_axioms = sorted(dict.fromkeys(
            str(x).strip() for x in publisher_raw if str(x).strip()
        ))

    def positive_int(key: str, default: int) -> int:
        value = int(raw.get(key, default))
        if value < 1:
            raise ValueError(f"proof_probe.{key} must be >= 1")
        return value

    return {
        "imports": imports,
        "root": root,
        "expand_prefixes": prefixes,
        "permitted_axioms": permitted,
        "publisher_declared_axioms": publisher_axioms,
        "max_depth": positive_int("max_depth", 64),
        "max_nodes": positive_int("max_nodes", 5000),
        "batch_size": positive_int("batch_size", 64),
        "timeout_seconds": positive_int("timeout_seconds", 120),
        "scope_note": str(raw.get("scope_note") or "").strip(),
    }


def build_case_proof_audit(
    checkout: Path,
    manifest: dict[str, Any],
    *,
    build_fn: Callable[..., dict[str, Any]] = build_external_proof_audit,
) -> dict[str, Any]:
    validate_manifest(manifest)
    config = proof_probe_config(manifest)
    source = manifest["formal_source"]
    proof = build_fn(
        Path(checkout),
        config["imports"],
        config["root"],
        expected_revision=str(source.get("revision") or "").strip(),
        expected_source_path=str(source.get("file_path") or "").strip(),
        expected_source_hash=str(source.get("source_hash") or "").strip(),
        expand_prefixes=config["expand_prefixes"],
        permitted_axioms=config["permitted_axioms"],
        publisher_declared_axioms=config["publisher_declared_axioms"],
        max_depth=config["max_depth"],
        max_nodes=config["max_nodes"],
        batch_size=config["batch_size"],
        timeout_seconds=config["timeout_seconds"],
    )
    return {
        "format": FORMAT,
        "manifest_schema": manifest["schema"],
        "case_key": manifest["case_key"],
        "proof_probe": config,
        "proof_audit": proof,
    }


def _sanitise_for_registry(proof: dict[str, Any]) -> dict[str, Any]:
    copy = json.loads(json.dumps(proof, ensure_ascii=False))
    checkout = copy.get("checkout")
    if isinstance(checkout, dict):
        checkout.pop("checkout", None)
    return copy


def _latest_digest(audit: AuditRegistry, audit_case_id: str, dimension: str) -> str:
    latest = audit.get_audit_case(audit_case_id).get("coverage", {}).get(dimension)
    if not isinstance(latest, dict):
        return ""
    evidence = latest.get("evidence")
    return str(evidence.get("proof_audit_digest") or "").strip() if isinstance(evidence, dict) else ""


def _record_once(
    audit: AuditRegistry,
    audit_case_id: str,
    dimension: str,
    digest: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    if _latest_digest(audit, audit_case_id, dimension) == digest:
        return audit.get_audit_case(audit_case_id)["coverage"][dimension], False
    return audit.record_coverage(audit_case_id, payload), True


def _existing_finding_for_digest(
    audit: AuditRegistry,
    audit_case_id: str,
    finding_type: str,
    digest: str,
) -> dict[str, Any] | None:
    for finding in audit.get_audit_case(audit_case_id).get("findings", []):
        if finding.get("finding_type") != finding_type:
            continue
        event = finding.get("latest_event")
        evidence = event.get("evidence") if isinstance(event, dict) else None
        if isinstance(evidence, dict) and evidence.get("proof_audit_digest") == digest:
            return finding
    return None


def _create_axiom_finding_once(
    audit: AuditRegistry,
    audit_case_id: str,
    claim_version_id: str,
    digest: str,
    unexpected: list[str],
    contains_sorry: bool,
) -> tuple[dict[str, Any] | None, bool]:
    if not unexpected:
        return None, False
    existing = _existing_finding_for_digest(audit, audit_case_id, "unexpected-axiom", digest)
    if existing is not None:
        return existing, False
    severity = "critical" if contains_sorry else "high"
    finding = audit.create_finding(audit_case_id, {
        "finding_type": "unexpected-axiom",
        "severity": severity,
        "title": "Unexpected axiom in independently collected Lean closure",
        "summary": (
            "Lean.collectAxioms reported axioms outside this AuditCase's explicit allowlist: "
            + ", ".join(unexpected)
        ),
        "affected_claim_version_id": claim_version_id,
        "created_by": "MathHub proof runner",
        "status": "open",
        "initial_note": "Generated from an exact pinned-checkout proof audit; requires adversarial review.",
        "evidence": {
            "proof_audit_digest": digest,
            "unexpected_axioms": unexpected,
            "contains_sorryAx": contains_sorry,
        },
    })
    return finding, True


def persist_case_proof_audit(
    db_path: Path,
    manifest: dict[str, Any],
    case_run: dict[str, Any],
    *,
    ingest_fn: Callable[[Path, dict[str, Any]], dict[str, Any]] = ingest_manifest,
) -> dict[str, Any]:
    if case_run.get("format") != FORMAT:
        raise ValueError("unsupported proof case run format")
    if case_run.get("case_key") != manifest.get("case_key"):
        raise ValueError("proof case run does not belong to this manifest")

    ingest = ingest_fn(Path(db_path), manifest)
    audit_case_id = str(ingest["audit_case_id"])
    claim_version_id = str(ingest["claim_version_id"])
    audit = AuditRegistry(Path(db_path))

    proof = case_run.get("proof_audit")
    if not isinstance(proof, dict):
        raise ValueError("proof_audit is required")
    digest = str(proof.get("audit_digest") or "").strip()
    if not digest.startswith("sha256:"):
        raise ValueError("proof audit digest is missing")

    graph = proof.get("proof_dependencies") if isinstance(proof.get("proof_dependencies"), dict) else {}
    graph_complete = bool(graph.get("complete"))
    closed_world = bool(graph.get("closed_world"))
    boundary_count = int(graph.get("boundary_count") or 0)
    error_count = len(graph.get("inspection_errors") or [])
    dependency_status = "complete" if graph_complete and closed_world else "partial"
    dependency_note = (
        "Stored proof/value dependency route was mechanically traversed to closure."
        if dependency_status == "complete"
        else
        "Stored proof/value dependency route was mechanically traversed within the declared project scope; external-library boundaries or traversal limits keep this dimension partial."
    )

    safe_proof = _sanitise_for_registry(proof)
    dependency_coverage, dependency_created = _record_once(
        audit,
        audit_case_id,
        "proof-dependencies",
        digest,
        {
            "dimension": "proof-dependencies",
            "status": dependency_status,
            "scope": str(case_run.get("proof_probe", {}).get("scope_note") or ""),
            "reviewer": "MathHub proof runner",
            "note": dependency_note,
            "evidence": {
                "proof_audit_digest": digest,
                "graph_complete_within_scope": graph_complete,
                "closed_world": closed_world,
                "boundary_count": boundary_count,
                "inspection_error_count": error_count,
                "proof_audit": safe_proof,
            },
        },
    )

    closure = proof.get("axiom_closure") if isinstance(proof.get("axiom_closure"), dict) else {}
    if not closure.get("complete"):
        raise ValueError("proof audit does not contain a complete axiom closure")
    unexpected = [str(x) for x in closure.get("unexpected_axioms") or []]
    contains_sorry = bool(closure.get("contains_sorryAx"))
    axiom_status = "issue-found" if unexpected else "complete"
    axiom_note = (
        "Lean.collectAxioms independently re-derived the recursive axiom closure and every used axiom is within the case allowlist."
        if not unexpected
        else
        "Lean.collectAxioms independently re-derived the recursive axiom closure and found axioms outside the case allowlist."
    )
    axiom_coverage, axiom_created = _record_once(
        audit,
        audit_case_id,
        "axiom-closure",
        digest,
        {
            "dimension": "axiom-closure",
            "status": axiom_status,
            "scope": "recursive closure of the exact headline Lean declaration",
            "reviewer": "MathHub proof runner",
            "note": axiom_note,
            "evidence": {
                "proof_audit_digest": digest,
                "collector": closure.get("collector"),
                "axioms": closure.get("axioms") or [],
                "permitted_axioms": closure.get("permitted_axioms"),
                "unexpected_axioms": unexpected,
                "contains_sorryAx": contains_sorry,
                "publisher_declared_axioms": closure.get("publisher_declared_axioms"),
                "publisher_missing_axioms": closure.get("publisher_missing_axioms") or [],
                "publisher_only_axioms": closure.get("publisher_only_axioms") or [],
                "publisher_set_equal": closure.get("publisher_set_equal"),
            },
        },
    )

    finding, finding_created = _create_axiom_finding_once(
        audit,
        audit_case_id,
        claim_version_id,
        digest,
        unexpected,
        contains_sorry,
    )

    return {
        "audit_case_id": audit_case_id,
        "proof_audit_digest": digest,
        "proof_dependency_coverage_id": dependency_coverage.get("coverage_id"),
        "proof_dependency_coverage_created": dependency_created,
        "axiom_coverage_id": axiom_coverage.get("coverage_id"),
        "axiom_coverage_created": axiom_created,
        "unexpected_axiom_finding_id": None if finding is None else finding.get("finding_id"),
        "unexpected_axiom_finding_created": finding_created,
        "independent_mathhub_proof_records": ingest.get("independent_mathhub_proof_records"),
        "independent_mathhub_build_records": ingest.get("independent_mathhub_build_records"),
        "verification_claimed": False,
    }


def run_case_proof_audit(
    checkout: Path,
    manifest: dict[str, Any],
    *,
    db_path: Path | None = None,
    build_fn: Callable[..., dict[str, Any]] = build_external_proof_audit,
    ingest_fn: Callable[[Path, dict[str, Any]], dict[str, Any]] = ingest_manifest,
) -> dict[str, Any]:
    result = build_case_proof_audit(checkout, manifest, build_fn=build_fn)
    if db_path is not None:
        result["registry"] = persist_case_proof_audit(
            Path(db_path), manifest, result, ingest_fn=ingest_fn
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a manifest-configured external proof-route/axiom audit and optionally persist evidence"
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    result = run_case_proof_audit(args.checkout, manifest, db_path=args.db)
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
