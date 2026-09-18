from __future__ import annotations

import difflib
from typing import Any

from .blob_store import TestamurBlobStore, normalize_sha256_digest


def compare_blobs(
    blobs: TestamurBlobStore,
    left_hash: str,
    right_hash: str,
    *,
    encoding: str = "utf-8",
    context_lines: int = 3,
    max_bytes: int = 2 * 1024 * 1024,
) -> dict[str, Any]:
    """Mechanically compare exact locally retained bytes.

    UTF-8-compatible inputs receive a deterministic unified text diff. Binary or
    undecodable inputs still receive exact hash/size identity comparison. This
    function never produces semantic interpretation.
    """

    left_digest = normalize_sha256_digest(left_hash)
    right_digest = normalize_sha256_digest(right_hash)
    left_meta = blobs.describe(left_digest)
    right_meta = blobs.describe(right_digest)
    same_identity = left_digest == right_digest

    common: dict[str, Any] = {
        "left_content_hash": left_digest,
        "right_content_hash": right_digest,
        "left_byte_size": left_meta["byte_size"],
        "right_byte_size": right_meta["byte_size"],
        "same_exact_content": same_identity,
        "semantics": {
            "mechanical_comparison_only": True,
            "semantic_change_inferred": False,
            "publication_rights_implied": False,
        },
    }
    if same_identity:
        return {
            **common,
            "kind": "identical",
            "diff": "",
            "additions": 0,
            "deletions": 0,
        }

    if left_meta["byte_size"] > max_bytes or right_meta["byte_size"] > max_bytes:
        return {
            **common,
            "kind": "too_large_for_inline_diff",
            "max_bytes": int(max_bytes),
            "diff": None,
            "additions": None,
            "deletions": None,
        }

    left = blobs.get(left_digest, max_bytes=max_bytes)
    right = blobs.get(right_digest, max_bytes=max_bytes)
    try:
        left_text = left.decode(encoding)
        right_text = right.decode(encoding)
    except UnicodeDecodeError:
        return {
            **common,
            "kind": "binary",
            "encoding_attempted": encoding,
            "diff": None,
            "additions": None,
            "deletions": None,
        }

    left_lines = left_text.splitlines(keepends=True)
    right_lines = right_text.splitlines(keepends=True)
    diff_lines = list(
        difflib.unified_diff(
            left_lines,
            right_lines,
            fromfile=left_digest,
            tofile=right_digest,
            n=max(0, int(context_lines)),
            lineterm="\n",
        )
    )
    additions = sum(
        1
        for line in diff_lines
        if line.startswith("+") and not line.startswith("+++")
    )
    deletions = sum(
        1
        for line in diff_lines
        if line.startswith("-") and not line.startswith("---")
    )
    return {
        **common,
        "kind": "text",
        "encoding": encoding,
        "diff": "".join(diff_lines),
        "additions": additions,
        "deletions": deletions,
        "context_lines": max(0, int(context_lines)),
    }
