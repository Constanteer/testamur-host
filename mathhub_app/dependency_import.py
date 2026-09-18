from __future__ import annotations

import re
from collections import deque
from typing import Any

import server as core
from batch_import import (
    MAX_BATCH_IMPORT,
    _inspection_from_segment,
    _inspection_source,
    _segments,
    import_existing_declarations,
)
from claim_import import IMPORTABLE_LEAN_NAME


DEFAULT_MAX_DEPTH = 2
DEFAULT_MAX_CLAIMS = 160
MAX_AUTO_DEPTH = 5
MAX_AUTO_CLAIMS = 500
MAX_CLASSIFY_BATCH = 200
CLASSIFY_MARKER = "MATHHUB_CLASSIFY:"

CLASSIFY_COMMAND = r'''
open Lean Elab Command

elab "#mathhub_classify " id:ident : command => do
  let name := id.getId
  let env ← getEnv
  match env.constants.find? name with
  | some (.thmInfo _) =>
      if name.isInternal then
        logInfo m!"MATHHUB_CLASSIFY:{name}:internal"
      else
        logInfo m!"MATHHUB_CLASSIFY:{name}:theorem"
  | some _ => logInfo m!"MATHHUB_CLASSIFY:{name}:substrate"
  | none => logInfo m!"MATHHUB_CLASSIFY:{name}:missing"
'''


def _bounded_int(payload: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    raw = payload.get(key, default)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer") from exc
    return max(low, min(value, high))


def _root_names(raw: Any) -> list[str]:
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
        raise ValueError(f"automatic dependency import accepts at most {MAX_BATCH_IMPORT} root theorems")
    return names


def _inspect_batch(
    imports: list[str],
    names: list[str],
    environment_hash: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, str], int, int]:
    if not names:
        return {}, {}, 0, 0
    run = core.invoke_lean(
        _inspection_source(imports, names),
        timeout_seconds=min(900, max(core.INDEX_TIMEOUT_SECONDS, 60 + 10 * len(names))),
    )
    duration = int(run.get("duration_ms") or 0)
    if not run.get("ok"):
        message = str(run.get("engine_error") or run.get("output") or "Lean dependency inspection failed")
        return {}, {name: message for name in names}, 1, duration
    segments = _segments(str(run.get("output") or ""))
    inspections: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    for name in names:
        inspected = _inspection_from_segment(name, segments.get(name, ""), environment_hash)
        if inspected.get("ok"):
            inspections[name] = inspected
        else:
            errors[name] = "Lean emitted no complete theorem inspection"
    return inspections, errors, 1, duration


def _classification_source(imports: list[str], names: list[str]) -> str:
    import_lines = "\n".join(f"import {module}" for module in imports)
    commands = "\n".join(f"#mathhub_classify {name}" for name in names)
    return f"{import_lines}\n\n{CLASSIFY_COMMAND}\n{commands}\n"


