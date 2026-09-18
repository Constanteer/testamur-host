from __future__ import annotations

from collections import deque
from typing import Any, Mapping, Protocol

from .affectedness_integrity import audit_affectedness_basis
from .component_identity import canonical_json


class AffectednessAssessmentView(Protocol):
    def get_assessment(self, assessment_id: str) -> dict[str, Any] | None: ...


class LineageEdgeView(Protocol):
    def get_lineage(self, edge_id: str) -> dict[str, Any] | None: ...


class AdvisoryRevisionView(Protocol):
    def get_revision(self, event_revision_id: str) -> dict[str, Any] | None: ...


def _step(edge: Mapping[str, Any], *, traversal_from_ref: str, traversal_to_ref: str, traversal_reversed: bool) -> dict[str, Any]:
    return {"edge_id": edge.get("edge_id"), "upstream_ref": edge.get("upstream_ref"), "relation_type": edge.get("relation_type"), "downstream_ref": edge.get("downstream_ref"), "scope": edge.get("scope"), "component_mapping": edge.get("component_mapping"), "traversal_from_ref": traversal_from_ref, "traversal_to_ref": traversal_to_ref, "traversal_reversed": bool(traversal_reversed)}


def _traversal_adjacency(edges: list[dict[str, Any]]) -> dict[str, list[tuple[dict[str, Any], str, bool]]]:
    adjacency: dict[str, list[tuple[dict[str, Any], str, bool]]] = {}
    for edge in edges:
        upstream = str(edge.get("upstream_ref") or "")
        downstream = str(edge.get("downstream_ref") or "")
        if not upstream or not downstream:
            continue
        adjacency.setdefault(upstream, []).append((edge, downstream, False))
        if str(edge.get("relation_type") or "") == "EQUIVALENT_TO":
            scope = edge.get("scope")
            if isinstance(scope, Mapping) and scope:
                adjacency.setdefault(downstream, []).append((edge, upstream, True))
    for outgoing in adjacency.values():
        outgoing.sort(key=lambda item: (item[1], str(item[0].get("relation_type") or ""), str(item[0].get("edge_id") or ""), item[2]))
    return adjacency


def _ordered_paths(edges: list[dict[str, Any]], *, subject_revision: str, preferred_roots: list[str], max_paths: int) -> tuple[list[list[dict[str, Any]]], dict[str, Any]]:
    adjacency = _traversal_adjacency(edges)
    incoming_nodes: set[str] = set()
    for outgoing in adjacency.values():
        for _, next_ref, _ in outgoing:
            incoming_nodes.add(next_ref)
    roots = [root for root in preferred_roots if root in adjacency]
    if not roots:
        roots = sorted(set(adjacency) - incoming_nodes)
    if not roots:
        roots = sorted(adjacency)
    edge_budget = max(1, len(edges))
    path_limit = max(1, min(int(max_paths), 10000))
    queue: deque[tuple[str, tuple[tuple[dict[str, Any], str, str, bool], ...], frozenset[str], str | None]] = deque((root, (), frozenset({root}), None) for root in sorted(set(roots)))
    paths: list[list[dict[str, Any]]] = []
    expansion_limit = max(32, min(100000, edge_budget * edge_budget * 8))
    expansions = 0
    truncation_reasons: set[str] = set()
    while queue:
        if "max_paths" in truncation_reasons:
            break
        if expansions >= expansion_limit:
            truncation_reasons.add("expansion_limit")
            break
        node, path, seen, equivalence_scope_key = queue.popleft()
        for edge, next_ref, reversed_equivalence in adjacency.get(node, []):
            if expansions >= expansion_limit:
                truncation_reasons.add("expansion_limit")
                break
            expansions += 1
            if not next_ref or next_ref in seen:
                continue
            next_scope_key = equivalence_scope_key
            if str(edge.get("relation_type") or "") == "EQUIVALENT_TO":
                scope = edge.get("scope")
                if not isinstance(scope, Mapping) or not scope:
                    continue
                edge_scope_key = canonical_json(dict(scope))
                if equivalence_scope_key is not None and edge_scope_key != equivalence_scope_key:
                    continue
                next_scope_key = edge_scope_key
            next_path = (*path, (edge, node, next_ref, reversed_equivalence))
            if next_ref == subject_revision:
                paths.append([_step(item, traversal_from_ref=from_ref, traversal_to_ref=to_ref, traversal_reversed=reversed_step) for item, from_ref, to_ref, reversed_step in next_path])
                if len(paths) > path_limit:
                    truncation_reasons.add("max_paths")
                    break
                continue
            if len(next_path) < edge_budget:
                queue.append((next_ref, next_path, seen | {next_ref}, next_scope_key))
    returned_paths = paths[:path_limit]
    returned_paths.sort(key=lambda path: (len(path), tuple(str(step.get("edge_id") or "") for step in path), tuple(bool(step.get("traversal_reversed")) for step in path)))
    reasons = sorted(truncation_reasons)
    return returned_paths, {"complete": not reasons, "truncated": bool(reasons), "truncation_reasons": reasons, "max_paths": path_limit, "expansion_limit": expansion_limit, "expansions": expansions, "returned_paths": len(returned_paths), "pinned_edge_count": len(edges), "resource_bound_implies_no_alternate_path": False}


