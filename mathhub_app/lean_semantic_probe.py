from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from semantic_cone import ConstantInspection

IMPORT_RE = re.compile(r"^[A-Za-z0-9_.]+$")
# Keep command generation conservative. Quoted identifiers with whitespace are not
# accepted by this first adapter; callers can use canonical declaration names.
NAME_RE = re.compile(r"^[^\s;#]+$")

LEAN_PROBE_COMMAND = r'''
open Lean Elab Command

private def mathhubSemanticKind (info : ConstantInfo) : String :=
  match info with
  | .axiomInfo _ => "axiom"
  | .defnInfo _ => "definition"
  | .thmInfo _ => "theorem"
  | .opaqueInfo _ => "opaque"
  | .inductInfo _ => "inductive"
  | .ctorInfo _ => "constructor"
  | .recInfo _ => "recursor"
  | _ => "other"

private def mathhubSemanticValueRefs (info : ConstantInfo) : Array Name :=
  match info with
  | .defnInfo v => v.value.getUsedConstants
  | .thmInfo v => v.value.getUsedConstants
  | .opaqueInfo v => v.value.getUsedConstants
  | .inductInfo v => v.ctors.toArray
  | _ => #[]

elab "#mathhub_semantic_inspect " id:ident : command => do
  let name := id.getId
  let env ← getEnv
  let some info := env.find? name | throwError "unknown declaration {name}"
  logInfo m!"MATHHUB_SEM_KIND:{name}:{mathhubSemanticKind info}"
  for dep in info.type.getUsedConstants do
    if dep != name then logInfo m!"MATHHUB_SEM_TYPE_REF:{name}:{dep}"
  for dep in mathhubSemanticValueRefs info do
    if dep != name then logInfo m!"MATHHUB_SEM_VALUE_REF:{name}:{dep}"
'''


def validate_imports(imports: list[str]) -> list[str]:
    result = list(dict.fromkeys(str(x).strip() for x in imports if str(x).strip()))
    if not result:
        raise ValueError("at least one import is required")
    for module in result:
        if not IMPORT_RE.fullmatch(module):
            raise ValueError(f"invalid Lean import module: {module!r}")
    return result


def validate_names(names: list[str]) -> list[str]:
    result = list(dict.fromkeys(str(x).strip() for x in names if str(x).strip()))
    if not result:
        raise ValueError("at least one declaration name is required")
    for name in result:
        if not NAME_RE.fullmatch(name):
            raise ValueError(f"unsafe/unsupported Lean declaration name: {name!r}")
    return result


def probe_source(imports: list[str], names: list[str]) -> str:
    imports = validate_imports(imports)
    names = validate_names(names)
    import_lines = "\n".join(f"import {module}" for module in imports)
    inspect_lines = "\n".join(f"#mathhub_semantic_inspect {name}" for name in names)
    return f"{import_lines}\n\n{LEAN_PROBE_COMMAND}\n\n{inspect_lines}\n"


def lean_command(lake_bin: str, path: Path) -> list[str]:
    """Run Lean inside the checkout's Lake/elan environment.

    `lake env lean` is the stable project-aware invocation. Calling `lake lean`
    instead can be interpreted as a Lake target/script and is not the Lean CLI.
    """
    return [str(lake_bin), "env", "lean", str(path)]


def _marker_payload(line: str, marker: str) -> str | None:
    index = line.find(marker)
    if index < 0:
        return None
    return line[index + len(marker):].strip()


def parse_probe_output(output: str) -> dict[str, ConstantInspection]:
    kinds: dict[str, str] = {}
    type_refs: dict[str, list[str]] = {}
    value_refs: dict[str, list[str]] = {}

    for raw_line in str(output or "").splitlines():
        payload = _marker_payload(raw_line, "MATHHUB_SEM_KIND:")
        if payload is not None:
            name, sep, kind = payload.rpartition(":")
            if sep and name and kind:
                kinds[name] = kind.strip().lower()
            continue

        payload = _marker_payload(raw_line, "MATHHUB_SEM_TYPE_REF:")
        if payload is not None:
            name, sep, dep = payload.rpartition(":")
            if sep and name and dep:
                type_refs.setdefault(name, []).append(dep)
            continue

        payload = _marker_payload(raw_line, "MATHHUB_SEM_VALUE_REF:")
        if payload is not None:
            name, sep, dep = payload.rpartition(":")
            if sep and name and dep:
                value_refs.setdefault(name, []).append(dep)

    names = set(kinds) | set(type_refs) | set(value_refs)
    result: dict[str, ConstantInspection] = {}
    for name in sorted(names):
        result[name] = ConstantInspection(
            name=name,
            kind=kinds.get(name, "unknown"),
            type_refs=tuple(dict.fromkeys(type_refs.get(name, []))),
            value_refs=tuple(dict.fromkeys(value_refs.get(name, []))),
        )
    return result


def inspect_constants(
    checkout: Path,
    imports: list[str],
    names: list[str],
    *,
    timeout_seconds: int = 120,
    lake: str | None = None,
) -> dict[str, ConstantInspection]:
    checkout = Path(checkout).resolve()
    if not checkout.is_dir():
        raise ValueError(f"Lean checkout does not exist: {checkout}")
    source = probe_source(imports, names)
    lake_bin = lake or shutil.which("lake")
    if not lake_bin:
        raise RuntimeError("lake not found in PATH")

    fd, filename = tempfile.mkstemp(prefix="mathhub_semantic_", suffix=".lean", dir=checkout)
    os.close(fd)
    path = Path(filename)
    try:
        path.write_text(source, encoding="utf-8")
        try:
            proc = subprocess.run(
                lean_command(lake_bin, path),
                cwd=checkout,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env={**os.environ, "NO_COLOR": "1"},
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            raise TimeoutError(
                f"Lean semantic probe timed out after {timeout_seconds}s: {(stdout + stderr)[-2000:]}"
            ) from exc

        output = "\n".join(x for x in (proc.stdout.strip(), proc.stderr.strip()) if x)
        if proc.returncode != 0:
            raise RuntimeError(f"Lean semantic probe failed ({proc.returncode}):\n{output[-8000:]}")
        parsed = parse_probe_output(output)
        missing = [name for name in validate_names(names) if name not in parsed]
        if missing:
            raise RuntimeError(f"Lean probe returned no ConstantInfo markers for: {', '.join(missing)}")
        return parsed
    finally:
        path.unlink(missing_ok=True)


def serialize(inspections: dict[str, ConstantInspection]) -> dict[str, Any]:
    return {
        "format": "mathhub-lean-semantic-probe-v1",
        "constants": [
            {
                "name": item.name,
                "kind": item.kind,
                "type_refs": list(item.type_refs),
                "value_refs": list(item.value_refs),
            }
            for item in (inspections[name] for name in sorted(inspections))
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect Lean ConstantInfo for semantic-cone construction")
    parser.add_argument("checkout", type=Path, help="local pinned Lean/Lake project checkout")
    parser.add_argument("--import", dest="imports", action="append", required=True, help="Lean module to import; repeatable")
    parser.add_argument("--name", dest="names", action="append", required=True, help="declaration to inspect; repeatable")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    result = inspect_constants(args.checkout, args.imports, args.names, timeout_seconds=args.timeout)
    print(json.dumps(serialize(result), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
