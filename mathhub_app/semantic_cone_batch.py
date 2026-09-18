from __future__ import annotations

from collections import deque
from typing import Any, Callable

from semantic_cone import ConstantInspection, DEFINITIONAL_KINDS


def _inspection(raw: ConstantInspection | dict[str, Any], name: str) -> ConstantInspection:
    return raw if isinstance(raw, ConstantInspection) else ConstantInspection.from_mapping(name, raw)


def build_semantic_cone_batched(
    root_name: str,
    statement_refs: list[str] | tuple[str, ...],
    inspect_many: Callable[[list[str]], dict[str, ConstantInspection | dict[str, Any]]],
    *,
    max_depth: int = 8,
    max_nodes: int = 500,
    batch_size: int = 32,
    expand_name: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Build a batched semantic dependency cone.

    The root theorem's *type* references seed traversal. Type references are always
    followed for declarations inside the configured semantic scope; definition
    bodies are followed only for definitional/formal-substrate constants. Theorem
    proof bodies are never traversed.

    ``expand_name`` is an optional scope predicate. A rejected constant remains in
    the output as an explicit boundary edge/node, but is not inspected recursively.
    This is important for external audits: project definitions can be expanded while
    Mathlib primitives such as ``ContDiffOn`` or ``MemLp`` remain named semantic
    boundary atoms rather than consuming the node budget with library internals.

    ``complete`` means traversal completed within the configured scope and budgets.
    It does *not* mean the cone is closed-world when boundary nodes exist, and it
    never means equivalence to an external mathematical statement has been proved.
    """
    root = str(root_name or "").strip()
    if not root:
        raise ValueError("root_name is required")
    if max_depth < 1:
        raise ValueError("max_depth must be >= 1")
    if max_nodes < 1:
        raise ValueError("max_nodes must be >= 1")
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")

    seeds = list(dict.fromkeys(str(x) for x in statement_refs if str(x) and str(x) != root))
    queue: deque[tuple[str, str, int, str]] = deque(
        (root, name, 1, "statement-ref") for name in seeds
    )
    visited: set[str] = set()
    scheduled: set[str] = set()
    nodes: dict[str, dict[str, Any]] = {}
    boundaries: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    truncated = False
    reasons: list[str] = []
    lean_batches = 0

    def truncate(reason: str) -> None:
        nonlocal truncated
        truncated = True
        if reason not in reasons:
            reasons.append(reason)

    def record_boundary(parent: str, name: str, depth: int, relation: str) -> None:
        item = boundaries.setdefault(name, {
            "name": name,
            "depth": depth,
            "incoming": [],
        })
        item["depth"] = min(int(item["depth"]), depth)
        incoming = {"from": parent, "relation": relation}
        if incoming not in item["incoming"]:
            item["incoming"].append(incoming)

    while queue:
        batch: list[tuple[str, int]] = []

        while queue and len(batch) < batch_size:
            parent, name, depth, relation = queue.popleft()
            if depth > max_depth:
                truncate("max-depth")
                continue

            edge: dict[str, Any] = {
                "from": parent,
                "to": name,
                "depth": depth,
                "relation": relation,
            }

            if expand_name is not None and not expand_name(name):
                edge["boundary"] = True
                edges.append(edge)
                record_boundary(parent, name, depth, relation)
                continue

            edges.append(edge)
            if name in visited or name in scheduled:
                continue
            if len(visited) + len(scheduled) >= max_nodes:
                truncate("max-nodes")
                continue

            scheduled.add(name)
            batch.append((name, depth))

        if not batch:
            continue

        names = [name for name, _ in batch]
        lean_batches += 1
        try:
            raw_batch = inspect_many(names)
        except Exception as exc:
            message = str(exc)
            for name, depth in batch:
                scheduled.discard(name)
                visited.add(name)
                nodes[name] = {
                    "name": name,
                    "kind": "unknown",
                    "depth": depth,
                    "inspection_ok": False,
                }
                errors.append({"constant": name, "error": message})
            continue

        for name, depth in batch:
            scheduled.discard(name)
            visited.add(name)
            raw = raw_batch.get(name)
            if raw is None:
                nodes[name] = {
                    "name": name,
                    "kind": "unknown",
                    "depth": depth,
                    "inspection_ok": False,
                }
                errors.append({"constant": name, "error": "batch inspector returned no result"})
                continue

            item = _inspection(raw, name)
            kind = item.kind or "unknown"
            nodes[name] = {
                "name": name,
                "kind": kind,
                "depth": depth,
                "inspection_ok": True,
                "type_refs": list(item.type_refs),
                "value_refs": list(item.value_refs),
                "value_expanded": kind in DEFINITIONAL_KINDS,
            }

            children: list[tuple[str, str]] = [
                (child, "type-ref")
                for child in item.type_refs
                if child and child != name and child != root
            ]
            if kind in DEFINITIONAL_KINDS:
                children.extend(
                    (child, "definition-body-ref")
                    for child in item.value_refs
                    if child and child != name and child != root
                )

            seen: set[tuple[str, str]] = set()
            for child, relation in children:
                key = (child, relation)
                if key in seen:
                    continue
                seen.add(key)
                queue.append((name, child, depth + 1, relation))

    boundary_items = sorted(boundaries.values(), key=lambda x: (x["depth"], x["name"]))
    return {
        "format": "mathhub-semantic-cone-v1",
        "execution": "batched",
        "root_name": root,
        "complete": not truncated and not errors,
        "closed_world": not boundary_items,
        "truncated": truncated,
        "truncation_reasons": reasons,
        "inspection_errors": errors,
        "scope": {
            "mode": "all-constants" if expand_name is None else "predicate-boundary",
            "boundary_count": len(boundary_items),
        },
        "budgets": {
            "max_depth": max_depth,
            "max_nodes": max_nodes,
            "batch_size": batch_size,
        },
        "node_count": len(nodes),
        "boundary_count": len(boundary_items),
        "edge_count": len(edges),
        "inspection_batches": lean_batches,
        "nodes": sorted(nodes.values(), key=lambda x: (x["depth"], x["name"])),
        "boundaries": boundary_items,
        "edges": edges,
    }
