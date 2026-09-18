from __future__ import annotations

from typing import Any, Mapping, Protocol

RELIANCE_NEIGHBORHOOD_SCHEMA = "testamur.reliance-neighborhood.v1"


class RelianceReceiptReader(Protocol):
    def list(
        self,
        *,
        scope_ref: str,
        reliant_ref: str | None = None,
        object_ref: str | None = None,
    ) -> list[dict[str, Any]]: ...


def _required(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    return text


def _node_key(kind: str, ref: str) -> tuple[str, str]:
    return str(kind), str(ref)


def export_reliance_neighborhood(
    store: RelianceReceiptReader,
    *,
    scope_ref: str,
    reliant_ref: str | None = None,
    object_ref: str | None = None,
) -> dict[str, Any]:
    """Export the exact mixed neighborhood of durable reliance receipts.

    This is a product/read projection, not a new truth model. It combines the
    receipt, downstream reliant object, pinned upstream objects/revisions,
    selected policies, assurances and topology relations into one deterministic
    graph. Generic citations/dependencies that were never promoted to reliance
    are deliberately absent. Current assessment unavailability is projected
    explicitly and never erases the historical receipt or coerces UNKNOWN to a
    negative policy verdict.
    """

    scope = _required(scope_ref, "scope_ref")
    receipts = store.list(
        scope_ref=scope,
        reliant_ref=(
            None if reliant_ref is None else _required(reliant_ref, "reliant_ref")
        ),
        object_ref=(
            None if object_ref is None else _required(object_ref, "object_ref")
        ),
    )

    nodes: dict[tuple[str, str], dict[str, Any]] = {}
    edges: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}

    def add_node(kind: str, ref: Any, **attrs: Any) -> None:
        text = str(ref or "").strip()
        if not text:
            return
        key = _node_key(kind, text)
        current = nodes.setdefault(key, {"kind": kind, "ref": text})
        for name, value in attrs.items():
            if value is not None:
                current[name] = value

    def add_edge(
        kind: str,
        source_kind: str,
        source_ref: Any,
        target_kind: str,
        target_ref: Any,
        **attrs: Any,
    ) -> None:
        source = str(source_ref or "").strip()
        target = str(target_ref or "").strip()
        if not source or not target:
            return
        key = (kind, source_kind, source, target_kind, target)
        edge = edges.setdefault(
            key,
            {
                "kind": kind,
                "from": {"kind": source_kind, "ref": source},
                "to": {"kind": target_kind, "ref": target},
            },
        )
        for name, value in attrs.items():
            if value is not None:
                edge[name] = value

    for receipt in sorted(
        receipts,
        key=lambda item: (
            str(item.get("reliant_ref") or ""),
            str(item.get("purpose") or ""),
            str(item.get("receipt_id") or ""),
        ),
    ):
        if not isinstance(receipt, Mapping):
            raise ValueError("reliance store returned a non-mapping receipt")
        receipt_id = _required(receipt.get("receipt_id"), "receipt_id")
        reliant = _required(receipt.get("reliant_ref"), "receipt.reliant_ref")
        relied = _required(receipt.get("object_ref"), "receipt.object_ref")
        purpose = _required(receipt.get("purpose"), "receipt.purpose")
        stale = bool(receipt.get("stale", False))
        assessment_available = bool(receipt.get("assessment_available", True))
        current_admissible = receipt.get("current_admissible")
        current_state = receipt.get("current_assessment_state")
        reconsideration_required = bool(
            receipt.get("reconsideration_required", stale)
        )
        staleness_reason_types = sorted(
            {
                str(item.get("type"))
                for item in receipt.get("staleness_reasons", [])
                if isinstance(item, Mapping) and item.get("type")
            }
        )

        add_node(
            "reliance",
            receipt_id,
            purpose=purpose,
            stale=stale,
            created_at=receipt.get("created_at"),
        )
        receipt_node = nodes[_node_key("reliance", receipt_id)]
        # Preserve explicit null: UNKNOWN/no-current-assessment is distinct from
        # an evaluated ``False`` admission decision.
        receipt_node["assessment_available"] = assessment_available
        receipt_node["current_admissible"] = current_admissible
        receipt_node["current_assessment_state"] = (
            None if current_state is None else str(current_state)
        )
        receipt_node["reconsideration_required"] = reconsideration_required
        receipt_node["staleness_reason_types"] = staleness_reason_types

        add_node(
            "object",
            reliant,
            role="reliant",
            revision_ref=receipt.get("reliant_revision_ref"),
        )
        add_node("object", relied, role="relied_root")
        add_edge("declares-reliance", "object", reliant, "reliance", receipt_id)
        add_edge(
            "relies-on",
            "reliance",
            receipt_id,
            "object",
            relied,
            purpose=purpose,
        )

        pinned = receipt.get("pinned_revisions", {})
        if not isinstance(pinned, Mapping):
            raise ValueError("receipt.pinned_revisions must be a mapping")
        for basis_ref, revision_ref in sorted(
            pinned.items(), key=lambda item: str(item[0])
        ):
            basis = str(basis_ref)
            revision = None if revision_ref is None else str(revision_ref)
            add_node("object", basis, revision_ref=revision)
            add_edge(
                "pins-revision",
                "reliance",
                receipt_id,
                "object",
                basis,
                revision_ref=revision,
            )

        for policy_id in sorted(map(str, receipt.get("policy_ids") or [])):
            add_node("policy", policy_id)
            add_edge(
                "evaluated-under",
                "reliance",
                receipt_id,
                "policy",
                policy_id,
            )

        for assurance_id in sorted(map(str, receipt.get("assurance_ids") or [])):
            add_node("assurance", assurance_id)
            add_edge(
                "assurance-basis",
                "reliance",
                receipt_id,
                "assurance",
                assurance_id,
            )

        for relation_id in sorted(map(str, receipt.get("relation_ids") or [])):
            add_node("relation", relation_id)
            add_edge(
                "topology-basis",
                "reliance",
                receipt_id,
                "relation",
                relation_id,
            )

    node_list = [nodes[key] for key in sorted(nodes)]
    edge_list = [edges[key] for key in sorted(edges)]
    return {
        "schema_version": RELIANCE_NEIGHBORHOOD_SCHEMA,
        "scope_ref": scope,
        "filters": {
            "reliant_ref": reliant_ref,
            "object_ref": object_ref,
        },
        "receipt_count": len(receipts),
        "nodes": node_list,
        "edges": edge_list,
        "semantics": {
            "actual_durable_reliance_only": True,
            "graph_implies_truth": False,
            "stale_implies_false": False,
            "unknown_implies_inadmissible": False,
            "unavailable_current_evidence_erases_reliance": False,
            "widespread_reliance_implies_correctness": False,
        },
    }


__all__ = [
    "RELIANCE_NEIGHBORHOOD_SCHEMA",
    "RelianceReceiptReader",
    "export_reliance_neighborhood",
]
