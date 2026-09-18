from __future__ import annotations

import re
from typing import Iterable

# These are the ordinary mathematical axioms Lean exposes as part of its logic.
# They do not enlarge MathHub's implementation TCB beyond the Lean kernel itself.
STANDARD_MATHEMATICAL_AXIOMS = frozenset({
    "propext",
    "Classical.choice",
    "Quot.sound",
})

# These names explicitly record that native/compiler evaluation was trusted.
EXACT_COMPILER_TRUST_AXIOMS = frozenset({
    "Lean.trustCompiler",
    "Lean.ofReduceBool",
    "Lean.ofReduceNat",
})

# Lean >= 4.29 may create a dedicated axiom per native_decide/bv_decide use.
# The exact theorem prefix is user-controlled; the native suffix is the signal.
GENERATED_COMPILER_AXIOM_RE = re.compile(
    r"\._native\.(?:native_decide|bv_decide)\.ax_[0-9]+$"
)


def is_compiler_trust_axiom(name: str) -> bool:
    value = str(name or "").strip()
    return value in EXACT_COMPILER_TRUST_AXIOMS or bool(GENERATED_COMPILER_AXIOM_RE.search(value))


def classify_axioms(axioms: Iterable[str] | None) -> dict[str, object]:
    """Classify the trusted base without changing proof-completeness status.

    `verified / partial / failed` answers whether Lean accepted a complete proof.
    This function answers a different question: what additional trusted base does
    that accepted proof rely on?
    """
    ordered = list(dict.fromkeys(str(x).strip() for x in (axioms or []) if str(x).strip()))
    compiler = [x for x in ordered if is_compiler_trust_axiom(x)]
    nonstandard = [
        x for x in ordered
        if x not in STANDARD_MATHEMATICAL_AXIOMS
        and x != "sorryAx"
        and not is_compiler_trust_axiom(x)
    ]

    if compiler and nonstandard:
        trust_class = "extended"
    elif compiler:
        trust_class = "compiler-assisted"
    elif nonstandard:
        trust_class = "axiomatized"
    else:
        trust_class = "kernel"

    return {
        "trust_class": trust_class,
        "compiler_trust_axioms": compiler,
        "nonstandard_axioms": nonstandard,
        "uses_trust_compiler": bool(compiler),
    }
