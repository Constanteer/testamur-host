from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from .advisory import TestamurAdvisoryStore
from .advisory_assessment import assess_advisory_revision
from .affectedness import TestamurAffectednessEngine, TestamurAffectednessStore
from .lineage import TestamurLineageStore


def assess_advisory_candidate(
    database_path: str | Path,
    *,
    event_revision_id: str,
    subject_revision: str,
    evidence: Iterable[Mapping[str, Any]],
    basis: Iterable[Mapping[str, Any]],
    scope: Mapping[str, Any] | None = None,
    lineage_path_edge_ids: Iterable[str] | None = None,
    analyzer: Mapping[str, Any] | None = None,
    policy_ref: str | None = None,
    supersedes_assessment_id: str | None = None,
) -> dict[str, Any]:
    """Record an explicit applicability assessment for one advisory candidate.

    The caller supplies evidence and basis, never a verdict. Applicability remains
    engine-resolved; identity overlap, fetching, change, or model exposure cannot
    silently become verification, reliance, or affectedness.
    """
    path = Path(database_path)
    advisories = TestamurAdvisoryStore(path)
    event_revision = advisories.get_revision(str(event_revision_id).strip())
    if event_revision is None:
        raise KeyError(str(event_revision_id).strip())
    engine = TestamurAffectednessEngine(
        lineage=TestamurLineageStore(path),
        assessments=TestamurAffectednessStore(path),
    )
    return assess_advisory_revision(
        engine,
        event_revision,
        subject_revision=subject_revision,
        evidence=evidence,
        basis=basis,
        scope=scope,
        lineage_path_edge_ids=lineage_path_edge_ids,
        analyzer=analyzer,
        policy_ref=policy_ref,
        supersedes_assessment_id=supersedes_assessment_id,
    )
