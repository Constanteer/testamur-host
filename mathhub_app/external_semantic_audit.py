from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from lean_semantic_probe import inspect_constants
from semantic_cone import ConstantInspection
from semantic_cone_batch import build_semantic_cone_batched

FORMAT = "mathhub-external-semantic-audit-v1"


def _run_git(checkout: Path, args: list[str]) -> str:
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git not found in PATH")
    proc = subprocess.run(
        [git, "-C", str(checkout), *args],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if proc.returncode != 0:
        output = "\n".join(x for x in (proc.stdout.strip(), proc.stderr.strip()) if x)
        raise RuntimeError(f"git {' '.join(args)} failed ({proc.returncode}): {output}")
    return proc.stdout.strip()


def git_checkout_metadata(checkout: Path, *, allow_dirty: bool = False) -> dict[str, Any]:
    checkout = Path(checkout).resolve()
    if not checkout.is_dir():
        raise ValueError(f"checkout does not exist: {checkout}")
    inside = _run_git(checkout, ["rev-parse", "--is-inside-work-tree"])
    if inside != "true":
        raise ValueError(f"not a Git work tree: {checkout}")
    head = _run_git(checkout, ["rev-parse", "HEAD"])
    # Ignore untracked build/cache output: the audit source invariant here is that
    # tracked repository content is pristine relative to HEAD. Supply-chain audit
    # handles caches and binaries separately.
    status = _run_git(checkout, ["status", "--porcelain", "--untracked-files=no"])
    dirty = bool(status.strip())
    if dirty and not allow_dirty:
        raise ValueError("tracked files in external audit checkout are dirty")
    return {
        "checkout": str(checkout),
        "git_head": head,
        "tracked_tree_dirty": dirty,
        "tracked_status": status.splitlines(),
    }


def _inspection_payload(item: ConstantInspection) -> dict[str, Any]:
    return {
        "name": item.name,
        "kind": item.kind,
        "type_refs": list(item.type_refs),
        "value_refs": list(item.value_refs),
    }


def _canonical_digest(payload: dict[str, Any]) -> str:
    # Local checkout path is operational metadata and must not make two otherwise
    # identical audits hash differently on two reviewers' machines.
    copy = json.loads(json.dumps(payload, ensure_ascii=False))
    if isinstance(copy.get("checkout"), dict):
        copy["checkout"].pop("checkout", None)
    copy.pop("audit_digest", None)
    encoded = json.dumps(copy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _checkout_file(checkout: Path, relative_path: str) -> Path:
    rel = Path(str(relative_path or "").strip())
    if not str(rel) or rel.is_absolute():
        raise ValueError("source path must be a non-empty checkout-relative path")
    root = Path(checkout).resolve()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("source path escapes the checkout") from exc
    if not candidate.is_file():
        raise ValueError(f"pinned source file does not exist: {rel.as_posix()}")
    return candidate


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _normalise_hash(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if raw.startswith("sha256:"):
        hex_part = raw.split(":", 1)[1]
    else:
        hex_part = raw
    if len(hex_part) != 64 or any(ch not in "0123456789abcdef" for ch in hex_part):
        raise ValueError(f"unsupported source hash: {value!r}")
    return "sha256:" + hex_part


def build_external_semantic_audit(
    checkout: Path,
    imports: list[str],
    root_name: str,
    *,
    expected_revision: str = "",
    expected_source_path: str = "",
    expected_source_hash: str = "",
    expand_prefixes: list[str] | tuple[str, ...] | None = None,
    max_depth: int = 8,
    max_nodes: int = 500,
    batch_size: int = 32,
    timeout_seconds: int = 120,
    allow_dirty: bool = False,
    git_metadata_fn: Callable[..., dict[str, Any]] = git_checkout_metadata,
    inspect_fn: Callable[..., dict[str, ConstantInspection]] = inspect_constants,
) -> dict[str, Any]:
    checkout = Path(checkout).resolve()
    imports = list(dict.fromkeys(str(x).strip() for x in imports if str(x).strip()))
    root = str(root_name or "").strip()
    expected = str(expected_revision or "").strip()
    source_path = str(expected_source_path or "").strip()
    source_hash = _normalise_hash(expected_source_hash)
    prefixes = list(dict.fromkeys(str(x).strip() for x in (expand_prefixes or []) if str(x).strip()))
    if not imports:
        raise ValueError("at least one Lean import is required")
    if not root:
        raise ValueError("root_name is required")
    if source_hash and not source_path:
        raise ValueError("expected_source_path is required when expected_source_hash is supplied")

    git_meta = git_metadata_fn(checkout, allow_dirty=allow_dirty)
    actual = str(git_meta.get("git_head") or "").strip()
    if expected and actual != expected:
        raise ValueError(f"checkout revision mismatch: expected {expected}, got {actual or '<unknown>'}")

    source_attestation: dict[str, Any] | None = None
    if source_path:
        pinned_file = _checkout_file(checkout, source_path)
        actual_source_hash = sha256_file(pinned_file)
        if source_hash and actual_source_hash != source_hash:
            raise ValueError(
                f"source hash mismatch for {source_path}: expected {source_hash}, got {actual_source_hash}"
            )
        source_attestation = {
            "file_path": Path(source_path).as_posix(),
            "expected_sha256": source_hash or None,
            "actual_sha256": actual_source_hash,
            "matches_expected": True if source_hash else None,
        }

    root_map = inspect_fn(
        checkout,
        imports,
        [root],
        timeout_seconds=timeout_seconds,
    )
    root_inspection = root_map.get(root)
    if root_inspection is None:
        raise RuntimeError(f"Lean ConstantInfo probe returned no root declaration: {root}")

    # Critical trust boundary: semantic traversal starts from the theorem TYPE,
    # never the root theorem proof/value refs.
    statement_refs = list(root_inspection.type_refs)

    def inspect_many(names: list[str]):
        return inspect_fn(
            checkout,
            imports,
            names,
            timeout_seconds=timeout_seconds,
        )

    expand_name: Callable[[str], bool] | None = None
    if prefixes:
        prefix_tuple = tuple(prefixes)
        expand_name = lambda name: name.startswith(prefix_tuple)

    cone = build_semantic_cone_batched(
        root,
        statement_refs,
        inspect_many,
        max_depth=max_depth,
        max_nodes=max_nodes,
        batch_size=batch_size,
        expand_name=expand_name,
    )

    payload: dict[str, Any] = {
        "format": FORMAT,
        "checkout": git_meta,
        "expected_revision": expected or None,
        "revision_matches_expected": True if expected else None,
        "source_attestation": source_attestation,
        "imports": imports,
        "root": _inspection_payload(root_inspection),
        "semantic_seed_policy": "root ConstantInfo.type refs only; root proof body excluded",
        "semantic_scope": {
            "expand_prefixes": prefixes,
            "external_constants_are_boundaries": bool(prefixes),
        },
        "cone": cone,
        "limitations": [
            "This artifact audits formal statement semantics, not the theorem proof argument route.",
            "A complete semantic cone means traversal completed within configured scope and budgets; it does not establish equivalence to an external natural-language or paper statement.",
            "Scoped external constants are explicit boundary atoms and are not recursively audited by this semantic-cone run.",
            "Git cleanliness covers tracked files; untracked Lake/build caches are intentionally ignored and require separate supply-chain provenance audit.",
        ],
    }
    payload["audit_digest"] = _canonical_digest(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a semantic definition cone from an exact local Lean repository checkout"
    )
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--expected-revision", default="")
    parser.add_argument("--source-path", default="")
    parser.add_argument("--source-hash", default="")
    parser.add_argument("--import", dest="imports", action="append", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--expand-prefix", dest="expand_prefixes", action="append", default=[])
    parser.add_argument("--max-depth", type=int, default=8)
    parser.add_argument("--max-nodes", type=int, default=500)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = build_external_semantic_audit(
        args.checkout,
        args.imports,
        args.root,
        expected_revision=args.expected_revision,
        expected_source_path=args.source_path,
        expected_source_hash=args.source_hash,
        expand_prefixes=args.expand_prefixes,
        max_depth=args.max_depth,
        max_nodes=args.max_nodes,
        batch_size=args.batch_size,
        timeout_seconds=args.timeout,
        allow_dirty=args.allow_dirty,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
