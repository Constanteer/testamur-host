from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from .policy import canonical_hash

ASSESSMENT_EXPLANATION_SCHEMA = "testamur.assessment-explanation.v1"
POLICY_COMPARISON_SCHEMA = "testamur.policy-comparison.v1"


class AssessmentEngineView(Protocol):
    def evaluate(
        self, *, scope_ref: str, object_ref: str, purpose: str
    ) -> dict[str, Any]: ...


def _required(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    return text


def _policy_summary(state: Mapping[str, Any]) -> dict[str, Any] | None:
    policy = state.get("policy")
    if not isinstance(policy, Mapping):
        return None
    return {
        "policy_id": policy.get("policy_id"),
        "policy_key": policy.get("policy_key"),
        "version": policy.get("version"),
        "content_hash": policy.get("content_hash"),
    }


def explain_assessment(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return a compact product DTO without weakening assessment semantics."""
    object_ref = _required(state.get("object_ref"), "assessment.object_ref")
    purpose = _required(state.get("purpose"), "assessment.purpose")
    blockers = [
        dict(item)
        for item in state.get("blockers", [])
        if isinstance(item, Mapping)
    ]
    root_causes = [
        dict(item)
        for item in state.get("root_causes", [])
        if isinstance(item, Mapping)
    ]
    restoration = [
        str(item) for item in state.get("restoration_work", []) if str(item)
    ]
    admissible = bool(state.get("admissible"))
    assessment_state = str(state.get("assessment_state") or "UNKNOWN")
    status = str(state.get("status") or "")

    if admissible:
        headline = f"{object_ref} is admissible for {purpose} under the selected policy."
    elif assessment_state == "UNKNOWN":
        if status == "unruled":
            headline = (
                f"{object_ref} has no applicable policy for {purpose}; "
                "current admissibility is unknown."
            )
        else:
            headline = (
                f"Current admissibility of {object_ref} for {purpose} is unknown; "
                "review the listed causes."
            )
    elif blockers:
        headline = f"{object_ref} is blocked for {purpose}; review the listed causes."
    else:
        headline = f"{object_ref} is not admissible for {purpose}."

    return {
        "schema_version": ASSESSMENT_EXPLANATION_SCHEMA,
        "object_ref": object_ref,
        "object_revision_ref": state.get("object_revision_ref"),
        "purpose": purpose,
        "admissible": admissible,
        "assessment_state": assessment_state,
        "status": status,
        "headline": headline,
        "policy": _policy_summary(state),
        "blockers": blockers,
        "root_causes": root_causes,
        "restoration_work": list(dict.fromkeys(restoration)),
        "semantics": {
            "admissible_implies_truth": False,
            "blocked_implies_false": False,
            "unknown_implies_false": False,
            "unknown_implies_inadmissible": False,
            "admissible_implies_authorized_transition": False,
            "stale_implies_false": False,
        },
    }


def compare_policy_outcomes(
    engine: AssessmentEngineView,
    *,
    scope_ref: str,
    object_ref: str,
    purposes: Sequence[str],
    snapshot_ref: str | None = None,
) -> dict[str, Any]:
    """Canonical multi-purpose comparison over one evaluator instance.

    Sequential reads are not automatically an atomic database snapshot. A
    caller-supplied ``snapshot_ref`` is retained only as declared provenance; it
    does not prove transactionally identical evidence across evaluations.
    """
    scope = _required(scope_ref, "scope_ref")
    obj = _required(object_ref, "object_ref")
    normalized = sorted({_required(item, "purpose") for item in purposes})
    if not normalized:
        raise ValueError("purposes must not be empty")

    outcomes: list[dict[str, Any]] = []
    for purpose in normalized:
        state = engine.evaluate(scope_ref=scope, object_ref=obj, purpose=purpose)
        if str(state.get("scope_ref") or "") != scope:
            raise ValueError("policy engine returned an assessment for a different scope")
        if str(state.get("object_ref") or "") != obj:
            raise ValueError("policy engine returned an assessment for a different object")
        if str(state.get("purpose") or "") != purpose:
            raise ValueError("policy engine returned an assessment for a different purpose")

        explanation = explain_assessment(state)
        policy = _policy_summary(state)
        blockers = list(explanation["blockers"])
        restoration = list(explanation["restoration_work"])
        outcomes.append(
            {
                "purpose": purpose,
                "admissible": bool(state.get("admissible")),
                "assessment_state": str(
                    state.get("assessment_state") or "UNKNOWN"
                ),
                "status": str(state.get("status") or ""),
                "policy": policy,
                "policy_id": None if policy is None else policy.get("policy_id"),
                "object_revision_ref": state.get("object_revision_ref"),
                "blockers": blockers,
                "blocker_types": sorted(
                    {
                        str(item.get("type"))
                        for item in blockers
                        if item.get("type")
                    }
                ),
                "restoration_work": restoration,
                "explanation": explanation,
            }
        )

    signatures = {
        canonical_hash(
            {
                "admissible": item["admissible"],
                "assessment_state": item["assessment_state"],
                "status": item["status"],
                "policy_id": item["policy_id"],
                "blockers": item["blockers"],
            }
        )
        for item in outcomes
    }
    declared_snapshot = (
        None if snapshot_ref is None else _required(snapshot_ref, "snapshot_ref")
    )
    comparison_core = {
        "scope_ref": scope,
        "object_ref": obj,
        "purposes": normalized,
        "snapshot_ref": declared_snapshot,
        "outcomes": outcomes,
    }
    return {
        "schema_version": POLICY_COMPARISON_SCHEMA,
        "comparison_id": "tst:policy-comparison:" + canonical_hash(comparison_core),
        **comparison_core,
        "outcomes_differ": len(signatures) > 1,
        "consistency": {
            "same_evidence_view_instance": True,
            "atomic_snapshot_guaranteed": False,
            "snapshot_ref": declared_snapshot,
            "snapshot_ref_is_declared_provenance": declared_snapshot is not None,
        },
        "semantics": {
            "different_outcomes_may_be_correct": True,
            "policy_is_purpose_dependent": True,
            "same_logical_subject": True,
            "identical_evidence_snapshot_guaranteed": False,
            "snapshot_label_implies_atomicity": False,
            "comparison_implies_universal_truth": False,
            "comparison_produces_universal_trust_score": False,
            "universal_trust_score": None,
        },
    }


__all__ = [
    "ASSESSMENT_EXPLANATION_SCHEMA",
    "POLICY_COMPARISON_SCHEMA",
    "AssessmentEngineView",
    "compare_policy_outcomes",
    "explain_assessment",
]
