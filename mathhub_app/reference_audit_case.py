from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from audit_registry import AuditRegistry
from external_audit_ingest import ingest_manifest, validate_manifest
from external_reference_audit import build_external_reference_audit, validate_parity_groups

FORMAT = "mathhub-reference-audit-case-run-v1"


def _required_text(parent: dict[str, Any], key: str) -> str:
    value = str(parent.get(key) or "").strip()
    if not value:
        raise ValueError(f"reference_probe.{key} is required")
    return value


def reference_probe_config(manifest: dict[str, Any]) -> dict[str, Any]:
    raw = manifest.get("reference_probe")
    if not isinstance(raw, dict):
        raise ValueError("manifest.reference_probe must be an object")

    root_module = _required_text(raw, "root_module")

    forbidden_raw = raw.get("forbidden_import_prefixes")
    if not isinstance(forbidden_raw, list):
        raise ValueError("reference_probe.forbidden_import_prefixes must be a list")
    forbidden = list(dict.fromkeys(str(x).strip() for x in forbidden_raw if str(x).strip()))
    if not forbidden:
        raise ValueError("reference_probe.forbidden_import_prefixes must not be empty")

    groups_raw = raw.get("parity_groups")
    groups = validate_parity_groups(groups_raw)

    def positive_int(key: str, default: int) -> int:
        value = int(raw.get(key, default))
        if value < 1:
            raise ValueError(f"reference_probe.{key} must be >= 1")
        return value

    return {
        "root_module": root_module,
        "forbidden_import_prefixes": forbidden,
        "parity_groups": groups,
        "max_modules": positive_int("max_modules", 5000),
        "timeout_seconds": positive_int("timeout_seconds", 120),
        "scope_note": str(raw.get("scope_note") or "").strip(),
    }


def build_case_reference_audit(
    checkout: Path,
    manifest: dict[str, Any],
    *,
    build_fn: Callable[..., dict[str, Any]] = build_external_reference_audit,
) -> dict[str, Any]:
    validate_manifest(manifest)
    config = reference_probe_config(manifest)
    source = manifest["formal_source"]
    reference = build_fn(
        Path(checkout),
        config["root_module"],
        config["parity_groups"],
        expected_revision=str(source.get("revision") or "").strip(),
        expected_source_path=str(source.get("file_path") or "").strip(),
        expected_source_hash=str(source.get("source_hash") or "").strip(),
        forbidden_import_prefixes=config["forbidden_import_prefixes"],
        max_modules=config["max_modules"],
        timeout_seconds=config["timeout_seconds"],
    )
    return {
        "format": FORMAT,
        "manifest_schema": manifest["schema"],
        "case_key": manifest["case_key"],
        "reference_probe": config,
        "reference_audit": reference,
    }


def _sanitise_for_registry(reference: dict[str, Any]) -> dict[str, Any]:
    copy = json.loads(json.dumps(reference, ensure_ascii=False))
    checkout = copy.get("checkout")
    if isinstance(checkout, dict):
        checkout.pop("checkout", None)
    return copy


def _latest_digest(audit: AuditRegistry, audit_case_id: str, dimension: str) -> str:
    latest = audit.get_audit_case(audit_case_id).get("coverage", {}).get(dimension)
    if not isinstance(latest, dict):
        return ""
    evidence = latest.get("evidence")
    if not isinstance(evidence, dict):
        return ""
    return str(evidence.get("reference_audit_digest") or "").strip()


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


def _existing_finding(
    audit: AuditRegistry,
    audit_case_id: str,
    finding_type: str,
    digest: str,
    finding_key: str,
) -> dict[str, Any] | None:
    for finding in audit.get_audit_case(audit_case_id).get("findings", []):
        if finding.get("finding_type") != finding_type:
            continue
        event = finding.get("latest_event")
        evidence = event.get("evidence") if isinstance(event, dict) else None
        if not isinstance(evidence, dict):
            continue
        if (
            evidence.get("reference_audit_digest") == digest
            and evidence.get("reference_finding_key") == finding_key
        ):
            return finding
    return None


