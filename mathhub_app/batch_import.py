from __future__ import annotations

import hashlib
import re
from typing import Any

import server as core
from claim_import import IMPORTABLE_LEAN_NAME, _persist_preflight_build, _stable_alias
from presentation import canonical_from_declaration, suggested_title
from registry import parse_ref
from trust import classify_axioms


MAX_BATCH_IMPORT = 40
BATCH_TIMEOUT_MAX_SECONDS = 900
BATCH_BEGIN_MARKER = "MATHHUB_BATCH_BEGIN:"

BATCH_MARKER_COMMAND = r'''
open Lean Elab Command

elab "#mathhub_batch_begin " id:ident : command => do
  logInfo m!"MATHHUB_BATCH_BEGIN:{id.getId}"
'''


def _unique_names(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raise ValueError("declaration_names must be a list")
    names: list[str] = []
    for value in raw:
        name = str(value or "").strip()
        if not name or not IMPORTABLE_LEAN_NAME.fullmatch(name):
            raise ValueError(f"invalid importable Lean declaration name: {name!r}")
        if name not in names:
            names.append(name)
    if not names:
        raise ValueError("declaration_names must contain at least one theorem")
    if len(names) > MAX_BATCH_IMPORT:
        raise ValueError(f"batch import accepts at most {MAX_BATCH_IMPORT} theorem names")
    return names


def _timeout_for(count: int) -> int:
    return min(BATCH_TIMEOUT_MAX_SECONDS, max(core.INDEX_TIMEOUT_SECONDS, 60 + 10 * count))


def _segments(output: str) -> dict[str, str]:
    matches = list(re.finditer(re.escape(BATCH_BEGIN_MARKER) + r"([^\s]+)", output))
    result: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = match.group(1)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(output)
        result[name] = output[match.start():end]
    return result


def _inspection_source(imports: list[str], names: list[str]) -> str:
    import_lines = "\n".join(f"import {module}" for module in imports)
    blocks = []
    for name in names:
        blocks.append(
            f"#mathhub_batch_begin {name}\n"
            f"#mathhub_inspect_decl {name}\n"
            f"#print axioms {name}\n"
        )
    return (
        f"import Mathlib.Tactic.MinImports\n{import_lines}\n\n"
        f"{core.LEAN_AUDIT_COMMAND}\n{BATCH_MARKER_COMMAND}\n"
        + "\n".join(blocks)
    )


def _inspection_from_segment(name: str, segment: str, environment_hash: str) -> dict[str, Any]:
    type_hash = core.parse_type_hash(segment)
    pretty_type = core.parse_pretty_type(segment)
    return {
        "ok": bool(type_hash and pretty_type),
        "declaration_name": name,
        "type_hash": type_hash,
        "pretty_type": pretty_type,
        "formal_fingerprint": core.fingerprint(type_hash, environment_hash),
        "statement_dependencies": core.parse_marker(segment, "MATHHUB_STATEMENT_REF:"),
        "proof_dependencies": core.parse_marker(segment, "MATHHUB_PROOF_REF:"),
        "transitive_dependencies": core.parse_marker(segment, "MATHHUB_TRANSITIVE:"),
        "axioms": core.parse_axioms(segment),
        "output": segment,
    }


def _audit_source(
    imports: list[str],
    names: list[str],
    inspections: dict[str, dict[str, Any]],
    environment_hash: str,
) -> tuple[str, dict[str, dict[str, str]]]:
    import_lines = "\n".join(f"import {module}" for module in imports)
    specs: dict[str, dict[str, str]] = {}
    blocks: list[str] = []
    for external_name in names:
        inspected = inspections[external_name]
        pretty_type = str(inspected["pretty_type"])
        alias = _stable_alias(external_name, environment_hash)
        declaration = f"theorem {alias} : {pretty_type}"
        proof = f"by\n  exact {external_name}"
        specs[alias] = {
            "external_name": external_name,
            "alias": alias,
            "declaration": declaration,
            "proof": proof,
        }
        blocks.append(
            f"#mathhub_batch_begin {alias}\n"
            f"#min_imports in\n{declaration} :=\n{proof}\n"
            f"#print axioms {alias}\n"
            f"#mathhub_inspect_decl {alias}\n"
        )
    source = (
        f"import Mathlib.Tactic.MinImports\n{import_lines}\n\n"
        f"{core.LEAN_AUDIT_COMMAND}\n{BATCH_MARKER_COMMAND}\n"
        + "\n".join(blocks)
    )
    return source, specs


def _preflight_from_segment(
    spec: dict[str, str],
    segment: str,
    imports: list[str],
    environment_hash: str,
    average_duration_ms: int,
) -> dict[str, Any]:
    axioms = core.parse_axioms(segment)
    trust = classify_axioms(axioms)
    type_hash = core.parse_type_hash(segment)
    pretty_type = core.parse_pretty_type(segment)
    proof_dependencies = core.parse_marker(segment, "MATHHUB_PROOF_REF:")
    statement_dependencies = core.parse_marker(segment, "MATHHUB_STATEMENT_REF:")
    transitive = core.parse_marker(segment, "MATHHUB_TRANSITIVE:")
    compile_source = core.source_for(
        imports,
        spec["declaration"],
        spec["proof"],
        spec["alias"],
        False,
    )
    return {
        "ok": bool(type_hash and pretty_type),
        "audit_ok": bool(type_hash and pretty_type),
        "returncode": 0,
        "theorem_name": spec["alias"],
        "axioms": axioms,
        "contains_sorry": "sorryAx" in axioms or "declaration uses 'sorry'" in segment,
        "uses_trust_compiler": bool(trust["compiler_trust_axioms"]),
        "minimal_imports": core.parse_imports(segment) or list(imports),
        "statement_dependencies": statement_dependencies,
        "proof_dependencies": proof_dependencies,
        "direct_dependencies": proof_dependencies,
        "transitive_dependencies": transitive,
        "dependencies": transitive,
        "type_hash": type_hash,
        "pretty_type": pretty_type,
        "formal_fingerprint": core.fingerprint(type_hash, environment_hash),
        "source_hash": hashlib.sha256(compile_source.encode()).hexdigest(),
        "duration_ms": average_duration_ms,
        "audit_duration_ms": average_duration_ms,
        "output": segment,
    }


def _already_registered(external_name: str, environment_hash: str) -> dict[str, Any] | None:
    existing = core.FORMAL.claim_for_name(external_name, environment_hash)
    if existing is None:
        return None
    return {
        "declaration_name": external_name,
        "status": "already_registered",
        "already_registered": True,
        "claim_id": existing["claim_id"],
        "claim_version_id": existing["claim_version_id"],
    }


def _persist_one(
    *,
    external_name: str,
    imports: list[str],
    environment: dict[str, Any],
    inspected: dict[str, Any],
    preflight: dict[str, Any],
    common: dict[str, Any],
) -> dict[str, Any]:
    environment_hash = str(environment["environment_hash"])
    duplicate = _already_registered(external_name, environment_hash)
    if duplicate is not None:
        return duplicate
    if not preflight.get("ok") or not preflight.get("audit_ok"):
        raise ValueError("batch bridge theorem did not complete Lean dependency audit")
    if preflight.get("contains_sorry"):
        raise ValueError("batch import bridge unexpectedly depends on sorryAx")

    alias = _stable_alias(external_name, environment_hash)
    declaration = f"theorem {alias} : {str(inspected['pretty_type'])}"
    proof_source = f"by\n  exact {external_name}"
    canonical = canonical_from_declaration(declaration)
    created = core.register_claim(
        {
            "title": suggested_title(external_name),
            "statement": canonical["text"],
            "latex": canonical["latex"],
            "area": str(common.get("area") or "").strip(),
            "claim_author": str(common.get("claim_author") or "").strip(),
            "formal_declaration": declaration,
            "lean_name": alias,
            "proof_author": str(common.get("proof_author") or "").strip(),
            "imports": imports,
            "proof_source": proof_source,
            "build": False,
        }
    )
    proof_id = str(created.get("created_proof_id") or "")
    if not proof_id:
        raise RuntimeError("batch import bridge created no Proof record")

    persisted_build = _persist_preflight_build(proof_id, preflight, environment)
    proof = core.REGISTRY.get_proof(proof_id)
    claim_id = parse_ref(str(proof["claim_id"]), "CLM")
    version_id = parse_ref(str(proof["claim_version_id"]), "VER")
    build_id = parse_ref(str(persisted_build["build_id"]), "BLD")
    external_binding = core.FORMAL.record_binding(
        claim_id=claim_id,
        claim_version_id=version_id,
        source_build_id=build_id,
        lean_name=external_name,
        environment_hash=environment_hash,
        type_hash=str(inspected.get("type_hash") or preflight.get("type_hash") or ""),
        formal_fingerprint=str(
            inspected.get("formal_fingerprint") or preflight.get("formal_fingerprint") or ""
        ),
        origin="imported-declaration",
    )
    return {
        "declaration_name": external_name,
        "status": "imported",
        "already_registered": False,
        "claim_id": str(proof["claim_id"]),
        "claim_version_id": str(proof["claim_version_id"]),
        "proof_id": proof_id,
        "build_id": persisted_build["build_id"],
        "external_binding": external_binding,
        "import_alias": alias,
    }


def import_existing_declarations(payload: dict[str, Any]) -> dict[str, Any]:
    names = _unique_names(payload.get("declaration_names") or payload.get("names"))
    imports = core.validate_imports(payload.get("imports") or ["Mathlib"])
    environment = core.runtime_status()
    if not environment.get("ready"):
        raise ValueError(str(environment.get("message") or "Lean environment unavailable"))
    environment_hash = str(environment.get("environment_hash") or "")
    if not environment_hash:
        raise ValueError("pinned Lean environment has no environment hash")

    items: list[dict[str, Any]] = []
    pending: list[str] = []
    for name in names:
        existing = _already_registered(name, environment_hash)
        if existing is not None:
            items.append(existing)
        else:
            pending.append(name)

    if not pending:
        return {
            "ok": True,
            "requested": len(names),
            "imported": 0,
            "already_registered": len(items),
            "failed": 0,
            "lean_invocations": 0,
            "items": items,
        }

    timeout = _timeout_for(len(pending))
    inspect_run = core.invoke_lean(
        _inspection_source(imports, pending),
        timeout_seconds=timeout,
    )
    if not inspect_run.get("ok"):
        message = str(
            inspect_run.get("engine_error")
            or inspect_run.get("output")
            or "Lean batch inspection failed"
        )
        items.extend(
            {"declaration_name": name, "status": "failed", "error": message}
            for name in pending
        )
        return {
            "ok": False,
            "requested": len(names),
            "imported": 0,
            "already_registered": sum(1 for item in items if item.get("already_registered")),
            "failed": len(pending),
            "lean_invocations": 1,
            "inspect_duration_ms": int(inspect_run.get("duration_ms") or 0),
            "items": items,
        }

    inspect_segments = _segments(str(inspect_run.get("output") or ""))
    inspections: dict[str, dict[str, Any]] = {}
    audit_names: list[str] = []
    for name in pending:
        segment = inspect_segments.get(name, "")
        inspected = _inspection_from_segment(name, segment, environment_hash)
        if not inspected.get("ok"):
            items.append(
                {
                    "declaration_name": name,
                    "status": "failed",
                    "error": "Lean emitted no complete type inspection for this theorem",
                }
            )
        else:
            inspections[name] = inspected
            audit_names.append(name)

    if not audit_names:
        return {
            "ok": False,
            "requested": len(names),
            "imported": 0,
            "already_registered": sum(1 for item in items if item.get("already_registered")),
            "failed": sum(1 for item in items if item.get("status") == "failed"),
            "lean_invocations": 1,
            "inspect_duration_ms": int(inspect_run.get("duration_ms") or 0),
            "items": items,
        }

    audit_source, specs = _audit_source(
        imports,
        audit_names,
        inspections,
        environment_hash,
    )
    audit_run = core.invoke_lean(audit_source, timeout_seconds=timeout)
    if not audit_run.get("ok"):
        message = str(
            audit_run.get("engine_error")
            or audit_run.get("output")
            or "Lean batch bridge audit failed"
        )
        items.extend(
            {"declaration_name": name, "status": "failed", "error": message}
            for name in audit_names
        )
        return {
            "ok": False,
            "requested": len(names),
            "imported": 0,
            "already_registered": sum(1 for item in items if item.get("already_registered")),
            "failed": sum(1 for item in items if item.get("status") == "failed"),
            "lean_invocations": 2,
            "inspect_duration_ms": int(inspect_run.get("duration_ms") or 0),
            "audit_duration_ms": int(audit_run.get("duration_ms") or 0),
            "items": items,
        }

    audit_segments = _segments(str(audit_run.get("output") or ""))
    average_duration = max(1, round(int(audit_run.get("duration_ms") or 0) / max(1, len(audit_names))))
    common = {
        "area": payload.get("area"),
        "claim_author": payload.get("claim_author"),
        "proof_author": payload.get("proof_author"),
    }
    imported_any = False
    for name in audit_names:
        alias = _stable_alias(name, environment_hash)
        segment = audit_segments.get(alias, "")
        preflight = _preflight_from_segment(
            specs[alias],
            segment,
            imports,
            environment_hash,
            average_duration,
        )
        try:
            item = _persist_one(
                external_name=name,
                imports=imports,
                environment=environment,
                inspected=inspections[name],
                preflight=preflight,
                common=common,
            )
            imported_any = imported_any or item.get("status") == "imported"
            items.append(item)
        except Exception as exc:
            items.append(
                {
                    "declaration_name": name,
                    "status": "failed",
                    "error": str(exc),
                }
            )

    if imported_any:
        with core.ARGUMENT.connect() as conn:
            core.ARGUMENT._reconcile(conn, environment_hash)

    order = {name: index for index, name in enumerate(names)}
    items.sort(key=lambda item: order.get(str(item.get("declaration_name") or ""), len(order)))
    imported_count = sum(1 for item in items if item.get("status") == "imported")
    existing_count = sum(1 for item in items if item.get("status") == "already_registered")
    failed_count = sum(1 for item in items if item.get("status") == "failed")
    return {
        "ok": failed_count == 0,
        "requested": len(names),
        "imported": imported_count,
        "already_registered": existing_count,
        "failed": failed_count,
        "lean_invocations": 2,
        "inspect_duration_ms": int(inspect_run.get("duration_ms") or 0),
        "audit_duration_ms": int(audit_run.get("duration_ms") or 0),
        "items": items,
    }
