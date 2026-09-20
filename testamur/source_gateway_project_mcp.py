from __future__ import annotations

from typing import Any, Mapping

from . import source_gateway_mcp as _base
from .advisory_write_route import record_advisory_assessment


PROJECT_REVALIDATION_TOOL = {
    "name": "testamur.project_advisory_revalidation",
    "title": "Inspect Project advisory revalidation work",
    "description": (
        "Return the canonical advisory revalidation work projection for an existing Project. "
        "Dependency change is a review trigger, not an invalidity or affectedness verdict; "
        "recorded assessments are not generic verification."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"project_ref": {"type": "string"}},
        "required": ["project_ref"],
        "additionalProperties": False,
    },
}

ADVISORY_ASSESSMENT_TOOL = {
    "name": "testamur.record_advisory_assessment",
    "title": "Record advisory affectedness evidence",
    "description": (
        "Record evidence and provenance for one immutable advisory revision through the canonical "
        "affectedness engine. The caller cannot supply a verdict/state/trust score; recording evidence "
        "is not verification, reliance, or an affectedness verdict by itself."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "event_revision_id": {"type": "string"}, "subject_revision": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "object"}},
            "basis": {"type": "array", "items": {"type": "object"}}, "scope": {"type": "object"},
            "lineage_path_edge_ids": {"type": "array", "items": {"type": "string"}},
            "analyzer": {"type": "object"}, "policy_ref": {"type": "string"},
            "supersedes_assessment_id": {"type": "string"},
        },
        "required": ["event_revision_id", "subject_revision", "evidence", "basis"],
        "additionalProperties": False,
    },
}

PROJECT_ADVISORY_ASSESSMENT_TOOL = {
    "name": "testamur.record_project_advisory_assessment",
    "title": "Record evidence for a current Project advisory candidate",
    "description": (
        "Record evidence for an exact advisory candidate currently exposed by one Project's canonical "
        "revalidation projection. Project membership is checked before the canonical affectedness write. "
        "The caller supplies evidence, never a verdict/state/trust score; candidate membership is not "
        "affectedness, verification, or reliance."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "project_ref": {"type": "string"}, "event_revision_id": {"type": "string"},
            "subject_revision": {"type": "string"}, "evidence": {"type": "array", "items": {"type": "object"}},
            "basis": {"type": "array", "items": {"type": "object"}}, "scope": {"type": "object"},
            "lineage_path_edge_ids": {"type": "array", "items": {"type": "string"}},
            "analyzer": {"type": "object"}, "policy_ref": {"type": "string"},
            "supersedes_assessment_id": {"type": "string"},
        },
        "required": ["project_ref", "event_revision_id", "subject_revision", "evidence", "basis"],
        "additionalProperties": False,
    },
}


def _project_revalidation(arguments: Mapping[str, Any]) -> dict[str, Any]:
    service = _base.TestamurProductService.integrated(_base._db_path())
    detail = _base.project_with_advisory_reviews(service, _base._required(arguments, "project_ref"))
    if detail.get("ok") is not True: return detail
    supply = detail.get("supply_chain") if isinstance(detail.get("supply_chain"), Mapping) else {}
    projection = supply.get("advisory_revalidation") if isinstance(supply.get("advisory_revalidation"), Mapping) else {}
    return {"ok": True, "schema": "testamur.mcp.project-advisory-revalidation.v1", "project": detail["project"],
            "advisory_revalidation": dict(projection), "semantics": {"changed_is_not_invalid": True,
            "changed_is_not_affectedness_verdict": True, "stale_is_not_false": True,
            "candidate_overlap_is_not_affectedness_verdict": True,
            "recorded_assessment_is_not_generic_verification": True, "generic_trust_score_used": False}}


def _record_advisory_assessment(arguments: Mapping[str, Any]) -> dict[str, Any]:
    service = _base.TestamurProductService.integrated(_base._db_path())
    return {"ok": True, "schema": "testamur.mcp.advisory-assessment.v1",
            "assessment": record_advisory_assessment(service, arguments),
            "semantics": {"recorded_is_not_verified": True, "recorded_is_not_relied": True,
            "lineage_is_not_affectedness_verdict": True, "caller_supplies_verdict": False,
            "generic_trust_score_used": False}}


def _record_project_advisory_assessment(arguments: Mapping[str, Any]) -> dict[str, Any]:
    service = _base.TestamurProductService.integrated(_base._db_path())
    project_ref = _base._required(arguments, "project_ref")
    event_revision_id = _base._required(arguments, "event_revision_id")
    subject_revision = _base._required(arguments, "subject_revision")
    detail = _base.project_with_advisory_reviews(service, project_ref)
    if detail.get("ok") is not True: return detail
    supply = detail.get("supply_chain") if isinstance(detail.get("supply_chain"), Mapping) else {}
    projection = supply.get("advisory_revalidation") if isinstance(supply.get("advisory_revalidation"), Mapping) else {}
    reviews = projection.get("reviews") if isinstance(projection.get("reviews"), list) else []
    matched = any(isinstance(review, Mapping) and str(review.get("event_revision_id") or "") == event_revision_id and
                  any(isinstance(subject, Mapping) and str(subject.get("subject_revision") or "") == subject_revision
                      for subject in (review.get("subjects") if isinstance(review.get("subjects"), list) else []))
                  for review in reviews)
    if not matched:
        raise ValueError("event_revision_id/subject_revision is not a current exact advisory candidate for this project")
    write_payload = {key: value for key, value in arguments.items() if key != "project_ref"}
    return {"ok": True, "schema": "testamur.mcp.project-advisory-assessment.v1", "project": detail["project"],
            "assessment": record_advisory_assessment(service, write_payload),
            "semantics": {"candidate_membership_is_not_affectedness_verdict": True,
            "recorded_is_not_verified": True, "recorded_is_not_relied": True,
            "lineage_is_not_affectedness_verdict": True, "caller_supplies_verdict": False,
            "generic_trust_score_used": False}}


def _install() -> None:
    for tool in (PROJECT_REVALIDATION_TOOL, ADVISORY_ASSESSMENT_TOOL, PROJECT_ADVISORY_ASSESSMENT_TOOL):
        if not any(existing.get("name") == tool["name"] for existing in _base.TOOLS): _base.TOOLS.append(tool)
    base_call = _base._call_tool
    if getattr(base_call, "_testamur_project_extension", False): return
    def call_tool(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if name == PROJECT_REVALIDATION_TOOL["name"]: return _project_revalidation(arguments)
        if name == ADVISORY_ASSESSMENT_TOOL["name"]: return _record_advisory_assessment(arguments)
        if name == PROJECT_ADVISORY_ASSESSMENT_TOOL["name"]: return _record_project_advisory_assessment(arguments)
        return base_call(name, arguments)
    setattr(call_tool, "_testamur_project_extension", True)
    _base._call_tool = call_tool


def main() -> int:
    _install()
    return int(_base.main())


if __name__ == "__main__":
    raise SystemExit(main())
