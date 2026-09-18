from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from external_semantic_audit import git_checkout_metadata
from lean_axiom_probe import inspect_axioms
from lean_semantic_probe import inspect_constants
from proof_dependency_graph import build_proof_dependency_graph
from semantic_cone import ConstantInspection

FORMAT = "mathhub-external-proof-audit-v1"


def _normalise_hash(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    hex_part = raw.split(":", 1)[1] if raw.startswith("sha256:") else raw
    if len(hex_part) != 64 or any(ch not in "0123456789abcdef" for ch in hex_part):
        raise ValueError(f"unsupported source hash: {value!r}")
    return "sha256:" + hex_part


def _checkout_file(checkout: Path, relative_path: str) -> Path:
    root = Path(checkout).resolve()
    rel = Path(str(relative_path or "").strip())
    if not str(rel) or rel.is_absolute():
        raise ValueError("source path must be a non-empty checkout-relative path")
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("source path escapes the checkout") from exc
    if not candidate.is_file():
        raise ValueError(f"pinned source file does not exist: {rel.as_posix()}")
    return candidate


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _canonical_digest(payload: dict[str, Any]) -> str:
    copy = json.loads(json.dumps(payload, ensure_ascii=False))
    if isinstance(copy.get("checkout"), dict):
        copy["checkout"].pop("checkout", None)
    copy.pop("audit_digest", None)
    encoded = json.dumps(copy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _inspection_payload(item: ConstantInspection) -> dict[str, Any]:
    return {
        "name": item.name,
        "kind": item.kind,
        "type_refs": list(item.type_refs),
        "value_refs": list(item.value_refs),
    }


def build_external_proof_audit(
    checkout: Path,
    imports: list[str],
    root_name: str,
    *,
    expected_revision: str = "",
    expected_source_path: str = "",
    expected_source_hash: str = "",
    expand_prefixes: list[str] | tuple[str, ...] | None = None,
    permitted_axioms: list[str] | tuple[str, ...] | None = None,
    publisher_declared_axioms: list[str] | tuple[str, ...] | None = None,
    max_depth: int = 64,
    max_nodes: int = 5000,
    batch_size: int = 64,
    timeout_seconds: int = 120,
    allow_dirty: bool = False,
    git_metadata_fn: Callable[..., dict[str, Any]] = git_checkout_metadata,
    inspect_fn: Callable[..., dict[str, ConstantInspection]] = inspect_constants,
    axiom_fn: Callable[..., dict[str, tuple[str, ...]]] = inspect_axioms,
) -> dict[str, Any]:
    checkout = Path(checkout).resolve()
    modules = list(dict.fromkeys(str(x).strip() for x in imports if str(x).strip()))
    root = str(root_name or "").strip()
    expected = str(expected_revision or "").strip()
    source_path = str(expected_source_path or "").strip()
    source_hash = _normalise_hash(expected_source_hash)
    prefixes = list(dict.fromkeys(str(x).strip() for x in (expand_prefixes or []) if str(x).strip()))
    allowed_axioms = sorted(dict.fromkeys(str(x).strip() for x in (permitted_axioms or []) if str(x).strip()))
    publisher_axioms = sorted(dict.fromkeys(
        str(x).strip() for x in (publisher_declared_axioms or []) if str(x).strip()
    ))

    if not modules:
        raise ValueError("at least one Lean import is required")
    if not root:
        raise ValueError("root_name is required")
    if source_hash and not source_path:
        raise ValueError("expected_source_path is required when expected_source_hash is supplied")

    git_meta = git_metadata_fn(checkout, allow_dirty=allow_dirty)
    actual_revision = str(git_meta.get("git_head") or "").strip()
    if expected and actual_revision != expected:
        raise ValueError(
            f"checkout revision mismatch: expected {expected}, got {actual_revision or '<unknown>'}"
        )

    source_attestation: dict[str, Any] | None = None
    if source_path:
        actual_hash = _sha256_file(_checkout_file(checkout, source_path))
        if source_hash and actual_hash != source_hash:
            raise ValueError(
                f"source hash mismatch for {source_path}: expected {source_hash}, got {actual_hash}"
            )
        source_attestation = {
            "file_path": Path(source_path).as_posix(),
            "expected_sha256": source_hash or None,
            "actual_sha256": actual_hash,
            "matches_expected": True if source_hash else None,
        }

    root_map = inspect_fn(
        checkout,
        modules,
        [root],
        timeout_seconds=timeout_seconds,
    )
    root_inspection = root_map.get(root)
    if root_inspection is None:
        raise RuntimeError(f"Lean ConstantInfo probe returned no root declaration: {root}")

    def inspect_many(names: list[str]):
        return inspect_fn(
            checkout,
            modules,
            names,
            timeout_seconds=timeout_seconds,
        )

    expand_name = None
    if prefixes:
        prefix_tuple = tuple(prefixes)
        expand_name = lambda name: name.startswith(prefix_tuple)

    graph = build_proof_dependency_graph(
        root,
        list(root_inspection.value_refs),
        inspect_many,
        max_depth=max_depth,
        max_nodes=max_nodes,
        batch_size=batch_size,
        expand_name=expand_name,
    )

    axiom_map = axiom_fn(
        checkout,
        modules,
        [root],
        timeout_seconds=timeout_seconds,
    )
    if root not in axiom_map:
        raise RuntimeError(f"Lean axiom probe returned no closure for root declaration: {root}")
    axioms = sorted(dict.fromkeys(axiom_map[root]))
    unexpected = sorted(set(axioms) - set(allowed_axioms)) if permitted_axioms is not None else []
    missing_from_publisher = sorted(set(axioms) - set(publisher_axioms)) if publisher_declared_axioms is not None else []
    publisher_only = sorted(set(publisher_axioms) - set(axioms)) if publisher_declared_axioms is not None else []
    contains_sorry = any(
        axiom == "sorryAx" or axiom.endswith(".sorryAx") or "sorryAx" in axiom
        for axiom in axioms
    )

    payload: dict[str, Any] = {
        "format": FORMAT,
        "checkout": git_meta,
        "expected_revision": expected or None,
        "revision_matches_expected": True if expected else None,
        "source_attestation": source_attestation,
        "imports": modules,
        "root": _inspection_payload(root_inspection),
        "proof_seed_policy": "root ConstantInfo.value refs only",
        "proof_scope": {
            "expand_prefixes": prefixes,
            "external_constants_are_boundaries": bool(prefixes),
        },
        "proof_dependencies": graph,
        "axiom_closure": {
            "complete": True,
            "collector": "Lean.collectAxioms",
            "axioms": axioms,
            "contains_sorryAx": contains_sorry,
            "permitted_axioms": allowed_axioms if permitted_axioms is not None else None,
            "unexpected_axioms": unexpected,
            "publisher_declared_axioms": publisher_axioms if publisher_declared_axioms is not None else None,
            "publisher_missing_axioms": missing_from_publisher,
            "publisher_only_axioms": publisher_only,
            "publisher_set_equal": (
                set(axioms) == set(publisher_axioms)
                if publisher_declared_axioms is not None else None
            ),
        },
        "limitations": [
            "The dependency graph follows the stored Lean proof/value expression and is not itself kernel verification.",
            "Scoped external-library dependencies remain explicit boundary atoms rather than being recursively expanded in the project proof graph.",
            "The axiom closure is independently recomputed from the pinned checkout with Lean.collectAxioms, but uses the same Lean environment rather than an independent proof checker.",
            "Tracked-tree cleanliness does not attest untracked Lake/build caches or toolchain binaries; those remain supply-chain obligations.",
        ],
    }
    payload["audit_digest"] = _canonical_digest(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit a theorem's stored Lean proof route and recursive axiom closure"
    )
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--expected-revision", default="")
    parser.add_argument("--source-path", default="")
    parser.add_argument("--source-hash", default="")
    parser.add_argument("--import", dest="imports", action="append", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--expand-prefix", dest="expand_prefixes", action="append", default=[])
    parser.add_argument("--permit-axiom", dest="permitted_axioms", action="append")
    parser.add_argument("--publisher-axiom", dest="publisher_axioms", action="append")
    parser.add_argument("--max-depth", type=int, default=64)
    parser.add_argument("--max-nodes", type=int, default=5000)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = build_external_proof_audit(
        args.checkout,
        args.imports,
        args.root,
        expected_revision=args.expected_revision,
        expected_source_path=args.source_path,
        expected_source_hash=args.source_hash,
        expand_prefixes=args.expand_prefixes,
        permitted_axioms=args.permitted_axioms,
        publisher_declared_axioms=args.publisher_axioms,
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
