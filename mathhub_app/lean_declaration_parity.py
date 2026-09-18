from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from lean_semantic_probe import lean_command

IMPORT_RE = re.compile(r"^[A-Za-z0-9_.]+$")
NAME_RE = re.compile(r"^[^\s;#]+$")
FORMAT = "mathhub-lean-declaration-parity-v1"

LEAN_FINGERPRINT_COMMAND = r'''
open Lean Elab Command

private def mathhubFpKind (info : ConstantInfo) : String :=
  match info with
  | .axiomInfo _ => "axiom"
  | .defnInfo _ => "definition"
  | .thmInfo _ => "theorem"
  | .opaqueInfo _ => "opaque"
  | .inductInfo _ => "inductive"
  | .ctorInfo _ => "constructor"
  | .recInfo _ => "recursor"
  | _ => "other"

private def mathhubFpValueHash? (info : ConstantInfo) : Option UInt64 :=
  match info with
  | .defnInfo v => some v.value.hash
  | .thmInfo v => some v.value.hash
  | .opaqueInfo v => some v.value.hash
  | _ => none

elab "#mathhub_decl_fingerprint " id:ident : command => do
  let name := id.getId
  let env ← getEnv
  let some info := env.find? name | throwError "unknown declaration {name}"
  logInfo m!"MATHHUB_FP_KIND:{name}:{mathhubFpKind info}"
  logInfo m!"MATHHUB_FP_TYPE:{name}:{info.type.hash}"
  if let some h := mathhubFpValueHash? info then
    logInfo m!"MATHHUB_FP_VALUE:{name}:{h}"
  match info with
  | .inductInfo v =>
      for ctor in v.ctors do
        let some ci := env.find? ctor | throwError "missing constructor {ctor} for {name}"
        logInfo m!"MATHHUB_FP_CTOR:{name}:{ctor}:{ci.type.hash}"
  | _ => pure ()
'''


@dataclass(frozen=True)
class DeclarationFingerprint:
    name: str
    kind: str
    type_hash: str
    value_hash: str | None = None
    constructors: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "type_hash": self.type_hash,
            "value_hash": self.value_hash,
            "constructors": [
                {"name": name, "type_hash": type_hash}
                for name, type_hash in self.constructors
            ],
        }


def _safe_import(module: str) -> str:
    value = str(module or "").strip()
    if not IMPORT_RE.fullmatch(value):
        raise ValueError(f"invalid Lean import module: {module!r}")
    return value


def _safe_names(names: list[str]) -> list[str]:
    result = list(dict.fromkeys(str(x).strip() for x in names if str(x).strip()))
    if not result:
        raise ValueError("at least one declaration name is required")
    for name in result:
        if not NAME_RE.fullmatch(name):
            raise ValueError(f"unsafe/unsupported Lean declaration name: {name!r}")
    return result


def fingerprint_source(module: str, names: list[str]) -> str:
    module = _safe_import(module)
    names = _safe_names(names)
    calls = "\n".join(f"#mathhub_decl_fingerprint {name}" for name in names)
    return f"import {module}\n\n{LEAN_FINGERPRINT_COMMAND}\n\n{calls}\n"


def _payload(line: str, marker: str) -> str | None:
    pos = line.find(marker)
    return None if pos < 0 else line[pos + len(marker):].strip()


def parse_fingerprint_output(output: str) -> dict[str, DeclarationFingerprint]:
    kinds: dict[str, str] = {}
    types: dict[str, str] = {}
    values: dict[str, str] = {}
    ctors: dict[str, list[tuple[str, str]]] = {}

    for line in str(output or "").splitlines():
        p = _payload(line, "MATHHUB_FP_KIND:")
        if p is not None:
            name, sep, kind = p.rpartition(":")
            if sep:
                kinds[name] = kind
            continue
        p = _payload(line, "MATHHUB_FP_TYPE:")
        if p is not None:
            name, sep, h = p.rpartition(":")
            if sep:
                types[name] = h
            continue
        p = _payload(line, "MATHHUB_FP_VALUE:")
        if p is not None:
            name, sep, h = p.rpartition(":")
            if sep:
                values[name] = h
            continue
        p = _payload(line, "MATHHUB_FP_CTOR:")
        if p is not None:
            prefix, sep, h = p.rpartition(":")
            root_ctor, sep2, ctor = prefix.rpartition(":")
            if sep and sep2:
                # Root and constructor names share the same namespace for ordinary
                # structures; identify the root as the longest known prefix when
                # possible. The command format puts root before ctor, and root is
                # known from KIND/TYPE markers.
                root = next(
                    (name for name in kinds if prefix.startswith(name + ":")),
                    root_ctor,
                )
                ctor_name = prefix[len(root) + 1:] if prefix.startswith(root + ":") else ctor
                ctors.setdefault(root, []).append((ctor_name, h))

    names = set(kinds) | set(types) | set(values) | set(ctors)
    result: dict[str, DeclarationFingerprint] = {}
    for name in sorted(names):
        result[name] = DeclarationFingerprint(
            name=name,
            kind=kinds.get(name, "unknown"),
            type_hash=types.get(name, ""),
            value_hash=values.get(name),
            constructors=tuple(sorted(dict.fromkeys(ctors.get(name, [])))),
        )
    return result


