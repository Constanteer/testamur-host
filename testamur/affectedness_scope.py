from __future__ import annotations

from typing import Any, Iterable, Mapping

from .component_identity import canonical_json


def _scope(value: Any, *, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    normalized = dict(value)
    canonical_json(normalized)
    return normalized or None


def applicability_scope_conflict(
    evidence: Iterable[Mapping[str, Any]],
    *,
    assessment_scope: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Detect evidence that cannot safely be combined into one scoped verdict.

    Affectedness is event/condition/scope-specific. Evidence produced for two
    distinct explicit scopes must therefore not be combined merely because its
    signals happen to compose into a terminal state. When the assessment declares
    an explicit scope, every explicitly scoped evidence item must match it exactly.

    Unscoped evidence is not rejected here: it may be genuinely scope-independent.
    This helper deliberately does not infer subset/superset relationships between
    arbitrary scope mappings. A future domain-specific rule may establish such a
    relation with evidence, but the generic W5 engine fails closed instead.
    """

    expected = _scope(assessment_scope, field="assessment_scope")
    explicit: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(evidence):
        if not isinstance(raw, Mapping):
            raise ValueError("applicability evidence entries must be mappings")
        item_scope = _scope(raw.get("scope"), field=f"evidence[{index}].scope")
        if item_scope is None:
            continue
        key = canonical_json(item_scope)
        explicit.setdefault(key, item_scope)

    if expected is not None:
        expected_key = canonical_json(expected)
        incompatible = [explicit[key] for key in sorted(explicit) if key != expected_key]
        if incompatible:
            return {
                "reason": "applicability_evidence_scope_mismatch",
                "assessment_scope": expected,
                "evidence_scopes": [explicit[key] for key in sorted(explicit)],
                "incompatible_scopes": incompatible,
            }
        return None

    if len(explicit) > 1:
        return {
            "reason": "conflicting_applicability_evidence_scopes",
            "assessment_scope": None,
            "evidence_scopes": [explicit[key] for key in sorted(explicit)],
        }
    return None
