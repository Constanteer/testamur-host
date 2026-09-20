from __future__ import annotations

import re
from typing import Any


_NUMERIC_VERSION = re.compile(r"^v?(\d+(?:\.\d+)*)(?:[-+].*)?$")


def _numeric_version(value: object) -> tuple[int, ...] | None:
    """Return a conservative comparable tuple for simple numeric versions.

    Ecosystem version schemes differ, so Testamur must not pretend arbitrary
    package versions are totally ordered.  This intentionally classifies only
    unambiguous dotted-numeric versions; everything else remains a mechanical
    version change with unknown direction.
    """
    match = _NUMERIC_VERSION.fullmatch(str(value or "").strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _transition(item: dict[str, Any]) -> str | None:
    if not item.get("version_changed"):
        return None
    before = item.get("before") or []
    after = item.get("after") or []
    if len(before) != 1 or len(after) != 1:
        return "version-changed"
    old = _numeric_version(before[0].get("version"))
    new = _numeric_version(after[0].get("version"))
    if old is None or new is None:
        return "version-changed"
    width = max(len(old), len(new))
    old_cmp = old + (0,) * (width - len(old))
    new_cmp = new + (0,) * (width - len(new))
    if new_cmp > old_cmp:
        return "upgraded"
    if new_cmp < old_cmp:
        return "downgraded"
    return "version-changed"


def install_dependency_version_transitions() -> None:
    """Add explicit, conservative transition labels to mechanical scan diffs."""
    from . import supply_chain

    current = supply_chain.diff_project_supply_chain
    if getattr(current, "_testamur_dependency_version_transitions", False):
        return

    def diff_project_supply_chain(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = current(*args, **kwargs)
        transitions = {"upgraded": 0, "downgraded": 0, "version-changed": 0}
        for item in result.get("dependencies", {}).get("changed", []):
            transition = _transition(item)
            if transition is None:
                continue
            item["version_transition"] = transition
            transitions[transition] += 1
        result.setdefault("counts", {}).update(
            {
                "dependencies_upgraded": transitions["upgraded"],
                "dependencies_downgraded": transitions["downgraded"],
                "dependencies_version_changed_unclassified": transitions["version-changed"],
            }
        )
        result.setdefault("semantics", {}).update(
            {
                "version_transition_is_mechanical": True,
                "version_order_implies_validity_or_safety": False,
                "unrecognized_version_order_is_not_guessed": True,
            }
        )
        return result

    diff_project_supply_chain._testamur_dependency_version_transitions = True  # type: ignore[attr-defined]
    supply_chain.diff_project_supply_chain = diff_project_supply_chain
