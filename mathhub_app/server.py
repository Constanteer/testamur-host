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
import threading
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
SETUP_TIMEOUT_SECONDS = int(os.environ.get("MATHHUB_SETUP_TIMEOUT", "1800"))
AUTO_SETUP = os.environ.get("MATHHUB_AUTO_SETUP", "1").strip().lower() not in {"0", "false", "no", "off"}
MAX_SOURCE_CHARS = 200_000
IMPORT_RE = re.compile(r"^[A-Za-z0-9_.]+$")
DECL_NAME_RE = re.compile(r"\b(?:theorem|lemma|def)\s+([^\s:(]+)")
LEAN_NAME_RE = re.compile(r"^[^\s]+$")

DATA_ROOT = Path(os.environ.get("MATHHUB_DATA_DIR", str(ROOT / ".mathub"))).expanduser().resolve()
DATA_ROOT.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_ROOT / "registry.sqlite3"
REGISTRY = Registry(DB_PATH)
FORMAL = FormalIndex(DB_PATH)
ARGUMENT = ArgumentIndex(DB_PATH)
PRESENTATION_BUILDS = BuildPresentationRecorder(DB_PATH)
RUNTIME_SETUP_MARKER = DATA_ROOT / "runtime-setup.json"

_RUNTIME_SETUP_LOCK = threading.Lock()
_RUNTIME_SETUP_STATE: dict[str, Any] = {
    "running": False,
    "attempted": False,
    "stage": "",
    "step": 0,
    "steps": 0,
    "error": "",
    "started_at": 0.0,
    "finished_at": 0.0,
}


def runtime_setup_status() -> dict[str, Any]:
    with _RUNTIME_SETUP_LOCK:
        return dict(_RUNTIME_SETUP_STATE)


