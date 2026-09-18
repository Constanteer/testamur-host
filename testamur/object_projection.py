from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from .contracts import EXTENSION_OBJECT_KINDS, ObjectKind, classify_object_ref
from .runtime_protocol import canonical_hash, canonical_json


BRIDGE_SCHEMA = "testamur.bridge.v1"

_EXTENSION_PREFIXES: dict[ObjectKind, str] = {
    ObjectKind.WORK_SESSION: "tst:work-session:",
    ObjectKind.RELIANCE: "tst:reliance:",
    ObjectKind.POLICY: "tst:policy:",
    ObjectKind.ASSESSMENT: "tst:assessment:",
    ObjectKind.LINEAGE: "tst:lineage:",
    ObjectKind.AFFECTEDNESS: "tst:affectedness:",
    ObjectKind.ADVISORY: "tst:advisory:",
    ObjectKind.ATTESTATION: "tst:attestation:",
}


def _json_copy(value: Mapping[str, Any], *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    return json.loads(canonical_json(dict(value)))


def extension_object_id(kind: ObjectKind | str, identity: Mapping[str, Any]) -> str:
    """Return the canonical deterministic ID for one extension object.

    This is intentionally limited to the shared extension families. Existing
    core stores retain their own identity algorithms. New runtime runs use the
    canonical ``tst:run:*`` family; historical ``wtn:run:*`` IDs are
    read-only compatibility inputs rather than an active identity owner.
    """

    resolved = ObjectKind(kind)
    if resolved not in EXTENSION_OBJECT_KINDS:
        raise ValueError(f"{resolved.value} is not a Testamur extension object kind")
    normalized = _json_copy(identity, field="identity")
    if not normalized:
        raise ValueError("identity must not be empty")
    prefix = _EXTENSION_PREFIXES[resolved]
    return prefix + canonical_hash({"kind": resolved.value, "identity": normalized})


@dataclass(frozen=True, slots=True)
class BridgeEnvelope:
    """Versioned, semantics-neutral projection metadata for legacy migration.

    A bridge envelope records how a legacy object was projected into the
    canonical Testamur identity space. It does not assert that the source or
    projected payload is verified, admissible, true, current, or authoritative.
    """

    kind: ObjectKind
    canonical_ref: str
    source_ref: str
    source_namespace: str
    payload: dict[str, Any]
    provenance: dict[str, Any]
    source_schema: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in EXTENSION_OBJECT_KINDS:
            raise ValueError(f"{self.kind.value} is not a bridgeable extension kind")
        canonical = classify_object_ref(self.canonical_ref)
        if not canonical.durable or canonical.kind is not self.kind:
            raise ValueError(
                f"canonical_ref {self.canonical_ref!r} does not match kind {self.kind.value!r}"
            )
        if not str(self.source_ref).strip():
            raise ValueError("source_ref must not be empty")
        if not str(self.source_namespace).strip():
            raise ValueError("source_namespace must not be empty")
        canonical_json(self.payload)
        canonical_json(self.provenance)

    @property
    def source_identity(self) -> tuple[str, str]:
        return (self.source_namespace, self.source_ref)

    def logical_record(self) -> dict[str, Any]:
        return {
            "schema": BRIDGE_SCHEMA,
            "kind": self.kind.value,
            "canonical_ref": self.canonical_ref,
            "source": {
                "namespace": self.source_namespace,
                "ref": self.source_ref,
                "schema": self.source_schema,
            },
            "payload": _json_copy(self.payload, field="payload"),
            "provenance": _json_copy(self.provenance, field="provenance"),
            "semantic_guarantees": {
                "identity_projection_only": True,
                "verification_promoted": False,
                "truth_promoted": False,
                "authority_promoted": False,
            },
        }

    @property
    def bridge_digest(self) -> str:
        return "sha256:" + canonical_hash(self.logical_record())

    def to_json(self) -> dict[str, Any]:
        result = self.logical_record()
        result["bridge_digest"] = self.bridge_digest
        return result


class ProjectionRelation(StrEnum):
    IDENTICAL = "identical"
    DIFFERENT_IDENTITY = "different_identity"
    SAME_IDENTITY_CHANGED = "same_identity_changed"
    ORIGIN_CONFLICT = "origin_conflict"


class BridgeConflictError(ValueError):
    """Raised when migration would silently conflate incompatible projections."""


@dataclass(frozen=True, slots=True)
class ProjectionComparison:
    relation: ProjectionRelation
    canonical_ref: str | None
    left_digest: str
    right_digest: str
    changed_fields: tuple[str, ...]

    @property
    def identical(self) -> bool:
        return self.relation is ProjectionRelation.IDENTICAL

    def to_json(self) -> dict[str, Any]:
        return {
            "relation": self.relation.value,
            "canonical_ref": self.canonical_ref,
            "left_digest": self.left_digest,
            "right_digest": self.right_digest,
            "changed_fields": list(self.changed_fields),
        }


def compare_bridge_envelopes(
    left: BridgeEnvelope,
    right: BridgeEnvelope,
) -> ProjectionComparison:
    """Mechanically compare two migration projections without semantic promotion.

    The same canonical identity may legitimately acquire a later legacy payload,
    but that is a revision decision, not idempotence.  Such divergence is made
    explicit as ``SAME_IDENTITY_CHANGED``.  If the same canonical identity is
    claimed by different origin references/namespaces, the result is the stronger
    ``ORIGIN_CONFLICT`` state.
    """

    if left.canonical_ref != right.canonical_ref:
        return ProjectionComparison(
            relation=ProjectionRelation.DIFFERENT_IDENTITY,
            canonical_ref=None,
            left_digest=left.bridge_digest,
            right_digest=right.bridge_digest,
            changed_fields=("canonical_ref",),
        )

    changed: list[str] = []
    if left.kind is not right.kind:
        changed.append("kind")
    if left.source_namespace != right.source_namespace:
        changed.append("source_namespace")
    if left.source_ref != right.source_ref:
        changed.append("source_ref")
    if left.source_schema != right.source_schema:
        changed.append("source_schema")
    if left.payload != right.payload:
        changed.append("payload")
    if left.provenance != right.provenance:
        changed.append("provenance")

    if not changed:
        relation = ProjectionRelation.IDENTICAL
    elif {"source_namespace", "source_ref"} & set(changed):
        relation = ProjectionRelation.ORIGIN_CONFLICT
    else:
        relation = ProjectionRelation.SAME_IDENTITY_CHANGED

    return ProjectionComparison(
        relation=relation,
        canonical_ref=left.canonical_ref,
        left_digest=left.bridge_digest,
        right_digest=right.bridge_digest,
        changed_fields=tuple(changed),
    )


def reconcile_bridge_envelopes(
    existing: BridgeEnvelope,
    candidate: BridgeEnvelope,
    *,
    allow_explicit_revision: bool = False,
) -> BridgeEnvelope:
    """Return the accepted projection or fail closed on identity ambiguity.

    ``allow_explicit_revision`` only permits a changed projection from the same
    origin.  It never permits a different legacy origin to claim the same
    canonical identity.  The caller remains responsible for persisting revision
    history; this function merely prevents silent overwrite/conflation.
    """

    comparison = compare_bridge_envelopes(existing, candidate)
    if comparison.relation is ProjectionRelation.IDENTICAL:
        return existing
    if comparison.relation is ProjectionRelation.DIFFERENT_IDENTITY:
        raise BridgeConflictError("cannot reconcile projections with different canonical identities")
    if comparison.relation is ProjectionRelation.ORIGIN_CONFLICT:
        raise BridgeConflictError(
            "canonical legacy identity is claimed by different source origins: "
            + ", ".join(comparison.changed_fields)
        )
    if allow_explicit_revision:
        return candidate
    raise BridgeConflictError(
        "same canonical legacy identity has a changed projection; record an explicit revision "
        "instead of silently overwriting migration state"
    )


def project_legacy_extension(
    kind: ObjectKind | str,
    *,
    identity: Mapping[str, Any],
    payload: Mapping[str, Any],
    source_ref: str,
    provenance: Mapping[str, Any],
    source_namespace: str = "witness",
    source_schema: str | None = None,
) -> BridgeEnvelope:
    """Create an idempotent projection envelope for a legacy extension object."""

    resolved = ObjectKind(kind)
    canonical_ref = extension_object_id(resolved, identity)
    return BridgeEnvelope(
        kind=resolved,
        canonical_ref=canonical_ref,
        source_ref=str(source_ref),
        source_namespace=str(source_namespace),
        source_schema=None if source_schema is None else str(source_schema),
        payload=_json_copy(payload, field="payload"),
        provenance=_json_copy(provenance, field="provenance"),
    )


__all__ = [
    "BRIDGE_SCHEMA",
    "BridgeConflictError",
    "BridgeEnvelope",
    "ProjectionComparison",
    "ProjectionRelation",
    "compare_bridge_envelopes",
    "extension_object_id",
    "project_legacy_extension",
    "reconcile_bridge_envelopes",
]