def _parse_classifications(output: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, kind in re.findall(re.escape(CLASSIFY_MARKER) + r"([^:\s]+):(theorem|substrate|internal|missing)", output):
        result[name] = kind
    return result


def _classify_unbound(
    imports: list[str],
    names: list[str],
) -> tuple[dict[str, str], int, int, list[str]]:
    result: dict[str, str] = {}
    invocations = 0
    duration_ms = 0
    errors: list[str] = []
    for offset in range(0, len(names), MAX_CLASSIFY_BATCH):
        chunk = names[offset : offset + MAX_CLASSIFY_BATCH]
        run = core.invoke_lean(
            _classification_source(imports, chunk),
            timeout_seconds=min(900, max(core.INDEX_TIMEOUT_SECONDS, 30 + 2 * len(chunk))),
        )
        invocations += 1
        duration_ms += int(run.get("duration_ms") or 0)
        if not run.get("ok"):
            errors.append(str(run.get("engine_error") or run.get("output") or "Lean classification failed"))
            continue
        parsed = _parse_classifications(str(run.get("output") or ""))
        result.update(parsed)
        for name in chunk:
            result.setdefault(name, "missing")
    return result, invocations, duration_ms, errors


def _binding_build_for_name(name: str, environment_hash: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    binding = core.FORMAL.claim_for_name(name, environment_hash)
    if binding is None:
        return None
    build_ref = str(binding.get("source_build_id") or "")
    if not build_ref:
        claim = core.REGISTRY.get_claim(str(binding["claim_id"]))
        for proof in claim.get("proofs", []):
            builds = proof.get("builds") or []
            if builds:
                build_ref = str(builds[0]["build_id"])
                break
    if not build_ref:
        return None
    build = core.REGISTRY.get_build(build_ref)
    proof = core.REGISTRY.get_proof(str(build["proof_id"]))
    return proof, build


def _attach_external_argument_evidence(
    name: str,
    inspected: dict[str, Any],
    environment: dict[str, Any],
) -> bool:
    """Attach the original library theorem's proof refs to its imported Proof Build.

    The local bridge theorem is the verification witness. For imported declarations,
    the mathematical argument evidence comes from the original theorem ConstantInfo
    in the same pinned environment. ArgumentIndex remains append-only: this only adds
    raw constant observations to the already-verified Build.
    """
    environment_hash = str(environment.get("environment_hash") or "")
    pair = _binding_build_for_name(name, environment_hash)
    if pair is None:
        return False
    proof, build = pair
    proof_refs = list(dict.fromkeys(str(x) for x in inspected.get("proof_dependencies") or [] if str(x)))
    statement_refs = list(dict.fromkeys(str(x) for x in inspected.get("statement_dependencies") or [] if str(x)))
    transitive_refs = list(dict.fromkeys(str(x) for x in inspected.get("transitive_dependencies") or [] if str(x)))
    core.ARGUMENT.record_build(
        proof,
        build,
        {
            "statement_dependencies": statement_refs,
            "proof_dependencies": proof_refs,
            "direct_dependencies": proof_refs,
            "transitive_dependencies": transitive_refs,
            "dependencies": transitive_refs,
        },
        environment,
    )
    return True


def import_dependency_closure(payload: dict[str, Any]) -> dict[str, Any]:
    roots = _root_names(payload.get("declaration_names") or payload.get("names"))
    imports = core.validate_imports(payload.get("imports") or ["Mathlib"])
    max_depth = _bounded_int(payload, "max_depth", DEFAULT_MAX_DEPTH, 0, MAX_AUTO_DEPTH)
    max_claims = _bounded_int(payload, "max_claims", DEFAULT_MAX_CLAIMS, len(roots), MAX_AUTO_CLAIMS)
    environment = core.runtime_status()
    if not environment.get("ready"):
        raise ValueError(str(environment.get("message") or "Lean environment unavailable"))
    environment_hash = str(environment.get("environment_hash") or "")
    if not environment_hash:
        raise ValueError("pinned Lean environment has no environment hash")

    queue: deque[tuple[str, int]] = deque((name, 0) for name in roots)
    scheduled = set(roots)
    processed: set[str] = set()
    items: dict[str, dict[str, Any]] = {}
    dependency_parents: dict[str, set[str]] = {}
    substrate_refs: set[str] = set()
    unimportable_refs: set[str] = set()
    classification_errors: list[str] = []
    lean_invocations = 0
    lean_duration_ms = 0
    truncated = False

    while queue:
        depth = queue[0][1]
        current: list[str] = []
        while queue and queue[0][1] == depth and len(current) < MAX_BATCH_IMPORT:
            name, _ = queue.popleft()
            if name in processed:
                continue
            processed.add(name)
            current.append(name)
        if not current:
            continue

        inspections, inspection_errors, inspect_calls, inspect_ms = _inspect_batch(
            imports, current, environment_hash
        )
        lean_invocations += inspect_calls
        lean_duration_ms += inspect_ms

        batch = import_existing_declarations(
            {
                "declaration_names": current,
                "imports": imports,
                "area": payload.get("area"),
                "claim_author": payload.get("claim_author"),
                "proof_author": payload.get("proof_author"),
            }
        )
        lean_invocations += int(batch.get("lean_invocations") or 0)
        lean_duration_ms += int(batch.get("inspect_duration_ms") or 0) + int(batch.get("audit_duration_ms") or 0)
        batch_items = {str(item.get("declaration_name") or ""): dict(item) for item in batch.get("items", [])}

        next_candidates: dict[str, set[str]] = {}
        for name in current:
            item = batch_items.get(name, {"declaration_name": name, "status": "failed", "error": "batch import returned no result"})
            item["depth"] = depth
            if name in inspection_errors:
                item.setdefault("argument_warning", inspection_errors[name])
            inspected = inspections.get(name)
            if inspected and item.get("status") in {"imported", "already_registered"}:
                _attach_external_argument_evidence(name, inspected, environment)
                source_refs = list(inspected.get("proof_dependencies") or [])
                item["source_proof_dependencies"] = source_refs
                item["argument_evidence_origin"] = "external-declaration"
                if depth < max_depth:
                    for ref in source_refs:
                        ref = str(ref)
                        if not ref or ref == name or ref in scheduled:
                            continue
                        dependency_parents.setdefault(ref, set()).add(name)
                        if IMPORTABLE_LEAN_NAME.fullmatch(ref):
                            next_candidates.setdefault(ref, set()).add(name)
                        else:
                            unimportable_refs.add(ref)
            items[name] = item

        if depth >= max_depth or not next_candidates:
            continue

        known_theorems: list[str] = []
        unbound: list[str] = []
        for candidate in next_candidates:
            if core.FORMAL.claim_for_name(candidate, environment_hash) is not None:
                known_theorems.append(candidate)
            else:
                unbound.append(candidate)

        classifications, classify_calls, classify_ms, classify_errors = _classify_unbound(imports, unbound)
        lean_invocations += classify_calls
        lean_duration_ms += classify_ms
        classification_errors.extend(classify_errors)
        for name, kind in classifications.items():
            if kind == "theorem":
                known_theorems.append(name)
            else:
                substrate_refs.add(name)

        for candidate in known_theorems:
            if candidate in scheduled:
                continue
            if len(scheduled) >= max_claims:
                truncated = True
                break
            scheduled.add(candidate)
            queue.append((candidate, depth + 1))
        if len(scheduled) >= max_claims and any(c not in scheduled for c in known_theorems):
            truncated = True

    with core.ARGUMENT.connect() as conn:
        core.ARGUMENT._reconcile(conn, environment_hash)

    ordered_items = sorted(items.values(), key=lambda item: (int(item.get("depth") or 0), roots.index(item["declaration_name"]) if item.get("declaration_name") in roots else 10**9, str(item.get("declaration_name") or "")))
    root_items = [items[name] for name in roots if name in items]
    imported_count = sum(1 for item in ordered_items if item.get("status") == "imported")
    existing_count = sum(1 for item in ordered_items if item.get("status") == "already_registered")
    failed_count = sum(1 for item in ordered_items if item.get("status") == "failed")
    auto_items = [item for item in ordered_items if int(item.get("depth") or 0) > 0]
    return {
        "ok": all(item.get("status") in {"imported", "already_registered"} for item in root_items),
        "roots": roots,
        "requested_roots": len(roots),
        "max_depth": max_depth,
        "max_claims": max_claims,
        "processed_claims": len(ordered_items),
        "imported": imported_count,
        "already_registered": existing_count,
        "failed": failed_count,
        "auto_dependency_claims": len(auto_items),
        "truncated": truncated,
        "lean_invocations": lean_invocations,
        "lean_duration_ms": lean_duration_ms,
        "argument_evidence_origin": "external-declaration",
        "root_items": root_items,
        "items": ordered_items,
        "substrate_refs": sorted(substrate_refs),
        "unimportable_refs": sorted(unimportable_refs),
        "classification_errors": classification_errors,
        "dependency_parents": {name: sorted(parents) for name, parents in sorted(dependency_parents.items())},
    }