def _runtime_setup_fingerprint() -> str:
    payload = {
        "lean_toolchain": file_hash(ROOT / "lean-toolchain"),
        "lakefile": file_hash(ROOT / "lakefile.toml"),
        "manifest": file_hash(ROOT / "lake-manifest.json"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _runtime_setup_marker_current() -> bool:
    try:
        data = json.loads(RUNTIME_SETUP_MARKER.read_text(encoding="utf-8"))
        return bool(data.get("complete")) and data.get("fingerprint") == _runtime_setup_fingerprint()
    except (OSError, json.JSONDecodeError, AttributeError):
        return False


def _write_runtime_setup_marker() -> None:
    RUNTIME_SETUP_MARKER.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME_SETUP_MARKER.write_text(
        json.dumps({"complete": True, "fingerprint": _runtime_setup_fingerprint()}),
        encoding="utf-8",
    )


def _setup_output(proc: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(
        chunk for chunk in [(proc.stdout or "").strip(), (proc.stderr or "").strip()] if chunk
    )


def _mathlib_checkout_is_corrupt(output: str) -> bool:
    text = str(output or "").casefold()
    markers = (
        "could not resolve 'head' to a commit",
        'could not resolve "head" to a commit',
        "repository may be corrupt",
        "not a git repository",
        "bad object head",
        "fatal: ambiguous argument 'head'",
        "invalid object name 'head'",
    )
    return any(marker in text for marker in markers)


def _transient_git_failure(output: str) -> bool:
    text = str(output or "").casefold()
    markers = (
        "early eof",
        "invalid index-pack output",
        "rpc failed",
        "curl 92",
        "http/2 stream",
        "connection reset",
        "connection timed out",
        "could not resolve host",
        "temporary failure",
    )
    return any(marker in text for marker in markers)


def _reset_mathlib_checkout() -> bool:
    """Remove only the disposable Mathlib checkout; durable MathHub data lives elsewhere."""
    checkout = ROOT / ".lake" / "packages" / "mathlib"
    try:
        if checkout.is_symlink() or checkout.is_file():
            checkout.unlink(missing_ok=True)
            return True
        if checkout.is_dir():
            shutil.rmtree(checkout)
            return True
    except OSError:
        return False
    return False


def _run_setup_command(command: list[str], *, retries: int = 1) -> None:
    last_output = ""
    for attempt in range(1, max(1, retries) + 1):
        proc = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=SETUP_TIMEOUT_SECONDS,
            env={**os.environ, "NO_COLOR": "1"},
        )
        if proc.returncode == 0:
            return

        output = _setup_output(proc)
        last_output = output
        is_lake_update = len(command) >= 2 and command[-1] == "update"
        if is_lake_update and _mathlib_checkout_is_corrupt(output):
            _reset_mathlib_checkout()
            if attempt < retries:
                time.sleep(min(2.0 * attempt, 5.0))
                continue
        if is_lake_update and _transient_git_failure(output) and attempt < retries:
            time.sleep(min(2.0 * attempt, 5.0))
            continue

        tail = output[-4000:] if output else f"exit code {proc.returncode}"
        raise RuntimeError(f"{' '.join(command[1:])} failed: {tail}")

    tail = last_output[-4000:] if last_output else "setup command failed"
    raise RuntimeError(f"{' '.join(command[1:])} failed after {retries} attempts: {tail}")


def _runtime_setup_worker(lake: str) -> None:
    error = ""
    try:
        commands: list[tuple[str, list[str]]] = []
        elan = shutil.which("elan")
        toolchain_file = ROOT / "lean-toolchain"
        if elan and toolchain_file.is_file():
            toolchain = toolchain_file.read_text(encoding="utf-8").strip()
            if toolchain:
                commands.extend(
                    (
                        ("Installing the pinned Lean toolchain…", [elan, "toolchain", "install", toolchain]),
                        ("Selecting the Lean toolchain…", [elan, "default", toolchain]),
                    )
                )
        commands.extend(
            (
                ("Resolving Mathlib dependencies…", [lake, "update"]),
                ("Downloading the compiled Mathlib cache…", [lake, "exe", "cache", "get"]),
            )
        )
        with _RUNTIME_SETUP_LOCK:
            _RUNTIME_SETUP_STATE["steps"] = len(commands)
        for index, (stage, command) in enumerate(commands, start=1):
            with _RUNTIME_SETUP_LOCK:
                _RUNTIME_SETUP_STATE["stage"] = stage
                _RUNTIME_SETUP_STATE["step"] = index
            retries = 3 if len(command) >= 2 and command[-1] == "update" else 1
            _run_setup_command(command, retries=retries)
        _write_runtime_setup_marker()
        with _RUNTIME_SETUP_LOCK:
            _RUNTIME_SETUP_STATE["stage"] = "Math library ready"
            _RUNTIME_SETUP_STATE["step"] = len(commands)
    except subprocess.TimeoutExpired:
        error = f"Math library setup timed out after {SETUP_TIMEOUT_SECONDS}s"
    except Exception as exc:
        error = str(exc)
    finally:
        with _RUNTIME_SETUP_LOCK:
            _RUNTIME_SETUP_STATE["running"] = False
            _RUNTIME_SETUP_STATE["attempted"] = True
            _RUNTIME_SETUP_STATE["error"] = error
            _RUNTIME_SETUP_STATE["finished_at"] = time.time()


def request_runtime_setup(*, force: bool = False) -> bool:
    """Start a best-effort background setup of the pinned Lean/Mathlib environment.

    Normal users should never need to run Lake commands manually. The worker is
    deliberately started only by the running server, never merely by importing
    this module in tests or tooling.
    """
    if not AUTO_SETUP:
        return False
    lake = shutil.which("lake")
    if not lake:
        return False
    if _runtime_setup_marker_current():
        return False
    now = time.time()
    with _RUNTIME_SETUP_LOCK:
        if _RUNTIME_SETUP_STATE["running"]:
            return False
        if (
            not force
            and _RUNTIME_SETUP_STATE["attempted"]
            and now - float(_RUNTIME_SETUP_STATE["finished_at"] or 0.0) < 300
        ):
            return False
        _RUNTIME_SETUP_STATE.update(
            {
                "running": True,
                "attempted": True,
                "stage": "Preparing the Math library…",
                "step": 0,
                "steps": 0,
                "error": "",
                "started_at": now,
                "finished_at": 0.0,
            }
        )
    threading.Thread(
        target=_runtime_setup_worker,
        args=(lake,),
        name="mathhub-runtime-setup",
        daemon=True,
    ).start()
    return True


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
    environment_present = bool(lake and manifest and mathlib_checkout)
    prepared = _runtime_setup_marker_current()
    ready = bool(environment_present and (prepared or not AUTO_SETUP))
    setup = runtime_setup_status()
    if ready:
        message = "Math library ready"
    elif setup.get("running"):
        message = "Preparing the Math library automatically…"
    elif not lake:
        message = "Lean runtime unavailable"
    elif setup.get("error"):
        message = "Math library setup needs attention"
    else:
        message = "Math library is not ready yet"
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
        "auto_setup": AUTO_SETUP,
        "setup_prepared": prepared,
        "setup_running": bool(setup.get("running")),
        "setup_stage": str(setup.get("stage") or ""),
        "setup_step": int(setup.get("step") or 0),
        "setup_steps": int(setup.get("steps") or 0),
        "setup_error": str(setup.get("error") or ""),
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

LEAN_DIRECT_DEP_COMMAND = r'''
open Lean Elab Command

private def mathhubDirectProofConstants (info : ConstantInfo) : Array Name :=
  match info with
  | .defnInfo v => v.value.getUsedConstants
  | .thmInfo v => v.value.getUsedConstants
  | .opaqueInfo v => v.value.getUsedConstants
  | _ => #[]

elab "#mathhub_direct_deps " id:ident : command => do
  let name := id.getId
  let env ← getEnv
  let some info := env.find? name | throwError "unknown declaration {name}"
  logInfo m!"MATHHUB_DIRECT_BEGIN:{name}"
  let pretty ← liftTermElabM do
    Lean.PrettyPrinter.ppExpr info.type
  logInfo m!"MATHHUB_PRETTY_TYPE:{pretty.pretty 1000000}"
  for dep in info.type.getUsedConstants do
    if dep != name then logInfo m!"MATHHUB_STATEMENT_REF:{dep}"
  for dep in mathhubDirectProofConstants info do
    if dep != name then logInfo m!"MATHHUB_PROOF_REF:{dep}"
  logInfo m!"MATHHUB_DIRECT_END:{name}"
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


def direct_dependency_source(imports: list[str], name: str) -> str:
    import_lines = "\n".join(f"import {module}" for module in imports)
    return (
        f"{import_lines}\n\n{LEAN_DIRECT_DEP_COMMAND}\n"
        f"#mathhub_direct_deps {name}\n"
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


def inspect_direct_dependencies(payload: dict[str, Any]) -> dict[str, Any]:
    """Fast dependency inspection for interactive graph expansion.

    Unlike inspect_declaration(), this deliberately skips MinImports transitive
    closure, type partitioning and #print axioms. It reads the declaration type
    and proof constants only, so the graph can recurse one level at a time
    without paying the full audit cost on every click.
    """
    imports = validate_imports(payload.get("imports"))
    name = str(payload.get("declaration_name") or payload.get("name") or "").strip()
    if not name or not LEAN_NAME_RE.fullmatch(name):
        raise ValueError("declaration_name must be a Lean identifier")
    source = direct_dependency_source(imports, name)
    run = invoke_lean(source, timeout_seconds=min(INDEX_TIMEOUT_SECONDS, 20))
    if run.get("engine_error"):
        return {"error": run["engine_error"], **run}
    output = run.get("output", "")
    statement = parse_marker(output, "MATHHUB_STATEMENT_REF:")
    proof = parse_marker(output, "MATHHUB_PROOF_REF:")
    direct = unique([*statement, *proof])
    return {
        **run,
        "declaration_name": name,
        "imports": imports,
        "statement_dependencies": statement,
        "proof_dependencies": proof,
        "direct_dependencies": direct,
        "dependencies": direct,
        "transitive_dependencies": [],
        "axioms": [],
        "pretty_type": parse_pretty_type(output),
        "inspection_mode": "lean-direct",
        "lean_invocations": 1,
        "fast_path": True,
        "source_hash": hashlib.sha256(source.encode()).hexdigest(),
    }


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
            request_runtime_setup()
            self.json_response(200, runtime_status())
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if path == "/api/runtime/setup":
                request_runtime_setup(force=True)
                self.json_response(202, runtime_status())
                return
            if path == "/api/lean/check":
                self.json_response(200, audit(payload))
                return
            if path == "/api/lean/inspect":
                self.json_response(200, inspect_declaration(payload))
                return
            if path == "/api/lean/direct-dependencies":
                self.json_response(200, inspect_direct_dependencies(payload))
                return
            self.json_response(404, {"error": "unknown API endpoint"})
        except (ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
            self.json_response(400, {"error": str(exc), "ok": False})
        except Exception as exc:
            self.json_response(500, {"error": f"internal error: {exc}", "ok": False})


if __name__ == "__main__":
    request_runtime_setup()
    print(f"MathHub engine: http://{HOST}:{PORT}")
    print(runtime_status()["message"])
    print("Semantic evidence writes: FormalBinding + FormalTypeShape/Partition + ProofDependency.")
    print("Security: local trusted-code prototype only.")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()