def _create_finding_once(
    audit: AuditRegistry,
    audit_case_id: str,
    claim_version_id: str,
    *,
    finding_type: str,
    severity: str,
    title: str,
    summary: str,
    digest: str,
    finding_key: str,
    evidence: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    existing = _existing_finding(
        audit, audit_case_id, finding_type, digest, finding_key
    )
    if existing is not None:
        return existing, False
    finding = audit.create_finding(audit_case_id, {
        "finding_type": finding_type,
        "severity": severity,
        "title": title,
        "summary": summary,
        "affected_claim_version_id": claim_version_id,
        "created_by": "MathHub reference runner",
        "status": "open",
        "initial_note": "Generated from an exact pinned-checkout reference-isolation audit; requires adversarial review.",
        "evidence": {
            "reference_audit_digest": digest,
            "reference_finding_key": finding_key,
            **evidence,
        },
    })
    return finding, True


def persist_case_reference_audit(
    db_path: Path,
    manifest: dict[str, Any],
    case_run: dict[str, Any],
    *,
    ingest_fn: Callable[[Path, dict[str, Any]], dict[str, Any]] = ingest_manifest,
) -> dict[str, Any]:
    if case_run.get("format") != FORMAT:
        raise ValueError("unsupported reference case run format")
    if case_run.get("case_key") != manifest.get("case_key"):
        raise ValueError("reference case run does not belong to this manifest")

    ingest = ingest_fn(Path(db_path), manifest)
    audit_case_id = str(ingest["audit_case_id"])
    claim_version_id = str(ingest["claim_version_id"])
    audit = AuditRegistry(Path(db_path))

    reference = case_run.get("reference_audit")
    if not isinstance(reference, dict):
        raise ValueError("reference_audit is required")
    digest = str(reference.get("audit_digest") or "").strip()
    if not digest.startswith("sha256:"):
        raise ValueError("reference audit digest is missing")

    summary = reference.get("summary") if isinstance(reference.get("summary"), dict) else {}
    imports = reference.get("source_import_closure") if isinstance(reference.get("source_import_closure"), dict) else {}
    parity = reference.get("declaration_parity") if isinstance(reference.get("declaration_parity"), list) else []
    safe_reference = _sanitise_for_registry(reference)

    forbidden = bool(summary.get("forbidden_import_reachable"))
    import_complete = bool(summary.get("import_closure_complete"))
    parity_equal = bool(summary.get("parity_all_equal"))
    mismatch_groups = [str(x) for x in summary.get("parity_mismatch_groups") or []]

    # This dimension remains partial even on exact parity: Comparator/reference
    # parity is only one bridge in the full Clay -> formal theorem alignment chain.
    alignment_status = "issue-found" if not parity_equal else "partial"
    alignment_note = (
        "Selected challenge/reference declarations and submission declarations have identical configured Lean fingerprints in isolated module environments. This narrows source alignment but does not establish equivalence to the Clay statement."
        if parity_equal
        else
        "One or more selected challenge/reference declarations differ from the submission declarations in isolated Lean environments; source alignment requires review."
    )
    alignment_coverage, alignment_created = _record_once(
        audit,
        audit_case_id,
        "source-alignment",
        digest,
        {
            "dimension": "source-alignment",
            "status": alignment_status,
            "scope": str(case_run.get("reference_probe", {}).get("scope_note") or ""),
            "reviewer": "MathHub reference runner",
            "note": alignment_note,
            "evidence": {
                "reference_audit_digest": digest,
                "parity_all_equal": parity_equal,
                "parity_mismatch_groups": mismatch_groups,
                "declaration_parity": safe_reference.get("declaration_parity") or [],
                "important_boundary": "Formal reference parity is not Clay semantic equivalence.",
            },
        },
    )

    # Repository-local import closure is a supply-chain/reference-isolation signal,
    # not a complete supply-chain audit of Lake dependencies or binaries.
    supply_status = "issue-found" if forbidden else "partial"
    supply_note = (
        "A forbidden challenge/reference module is reachable from the repository-local submission import closure."
        if forbidden
        else (
            "No forbidden challenge/reference module is reachable in the complete repository-local submission import closure. External Lake dependencies and binaries remain outside this pass."
            if import_complete else
            "No forbidden challenge/reference module was observed, but the repository-local import closure was truncated/incomplete, so isolation is not established."
        )
    )
    supply_coverage, supply_created = _record_once(
        audit,
        audit_case_id,
        "supply-chain",
        digest,
        {
            "dimension": "supply-chain",
            "status": supply_status,
            "scope": "repository-local Lean source import closure and challenge/reference isolation",
            "reviewer": "MathHub reference runner",
            "note": supply_note,
            "evidence": {
                "reference_audit_digest": digest,
                "forbidden_import_reachable": forbidden,
                "forbidden_hits": imports.get("forbidden_hits") or [],
                "import_closure_complete": import_complete,
                "root_module": imports.get("root_module"),
                "local_module_count": imports.get("local_module_count"),
                "unresolved_external_imports": imports.get("unresolved_external_imports") or [],
            },
        },
    )

    findings: list[dict[str, Any]] = []
    if forbidden:
        finding, created = _create_finding_once(
            audit,
            audit_case_id,
            claim_version_id,
            finding_type="dependency-gap",
            severity="critical",
            title="Challenge/reference module reachable from submission import closure",
            summary=(
                "The repository-local source import graph reaches a module explicitly forbidden by the AuditCase reference-isolation policy. "
                "This can invalidate the claim that the submission is independent of challenge placeholders and must be investigated before trusting the comparator separation."
            ),
            digest=digest,
            finding_key="forbidden-reference-import",
            evidence={"forbidden_hits": imports.get("forbidden_hits") or []},
        )
        findings.append({"finding_id": finding.get("finding_id"), "created": created})

    parity_by_key = {str(group.get("key")): group for group in parity if isinstance(group, dict)}
    config_groups = {
        str(group.get("key")): group
        for group in case_run.get("reference_probe", {}).get("parity_groups", [])
        if isinstance(group, dict)
    }
    for key in mismatch_groups:
        observed = parity_by_key.get(key, {})
        configured = config_groups.get(key, {})
        role = str(configured.get("role") or observed.get("role") or "").strip()
        headline = role == "headline-statement"
        finding_type = "statement-mismatch" if headline else "documentation-mismatch"
        title = (
            "Headline theorem type differs from independent reference"
            if headline else
            f"Reference declaration fingerprints differ: {key}"
        )
        finding, created = _create_finding_once(
            audit,
            audit_case_id,
            claim_version_id,
            finding_type=finding_type,
            severity="critical" if headline else "high",
            title=title,
            summary=(
                "The configured type-only headline comparison differs between the independent challenge/reference module and the submission module."
                if headline else
                "One or more copied reference definitions/structures differ between the independent reference module and the submission-side definitions. The mechanical audit does not classify the drift as strengthening or weakening; semantic review is required."
            ),
            digest=digest,
            finding_key=f"parity:{key}",
            evidence={
                "parity_group": key,
                "role": role,
                "comparison": observed,
            },
        )
        findings.append({"finding_id": finding.get("finding_id"), "created": created})

    return {
        "audit_case_id": audit_case_id,
        "reference_audit_digest": digest,
        "source_alignment_coverage_id": alignment_coverage.get("coverage_id"),
        "source_alignment_coverage_created": alignment_created,
        "supply_chain_coverage_id": supply_coverage.get("coverage_id"),
        "supply_chain_coverage_created": supply_created,
        "findings": findings,
        "independent_mathhub_proof_records": ingest.get("independent_mathhub_proof_records"),
        "independent_mathhub_build_records": ingest.get("independent_mathhub_build_records"),
        "verification_claimed": False,
        "clay_semantic_equivalence_claimed": False,
    }


def run_case_reference_audit(
    checkout: Path,
    manifest: dict[str, Any],
    *,
    db_path: Path | None = None,
    build_fn: Callable[..., dict[str, Any]] = build_external_reference_audit,
    ingest_fn: Callable[[Path, dict[str, Any]], dict[str, Any]] = ingest_manifest,
) -> dict[str, Any]:
    result = build_case_reference_audit(checkout, manifest, build_fn=build_fn)
    if db_path is not None:
        result["registry"] = persist_case_reference_audit(
            Path(db_path), manifest, result, ingest_fn=ingest_fn
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a manifest-configured reference-isolation/parity audit and optionally persist evidence"
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    result = run_case_reference_audit(args.checkout, manifest, db_path=args.db)
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")

    ref = result["reference_audit"]
    summary = ref.get("summary", {})
    bad = bool(summary.get("forbidden_import_reachable")) or not bool(summary.get("parity_all_equal"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
