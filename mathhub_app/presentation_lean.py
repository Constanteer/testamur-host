from __future__ import annotations

from typing import Any

import server as core
from presentation import canonical_from_declaration

PRETTY_MARKER = "MATHHUB_PRETTY_TYPE:"


def parse_pretty_type(output: str) -> str:
    for line in str(output or "").splitlines():
        if PRETTY_MARKER in line:
            surface = line.split(PRETTY_MARKER, 1)[1].strip()
            if surface:
                return surface
    return ""


def inspect_pretty_type(payload: dict[str, Any]) -> dict[str, Any]:
    """Reuse the canonical Lean declaration inspector.

    `server.inspect_declaration` now emits the pretty type in the same
    ConstantInfo inspection that supplies hashes and dependencies, so there is
    no second presentation-specific Lean metacommand to maintain.
    """
    result = core.inspect_declaration(payload)
    if result.get("ok") and not result.get("pretty_type"):
        result = {
            **result,
            "ok": False,
            "presentation_error": "Lean completed but emitted no pretty-type marker",
        }
    return result


def canonical_from_pretty_type(declaration_name: str, pretty_type: str) -> dict[str, Any]:
    """Feed Lean's pretty-printed type into the deterministic Presentation IR.

    This remains presentation data. The authoritative object is the original
    elaborated Expr in Lean's environment; the pretty string is only a stable
    renderer input/provenance artifact.
    """
    surface = str(pretty_type or "").strip()
    if not surface:
        raise ValueError("pretty_type is required")
    rendered = canonical_from_declaration(f"theorem {declaration_name} : {surface}")
    rendered["input_basis"] = "lean-pretty-type"
    if isinstance(rendered.get("ir"), dict):
        rendered["ir"] = {**rendered["ir"], "input_basis": "lean-pretty-type"}
    return rendered
