from __future__ import annotations

from typing import Any, Iterable, Mapping, Protocol

from .assessment import AssessmentState


class _RelianceReader(Protocol):
    def verify(self, receipt_id: str) -> dict[str, Any]: ...


class _BlastRadiusReader(Protocol):
    def blast_radius(
        self,
        *,
        scope_ref: str,
        changed_object_refs: Iterable[str],
    ) -> dict[str, Any]: ...


def _unavailable_recovery(
    receipt: Mapping[str, Any],
    *,
    basis_object_refs: Iterable[str] = (),
) -> list[dict[str, Any]]:
    refs = sorted({str(ref) for ref in basis_object_refs if str(ref)})
    if not refs:
        fallback = str(receipt.get("object_ref") or "")
        refs = [fallback] if fallback else []
    return [
        {
            "action": "restore_or_reproject_current_evidence",
            "object_ref": object_ref,
            "purpose": str(receipt.get("purpose") or ""),
            "then": "reverify_reliance_receipt",
        }
        for object_ref in refs
    ]


def _staleness_recovery(receipt: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Derive deterministic review actions without turning them into verdicts."""
    hints: list[dict[str, Any]] = []
    purpose = str(receipt.get("purpose") or "")
    for reason in receipt.get("staleness_reasons", []):
        if not isinstance(reason, Mapping):
            continue
        reason_type = str(reason.get("type") or "")
        if reason_type in {"object_revision_changed", "reliant_revision_changed"}:
            object_ref = str(
                reason.get("object_ref")
                or reason.get("reliant_ref")
                or receipt.get("object_ref")
                or ""
            )
            hints.append({
                "action": "review_changed_revision",
                "object_ref": object_ref,
                "purpose": purpose,
                "reason_type": reason_type,
                "then": "reverify_reliance_receipt",
            })
        elif reason_type == "reliant_revision_not_assessable":
            hints.append({
                "action": "restore_or_reproject_reliant_revision",
                "object_ref": str(
                    reason.get("reliant_ref")
                    or receipt.get("reliant_ref")
                    or ""
                ),
                "purpose": purpose,
                "reason_type": reason_type,
                "then": "reverify_reliance_receipt",
            })
        elif reason_type == "assurance_selection_changed":
            hints.append({
                "action": "review_current_assurance_selection",
                "object_ref": str(receipt.get("object_ref") or ""),
                "purpose": purpose,
                "reason_type": reason_type,
                "then": "reverify_reliance_receipt",
            })
        elif reason_type == "policy_changed":
            hints.append({
                "action": "review_current_policy",
                "object_ref": str(receipt.get("object_ref") or ""),
                "purpose": purpose,
                "reason_type": reason_type,
                "then": "reverify_reliance_receipt",
            })
        elif reason_type == "dependency_topology_changed":
            hints.append({
                "action": "review_dependency_topology",
                "object_ref": str(receipt.get("object_ref") or ""),
                "purpose": purpose,
                "reason_type": reason_type,
                "then": "reverify_reliance_receipt",
            })
        elif reason_type == "admission_decision_changed":
            hints.append({
                "action": "review_admission_change",
                "object_ref": str(receipt.get("object_ref") or ""),
                "purpose": purpose,
                "reason_type": reason_type,
                "then": "reverify_reliance_receipt",
            })
    unique: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for hint in hints:
        key = (
            str(hint.get("action") or ""),
            str(hint.get("object_ref") or ""),
            str(hint.get("purpose") or ""),
            str(hint.get("reason_type") or ""),
            str(hint.get("then") or ""),
        )
        unique[key] = hint
    return [unique[key] for key in sorted(unique)]


def reliance_health(store: _RelianceReader, receipt_id: str) -> dict[str, Any]:
    """Project one canonical immutable receipt into compact current review state.

    Availability/staleness semantics belong to ``RelianceStore.verify``.  This
    helper deliberately does not inspect SQLite rows, the policy engine, or the
    evidence view.  It only adds recovery hints and stable presentation fields.
    Missing receipts therefore remain lookup errors from the canonical store.
    """

    verified = store.verify(str(receipt_id))
    assessment_available = bool(verified.get("assessment_available", True))
    unavailable_refs = sorted(
        {str(ref) for ref in verified.get("unavailable_basis_object_refs", []) if str(ref)}
    )
    recovery = [
        dict(item)
        for item in verified.get("recovery_hints", [])
        if isinstance(item, Mapping)
    ]
    if unavailable_refs or not assessment_available:
        recovery.extend(
            _unavailable_recovery(
                verified,
                basis_object_refs=unavailable_refs,
            )
        )
    recovery.extend(_staleness_recovery(verified))
    deduped_recovery: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for hint in recovery:
        key = (
            str(hint.get("action") or ""),
            str(hint.get("object_ref") or ""),
            str(hint.get("purpose") or ""),
            str(hint.get("reason_type") or ""),
            str(hint.get("then") or ""),
        )
        deduped_recovery[key] = hint
    recovery = [deduped_recovery[key] for key in sorted(deduped_recovery)]

    current_admissible = verified.get("current_admissible")
    current_state = verified.get("current_assessment_state")
    if not assessment_available:
        # Lack of a current assessment is UNKNOWN, not a negative policy verdict.
        current_admissible = None
        current_state = AssessmentState.UNKNOWN.value

    return {
        **verified,
        "assessment_available": assessment_available,
        "current_admissible": current_admissible,
        "current_assessment_state": current_state,
        "unavailable_basis_object_refs": unavailable_refs,
        "reconsideration_required": bool(
            verified.get("reconsideration_required", verified.get("stale"))
        ),
        "recovery_hints": recovery,
        "semantics": {
            **dict(verified.get("semantics") or {}),
            "recovery_hint_implies_verdict": False,
        },
    }


def resilient_blast_radius(
    store: _BlastRadiusReader,
    *,
    scope_ref: str,
    changed_object_refs: Iterable[str],
) -> dict[str, Any]:
    """Compatibility alias for the canonical store-level blast-radius contract.

    Older callers imported this helper while resilience lived in the read layer.
    ``RelianceStore.blast_radius`` now owns that behavior, including UNKNOWN when
    current evidence is unavailable.  Keep this function as a thin public-API
    delegate so convergence does not preserve a second SQLite traversal engine.
    """

    scope = str(scope_ref or "").strip()
    if not scope:
        raise ValueError("scope_ref must not be empty")
    changed = sorted(
        {str(item).strip() for item in changed_object_refs if str(item).strip()}
    )
    blast_radius = getattr(store, "blast_radius", None)
    if not callable(blast_radius):
        raise TypeError("resilient blast radius requires canonical blast_radius")
    result = blast_radius(scope_ref=scope, changed_object_refs=changed)
    if str(result.get("scope_ref") or "") != scope:
        raise RuntimeError("reliance blast radius returned a different scope")
    returned = sorted(map(str, result.get("changed_object_refs") or []))
    if returned != changed:
        raise RuntimeError("reliance blast radius changed the requested object basis")
    return result


def reliance_health_summary(item: Mapping[str, Any]) -> dict[str, Any]:
    reasons = [
        str(reason.get("type") or "unknown")
        for reason in item.get("staleness_reasons", [])
        if isinstance(reason, Mapping)
    ]
    admissible = item.get("current_admissible")
    if admissible is not None:
        admissible = bool(admissible)
    recovery_hints = [
        dict(hint)
        for hint in item.get("recovery_hints", [])
        if isinstance(hint, Mapping)
    ]
    recovery_hints.sort(
        key=lambda hint: (
            str(hint.get("action") or ""),
            str(hint.get("object_ref") or ""),
            str(hint.get("purpose") or ""),
            str(hint.get("reason_type") or ""),
            str(hint.get("then") or ""),
        )
    )
    return {
        "receipt_id": str(item.get("receipt_id") or ""),
        "reliant_ref": str(item.get("reliant_ref") or ""),
        "object_ref": str(item.get("object_ref") or ""),
        "purpose": str(item.get("purpose") or ""),
        "stale": bool(item.get("stale")),
        "current_admissible": admissible,
        "current_assessment_state": str(
            item.get("current_assessment_state") or AssessmentState.UNKNOWN.value
        ),
        "staleness_reason_types": sorted(set(reasons)),
        "reconsideration_required": bool(
            item.get("reconsideration_required", item.get("stale"))
        ),
        "recovery_hints": recovery_hints,
        "semantics": {
            "stale_implies_false": False,
            "unknown_implies_false": False,
            "unknown_implies_inadmissible": False,
            "recovery_hint_implies_verdict": False,
        },
    }