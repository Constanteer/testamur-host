from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Mapping, Protocol

from .component_identity import canonical_json


class LineageEdgeView(Protocol):
    def get_lineage(self, edge_id: str) -> dict[str, Any] | None: ...


class AdvisoryRevisionView(Protocol):
    def get_revision(self, event_revision_id: str) -> dict[str, Any] | None: ...


def _string(value: Any, *, field: str, required: bool = True) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"{field} must be a string")
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    text = value.strip()
    if not text:
        if required:
            raise ValueError(f"{field} must not be empty")
        return None
    return text


def _basis_entries(value: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize projected basis without treating malformed data as absence."""

    malformed: list[dict[str, Any]] = []
    if value is None:
        return [], malformed
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
        return [], [{"index": None, "reason": "basis_must_be_sequence_of_mappings"}]

    entries: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            malformed.append({"index": index, "reason": "basis_entry_must_be_mapping"})
            continue
        item = dict(raw)
        try:
            kind = _string(item.get("kind"), field=f"basis[{index}].kind")
            ref = _string(item.get("ref"), field=f"basis[{index}].ref")
        except ValueError as exc:
            malformed.append(
                {"index": index, "reason": "basis_identity_invalid", "detail": str(exc)}
            )
            continue
        assert kind is not None and ref is not None
        item["kind"] = kind
        item["ref"] = ref
        try:
            canonical_json(item)
        except (TypeError, ValueError) as exc:
            malformed.append(
                {"index": index, "reason": "basis_entry_not_json_serializable", "detail": str(exc)}
            )
            continue
        entries.append(item)
    return entries, malformed


def _reference_sequence(value: Any, *, field: str) -> tuple[list[str], list[dict[str, Any]]]:
    if value is None:
        return [], []
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
        return [], [{"index": None, "reason": f"{field}_must_be_sequence_of_strings"}]
    refs: list[str] = []
    malformed: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        try:
            ref = _string(raw, field=f"{field}[{index}]")
        except ValueError as exc:
            malformed.append(
                {"index": index, "reason": f"{field}_identity_invalid", "detail": str(exc)}
            )
            continue
        assert ref is not None
        refs.append(ref)
    return sorted(set(refs)), malformed


def _conflicting_duplicate_basis_refs(
    basis: list[dict[str, Any]],
) -> list[dict[str, str]]:
    by_identity: dict[tuple[str, str], str] = {}
    conflicts: set[tuple[str, str]] = set()
    for item in basis:
        kind = item["kind"]
        ref = item["ref"]
        encoded = canonical_json(item)
        key = (kind, ref)
        previous = by_identity.get(key)
        if previous is None:
            by_identity[key] = encoded
        elif previous != encoded:
            conflicts.add(key)
    return [{"kind": kind, "ref": ref} for kind, ref in sorted(conflicts)]


def audit_affectedness_basis(
    assessment: Mapping[str, Any],
    *,
    lineage: LineageEdgeView | None = None,
    advisories: AdvisoryRevisionView | None = None,
) -> dict[str, Any]:
    """Audit whether an immutable assessment's pinned basis is self-consistent.

    This is deliberately an integrity check, not an affectedness resolver. It
    never upgrades POTENTIALLY_AFFECTED to a terminal verdict and never treats
    malformed or missing evidence as evidence of safety.
    """

    if not isinstance(assessment, Mapping):
        raise ValueError("assessment must be a mapping")

    basis, malformed_basis = _basis_entries(assessment.get("basis"))
    pinned_path_ids, malformed_path_refs = _reference_sequence(
        assessment.get("lineage_path_edge_ids"),
        field="lineage_path_edge_ids",
    )
    conflicting_duplicate_basis_refs = _conflicting_duplicate_basis_refs(basis)

    basis_lineage_ids = sorted(
        {item["ref"] for item in basis if item["kind"] == "lineage_edge"}
    )
    missing_from_basis = sorted(set(pinned_path_ids) - set(basis_lineage_ids))
    unpinned_basis_edges = sorted(set(basis_lineage_ids) - set(pinned_path_ids))

    malformed_event_revision_id = False
    try:
        event_revision_id = _string(
            assessment.get("event_revision_id"),
            field="event_revision_id",
            required=False,
        )
    except ValueError:
        malformed_event_revision_id = True
        event_revision_id = None

    advisory_basis_ids = sorted(
        {item["ref"] for item in basis if item["kind"] == "advisory_revision"}
    )
    advisory_basis_matches = (
        not advisory_basis_ids
        or (
            event_revision_id is not None
            and advisory_basis_ids == [event_revision_id]
        )
    )

    missing_lineage_edges: list[str] = []
    if lineage is not None:
        missing_lineage_edges = [
            edge_id for edge_id in pinned_path_ids if lineage.get_lineage(edge_id) is None
        ]

    missing_advisory_revision = False
    if advisories is not None and event_revision_id:
        missing_advisory_revision = advisories.get_revision(event_revision_id) is None

    reasons: list[str] = []
    if malformed_basis:
        reasons.append("malformed_basis")
    if malformed_path_refs:
        reasons.append("malformed_lineage_path_edge_ids")
    if malformed_event_revision_id:
        reasons.append("malformed_event_revision_id")
    if conflicting_duplicate_basis_refs:
        reasons.append("conflicting_duplicate_basis_ref")
    if missing_from_basis:
        reasons.append("path_edge_not_in_basis")
    if unpinned_basis_edges:
        reasons.append("basis_lineage_edge_not_in_pinned_path")
    if not advisory_basis_matches:
        reasons.append("advisory_basis_revision_mismatch")
    if missing_lineage_edges:
        reasons.append("missing_lineage_edges")
    if missing_advisory_revision:
        reasons.append("missing_advisory_revision")

    return {
        "valid": not reasons,
        "complete": not (
            malformed_basis
            or malformed_path_refs
            or malformed_event_revision_id
            or missing_lineage_edges
            or missing_advisory_revision
        ),
        "reasons": reasons,
        "event_revision_id": event_revision_id,
        "advisory_basis_revision_ids": advisory_basis_ids,
        "pinned_lineage_edge_ids": pinned_path_ids,
        "basis_lineage_edge_ids": basis_lineage_ids,
        "path_edges_missing_from_basis": missing_from_basis,
        "basis_edges_missing_from_path": unpinned_basis_edges,
        "conflicting_duplicate_basis_refs": conflicting_duplicate_basis_refs,
        "malformed_basis_entries": malformed_basis,
        "malformed_lineage_path_edge_refs": malformed_path_refs,
        "malformed_event_revision_id": malformed_event_revision_id,
        "missing_lineage_edge_ids": missing_lineage_edges,
        "missing_advisory_revision": missing_advisory_revision,
        "semantics": {
            "integrity_is_not_affectedness_verdict": True,
            "malformed_projection_is_not_missing_evidence": True,
            "conflicting_duplicate_basis_is_not_silently_collapsed": True,
            "missing_reference_is_not_disproof": True,
            "lineage_propagation_is_not_vulnerability_verdict": True,
        },
    }
