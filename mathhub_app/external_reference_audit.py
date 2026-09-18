from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from external_semantic_audit import git_checkout_metadata
from lean_declaration_parity import compare_modules
from lean_import_audit import build_local_import_closure

FORMAT = "mathhub-external-reference-audit-v1"


def _normalise_hash(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    hex_part = raw.split(":", 1)[1] if raw.startswith("sha256:") else raw
    if len(hex_part) != 64 or any(ch not in "0123456789abcdef" for ch in hex_part):
        raise ValueError(f"unsupported source hash: {value!r}")
    return "sha256:" + hex_part


def _source_attestation(checkout: Path, relative_path: str, expected_hash: str) -> dict[str, Any] | None:
    path_text = str(relative_path or "").strip()
    expected = _normalise_hash(expected_hash)
    if not path_text:
        if expected:
            raise ValueError("source path is required when source hash is supplied")
        return None
    root = Path(checkout).resolve()
    rel = Path(path_text)
    if rel.is_absolute():
        raise ValueError("source path must be checkout-relative")
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("source path escapes checkout") from exc
    if not candidate.is_file():
        raise ValueError(f"pinned source file does not exist: {path_text}")
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    actual = "sha256:" + digest
    if expected and actual != expected:
        raise ValueError(f"source hash mismatch for {path_text}: expected {expected}, got {actual}")
    return {
        "file_path": rel.as_posix(),
        "expected_sha256": expected or None,
        "actual_sha256": actual,
        "matches_expected": True if expected else None,
    }


def _canonical_digest(payload: dict[str, Any]) -> str:
    copy = json.loads(json.dumps(payload, ensure_ascii=False))
    if isinstance(copy.get("checkout"), dict):
        copy["checkout"].pop("checkout", None)
    copy.pop("audit_digest", None)
    encoded = json.dumps(copy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def validate_parity_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(groups, list) or not groups:
        raise ValueError("at least one declaration parity group is required")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(groups):
        if not isinstance(raw, dict):
            raise ValueError("parity groups must be objects")
        key = str(raw.get("key") or f"group-{index + 1}").strip()
        if not key or key in seen:
            raise ValueError(f"duplicate/empty parity group key: {key!r}")
        seen.add(key)
        left = str(raw.get("left_module") or "").strip()
        right = str(raw.get("right_module") or "").strip()
        names_raw = raw.get("names")
        if not left or not right or not isinstance(names_raw, list):
            raise ValueError(f"parity group {key!r} requires left_module, right_module and names")
        names = list(dict.fromkeys(str(x).strip() for x in names_raw if str(x).strip()))
        if not names:
            raise ValueError(f"parity group {key!r} has no declaration names")
        result.append({
            "key": key,
            "role": str(raw.get("role") or "").strip(),
            "left_module": left,
            "right_module": right,
            "names": names,
            "type_only": bool(raw.get("type_only")),
        })
    return result


def build_external_reference_audit(
    checkout: Path,
    root_module: str,
    parity_groups: list[dict[str, Any]],
    *,
    expected_revision: str = "",
    expected_source_path: str = "",
    expected_source_hash: str = "",
    forbidden_import_prefixes: list[str] | tuple[str, ...] = (),
    max_modules: int = 5000,
    timeout_seconds: int = 120,
    allow_dirty: bool = False,
    git_metadata_fn: Callable[..., dict[str, Any]] = git_checkout_metadata,
    import_audit_fn: Callable[..., dict[str, Any]] = build_local_import_closure,
    compare_fn: Callable[..., dict[str, Any]] = compare_modules,
) -> dict[str, Any]:
    checkout = Path(checkout).resolve()
    root = str(root_module or "").strip()
    expected = str(expected_revision or "").strip()
    forbidden = list(dict.fromkeys(str(x).strip() for x in forbidden_import_prefixes if str(x).strip()))
    groups = validate_parity_groups(parity_groups)
    if not root:
        raise ValueError("root_module is required")
    if max_modules < 1:
        raise ValueError("max_modules must be >= 1")
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be >= 1")

    git_meta = git_metadata_fn(checkout, allow_dirty=allow_dirty)
    actual_revision = str(git_meta.get("git_head") or "").strip()
    if expected and actual_revision != expected:
        raise ValueError(
            f"checkout revision mismatch: expected {expected}, got {actual_revision or '<unknown>'}"
        )
    source = _source_attestation(checkout, expected_source_path, expected_source_hash)

    imports = import_audit_fn(
        checkout,
        root,
        forbidden_prefixes=forbidden,
        max_modules=max_modules,
    )

    parity: list[dict[str, Any]] = []
    for group in groups:
        comparison = compare_fn(
            checkout,
            group["left_module"],
            group["right_module"],
            group["names"],
            type_only=group["type_only"],
            timeout_seconds=timeout_seconds,
        )
        parity.append({
            "key": group["key"],
            "role": group["role"],
            **comparison,
        })

    parity_mismatches = [group["key"] for group in parity if not group.get("all_equal")]
    payload: dict[str, Any] = {
        "format": FORMAT,
        "checkout": git_meta,
        "expected_revision": expected or None,
        "revision_matches_expected": True if expected else None,
        "source_attestation": source,
        "source_import_closure": imports,
        "declaration_parity": parity,
        "summary": {
            "forbidden_import_reachable": bool(imports.get("forbidden_reachable")),
            "import_closure_complete": bool(imports.get("complete_local_closure")),
            "parity_all_equal": not parity_mismatches,
            "parity_mismatch_groups": parity_mismatches,
        },
        "limitations": [
            "The import audit covers repository-local Lean source imports and does not recursively parse external Lake dependency sources.",
            "Declaration fingerprints compare selected elaborated Lean declarations in isolated module environments; they do not establish equivalence to the Clay or paper statement.",
            "A clean reference-isolation audit does not replace proof-expression, axiom, kernel-build or independent-checker audits.",
        ],
    }
    payload["audit_digest"] = _canonical_digest(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit challenge/reference isolation and declaration parity")
    parser.add_argument("config", type=Path, help="JSON object containing root_module and parity_groups")
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--expected-revision", default="")
    parser.add_argument("--source-path", default="")
    parser.add_argument("--source-hash", default="")
    parser.add_argument("--forbid-prefix", action="append", default=[])
    parser.add_argument("--max-modules", type=int, default=5000)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    result = build_external_reference_audit(
        args.checkout,
        cfg["root_module"],
        cfg["parity_groups"],
        expected_revision=args.expected_revision,
        expected_source_path=args.source_path,
        expected_source_hash=args.source_hash,
        forbidden_import_prefixes=args.forbid_prefix,
        max_modules=args.max_modules,
        timeout_seconds=args.timeout,
        allow_dirty=args.allow_dirty,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    bad = result["summary"]["forbidden_import_reachable"] or not result["summary"]["parity_all_equal"]
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
