from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from external_semantic_audit import git_checkout_metadata
from lean_axiom_probe import AXIOM_PROBE_COMMAND, parse_probe_output
from lean_semantic_probe import lean_command, validate_names

FORMAT = "mathhub-lean-obligation-audit-v1"
SHA256_RE = re.compile(r"^(?:sha256:)?([0-9a-fA-F]{64})$")


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _canonical_digest(payload: dict[str, Any]) -> str:
    copy = json.loads(json.dumps(payload, ensure_ascii=False))
    if isinstance(copy.get("checkout"), dict):
        copy["checkout"].pop("checkout", None)
    if isinstance(copy.get("bridge"), dict):
        copy["bridge"].pop("path", None)
    copy.pop("audit_digest", None)
    encoded = json.dumps(copy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return _sha256(encoded)


def _normalize_hash(value: str) -> str:
    match = SHA256_RE.fullmatch(str(value or "").strip())
    if not match:
        raise ValueError("expected SHA-256 must contain exactly 64 hexadecimal digits")
    return "sha256:" + match.group(1).lower()


def build_probe_source(bridge_source: str, theorem_names: list[str]) -> str:
    names = validate_names(theorem_names)
    commands = "\n".join(f"#mathhub_axioms {name}" for name in names)
    # The bridge source has already imported its target project modules. Importing
    # Lean.Elab.Print here guarantees `Lean.collectAxioms` is available independently
    # of those project imports.
    return (
        "import Lean.Elab.Print\n"
        + bridge_source.rstrip()
        + "\n\n"
        + AXIOM_PROBE_COMMAND
        + "\n\n"
        + commands
        + "\n"
    )


def run_lean_source(
    checkout: Path,
    source: str,
    *,
    timeout_seconds: int = 180,
    lake: str | None = None,
) -> tuple[int, str]:
    lake_bin = lake or shutil.which("lake")
    if not lake_bin:
        raise RuntimeError("lake not found in PATH")
    fd, filename = tempfile.mkstemp(prefix="mathhub_obligation_", suffix=".lean", dir=checkout)
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
                f"Lean obligation audit timed out after {timeout_seconds}s: {(stdout + stderr)[-4000:]}"
            ) from exc
        output = "\n".join(x for x in (proc.stdout.strip(), proc.stderr.strip()) if x)
        return proc.returncode, output
    finally:
        path.unlink(missing_ok=True)


def audit_lean_obligation(
    checkout: Path,
    bridge_file: Path,
    theorem_names: list[str],
    *,
    expected_revision: str,
    expected_bridge_hash: str | None = None,
    permitted_axioms: list[str] | tuple[str, ...] = (
        "propext", "Classical.choice", "Quot.sound"
    ),
    timeout_seconds: int = 180,
    git_metadata_fn: Callable[..., dict[str, Any]] = git_checkout_metadata,
    run_fn: Callable[..., tuple[int, str]] = run_lean_source,
) -> dict[str, Any]:
    checkout = Path(checkout).resolve()
    bridge_file = Path(bridge_file).resolve()
    names = validate_names(theorem_names)
    expected_revision = str(expected_revision or "").strip()
    if not expected_revision:
        raise ValueError("expected_revision is required")
    if not bridge_file.is_file():
        raise FileNotFoundError(bridge_file)

    git_meta = git_metadata_fn(checkout, allow_dirty=False)
    actual_revision = str(git_meta.get("git_head") or "").strip()
    if actual_revision != expected_revision:
        raise ValueError(
            f"checkout revision mismatch: expected {expected_revision}, got {actual_revision or '<unknown>'}"
        )

    bridge_bytes = bridge_file.read_bytes()
    bridge_hash = _sha256(bridge_bytes)
    if expected_bridge_hash is not None:
        expected_hash = _normalize_hash(expected_bridge_hash)
        if bridge_hash != expected_hash:
            raise ValueError(
                f"bridge source hash mismatch: expected {expected_hash}, got {bridge_hash}"
            )
    else:
        expected_hash = None

    bridge_source = bridge_bytes.decode("utf-8")
    source = build_probe_source(bridge_source, names)
    returncode, output = run_fn(checkout, source, timeout_seconds=timeout_seconds)

    axiom_map: dict[str, tuple[str, ...]] = {}
    if returncode == 0:
        axiom_map = parse_probe_output(output)
        missing = [name for name in names if name not in axiom_map]
        if missing:
            raise RuntimeError(
                "Lean compiled the obligation source but emitted no axiom root markers for: "
                + ", ".join(missing)
            )

    permitted = sorted(dict.fromkeys(str(x).strip() for x in permitted_axioms if str(x).strip()))
    theorem_results: list[dict[str, Any]] = []
    unexpected_union: set[str] = set()
    contains_sorry = False
    for name in names:
        axioms = list(axiom_map.get(name, ()))
        unexpected = sorted(x for x in axioms if x not in permitted)
        unexpected_union.update(unexpected)
        has_sorry = any(x == "sorryAx" or x.endswith(".sorryAx") for x in axioms)
        contains_sorry = contains_sorry or has_sorry
        theorem_results.append({
            "name": name,
            "axioms": axioms,
            "unexpected_axioms": unexpected,
            "contains_sorryAx": has_sorry,
        })

    compiled = returncode == 0
    axiom_policy_ok = compiled and not unexpected_union and not contains_sorry
    payload: dict[str, Any] = {
        "format": FORMAT,
        "checkout": git_meta,
        "expected_revision": expected_revision,
        "revision_matches_expected": True,
        "bridge": {
            "path": str(bridge_file),
            "sha256": bridge_hash,
            "expected_sha256": expected_hash,
            "hash_matches_expected": None if expected_hash is None else True,
        },
        "theorems": theorem_results,
        "permitted_axioms": permitted,
        "compiled": compiled,
        "lean_returncode": int(returncode),
        "axiom_policy_ok": axiom_policy_ok,
        "contains_sorryAx": contains_sorry,
        "unexpected_axioms": sorted(unexpected_union),
        "stderr_stdout_tail": output[-8000:] if not compiled else "",
        "formal_obligation_passed": compiled and axiom_policy_ok,
        "limitations": [
            "A passing bridge proves only the Lean propositions declared in the bridge source; a reviewer must still confirm those propositions faithfully encode the intended external semantic obligation.",
            "This runner uses the target checkout's Lake/Lean environment but does not by itself attest Lean binary provenance, dependency cache provenance, or an independent checker.",
        ],
    }
    payload["audit_digest"] = _canonical_digest(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile a formal alignment-obligation bridge in an exact pinned external Lean checkout and audit its axiom closure"
    )
    parser.add_argument("checkout", type=Path)
    parser.add_argument("bridge_file", type=Path)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--expected-bridge-hash")
    parser.add_argument("--theorem", dest="theorems", action="append", required=True)
    parser.add_argument("--permit-axiom", dest="permitted_axioms", action="append", default=[])
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    permitted = args.permitted_axioms or ["propext", "Classical.choice", "Quot.sound"]
    result = audit_lean_obligation(
        args.checkout,
        args.bridge_file,
        args.theorems,
        expected_revision=args.expected_revision,
        expected_bridge_hash=args.expected_bridge_hash,
        permitted_axioms=permitted,
        timeout_seconds=args.timeout,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0 if result["formal_obligation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
