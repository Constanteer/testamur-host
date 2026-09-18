#!/usr/bin/env python3
"""Local Lean engine and append-only MathHub registry.

The public server is mathhub.py. This module owns Lean execution and machine
provenance. It is a trusted local workstation, not an untrusted-code sandbox.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from argument_index import ArgumentIndex
from formal_index import FormalIndex
from presentation_build import BuildPresentationRecorder
from registry import Registry
from trust import classify_axioms

ROOT = Path(__file__).resolve().parent
HOST = os.environ.get("MATHHUB_HOST", "127.0.0.1")
PORT = int(os.environ.get("MATHHUB_PORT", "8000"))
COMPILE_TIMEOUT_SECONDS = int(os.environ.get("MATHHUB_COMPILE_TIMEOUT", "45"))
INDEX_TIMEOUT_SECONDS = int(os.environ.get("MATHHUB_INDEX_TIMEOUT", "120"))
MAX_SOURCE_CHARS = 200_000
IMPORT_RE = re.compile(r"^[A-Za-z0-9_.]+$")
DECL_NAME_RE = re.compile(r"\b(?:theorem|lemma|def)\s+([^\s:(]+)")
LEAN_NAME_RE = re.compile(r"^[^\s]+$")

DATA_ROOT = Path(os.environ.get("MATHHUB_DATA_DIR", str(ROOT / ".mathub"))).expanduser().resolve()\nDB_PATH = DATA_ROOT / "registry.sqlite3"
REGISTRY = Registry(DB_PATH)
FORMAL = FormalIndex(DB_PATH)
ARGUMENT = ArgumentIndex(DB_PATH)
PRESENTATION_BUILDS = BuildPresentationRecorder(DB_PATH)


def file_hash(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def run_version(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=5)
        lines = (result.stdout or result.stderr).strip().splitlines()
        return lines[0] if lines else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def mathlib_revision() -> str | None:
    manifest = ROOT / "lake-manifest.json"
    if not manifest.exists():
        return None
    try:
        for package in json.loads(manifest.read_text()).get("packages", []):
            if package.get("name") == "mathlib":
                return package.get("rev") or (package.get("git") or {}).get("rev")
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return None


def environment_metadata() -> dict[str, Any]:
    payload = {
        "lean_toolchain_hash": file_hash(ROOT / "lean-toolchain"),
        "lake_manifest_hash": file_hash(ROOT / "lake-manifest.json"),
        "lakefile_hash": file_hash(ROOT / "lakefile.toml"),
        "mathlib_revision": mathlib_revision(),
    }
    payload["environment_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return payload


def runtime_status() -> dict[str, Any]:
    lake = shutil.which("lake")
    lean = shutil.which("lean")
    manifest = (ROOT / "lake-manifest.json").exists()
    mathlib_checkout = (ROOT / ".lake/packages/mathlib").exists()
    ready = bool(lake and manifest and mathlib_checkout)
    if ready:
        message = "Pinned Lake environment available"
    elif not lake:
        message = "Lake not found; install Lean/elan first"
    else:
        message = "Run `lake update` and `lake exe cache get` once"
    return {
        "ready": ready,
        "message": message,
        "lake": lake,
        "lean": lean,
        "lean_version": run_version([lean, "--version"]) if lean else None,
        "lake_version": run_version([lake, "--version"]) if lake else None,
        "compile_timeout_seconds": COMPILE_TIMEOUT_SECONDS,
        "index_timeout_seconds": INDEX_TIMEOUT_SECONDS,
        "registry": str(REGISTRY.path),
        **environment_metadata(),
    }


def validate_imports(raw: Any) -> list[str]:
    imports = raw or ["Mathlib"]
    if not isinstance(imports, list) or not all(isinstance(item, str) for item in imports):
        raise ValueError("imports must be a list of Lean module names")
    imports = [item.strip() for item in imports if item.strip()] or ["Mathlib"]
    for module in imports:
        if not IMPORT_RE.fullmatch(module):
            raise ValueError(f"invalid import module: {module!r}")
    return imports


def validate_payload(payload: dict[str, Any]) -> tuple[list[str], str, str, str]:
    imports = validate_imports(payload.get("imports"))
    declaration = str(payload.get("declaration") or "").strip()
    proof = str(payload.get("proof") or "").strip()
    theorem_name = str(payload.get("theorem_name") or "").strip()
    if not declaration:
        raise ValueError("formal Lean declaration is required")
    if len(declaration) + len(proof) > MAX_SOURCE_CHARS:
        raise ValueError("Lean source is too large")
    if ":=" in declaration:
        raise ValueError("put proof source in the Proof field, not inside the Claim declaration")
    match = DECL_NAME_RE.search(declaration)
    if match:
        theorem_name = match.group(1)
    if not theorem_name or not LEAN_NAME_RE.fullmatch(theorem_name):
        raise ValueError("could not determine the Lean declaration name")
    return imports, declaration, proof or "by\n  sorry", theorem_name


# Lean 4.34: Expr.getUsedConstants returns Array Name. Statement and proof refs are
# emitted separately. Only MATHHUB_PROOF_REF observations may become ProofDependency.
# Inspector output is bracketed because theorem proofs may themselves emit logInfo;
# consumers select the final inspector-owned block before parsing formal evidence.
LEAN_AUDIT_COMMAND = r'''
open Lean Elab Command

private def mathhubProofConstants (info : ConstantInfo) : Array Name :=
  match info with
  | .defnInfo v => v.value.getUsedConstants
  | .thmInfo v => v.value.getUsedConstants
  | .opaqueInfo v => v.value.getUsedConstants
  | _ => #[]

private partial def mathhubTypePartition (e : Expr) : List Expr × Expr :=
  match e with
  | .forallE _ domain body _ =>
      let (domains, result) := mathhubTypePartition body
      (domain :: domains, result)
  | .letE _ _ value body _ =>
      mathhubTypePartition (body.instantiate1 value)
  | .mdata _ body =>
      mathhubTypePartition body
  | _ => ([], e)

private partial def mathhubTransparentCore (e : Expr) : Expr :=
  match e with
  | .letE _ _ value body _ =>
      mathhubTransparentCore (body.instantiate1 value)
  | .mdata _ body =>
      mathhubTransparentCore body
  | _ => e

elab "#mathhub_inspect_decl " id:ident : command => do
  let name := id.getId
  let env ← getEnv
  let some info := env.find? name | throwError "unknown declaration {name}"
  logInfo m!"MATHHUB_INSPECT_BEGIN:{name}"
  logInfo m!"MATHHUB_DECL_NAME:{name}"
  logInfo m!"MATHHUB_TYPE_HASH:{info.type.hash}"
  let pretty ← liftTermElabM do
    Lean.PrettyPrinter.ppExpr info.type
  logInfo m!"MATHHUB_PRETTY_TYPE:{pretty.pretty 1000000}"
  let (binderDomains, resultType) := mathhubTypePartition info.type
  let binderCount := binderDomains.length
  logInfo m!"MATHHUB_TYPE_SHAPE_BEGIN:{name}"
  logInfo m!"MATHHUB_TYPE_SHAPE_VERSION:lean-expr-result-v1"
  logInfo m!"MATHHUB_RESULT_BINDERS:{binderCount}"
  match resultType.getAppFn with
  | .const head _ => logInfo m!"MATHHUB_RESULT_HEAD:{head}"
  | _ => pure ()
  logInfo m!"MATHHUB_TYPE_SHAPE_END:{name}"
  logInfo m!"MATHHUB_TYPE_PARTITION_BEGIN:{name}"
  logInfo m!"MATHHUB_TYPE_PARTITION_VERSION:lean-expr-partition-v1"
  logInfo m!"MATHHUB_PARTITION_BINDERS:{binderCount}"
  for domain in binderDomains do
    let domainCore := mathhubTransparentCore domain
    match domainCore.getAppFn with
    | .const head _ => logInfo m!"MATHHUB_BINDER_DOMAIN_HEAD:{head}"
    | _ => pure ()
  for dep in resultType.getUsedConstants do
    if dep != name then logInfo m!"MATHHUB_RESULT_REF:{dep}"
  logInfo m!"MATHHUB_TYPE_PARTITION_END:{name}"
  for dep in info.type.getUsedConstants do
    if dep != name then logInfo m!"MATHHUB_STATEMENT_REF:{dep}"
  for dep in mathhubProofConstants info do
    if dep != name then logInfo m!"MATHHUB_PROOF_REF:{dep}"
  let transitive ← Mathlib.Command.MinImports.getVisited name
  for dep in transitive do
    if dep != name then logInfo m!"MATHHUB_TRANSITIVE:{dep}"
  logInfo m!"MATHHUB_INSPECT_END:{name}"
'''


def source_for(imports: list[str], declaration: str, proof: str, name: str, audit_mode: bool) -> str:
    import_lines = "\n".join(f"import {module}" for module in imports)
    body = f"{declaration} :=\n{proof}\n"
    if not audit_mode:
        return f"{import_lines}\n\n{body}\n#print axioms {name}\n"
    return (
        f"import Mathlib.Tactic.MinImports\n{import_lines}\n\n{LEAN_AUDIT_COMMAND}\n"
        f"#min_imports in\n{body}\n#print axioms {name}\n#mathhub_inspect_decl {name}\n"
    )


def inspection_source(imports: list[str], name: str) -> str:
    import_lines = "\n".join(f"import {module}" for module in imports)
    return (
        f"import Mathlib.Tactic.MinImports\n{import_lines}\n\n{LEAN_AUDIT_COMMAND}\n"
        f"#check {name}\n#print axioms {name}\n#mathhub_inspect_decl {name}\n"
    )


def invoke_lean(source: str, *, timeout_seconds: int) -> dict[str, Any]:
    status = runtime_status()
    if not status["ready"]:
        return {"ok": False, "engine_error": status["message"], "output": "", "duration_ms": 0}
    fd, filename = tempfile.mkstemp(prefix="mathhub_", suffix=".lean", dir=ROOT)
    os.close(fd)
    path = Path(filename)
    try:
        path.write_text(source)
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                [status["lake"], "lean", str(path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env={**os.environ, "NO_COLOR": "1"},
            )
            duration_ms = round((time.perf_counter() - started) * 1000)
            output = "\n".join(
                chunk for chunk in [proc.stdout.strip(), proc.stderr.strip()] if chunk
            ).strip()
            return {
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
                "output": output,
                "duration_ms": duration_ms,
            }
        except subprocess.TimeoutExpired as exc:
            duration_ms = round((time.perf_counter() - started) * 1000)
            output = "\n".join(chunk for chunk in [(exc.stdout or ""), (exc.stderr or "")] if chunk)
            return {
                "ok": False,
                "returncode": 124,
                "output": output,
                "duration_ms": duration_ms,
                "timed_out": True,
            }
    finally:
        path.unlink(missing_ok=True)


def unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def parse_axioms(output: str) -> list[str]:
    # `#print axioms` is emitted after the theorem proof. A proof may log an
    # earlier lookalike line, so the final matching command output wins.
    matches = re.findall(r"depends on axioms:\s*\[([^\]]*)\]", output or "")
    if not matches:
        return []
    return unique([item.strip().strip("'\"") for item in matches[-1].split(",")])


def parse_imports(output: str) -> list[str]:
    return unique(
        re.findall(
            r"(?m)^\s*(?:[^\n]*?\binfo:\s*)?(?:public\s+)?import\s+([A-Za-z0-9_.]+)\s*$",
            output,
        )
    )


def inspector_payload(output: str) -> str:
    """Select the final complete #mathhub_inspect_decl block when present.

    Older recorded output had no outer bracket, so absence falls back to the full
    string for backward compatibility. New audits are resistant to proof-emitted
    lookalike markers because the real inspector runs after the theorem proof.
    """
    text = str(output or "")
    ends = list(re.finditer(r"MATHHUB_INSPECT_END:([^\s]+)", text))
    if not ends:
        return text
    end = ends[-1]
    name = end.group(1)
    prefix = text[: end.start()]
    begins = list(
        re.finditer(re.escape("MATHHUB_INSPECT_BEGIN:" + name) + r"(?=\s|$)", prefix)
    )
    if not begins:
        return text
    return prefix[begins[-1].end() :]


def parse_marker(output: str, marker: str) -> list[str]:
    payload = inspector_payload(output)
    return unique(re.findall(re.escape(marker) + r"([^\s]+)", payload))


def parse_type_hash(output: str) -> str:
    match = re.search(r"MATHHUB_TYPE_HASH:([0-9]+)", inspector_payload(output))
    return match.group(1) if match else ""


def parse_pretty_type(output: str) -> str:
    match = re.search(r"(?m)MATHHUB_PRETTY_TYPE:(.*)$", inspector_payload(output))
    return match.group(1).strip() if match else ""


def fingerprint(type_hash: str, environment_hash: str) -> str:
    if not (type_hash and environment_hash):
        return ""
    return hashlib.sha256(f"lean-expr-v1:{environment_hash}:{type_hash}".encode()).hexdigest()


def enrich(result: dict[str, Any], output: str) -> None:
    result["statement_dependencies"] = parse_marker(output, "MATHHUB_STATEMENT_REF:")
    result["proof_dependencies"] = parse_marker(output, "MATHHUB_PROOF_REF:")
    result["direct_dependencies"] = result["proof_dependencies"]
    result["transitive_dependencies"] = parse_marker(output, "MATHHUB_TRANSITIVE:")
    result["dependencies"] = result["transitive_dependencies"]
    result["type_hash"] = parse_type_hash(output)
    result["pretty_type"] = parse_pretty_type(output)
    result["formal_fingerprint"] = fingerprint(
        result["type_hash"], environment_metadata()["environment_hash"]
    )
    result["type_shape"] = FormalIndex._shape_from_output(output)
    result["type_partition"] = FormalIndex._partition_from_output(output)


def audit(payload: dict[str, Any]) -> dict[str, Any]:
    imports, declaration, proof, theorem_name = validate_payload(payload)
    compile_source = source_for(imports, declaration, proof, theorem_name, False)
    compile_result = invoke_lean(compile_source, timeout_seconds=COMPILE_TIMEOUT_SECONDS)
    if compile_result.get("engine_error"):
        return {"error": compile_result["engine_error"], **compile_result}

    compile_output = compile_result.get("output", "")
    axioms = parse_axioms(compile_output)
    trust = classify_axioms(axioms)
    result: dict[str, Any] = {
        **compile_result,
        "theorem_name": theorem_name,
        "axioms": axioms,
        "contains_sorry": "sorryAx" in axioms or "declaration uses 'sorry'" in compile_output,
        "uses_trust_compiler": bool(trust["compiler_trust_axioms"]),
        "minimal_imports": [],
        "statement_dependencies": [],
        "proof_dependencies": [],
        "direct_dependencies": [],
        "transitive_dependencies": [],
        "dependencies": [],
        "type_hash": "",
        "pretty_type": "",
        "formal_fingerprint": "",
        "type_shape": None,
        "type_partition": None,
        "source_hash": hashlib.sha256(compile_source.encode()).hexdigest(),
    }
    if not compile_result.get("ok") or not payload.get("audit"):
        return result

    audit_result = invoke_lean(
        source_for(imports, declaration, proof, theorem_name, True),
        timeout_seconds=INDEX_TIMEOUT_SECONDS,
    )
    audit_output = audit_result.get("output", "")
    result["audit_ok"] = audit_result.get("ok", False)
    result["audit_duration_ms"] = audit_result.get("duration_ms", 0)
    result["duration_ms"] = result.get("duration_ms", 0) + audit_result.get("duration_ms", 0)
    result["minimal_imports"] = parse_imports(audit_output)
    enrich(result, audit_output)
    audit_axioms = parse_axioms(audit_output)
    if audit_axioms:
        result["axioms"] = audit_axioms
        result["contains_sorry"] = "sorryAx" in audit_axioms or result["contains_sorry"]
        result["uses_trust_compiler"] = bool(
            classify_axioms(audit_axioms)["compiler_trust_axioms"]
        )
    if not audit_result.get("ok"):
        result["audit_error"] = (
            f"Dependency indexing timed out after {INDEX_TIMEOUT_SECONDS}s."
            if audit_result.get("timed_out")
            else "Proof compiled, but dependency instrumentation failed."
        )
    result["output"] = (
        "=== compile ===\n" + compile_output + "\n\n=== dependency audit ===\n" + audit_output
    )
    return result


def inspect_declaration(payload: dict[str, Any]) -> dict[str, Any]:
    imports = validate_imports(payload.get("imports"))
    name = str(payload.get("declaration_name") or payload.get("name") or "").strip()
    if not name or not LEAN_NAME_RE.fullmatch(name):
        raise ValueError("declaration_name must be a Lean identifier")
    source = inspection_source(imports, name)
    run = invoke_lean(source, timeout_seconds=INDEX_TIMEOUT_SECONDS)
    if run.get("engine_error"):
        return {"error": run["engine_error"], **run}
    output = run.get("output", "")
    axioms = parse_axioms(output)
    result: dict[str, Any] = {
        **run,
        "declaration_name": name,
        "imports": imports,
        "axioms": axioms,
        "statement_dependencies": [],
        "proof_dependencies": [],
        "direct_dependencies": [],
        "transitive_dependencies": [],
        "dependencies": [],
        "type_hash": "",
        "pretty_type": "",
        "formal_fingerprint": "",
        "type_shape": None,
        "type_partition": None,
        "source_hash": hashlib.sha256(source.encode()).hexdigest(),
        **classify_axioms(axioms),
    }
    enrich(result, output)
    return result


def build_registered_proof(proof_id: str) -> dict[str, Any]:
    proof = REGISTRY.get_proof(proof_id)
    result = audit(
        {
            "imports": proof["imports"],
            "declaration": proof["formal_declaration"],
            "proof": proof["proof_source"],
            "theorem_name": proof["lean_name"],
            "audit": True,
        }
    )
    environment = runtime_status()
    build = REGISTRY.store_build(proof_id, result, environment)
    formal_binding = FORMAL.record_build(proof, build, result, environment)
    argument = ARGUMENT.record_build(proof, build, result, environment)
    presentation = PRESENTATION_BUILDS.record(proof, build, result, environment)
    return {
        **build,
        "formal_binding": formal_binding,
        "argument": argument,
        "presentation": presentation,
        "pretty_type": result.get("pretty_type", ""),
        "formal_fingerprint": result.get("formal_fingerprint", ""),
        "type_shape": result.get("type_shape"),
        "type_partition": result.get("type_partition"),
    }


def register_claim(payload: dict[str, Any]) -> dict[str, Any]:
    created = REGISTRY.create_claim(payload)
    proof_id = created.get("created_proof_id")
    build = None
    build_error = None
    if proof_id and payload.get("build", True):
        if runtime_status()["ready"]:
            try:
                build = build_registered_proof(proof_id)
            except Exception as exc:
                build_error = str(exc)
        else:
            build_error = "Lean environment unavailable; Proof saved but not built"
    result = REGISTRY.get_claim(created["claim_id"])
    result["created_proof_id"] = proof_id
    result["created_build"] = build
    result["build_error"] = build_error
    return result


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def json_response(self, status: int, body: Any) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> dict[str, Any]:
        size = int(self.headers.get("Content-Length", "0"))
        if size < 0 or size > MAX_SOURCE_CHARS * 3:
            raise ValueError("invalid request size")
        if not size:
            return {}
        payload = json.loads(self.rfile.read(size).decode())
        if not isinstance(payload, dict):
            raise ValueError("JSON object expected")
        return payload

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/.mathub"):
            self.send_error(404)
            return
        if path == "/api/runtime":
            self.json_response(200, runtime_status())
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if path == "/api/lean/check":
                self.json_response(200, audit(payload))
                return
            if path == "/api/lean/inspect":
                self.json_response(200, inspect_declaration(payload))
                return
            self.json_response(404, {"error": "unknown API endpoint"})
        except (ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
            self.json_response(400, {"error": str(exc), "ok": False})
        except Exception as exc:
            self.json_response(500, {"error": f"internal error: {exc}", "ok": False})


if __name__ == "__main__":
    print(f"MathHub engine: http://{HOST}:{PORT}")
    print(runtime_status()["message"])
    print("Semantic evidence writes: FormalBinding + FormalTypeShape/Partition + ProofDependency.")
    print("Security: local trusted-code prototype only.")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()