def fingerprint_module(
    checkout: Path,
    module: str,
    names: list[str],
    *,
    timeout_seconds: int = 120,
    lake: str | None = None,
) -> dict[str, DeclarationFingerprint]:
    checkout = Path(checkout).resolve()
    if not checkout.is_dir():
        raise ValueError(f"checkout does not exist: {checkout}")
    source = fingerprint_source(module, names)
    lake_bin = lake or shutil.which("lake")
    if not lake_bin:
        raise RuntimeError("lake not found in PATH")
    fd, filename = tempfile.mkstemp(prefix="mathhub_parity_", suffix=".lean", dir=checkout)
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
            raise TimeoutError(f"Lean declaration fingerprint timed out after {timeout_seconds}s") from exc
        output = "\n".join(x for x in (proc.stdout.strip(), proc.stderr.strip()) if x)
        if proc.returncode != 0:
            raise RuntimeError(f"Lean declaration fingerprint failed ({proc.returncode}):\n{output[-8000:]}")
        parsed = parse_fingerprint_output(output)
        missing = [name for name in _safe_names(names) if name not in parsed or not parsed[name].type_hash]
        if missing:
            raise RuntimeError(f"missing declaration fingerprints: {', '.join(missing)}")
        return parsed
    finally:
        path.unlink(missing_ok=True)


def compare_fingerprint_maps(
    left: dict[str, DeclarationFingerprint],
    right: dict[str, DeclarationFingerprint],
    names: list[str],
    *,
    type_only: bool = False,
) -> dict[str, Any]:
    """Compare elaborated declarations from two isolated module environments.

    `type_only=True` is the correct mode for theorem-statement parity: theorem proof
    values are expected to differ (for example a challenge `sorry` placeholder versus
    a submitted proof) and must not be misclassified as a statement mismatch.

    Constructor types remain part of type-only comparison because they define the
    fields/meaning of structures and inductive predicates.
    """
    checks: list[dict[str, Any]] = []
    mismatch_count = 0
    for name in _safe_names(names):
        a = left.get(name)
        b = right.get(name)
        if a is None or b is None:
            equal = False
            differences = ["missing-left" if a is None else "missing-right"]
        else:
            differences = []
            if a.kind != b.kind:
                differences.append("kind")
            if a.type_hash != b.type_hash:
                differences.append("type-hash")
            if not type_only and a.value_hash != b.value_hash:
                differences.append("value-hash")
            if a.constructors != b.constructors:
                differences.append("constructor-types")
            equal = not differences
        if not equal:
            mismatch_count += 1
        checks.append({
            "name": name,
            "equal": equal,
            "differences": differences,
            "left": None if a is None else a.as_dict(),
            "right": None if b is None else b.as_dict(),
        })
    return {
        "comparison_mode": "type-only" if type_only else "full-declaration",
        "all_equal": mismatch_count == 0,
        "mismatch_count": mismatch_count,
        "checks": checks,
    }


def compare_modules(
    checkout: Path,
    left_module: str,
    right_module: str,
    names: list[str],
    *,
    type_only: bool = False,
    timeout_seconds: int = 120,
    fingerprint_fn: Callable[..., dict[str, DeclarationFingerprint]] = fingerprint_module,
) -> dict[str, Any]:
    names = _safe_names(names)
    left = fingerprint_fn(checkout, left_module, names, timeout_seconds=timeout_seconds)
    right = fingerprint_fn(checkout, right_module, names, timeout_seconds=timeout_seconds)
    comparison = compare_fingerprint_maps(left, right, names, type_only=type_only)
    return {
        "format": FORMAT,
        "left_module": left_module,
        "right_module": right_module,
        "declarations": names,
        **comparison,
        "interpretation": (
            "selected declarations have identical Lean type/constructor fingerprints in their separate module environments"
            if comparison["all_equal"] and type_only else
            "selected declarations have identical full Lean structural fingerprints in their separate module environments"
            if comparison["all_equal"] else
            "selected declarations differ between module environments under the configured comparison mode"
        ),
        "limitations": [
            "Fingerprint equality is structural equality of selected elaborated Lean declarations, not equivalence to an external paper statement.",
            "Type-only mode intentionally ignores theorem/definition value hashes and is appropriate for statement parity across challenge and solution environments.",
            "For inductive/structure declarations, the root type and constructor types are compared; generated eliminators/projections are not exhaustively fingerprinted by v1.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare same-name Lean declarations across two separately imported modules")
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--left-module", required=True)
    parser.add_argument("--right-module", required=True)
    parser.add_argument("--name", dest="names", action="append", required=True)
    parser.add_argument("--type-only", action="store_true")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = compare_modules(
        args.checkout,
        args.left_module,
        args.right_module,
        args.names,
        type_only=args.type_only,
        timeout_seconds=args.timeout,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0 if result["all_equal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
