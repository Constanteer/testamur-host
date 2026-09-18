from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from audit_registry import AuditRegistry
from external_audit_ingest import ingest_manifest, validate_manifest
from external_semantic_audit import build_external_semantic_audit

FORMAT = "mathhub-semantic-audit-case-run-v1"


def _required_text(parent: dict[str, Any], key: str) -> str:
    value = str(parent.get(key) or "").strip()
    if not value:
        raise ValueError(f"semantic_probe.{key} is required")
    return value


def semantic_probe_config(manifest: dict[str, Any]) -> dict[str, Any]:
    raw = manifest.get("semantic_probe")
    if not isinstance(raw, dict):
        raise ValueError("manifest.semantic_probe must be an object")

    imports_raw = raw.get("imports")
    if not isinstance(imports_raw, list):
        raise ValueError("semantic_probe.imports must be a list")
    imports = list(dict.fromkeys(str(x).strip() for x in imports_raw if str(x).strip()))
    if not imports:
        raise ValueError("semantic_probe.imports must contain at least one module")

    root = _required_text(raw, "root")
    claim_name = str(manifest.get("claim", {}).get("lean_name") or "").strip()
    source_name = str(manifest.get("formal_source", {}).get("lean_name") or "").strip()
    if root != claim_name or root != source_name:
        raise ValueError(
            "semantic_probe.root must equal both claim.lean_name and formal_source.lean_name"
        )

    prefixes_raw = raw.get("expand_prefixes", [])
    if not isinstance(prefixes_raw, list):
        raise ValueError("semantic_probe.expand_prefixes must be a list")
    prefixes = list(dict.fromkeys(str(x).strip() for x in prefixes_raw if str(x).strip()))

    def positive_int(key: str, default: int) -> int:
        value = int(raw.get(key, default))
        if value < 1:
            raise ValueError(f"semantic_probe.{key} must be >= 1")
        return value

    return {
        "imports": imports,
        "root": root,
        "expand_prefixes": prefixes,
        "max_depth": positive_int("max_depth", 8),
        "max_nodes": positive_int("max_nodes", 500),
        "batch_size": positive_int("batch_size", 32),
        "timeout_seconds": positive_int("timeout_seconds", 120),
        "scope_note": str(raw.get("scope_note") or "").strip(),
    }


def build_case_semantic_audit(
    checkout: Path,
    manifest: dict[str, Any],
    *,
    build_fn: Callable[..., dict[str, Any]] = build_external_semantic_audit,
) -> dict[str, Any]:
    validate_manifest(manifest)
    config = semantic_probe_config(manifest)
    source = manifest["formal_source"]

    semantic = build_fn(
        Path(checkout),
        config["imports"],
        config["root"],
        expected_revision=str(source.get("revision") or "").strip(),
        expected_source_path=str(source.get("file_path") or "").strip(),
        expected_source_hash=str(source.get("source_hash") or "").strip(),
        expand_prefixes=config["expand_prefixes"],
        max_depth=config["max_depth"],
        max_nodes=config["max_nodes"],
        batch_size=config["batch_size"],
        timeout_seconds=config["timeout_seconds"],
    )

    return {
        "format": FORMAT,
        "manifest_schema": manifest["schema"],
        "case_key": manifest["case_key"],
        "semantic_probe": config,
        "semantic_audit": semantic,
    }


def _sanitise_for_registry(semantic: dict[str, Any]) -> dict[str, Any]:
    # Local checkout paths are useful in a command result but should not become
    # persistent registry identity/evidence. The semantic artifact digest already
    # excludes this machine-specific field as well.
    copy = json.loads(json.dumps(semantic, ensure_ascii=False))
    checkout = copy.get("checkout")
    if isinstance(checkout, dict):
        checkout.pop("checkout", None)
    return copy


def _latest_digest(audit: AuditRegistry, audit_case_id: str, dimension: str) -> str:
    case = audit.get_audit_case(audit_case_id)
    latest = case.get("coverage", {}).get(dimension)
    if not isinstance(latest, dict):
        return ""
    evidence = latest.get("evidence")
    if not isinstance(evidence, dict):
        return ""
    return str(evidence.get("semantic_audit_digest") or "").strip()


