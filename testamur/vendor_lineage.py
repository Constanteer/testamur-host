from __future__ import annotations

from typing import Any, Mapping

from .component_identity import ComponentIdentity, ComponentRevisionIdentity
from .lineage import TestamurLineageStore


VENDOR_PROJECTOR = "testamur.vendor-lineage-projection"
VENDOR_PROJECTOR_VERSION = "1"


def _required(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    return text


def _projection_inputs(
    *,
    evidence_ref: str,
    location: Mapping[str, Any],
    manifest_ref: str | None,
    analyzer: str,
    analyzer_version: str,
) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    evidence = _required(evidence_ref, field="evidence_ref")
    analyzer_id = _required(analyzer, field="analyzer")
    analyzer_ver = _required(analyzer_version, field="analyzer_version")
    manifest = (
        None
        if manifest_ref is None
        else _required(manifest_ref, field="manifest_ref")
    )
    if not isinstance(location, Mapping) or not dict(location):
        raise ValueError("location must be a non-empty mapping")
    location_payload = dict(location)
    evidence_payload: dict[str, Any] = {
        "ref": evidence,
        "evidence_class": "DERIVED",
        "analyzer": analyzer_id,
        "analyzer_version": analyzer_ver,
        "location": location_payload,
    }
    if manifest is not None:
        evidence_payload["manifest_ref"] = manifest
    return evidence_payload, location_payload, manifest


def record_vendored_component(
    store: TestamurLineageStore,
    *,
    container_revision: str,
    component_revision: str,
    component_identity: ComponentIdentity | Mapping[str, Any],
    evidence_ref: str,
    location: Mapping[str, Any],
    manifest_ref: str | None = None,
    analyzer: str = VENDOR_PROJECTOR,
    analyzer_version: str = VENDOR_PROJECTOR_VERSION,
) -> dict[str, Any]:
    """Record vendoring/embedding as a provenance-bearing ``CONTAINS`` edge.

    ``component_revision`` is an exact external/canonical revision reference supplied
    by the caller. Prefer :func:`record_vendored_component_revision` when a canonical
    ``ComponentRevisionIdentity`` is available; that path mechanically keeps stable
    component identity and revision identity coupled.

    A package name/version observation alone is never promoted to affectedness by
    this helper. Containment remains lineage evidence, not an applicability verdict.
    """

    downstream = _required(container_revision, field="container_revision")
    upstream = _required(component_revision, field="component_revision")
    evidence_payload, location_payload, manifest = _projection_inputs(
        evidence_ref=evidence_ref,
        location=location,
        manifest_ref=manifest_ref,
        analyzer=analyzer,
        analyzer_version=analyzer_version,
    )
    identity = (
        component_identity
        if isinstance(component_identity, ComponentIdentity)
        else ComponentIdentity.from_dict(component_identity)
    )

    return store.record_lineage(
        downstream,
        "CONTAINS",
        upstream,
        evidence=[evidence_payload],
        component_mapping={
            "upstream_component": identity.as_dict(),
            "downstream_location": location_payload,
        },
        metadata={
            "projection": "vendored_component",
            "manifest_ref": manifest,
            "identity_refs_are_type_stable_strings": True,
            "name_version_match_is_affectedness_verdict": False,
            "containment_is_affectedness_verdict": False,
        },
    )


def record_vendored_component_revision(
    store: TestamurLineageStore,
    *,
    container_revision: str,
    component_revision_identity: ComponentRevisionIdentity | Mapping[str, Any],
    evidence_ref: str,
    location: Mapping[str, Any],
    manifest_ref: str | None = None,
    analyzer: str = VENDOR_PROJECTOR,
    analyzer_version: str = VENDOR_PROJECTOR_VERSION,
) -> dict[str, Any]:
    """Record a canonical, revision-pinned vendored component.

    This is the preferred W1 integration hook for SBOM/manifest projection. It
    rejects version-only or locator-only identities: those observations are useful
    discovery evidence, but they do not establish the exact material identity needed
    for lineage-based advisory propagation. The canonical component revision ID is
    used as the upstream lineage endpoint, preventing a caller from accidentally
    pairing one component identity with another component's revision reference.
    """

    downstream = _required(container_revision, field="container_revision")
    revision_identity = (
        component_revision_identity
        if isinstance(component_revision_identity, ComponentRevisionIdentity)
        else ComponentRevisionIdentity.from_dict(component_revision_identity)
    )
    if not revision_identity.is_exact_revision:
        raise ValueError(
            "vendored component lineage requires revision-pinned or content-addressed identity; "
            "version/locator alone is insufficient"
        )
    evidence_payload, location_payload, manifest = _projection_inputs(
        evidence_ref=evidence_ref,
        location=location,
        manifest_ref=manifest_ref,
        analyzer=analyzer,
        analyzer_version=analyzer_version,
    )

    return store.record_lineage(
        downstream,
        "CONTAINS",
        revision_identity.revision_id,
        evidence=[evidence_payload],
        component_mapping={
            "upstream_component": revision_identity.component.as_dict(),
            "upstream_component_revision": revision_identity.as_dict(),
            "downstream_location": location_payload,
        },
        metadata={
            "projection": "vendored_component_revision",
            "manifest_ref": manifest,
            "identity_strength": revision_identity.identity_strength,
            "identity_refs_are_type_stable_strings": True,
            "name_version_match_is_affectedness_verdict": False,
            "containment_is_affectedness_verdict": False,
        },
    )
