from __future__ import annotations

from collections.abc import Iterable


_MATERIAL_PRESENT = "MATERIAL_PRESENT"
_MATERIAL_ABSENT = "MATERIAL_ABSENT"
_MITIGATION_ESTABLISHED = "MITIGATION_ESTABLISHED"
_OUT_OF_SCOPE = "OUT_OF_SCOPE"

_TERMINAL_SIGNAL_FAMILIES = {
    _MATERIAL_PRESENT: "affected",
    _MATERIAL_ABSENT: "disproven",
    _MITIGATION_ESTABLISHED: "mitigated",
    _OUT_OF_SCOPE: "not_applicable",
}


def conflicting_terminal_signals(signals: Iterable[object]) -> tuple[str, ...]:
    """Return terminal applicability signals that cannot be silently co-resolved.

    The affectedness engine has four distinct evidence-backed terminal meanings:
    affected, disproven, mitigated, and not-applicable. Evidence supporting more
    than one of those meanings is a conflict unless the engine has an explicit
    composition rule. In particular MATERIAL_PRESENT + MITIGATION_ESTABLISHED
    is the one supported composition: vulnerable material can remain present
    while a mitigation is independently established, yielding MITIGATED.

    This helper deliberately ignores weak/declared signals. It does not choose
    a verdict; callers must preserve the conflict as UNKNOWN.

    The helper intentionally depends only on string-valued signal semantics so
    affectedness.py can use it without a circular import. StrEnum values retain
    their canonical string representation.
    """

    normalized = {
        item.value if hasattr(item, "value") else str(item)
        for item in signals
    }
    terminal = set(normalized) & set(_TERMINAL_SIGNAL_FAMILIES)

    if terminal == {_MATERIAL_PRESENT, _MITIGATION_ESTABLISHED}:
        return ()

    families = {_TERMINAL_SIGNAL_FAMILIES[item] for item in terminal}
    if len(families) <= 1:
        return ()
    return tuple(sorted(terminal))
