from __future__ import annotations

from typing import Any, Mapping

from .lineage import LineageEvidenceClass, TestamurLineageStore


LEGACY_FORK_PROJECTOR = "testamur.legacy-fork-projection"
LEGACY_FORK_PROJECTOR_VERSION = "1"


def _required(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    return text


def _optional_string(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string when supplied")
    return value.strip() or None


def lineage_evidence_from_legacy_source_anchor(
    anchor: Mapping[str, Any],
) -> dict[str, Any]:
    """Project a legacy source EvidenceAnchor into W5 evidence metadata.

    The function accepts a plain mapping and has no runtime dependency on
    ``witness.*``. Exact source/evidence identifiers are preserved. Derived
    anchors must carry their versioned extractor identity; missing extractor
    provenance fails closed instead of being guessed. Identity-bearing legacy
    fields are never string-coerced during canonical projection.
    """

    if not isinstance(anchor, Mapping):
        raise ValueError("legacy source anchor must be a mapping")
    evidence_id = _required(anchor.get("evidence_id"), field="evidence_id")
    source_revision_id = _required(
        anchor.get("source_revision_id"), field="source_revision_id"
    )
    provenance = _required(
        anchor.get("provenance_class"), field="provenance_class"
    ).upper()
    try:
        evidence_class = LineageEvidenceClass(provenance).value
    except ValueError as exc:
        raise ValueError(
            "legacy source anchor provenance_class must be OBSERVED, DERIVED, or DECLARED"
        ) from exc

    projected: dict[str, Any] = {
        "ref": evidence_id,
        "evidence_class": evidence_class,
        "source_revision_ref": source_revision_id,
        "legacy_kind": _optional_string(anchor.get("kind"), field="kind"),
        "anchor_sha256": _optional_string(
            anchor.get("anchor_sha256"), field="anchor_sha256"
        ),
        "representation_ref": _optional_string(
            anchor.get("representation_id"), field="representation_id"
        ),
    }
    if evidence_class == LineageEvidenceClass.DERIVED.value:
        payload = anchor.get("anchor")
        if not isinstance(payload, Mapping):
            raise ValueError("derived legacy source anchor requires anchor mapping")
        projected["analyzer"] = _required(
            payload.get("extractor_id"), field="anchor.extractor_id"
        )
        projected["analyzer_version"] = _required(
            payload.get("extractor_version"), field="anchor.extractor_version"
        )
    return projected


def _revision_binding(
    refs: Mapping[str, str],
    object_id: str,
    *,
    field: str,
) -> str | None:
    raw = refs.get(object_id)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError(f"{field}[{object_id}] must be a string revision reference")
    value = raw.strip()
    return value or None


def project_legacy_fork_result(
    store: TestamurLineageStore,
    fork_result: Mapping[str, Any],
    *,
    source_revision_refs: Mapping[str, str],
    target_revision_refs: Mapping[str, str],
    fork_event_ref: str,
    projector_version: str = LEGACY_FORK_PROJECTOR_VERSION,
) -> dict[str, Any]:
    """Project a legacy project fork into revision-pinned DERIVED_FROM edges.

    Legacy ``fork_project`` returns an object-id map, but those object IDs are not
    exact artifact revisions. The caller must therefore supply exact source and
    target revision references for each copied object. Missing bindings remain
    unresolved and produce no lineage edge; malformed bindings fail closed.

    The projection records derivation only. It does not carry verification
    authority and does not imply semantic equivalence or advisory affectedness.
    """

    if not isinstance(fork_result, Mapping):
        raise ValueError("fork_result must be a mapping")
    object_map = fork_result.get("object_id_map")
    if not isinstance(object_map, Mapping):
        raise ValueError("fork_result.object_id_map must be a mapping")
    if not isinstance(source_revision_refs, Mapping):
        raise ValueError("source_revision_refs must be a mapping")
    if not isinstance(target_revision_refs, Mapping):
        raise ValueError("target_revision_refs must be a mapping")
    event_ref = _required(fork_event_ref, field="fork_event_ref")
    version = _required(projector_version, field="projector_version")

    normalized_map: list[tuple[str, str]] = []
    for raw_source_object_id, raw_target_object_id in object_map.items():
        source_object_id = _required(
            raw_source_object_id, field="source_object_id"
        )
        target_object_id = _required(
            raw_target_object_id, field="target_object_id"
        )
        normalized_map.append((source_object_id, target_object_id))

    projected: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    for source_object_id, target_object_id in sorted(normalized_map):
        upstream_revision = _revision_binding(
            source_revision_refs,
            source_object_id,
            field="source_revision_refs",
        )
        downstream_revision = _revision_binding(
            target_revision_refs,
            target_object_id,
            field="target_revision_refs",
        )
        if upstream_revision is None or downstream_revision is None:
            unresolved.append(
                {
                    "source_object_id": source_object_id,
                    "target_object_id": target_object_id,
                    "reason": "exact_revision_binding_missing",
                }
            )
            continue

        edge = store.record_lineage(
            downstream_revision,
            "DERIVED_FROM",
            upstream_revision,
            evidence=[
                {
                    "ref": event_ref,
                    "evidence_class": "DERIVED",
                    "analyzer": LEGACY_FORK_PROJECTOR,
                    "analyzer_version": version,
                    "source_object_id": source_object_id,
                    "target_object_id": target_object_id,
                }
            ],
            metadata={
                "legacy_projection": True,
                "source_project_id": fork_result.get("source_project_id"),
                "source_object_id": source_object_id,
                "target_object_id": target_object_id,
                "verification_runs_carried": fork_result.get(
                    "verification_runs_carried", 0
                ),
                "identity_refs_are_type_stable_strings": True,
                "verification_authority_carried": False,
            },
        )
        projected.append(edge)

    return {
        "projected_edges": projected,
        "unresolved": unresolved,
        "counts": {
            "mapped_objects": len(object_map),
            "projected_edges": len(projected),
            "unresolved": len(unresolved),
        },
        "semantics": {
            "exact_revision_binding_required": True,
            "legacy_object_id_is_revision_identity": False,
            "identity_refs_are_type_stable_strings": True,
            "verification_authority_inherited": False,
            "affectedness_verdict_implied": False,
        },
    }
