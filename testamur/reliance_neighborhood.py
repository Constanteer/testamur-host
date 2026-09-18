from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from typing import Any


NEIGHBORHOOD_SCHEMA_VERSION = "testamur.reliance-neighborhood.v1"


def _required(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    return text


def _relation_id(edge: Mapping[str, Any]) -> str:
    return str(edge.get("relation_id") or edge.get("id") or "")


def _relation_type(edge: Mapping[str, Any]) -> str:
    return str(edge.get("relation_type") or edge.get("kind") or edge.get("type") or "")


def _relation_source(edge: Mapping[str, Any]) -> str:
    return str(
        edge.get("from_ref")
        or edge.get("source_ref")
        or edge.get("source_object_id")
        or edge.get("source")
        or ""
    )


def _relation_target(edge: Mapping[str, Any]) -> str:
    return str(
        edge.get("to_ref")
        or edge.get("target_ref")
        or edge.get("target_object_id")
        or edge.get("target")
        or ""
    )


def _current_node(evidence: Any, object_ref: str) -> dict[str, Any]:
    """Read current node state without erasing historical reliance on read gaps."""
    state_available = True
    try:
        state = dict(evidence.object_state(object_ref))
    except (KeyError, ValueError):
        state = {}
        state_available = False

    try:
        revision_ref = evidence.revision_ref(object_ref)
    except (KeyError, ValueError):
        revision_ref = None
    revision_available = bool(revision_ref)

    node = {
        "object_ref": object_ref,
        "revision_ref": revision_ref,
        "kind": str(
            state.get("kind")
            or state.get("record_kind")
            or state.get("object_kind")
            or "unknown"
        ),
    }
    if not revision_available:
        node["current_revision_available"] = False
    if not state_available or not revision_available:
        node["current_evidence_available"] = False
        node["assessment_state"] = "UNKNOWN"
    return node


def _current_relations(evidence: Any, object_ref: str) -> list[dict[str, Any]]:
    try:
        outgoing = [dict(edge) for edge in evidence.outgoing_relations(object_ref)]
    except (KeyError, ValueError):
        outgoing = []
    try:
        incoming = [dict(edge) for edge in evidence.incoming_relations(object_ref)]
    except (KeyError, ValueError):
        incoming = []
    return [*outgoing, *incoming]


def export_mixed_reliance_neighborhood(
    *,
    scope_ref: str,
    seed_object_refs: Iterable[str],
    evidence: Any,
    reliance_store: Any,
    max_depth: int = 2,
    max_nodes: int = 256,
) -> dict[str, Any]:
    """Export a bounded deterministic evidence + actual-reliance neighborhood.

    Evidence relations describe graph context; immutable reliance receipts describe
    intentional reliance. They remain distinct edge families in the projection so
    citation/dependency/exposure can never be mistaken for reliance. Missing current
    evidence degrades node health to UNKNOWN but cannot erase a durable receipt.
    """
    scope = _required(scope_ref, "scope_ref")
    if max_depth < 0 or max_nodes < 1:
        raise ValueError("max_depth must be non-negative and max_nodes must be positive")
    seeds = sorted({_required(ref, "seed_object_ref") for ref in seed_object_refs})
    queue: deque[tuple[str, int]] = deque((ref, 0) for ref in seeds)
    queued = set(seeds)
    visited: set[str] = set()
    nodes: dict[str, dict[str, Any]] = {}
    evidence_edges: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    reliance_edges: dict[str, dict[str, Any]] = {}
    truncated = False

    while queue:
        object_ref, depth = queue.popleft()
        if object_ref in visited:
            continue
        if len(visited) >= max_nodes:
            truncated = True
            break
        visited.add(object_ref)
        nodes[object_ref] = _current_node(evidence, object_ref)
        if depth >= max_depth:
            continue

        relations = _current_relations(evidence, object_ref)
        for edge in sorted(
            relations,
            key=lambda item: (
                _relation_type(item),
                _relation_id(item),
                _relation_source(item),
                _relation_target(item),
            ),
        ):
            source = _relation_source(edge)
            target = _relation_target(edge)
            if not source or not target:
                continue
            key = (_relation_id(edge), _relation_type(edge), source, target)
            evidence_edges[key] = {
                "edge_family": "evidence_relation",
                "relation_id": key[0],
                "relation_type": key[1],
                "from_ref": source,
                "to_ref": target,
            }
            neighbor = target if source == object_ref else source
            if neighbor not in visited and neighbor not in queued:
                queue.append((neighbor, depth + 1))
                queued.add(neighbor)

        receipts = [
            *reliance_store.list(scope_ref=scope, reliant_ref=object_ref),
            *reliance_store.list(scope_ref=scope, object_ref=object_ref),
        ]
        for receipt in sorted(
            receipts,
            key=lambda item: str(item.get("receipt_id") or ""),
        ):
            receipt_id = _required(receipt.get("receipt_id"), "receipt_id")
            reliant = _required(receipt.get("reliant_ref"), "reliant_ref")
            relied = _required(receipt.get("object_ref"), "object_ref")
            edge = {
                "edge_family": "reliance",
                "receipt_id": receipt_id,
                "from_ref": reliant,
                "to_ref": relied,
                "purpose": str(receipt.get("purpose") or ""),
                "stale": bool(receipt.get("stale")),
                "pinned_revisions": dict(receipt.get("pinned_revisions") or {}),
            }
            if "assessment_available" in receipt:
                edge["assessment_available"] = bool(receipt.get("assessment_available"))
            if "current_admissible" in receipt:
                # Explicit null is meaningful: UNKNOWN is not evaluated False.
                edge["current_admissible"] = receipt.get("current_admissible")
            if "current_assessment_state" in receipt:
                current_state = receipt.get("current_assessment_state")
                edge["current_assessment_state"] = (
                    None if current_state is None else str(current_state)
                )
            if "reconsideration_required" in receipt:
                edge["reconsideration_required"] = bool(
                    receipt.get("reconsideration_required")
                )
            if "unavailable_basis_object_refs" in receipt:
                edge["unavailable_basis_object_refs"] = sorted(
                    map(str, receipt.get("unavailable_basis_object_refs") or [])
                )
            if "staleness_reasons" in receipt:
                edge["staleness_reason_types"] = sorted(
                    {
                        str(reason.get("type") or "unknown")
                        for reason in receipt.get("staleness_reasons", [])
                        if isinstance(reason, Mapping)
                    }
                )
            reliance_edges[receipt_id] = edge
            neighbor = relied if reliant == object_ref else reliant
            if neighbor not in visited and neighbor not in queued:
                queue.append((neighbor, depth + 1))
                queued.add(neighbor)

    return {
        "schema_version": NEIGHBORHOOD_SCHEMA_VERSION,
        "scope_ref": scope,
        "seed_object_refs": seeds,
        "max_depth": max_depth,
        "max_nodes": max_nodes,
        "truncated": truncated,
        "nodes": [nodes[key] for key in sorted(nodes)],
        "evidence_relations": [
            evidence_edges[key] for key in sorted(evidence_edges)
        ],
        "reliance_edges": [
            reliance_edges[key] for key in sorted(reliance_edges)
        ],
        "semantics": {
            "evidence_relation_implies_reliance": False,
            "reliance_implies_truth": False,
            "stale_implies_false": False,
            "unavailable_current_evidence_erases_reliance": False,
            "unknown_implies_inadmissible": False,
            "bounded": True,
            "deterministic": True,
        },
    }


__all__ = ["NEIGHBORHOOD_SCHEMA_VERSION", "export_mixed_reliance_neighborhood"]
