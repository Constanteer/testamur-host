from __future__ import annotations

import re
from typing import Any

# MathHub's mathematical node is a proposition-bearing declaration. Definitions,
# structures, classes and other vocabulary belong to the formal substrate instead.
# We deliberately keep this parser narrow: a Claim submission is one declaration,
# not an arbitrary Lean module with surrounding namespace/command structure.
_DECL_RE = re.compile(
    r"^\s*(?:(?:@\[[^\]]+\])\s*)*(?:(?:private|protected)\s+)?"
    r"(?P<kind>theorem|lemma|def|abbrev|axiom|opaque|structure|class|inductive)\s+"
    r"(?P<name>[^\s:(]+)",
    re.MULTILINE,
)

CLAIM_KINDS = {"theorem", "lemma"}


def declaration_kind(formal_declaration: Any) -> str | None:
    """Return the leading Lean declaration kind when it is recognizable."""
    text = str(formal_declaration or "")
    match = _DECL_RE.search(text)
    return match.group("kind") if match else None


def declaration_name(formal_declaration: Any) -> str | None:
    text = str(formal_declaration or "")
    match = _DECL_RE.search(text)
    return match.group("name") if match else None


def validate_claim_declaration(formal_declaration: Any) -> tuple[str, str]:
    """Validate the public mathematical Claim boundary.

    Claim nodes are theorem/lemma statements. Definitions and other Lean constants
    remain available to the machine substrate and dependency audit, but they are
    not registered as mathematical Claim nodes by the public API.
    """
    text = str(formal_declaration or "").strip()
    if not text:
        raise ValueError("formal Lean declaration is required")
    match = _DECL_RE.search(text)
    if not match:
        raise ValueError("a Claim must be a single Lean theorem or lemma declaration")
    kind = match.group("kind")
    name = match.group("name")
    if kind not in CLAIM_KINDS:
        raise ValueError(
            f"{kind} declarations belong to the formal substrate; "
            "MathHub Claim nodes must be theorem or lemma declarations"
        )
    return kind, name
