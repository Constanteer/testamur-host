#!/usr/bin/env python3
"""Check that the hosted shared Web bundle matches canonical Testamur.

CI can validate against the pinned canonical-web-manifest.json without checking
out the private canonical repository. During an explicit sync, pass a canonical
checkout path to compare both working trees directly:

    python scripts/check_canonical_web_sync.py ../testamur

The manifest source must exactly match CORE_SNAPSHOT, so updating the canonical
snapshot without regenerating its shared-Web manifest fails closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SHARED = (
    "testamur/web/index.html",
    "testamur/web/app.js",
    "testamur/web/styles.css",
    "testamur/web/contextual-guide.js",
    "testamur/web/contextual-guide.css",
    "testamur/web/demo-handoff.js",
    "testamur/web/demo-handoff.css",
    "testamur/web/baseline-handoff.js",
    "testamur/web/baseline-handoff.css",
    "testamur/web/supply-chain-guidance.js",
    "testamur/web/supply-chain-guidance.css",
    "testamur/web/supply-chain-actions.js",
    "testamur/web/advisory-review-ui.js",
    "testamur/web/advisory-revalidation-ui.js",
    "testamur/web/first-run-journey.js",
    "testamur/web/first-run-journey.css",
    "testamur/web/first-run-resume.js",
    "testamur/web/first-run-resume.css",
    "testamur/web/help-launcher.js",
    "testamur/web/help-launcher.css",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_blob_digest(path: Path) -> str:
    data = path.read_bytes()
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def check_checkout(canonical: Path, host: Path) -> list[str]:
    failures: list[str] = []
    for relative in SHARED:
        left = canonical / relative
        right = host / relative
        if not left.is_file():
            failures.append(f"canonical missing: {relative}")
            continue
        if not right.is_file():
            failures.append(f"host missing: {relative}")
            continue
        left_hash = digest(left)
        right_hash = digest(right)
        if left_hash != right_hash:
            failures.append(
                f"drift: {relative} canonical={left_hash[:12]} host={right_hash[:12]}"
            )
    return failures


def check_manifest(manifest_path: Path, host: Path) -> tuple[list[str], str]:
    failures: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"manifest unavailable: {manifest_path}: {exc}"], ""

    source = str(manifest.get("source") or "").strip()
    algorithm = str(manifest.get("algorithm") or "").strip()
    files = manifest.get("files")
    if algorithm != "git-blob-sha1":
        failures.append(f"unsupported manifest algorithm: {algorithm!r}")
    if not isinstance(files, dict):
        return [*failures, "manifest files must be an object"], source

    snapshot_path = host / "CORE_SNAPSHOT"
    try:
        snapshot = snapshot_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        failures.append(f"CORE_SNAPSHOT unavailable: {exc}")
        snapshot = ""
    if not source:
        failures.append("manifest source is empty")
    elif snapshot and source != snapshot:
        failures.append(f"manifest source mismatch: manifest={source} CORE_SNAPSHOT={snapshot}")

    expected = set(SHARED)
    recorded = set(str(key) for key in files)
    for missing in sorted(expected - recorded):
        failures.append(f"manifest missing: {missing}")
    for unexpected in sorted(recorded - expected):
        failures.append(f"manifest unexpected: {unexpected}")

    for relative in SHARED:
        expected_hash = files.get(relative)
        right = host / relative
        if not isinstance(expected_hash, str) or len(expected_hash) != 40:
            failures.append(f"manifest invalid hash: {relative}")
            continue
        if not right.is_file():
            failures.append(f"host missing: {relative}")
            continue
        actual_hash = git_blob_digest(right)
        if actual_hash != expected_hash:
            failures.append(
                f"drift: {relative} canonical={expected_hash[:12]} host={actual_hash[:12]}"
            )
    return failures, source


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "canonical",
        nargs="?",
        type=Path,
        help="optional path to a canonical testamur checkout for direct comparison",
    )
    parser.add_argument("--host", type=Path, default=Path.cwd(), help="path to testamur-host checkout")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("canonical-web-manifest.json"),
        help="pinned manifest used when no canonical checkout is supplied",
    )
    args = parser.parse_args()

    host = args.host.resolve()
    if args.canonical is not None:
        failures = check_checkout(args.canonical.resolve(), host)
        source = str(args.canonical.resolve())
    else:
        failures, source = check_manifest(args.manifest.resolve(), host)

    if failures:
        print("Canonical Web sync check failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print(f"Canonical Web sync check passed ({len(SHARED)} shared files; source={source}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
