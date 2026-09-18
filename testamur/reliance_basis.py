from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .policy import canonical_hash


def _assessment_revisions(assessment: Mapping[str, Any]) -> dict[str, str | None]:
    revisions: dict[str, str | None] = {}
    stack: list[Mapping[str, Any]] = [assessment]
    while stack:
        current = stack.pop()
        object_ref = str(current.get("object_ref") or "").strip()
        if object_ref:
            raw = current.get("object_revision_ref")
            revision = None if raw is None else str(raw).strip() or None
            if object_ref in revisions and revisions[object_ref] != revision:
                raise ValueError(
                    "assessment observed inconsistent revision identity for " + object_ref
                )
            revisions[object_ref] = revision
        for dependency in current.get("dependencies", []):
            if isinstance(dependency, Mapping):
                child = dependency.get("state")
                if isinstance(child, Mapping):
                    stack.append(child)
    return revisions


def _canonical_ids(values: Any) -> list[str]:
    """Normalize set-like basis identities before hashing or comparison."""
    return sorted({str(value).strip() for value in values if str(value).strip()})


def exact_basis_snapshot(
    engine: Any, assessment: Mapping[str, Any]
) -> dict[str, Any]:
    """Return the deterministic receipt basis observed by one policy evaluation.

    This is a projection only: it does not claim that the evidence provider was
    read atomically. Callers may compare two snapshots as an optimistic guard;
    W1 may later replace that observation window with a stable-read token.

    Object, assurance, policy and relation identities are set-like receipt basis
    dimensions. Canonicalizing them here makes the stability guard insensitive to
    provider iteration order while preserving real selection/topology changes.
    """
    basis = engine.basis(assessment)
    observed = _assessment_revisions(assessment)
    object_refs = _canonical_ids(basis["object_refs"])
    pinned = {ref: observed.get(ref) for ref in object_refs}
    assurance_ids = _canonical_ids(basis["assurance_ids"])
    policy_ids = _canonical_ids(basis["policy_ids"])
    relation_ids = _canonical_ids(basis["relation_ids"])
    return {
        "assessment_hash": canonical_hash(assessment),
        "pinned_revisions": pinned,
        "assurance_ids": assurance_ids,
        "policy_ids": policy_ids,
        "relation_ids": relation_ids,
        "topology_fingerprint": canonical_hash(relation_ids),
    }


def exact_basis_drift(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Explain deterministic drift between two observations of one exact basis."""
    reasons: list[dict[str, Any]] = []
    fields = (
        ("pinned_revisions", "object_revision_changed"),
        ("assurance_ids", "assurance_selection_changed"),
        ("policy_ids", "policy_changed"),
        ("relation_ids", "dependency_topology_changed"),
    )
    for field, reason_type in fields:
        was = before.get(field)
        now = after.get(field)
        if was != now:
            reasons.append({"type": reason_type, "was": was, "now": now})
    if before.get("assessment_hash") != after.get("assessment_hash"):
        reasons.append(
            {
                "type": "assessment_snapshot_changed",
                "was": before.get("assessment_hash"),
                "now": after.get("assessment_hash"),
            }
        )
    return reasons


def observe_stable_exact_basis(
    engine: Any,
    *,
    scope_ref: str,
    object_ref: str,
    purpose: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Optimistically observe one unchanged full reliance basis twice.

    Exact reliance freezes more than object revisions. Assurance selection,
    policy identity and dependency topology are also part of the justification
    basis. Until W1 exposes a stable-read token, two complete policy evaluations
    are the strongest provider-agnostic guard W3 can make without pretending to
    own the canonical evidence stores' transaction boundary.

    The first assessment is returned because it is the observation whose basis
    should be persisted. A changed second observation causes fail-closed issuance
    before any receipt is written. This remains optimistic: evidence can still
    change after the second observation and before a separate receipt-store
    transaction commits.
    """
    first = engine.evaluate(
        scope_ref=scope_ref, object_ref=object_ref, purpose=purpose
    )
    first_snapshot = exact_basis_snapshot(engine, first)
    second = engine.evaluate(
        scope_ref=scope_ref, object_ref=object_ref, purpose=purpose
    )
    second_snapshot = exact_basis_snapshot(engine, second)
    drift = exact_basis_drift(first_snapshot, second_snapshot)
    if drift:
        reason_types = ", ".join(item["type"] for item in drift)
        raise ValueError("exact reliance basis changed before commit: " + reason_types)
    return dict(first), first_snapshot


__all__ = [
    "exact_basis_drift",
    "exact_basis_snapshot",
    "observe_stable_exact_basis",
]
