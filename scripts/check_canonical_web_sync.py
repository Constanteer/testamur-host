#!/usr/bin/env python3
"""Check that the hosted shared Web bundle matches canonical Testamur.

Usage from a checkout of testamur-host:
    python scripts/check_canonical_web_sync.py ../testamur

This deliberately checks only files that are contractually shared. Hosted-only
routing/auth assets remain outside this boundary.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

SHARED = (
    "testamur/web/index.html",
    "testamur/web/app.js",
    "testamur/web/styles.css",
    "testamur/web/advisory-review-ui.js",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("canonical", type=Path, help="path to the canonical testamur checkout")
    parser.add_argument("--host", type=Path, default=Path.cwd(), help="path to testamur-host checkout")
    args = parser.parse_args()

    canonical = args.canonical.resolve()
    host = args.host.resolve()
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
            failures.append(f"drift: {relative} canonical={left_hash[:12]} host={right_hash[:12]}")

    if failures:
        print("Canonical Web sync check failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print(f"Canonical Web sync check passed ({len(SHARED)} shared files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
