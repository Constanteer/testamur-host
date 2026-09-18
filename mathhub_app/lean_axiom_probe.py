from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from lean_semantic_probe import lean_command, validate_imports, validate_names

FORMAT = "mathhub-lean-axiom-probe-v1"

AXIOM_PROBE_COMMAND = r'''
open Lean Elab Command

elab "#mathhub_axioms " id:ident : command => do
  let name := id.getId
  let env ← getEnv
  unless env.contains name do
    throwError "unknown declaration {name}"
  let axioms ← Lean.collectAxioms name
  logInfo m!"MATHHUB_AXIOM_ROOT:{name}"
  for axiom in axioms do
    logInfo m!"MATHHUB_AXIOM:{name}:{axiom}"
'''


def probe_source(imports: list[str], names: list[str]) -> str:
    modules = validate_imports(imports)
    constants = validate_names(names)
    # collectAxioms is the same Lean API used by `#print axioms`. Import its
    # command module explicitly instead of relying on a target project's imports.
    all_imports = list(dict.fromkeys(["Lean.Elab.Print", *modules]))
    import_lines = "\n".join(f"import {module}" for module in all_imports)
    commands = "\n".join(f"#mathhub_axioms {name}" for name in constants)
    return f"{import_lines}\n\n{AXIOM_PROBE_COMMAND}\n\n{commands}\n"


def _marker_payload(line: str, marker: str) -> str | None:
    index = line.find(marker)
    if index < 0:
        return None
    return line[index + len(marker):].strip()


def parse_probe_output(output: str) -> dict[str, tuple[str, ...]]:
    roots: set[str] = set()
    axioms: dict[str, list[str]] = {}

    for raw_line in str(output or "").splitlines():
        root = _marker_payload(raw_line, "MATHHUB_AXIOM_ROOT:")
        if root is not None:
            root = root.strip()
            if root:
                roots.add(root)
            continue

        payload = _marker_payload(raw_line, "MATHHUB_AXIOM:")
        if payload is None:
            continue
        owner, sep, axiom = payload.rpartition(":")
        if sep and owner and axiom:
            axioms.setdefault(owner, []).append(axiom)

    return {
        root: tuple(sorted(dict.fromkeys(axioms.get(root, []))))
        for root in sorted(roots)
    }


def inspect_axioms(
    checkout: Path,
    imports: list[str],
    names: list[str],
    *,
    timeout_seconds: int = 120,
    lake: str | None = None,
) -> dict[str, tuple[str, ...]]:
    checkout = Path(checkout).resolve()
    if not checkout.is_dir():
        raise ValueError(f"Lean checkout does not exist: {checkout}")
    requested = validate_names(names)
    source = probe_source(imports, requested)
    lake_bin = lake or shutil.which("lake")
    if not lake_bin:
        raise RuntimeError("lake not found in PATH")

    fd, filename = tempfile.mkstemp(prefix="mathhub_axioms_", suffix=".lean", dir=checkout)
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
                f"Lean axiom probe timed out after {timeout_seconds}s: {(stdout + stderr)[-2000:]}"
            ) from exc

        output = "\n".join(x for x in (proc.stdout.strip(), proc.stderr.strip()) if x)
        if proc.returncode != 0:
            raise RuntimeError(f"Lean axiom probe failed ({proc.returncode}):\n{output[-8000:]}")
        parsed = parse_probe_output(output)
        missing = [name for name in requested if name not in parsed]
        if missing:
            raise RuntimeError(f"Lean axiom probe returned no root markers for: {', '.join(missing)}")
        return parsed
    finally:
        path.unlink(missing_ok=True)


def serialize(results: dict[str, tuple[str, ...]]) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "results": [
            {"name": name, "axioms": list(results[name])}
            for name in sorted(results)
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect recursive Lean axiom closures")
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--import", dest="imports", action="append", required=True)
    parser.add_argument("--name", dest="names", action="append", required=True)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    result = inspect_axioms(
        args.checkout,
        args.imports,
        args.names,
        timeout_seconds=args.timeout,
    )
    print(json.dumps(serialize(result), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
