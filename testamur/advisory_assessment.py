from __future__ import annotations

from typing import Any, Iterable, Mapping, Protocol

from .advisory_resolution import advisory_identity_resolution_basis


class AffectednessAssessmentEngine(Protocol):
    def assess_from_evidence(
        self,
        *,
        event_revision_id: str,
        subject_revision: str,
        evidence: Iterable[Mapping[str, Any]],
        basis: Iterable[Mapping[str, Any]],
        event_id: str | None = None,
        scope: Mapping[str, Any] | None = None,
        lineage_path_edge_ids: Iterable[str] | None = None,
        analyzer: Mapping[str, Any] | None = None,
        policy_ref: str | None = None,
        supersedes_assessment_id: str | None = None,
    ) -> dict[str, Any]: ...


def _required(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    return text


def assess_advisory_revision(
    engine: AffectednessAssessmentEngine,
    event_revision: Mapping[str, Any],
    *,
    subject_revision: str,
    evidence: Iterable[Mapping[str, Any]],
    basis: Iterable[Mapping[str, Any]],
    scope: Mapping[str, Any] | None = None,
    lineage_path_edge_ids: Iterable[str] | None = None,
    analyzer: Mapping[str, Any] | None = None,
    policy_ref: str | None = None,
    supersedes_assessment_id: str | None = None,
) -> dict[str, Any]:
    """Assess one immutable advisory revision without losing identity-resolution basis.

    Provider/package identity resolution is evidence about *which exact upstream
    revision the advisory names*. It is not an affectedness verdict. When a caller
    moves from an advisory candidate to an applicability assessment, that resolution
    evidence must remain in the immutable assessment basis so the decision can be
    explained and revalidated later.

    This workflow deliberately delegates applicability state resolution to the
    affectedness engine. It adds no trust score and does not reinterpret a fetched
    advisory, lineage match, or resolved package identity as reliance or verification.
    """

    if not isinstance(event_revision, Mapping):
        raise ValueError("event_revision must be a mapping")
    event_revision_id = _required(
        event_revision.get("event_revision_id"), field="event_revision.event_revision_id"
    )
    raw_event_id = event_revision.get("event_id")
    event_id = None if raw_event_id is None else _required(
        raw_event_id, field="event_revision.event_id"
    )

    caller_basis = [dict(item) for item in basis]
    resolution_basis = advisory_identity_resolution_basis(event_revision)
    merged_basis = [
        {"kind": "advisory_revision", "ref": event_revision_id},
        *resolution_basis,
        *caller_basis,
    ]

    return engine.assess_from_evidence(
        event_id=event_id,
        event_revision_id=event_revision_id,
        subject_revision=_required(subject_revision, field="subject_revision"),
        evidence=evidence,
        basis=merged_basis,
        scope=scope,
        lineage_path_edge_ids=lineage_path_edge_ids,
        analyzer=analyzer,
        policy_ref=policy_ref,
        supersedes_assessment_id=supersedes_assessment_id,
    )
