from __future__ import annotations

from typing import Final

# Presentation notation is intentionally separate from parsing. The registry maps
# exact Lean heads to conservative human-facing labels; it never changes the
# underlying formal identifier or participates in verification.
APP_NOTATIONS: Final[dict[str, dict[str, str]]] = {
    "Nat.Prime": {"label": "Prime", "style": "operator"},
    "Function.Injective": {"label": "Injective", "style": "operator"},
    "Function.Surjective": {"label": "Surjective", "style": "operator"},
    "Function.Bijective": {"label": "Bijective", "style": "operator"},
    "Set.range": {"label": "range", "style": "operator"},
    "Set.Nonempty": {"label": "Nonempty", "style": "operator"},
    "Even": {"label": "Even", "style": "operator"},
    "Odd": {"label": "Odd", "style": "operator"},
    "StrictMono": {"label": "StrictMono", "style": "operator"},
    "Monotone": {"label": "Monotone", "style": "operator"},
    "Continuous": {"label": "Continuous", "style": "operator"},
    "ContinuousAt": {"label": "ContinuousAt", "style": "operator"},
    "Filter.Tendsto": {"label": "Tendsto", "style": "operator"},
    "Tendsto": {"label": "Tendsto", "style": "operator"},
}

TYPE_NOTATIONS: Final[dict[str, dict[str, str]]] = {
    "Set": {"label": "Set", "style": "constructor"},
    "List": {"label": "List", "style": "constructor"},
    "Finset": {"label": "Finset", "style": "constructor"},
    "Fin": {"label": "Fin", "style": "constructor"},
    "Filter": {"label": "Filter", "style": "constructor"},
    "Option": {"label": "Option", "style": "constructor"},
}


def app_label(head: str) -> str:
    entry = APP_NOTATIONS.get(head)
    if entry:
        return entry["label"]
    return head.split(".")[-1] if "." in head else head


def app_style(head: str) -> str:
    return APP_NOTATIONS.get(head, {}).get("style", "operator")


def type_label(head: str) -> str:
    entry = TYPE_NOTATIONS.get(head)
    if entry:
        return entry["label"]
    return head.split(".")[-1] if "." in head else head


def type_style(head: str) -> str:
    return TYPE_NOTATIONS.get(head, {}).get("style", "constructor")
