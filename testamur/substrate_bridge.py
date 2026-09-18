from __future__ import annotations

from typing import Any, Mapping

from .contracts import EXTENSION_OBJECT_KINDS, ObjectKind
from .legacy_adapter import ResolvedReference, resolve_reference
from .object_projection import (
    BridgeConflictError,
    BridgeEnvelope,
    ProjectionComparison,
    ProjectionRelation,
    compare_bridge_envelopes,
    extension_object_id,
    project_legacy_extension,
    reconcile_bridge_envelopes,
)


BRIDGE_IDENTITY_VERSION = "testamur.legacy-identity.v1"


def legacy_identity(
    source_ref: str,
    *,
    source_namespace: str = "witness",
) -> dict[str, str]:
    """Return the shared deterministic identity key for one legacy object.

    Domain workers should use this rather than inventing their own legacy-ID
    projection rule. Identity is intentionally based on origin namespace +
    origin reference, not on claims about equivalence, truth or authority.
    """

    namespace = str(source_namespace).strip()
    reference = str(source_ref).strip()
    if not namespace:
        raise ValueError("source_namespace must not be empty")
    if not reference:
        raise ValueError("source_ref must not be empty")
    return {
        "identity_version": BRIDGE_IDENTITY_VERSION,
        "source_namespace": namespace,
        "source_ref": reference,
    }


def legacy_extension_ref(
    kind: ObjectKind | str,
    source_ref: str,
    *,
    source_namespace: str = "witness",
) -> str:
    resolved = ObjectKind(kind)
    if resolved not in EXTENSION_OBJECT_KINDS:
        raise ValueError(f"{resolved.value} is not a bridgeable Testamur extension kind")
    return extension_object_id(
        resolved,
        legacy_identity(source_ref, source_namespace=source_namespace),
    )


def project_legacy_object(
    kind: ObjectKind | str,
    *,
    source_ref: str,
    payload: Mapping[str, Any],
    provenance: Mapping[str, Any],
    source_namespace: str = "witness",
    source_schema: str | None = None,
) -> BridgeEnvelope:
    """Project a legacy extension object using the shared W1 identity rule.

    The envelope preserves payload and caller-supplied provenance verbatim under
    canonical JSON normalization. Projection does not promote verification,
    truth or authority; domain workers remain responsible for domain-specific
    validation/revalidation.
    """

    resolved = ObjectKind(kind)
    identity = legacy_identity(source_ref, source_namespace=source_namespace)
    envelope = project_legacy_extension(
        resolved,
        identity=identity,
        payload=payload,
        source_ref=source_ref,
        provenance=provenance,
        source_namespace=source_namespace,
        source_schema=source_schema,
    )
    expected = legacy_extension_ref(
        resolved,
        source_ref,
        source_namespace=source_namespace,
    )
    if envelope.canonical_ref != expected:
        raise AssertionError("legacy projection identity diverged from shared bridge rule")
    return envelope


def compare_legacy_projections(
    existing: BridgeEnvelope,
    candidate: BridgeEnvelope,
) -> ProjectionComparison:
    """Compare two bridge envelopes through the stable W1 facade."""

    return compare_bridge_envelopes(existing, candidate)


def reconcile_legacy_projection(
    existing: BridgeEnvelope,
    candidate: BridgeEnvelope,
    *,
    allow_explicit_revision: bool = False,
) -> BridgeEnvelope:
    """Fail closed on silent legacy-identity conflation.

    Domain workers may explicitly accept a later projection from the *same*
    legacy origin as a revision. A different origin can never reuse the same
    canonical bridge identity through this API.
    """

    return reconcile_bridge_envelopes(
        existing,
        candidate,
        allow_explicit_revision=allow_explicit_revision,
    )


def resolve_mixed_reference(value: str) -> ResolvedReference:
    """Stable bridge entrypoint for mixed ``tst:*``/``wtn:*``/legacy refs."""

    return resolve_reference(value)


__all__ = [
    "BRIDGE_IDENTITY_VERSION",
    "BridgeConflictError",
    "BridgeEnvelope",
    "ProjectionComparison",
    "ProjectionRelation",
    "compare_legacy_projections",
    "legacy_extension_ref",
    "legacy_identity",
    "project_legacy_object",
    "reconcile_legacy_projection",
    "resolve_mixed_reference",
]
