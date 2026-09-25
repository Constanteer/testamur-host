from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .product_advisory_actions import assess_advisory_candidate
from .product_service import TestamurProductService

_ALLOWED_FIELDS = {
    "event_revision_id",
    "subject_revision",
    "evidence",
    "basis",
    "scope",
    "lineage_path_edge_ids",
    "analyzer",
    "policy_ref",
    "supersedes_assessment_id",
}
_FORBIDDEN_CONCLUSION_FIELDS = {"state", "verdict", "trust_score"}


def record_advisory_assessment(
    service: TestamurProductService,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the HTTP product payload and record one canonical assessment.

    The caller supplies evidence and provenance/basis, never a conclusion.  The
    affectedness engine remains the only component allowed to resolve state.
    """

    forbidden = sorted(set(payload) & _FORBIDDEN_CONCLUSION_FIELDS)
    if forbidden:
        raise ValueError(
            "advisory assessment does not accept caller-supplied conclusion fields: "
            + ", ".join(forbidden)
        )
    unknown = sorted(set(payload) - _ALLOWED_FIELDS)
    if unknown:
        raise ValueError("advisory assessment does not accept fields: " + ", ".join(unknown))

    event_revision_id = payload.get("event_revision_id")
    subject_revision = payload.get("subject_revision")
    evidence = payload.get("evidence")
    basis = payload.get("basis")
    if not isinstance(event_revision_id, str) or not event_revision_id.strip():
        raise ValueError("event_revision_id must be a non-empty string")
    if not isinstance(subject_revision, str) or not subject_revision.strip():
        raise ValueError("subject_revision must be a non-empty string")
    if isinstance(evidence, (str, bytes)) or not isinstance(evidence, Sequence):
        raise ValueError("evidence must be an array")
    if isinstance(basis, (str, bytes)) or not isinstance(basis, Sequence):
        raise ValueError("basis must be an array")
    if not all(isinstance(item, Mapping) for item in evidence):
        raise ValueError("evidence entries must be objects")
    if not all(isinstance(item, Mapping) for item in basis):
        raise ValueError("basis entries must be objects")

    scope = payload.get("scope")
    analyzer = payload.get("analyzer")
    lineage_path_edge_ids = payload.get("lineage_path_edge_ids")
    policy_ref = payload.get("policy_ref")
    supersedes_assessment_id = payload.get("supersedes_assessment_id")
    if scope is not None and not isinstance(scope, Mapping):
        raise ValueError("scope must be an object when provided")
    if analyzer is not None and not isinstance(analyzer, Mapping):
        raise ValueError("analyzer must be an object when provided")
    if lineage_path_edge_ids is not None:
        if isinstance(lineage_path_edge_ids, (str, bytes)) or not isinstance(lineage_path_edge_ids, Sequence):
            raise ValueError("lineage_path_edge_ids must be an array when provided")
        if not all(isinstance(item, str) for item in lineage_path_edge_ids):
            raise ValueError("lineage_path_edge_ids entries must be strings")
    if policy_ref is not None and not isinstance(policy_ref, str):
        raise ValueError("policy_ref must be a string when provided")
    if supersedes_assessment_id is not None and not isinstance(supersedes_assessment_id, str):
        raise ValueError("supersedes_assessment_id must be a string when provided")

    try:
        return assess_advisory_candidate(
            service.database_path,
            event_revision_id=event_revision_id.strip(),
            subject_revision=subject_revision.strip(),
            evidence=[dict(item) for item in evidence],
            basis=[dict(item) for item in basis],
            scope=None if scope is None else dict(scope),
            lineage_path_edge_ids=None if lineage_path_edge_ids is None else list(lineage_path_edge_ids),
            analyzer=None if analyzer is None else dict(analyzer),
            policy_ref=policy_ref,
            supersedes_assessment_id=supersedes_assessment_id,
        )
    except KeyError as exc:
        raise ValueError("event_revision_id does not identify a recorded immutable advisory revision") from exc