def _record_once(
    audit: AuditRegistry,
    audit_case_id: str,
    dimension: str,
    digest: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    if _latest_digest(audit, audit_case_id, dimension) == digest:
        latest = audit.get_audit_case(audit_case_id)["coverage"][dimension]
        return latest, False
    return audit.record_coverage(audit_case_id, payload), True


def persist_case_semantic_audit(
    db_path: Path,
    manifest: dict[str, Any],
    case_run: dict[str, Any],
    *,
    ingest_fn: Callable[[Path, dict[str, Any]], dict[str, Any]] = ingest_manifest,
) -> dict[str, Any]:
    if case_run.get("format") != FORMAT:
        raise ValueError("unsupported semantic case run format")
    if case_run.get("case_key") != manifest.get("case_key"):
        raise ValueError("semantic case run does not belong to this manifest")

    ingest = ingest_fn(Path(db_path), manifest)
    audit_case_id = str(ingest["audit_case_id"])
    audit = AuditRegistry(Path(db_path))
    semantic = case_run.get("semantic_audit")
    if not isinstance(semantic, dict):
        raise ValueError("semantic_audit is required")
    digest = str(semantic.get("audit_digest") or "").strip()
    if not digest.startswith("sha256:"):
        raise ValueError("semantic audit digest is missing")

    safe_semantic = _sanitise_for_registry(semantic)
    cone = semantic.get("cone") if isinstance(semantic.get("cone"), dict) else {}
    cone_complete = bool(cone.get("complete"))
    boundary_count = int(cone.get("boundary_count") or 0)
    error_count = len(cone.get("inspection_errors") or [])

    semantic_note = (
        "Mechanical semantic cone extracted within the configured project scope. "
        "This is evidence of definition expansion only; it does not establish Clay/paper equivalence."
        if cone_complete
        else
        "Mechanical semantic cone extraction was incomplete. The partial artifact is retained as evidence; "
        "no semantic-equivalence conclusion is permitted."
    )
    semantic_coverage, semantic_created = _record_once(
        audit,
        audit_case_id,
        "semantic-definitions",
        digest,
        {
            "dimension": "semantic-definitions",
            "status": "partial",
            "scope": str(case_run.get("semantic_probe", {}).get("scope_note") or ""),
            "reviewer": "MathHub semantic runner",
            "note": semantic_note,
            "evidence": {
                "semantic_audit_digest": digest,
                "cone_complete_within_scope": cone_complete,
                "boundary_count": boundary_count,
                "inspection_error_count": error_count,
                "semantic_audit": safe_semantic,
            },
        },
    )

    provenance_coverage: dict[str, Any] | None = None
    provenance_created = False
    source_attestation = semantic.get("source_attestation")
    if (
        semantic.get("revision_matches_expected") is True
        and isinstance(source_attestation, dict)
        and source_attestation.get("matches_expected") is True
    ):
        provenance_coverage, provenance_created = _record_once(
            audit,
            audit_case_id,
            "formal-source-provenance",
            digest,
            {
                "dimension": "formal-source-provenance",
                "status": "partial",
                "scope": "local pinned checkout revision and exact formal-source file bytes",
                "reviewer": "MathHub semantic runner",
                "note": (
                    "Pinned Git revision and exact formal-source bytes matched the manifest locally. "
                    "This remains partial: toolchain binaries, caches, signatures and source-built provenance "
                    "are separate supply-chain obligations."
                ),
                "evidence": {
                    "semantic_audit_digest": digest,
                    "git_head": semantic.get("checkout", {}).get("git_head"),
                    "tracked_tree_dirty": semantic.get("checkout", {}).get("tracked_tree_dirty"),
                    "source_attestation": source_attestation,
                },
            },
        )

    return {
        "audit_case_id": audit_case_id,
        "semantic_audit_digest": digest,
        "semantic_coverage_id": semantic_coverage.get("coverage_id"),
        "semantic_coverage_created": semantic_created,
        "provenance_coverage_id": None if provenance_coverage is None else provenance_coverage.get("coverage_id"),
        "provenance_coverage_created": provenance_created,
        "independent_mathhub_proof_records": ingest.get("independent_mathhub_proof_records"),
        "independent_mathhub_build_records": ingest.get("independent_mathhub_build_records"),
        "verification_claimed": False,
    }


def run_case_semantic_audit(
    checkout: Path,
    manifest: dict[str, Any],
    *,
    db_path: Path | None = None,
    build_fn: Callable[..., dict[str, Any]] = build_external_semantic_audit,
    ingest_fn: Callable[[Path, dict[str, Any]], dict[str, Any]] = ingest_manifest,
) -> dict[str, Any]:
    result = build_case_semantic_audit(checkout, manifest, build_fn=build_fn)
    if db_path is not None:
        result["registry"] = persist_case_semantic_audit(
            Path(db_path), manifest, result, ingest_fn=ingest_fn
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a manifest-configured external semantic audit and optionally persist partial evidence"
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    result = run_case_semantic_audit(args.checkout, manifest, db_path=args.db)
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