def explain_affectedness_path(assessments: AffectednessAssessmentView, lineage: LineageEdgeView, assessment_id: str, *, advisories: AdvisoryRevisionView | None = None, max_paths: int = 1000) -> dict[str, Any]:
    assessment = assessments.get_assessment(str(assessment_id))
    if assessment is None:
        raise KeyError(assessment_id)
    edge_ids = [str(item) for item in assessment.get("lineage_path_edge_ids") or []]
    edges: list[dict[str, Any]] = []
    missing_edge_ids: list[str] = []
    for edge_id in edge_ids:
        edge = lineage.get_lineage(edge_id)
        if edge is None:
            missing_edge_ids.append(edge_id)
            continue
        edges.append(edge)
    edges.sort(key=lambda edge: str(edge.get("edge_id") or ""))
    advisory_revision = None
    missing_advisory_revision = False
    event_revision_id = str(assessment.get("event_revision_id") or "")
    if advisories is not None and event_revision_id:
        advisory_revision = advisories.get_revision(event_revision_id)
        missing_advisory_revision = advisory_revision is None
    preferred_roots: list[str] = []
    if advisory_revision is not None:
        preferred_roots = sorted({str(item) for item in advisory_revision.get("upstream_refs") or [] if item})
    subject_revision = str(assessment.get("subject_revision") or "")
    paths, reconstruction = _ordered_paths(edges, subject_revision=subject_revision, preferred_roots=preferred_roots, max_paths=max_paths)
    integrity = audit_affectedness_basis(
        assessment,
        lineage=lineage,
        advisories=advisories,
    )
    missing_reference_reasons: set[str] = set()
    if missing_edge_ids:
        missing_reference_reasons.add("missing_lineage_edges")
    if missing_advisory_revision:
        missing_reference_reasons.add("missing_advisory_revision")
    if not integrity["valid"]:
        missing_reference_reasons.add("assessment_basis_inconsistent")
    if missing_reference_reasons:
        reasons = sorted({*reconstruction["truncation_reasons"], *missing_reference_reasons})
        reconstruction = {**reconstruction, "complete": False, "truncated": True, "truncation_reasons": reasons, "missing_pinned_edge_count": len(missing_edge_ids), "missing_advisory_revision": missing_advisory_revision, "basis_integrity_valid": bool(integrity["valid"]), "basis_integrity_reasons": list(integrity["reasons"])}
    else:
        reconstruction = {**reconstruction, "missing_pinned_edge_count": 0, "missing_advisory_revision": False, "basis_integrity_valid": True, "basis_integrity_reasons": []}
    return {"assessment": assessment, "advisory_revision": advisory_revision, "lineage_edges": edges, "path": paths[0] if paths else [], "paths": paths, "path_reconstruction": reconstruction, "basis_integrity": integrity, "missing_lineage_edge_ids": missing_edge_ids, "missing_advisory_revision": missing_advisory_revision, "basis": assessment.get("basis") or [], "applicability_evidence": assessment.get("evidence") or [], "explanation": assessment.get("explanation") or {}, "semantics": {"paths_use_recorded_edge_ids_only": True, "path_order_reconstructed_from_recorded_endpoints": True, "alternate_paths_preserved_within_bounds": True, "path_reconstruction_complete": reconstruction["complete"], "scoped_equivalence_symmetric": True, "equivalence_scope_chaining_requires_exact_match": True, "reverse_equivalence_traversal_is_explicit": True, "missing_references_are_explicit": True, "missing_references_are_reconstructed": False, "missing_advisory_revision_invalidates_complete_reconstruction": True, "basis_inconsistency_invalidates_complete_reconstruction": True, "lineage_path_is_affectedness_verdict": False}}
