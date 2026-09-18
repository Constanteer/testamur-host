from __future__ import annotations

from typing import Any, Iterable, Protocol

from .assessment_projection import (
    POLICY_COMPARISON_SCHEMA,
    compare_policy_outcomes,
)

POLICY_COMPARISON_SCHEMA_VERSION = POLICY_COMPARISON_SCHEMA


class PurposePolicyEvaluator(Protocol):
    def evaluate(
        self, *, scope_ref: str, object_ref: str, purpose: str
    ) -> dict[str, Any]: ...


def compare_policy_purposes(
    engine: PurposePolicyEvaluator,
    *,
    scope_ref: str,
    object_ref: str,
    purposes: Iterable[str],
) -> dict[str, Any]:
    """Compatibility name for the canonical multi-purpose comparison DTO.

    Comparison semantics live in ``assessment_projection.compare_policy_outcomes``.
    Keeping this historical import path as a thin delegate prevents two functions
    from claiming the same schema version while producing different structures.
    """
    return compare_policy_outcomes(
        engine,
        scope_ref=scope_ref,
        object_ref=object_ref,
        purposes=list(purposes),
    )


__all__ = [
    "POLICY_COMPARISON_SCHEMA_VERSION",
    "PurposePolicyEvaluator",
    "compare_policy_purposes",
]
