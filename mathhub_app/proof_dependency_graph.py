from __future__ import annotations

from collections import deque
from typing import Any, Callable

from semantic_cone import ConstantInspection


# ConstantInfo kinds whose stored value/body participates in a proof-expression
# dependency route. `inductive` exposes constructor names through the shared Lean
# probe and therefore remains traversable as formal substrate.
PROOF_BODY_KINDS = frozenset({
    "definition",
    "theorem",
    "opaque",
    "inductive",
})


def _inspection(raw: ConstantInspection | dict[str, Any], name: str) -> ConstantInspection:
    return raw if isinstance(raw, ConstantInspection) else ConstantInspection.from_mapping(name, raw)


def build_proof_dependency_graph(
    root_name: str,
    proof_refs: list[str] | tuple[str, ...],
    inspect_many: Callable[[list[str]], dict[str, ConstantInspection | dict[str, Any]]],
    *,
    max_depth: int = 64,
    max_nodes: int = 5000,
    batch_size: int = 64,
    expand_name: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Traverse constants reachable from a theorem's proof/value expression.

    This graph is intentionally different from the semantic definition cone:

    * traversal is seeded by the root theorem's **value/proof refs**;
    * statement/type refs are retained on inspected nodes as observations but do
      not drive the proof route;
    * theorem bodies *are* traversed;
    * an optional scope predicate leaves rejected constants as explicit boundary
      nodes instead of silently dropping them or exploding into an external library.

    The graph is evidence about the stored Lean proof-expression route. It is not a
    kernel-verification result and it is not an axiom closure; those are separate
    artifacts.
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

    seeds = list(dict.fromkeys(
        str(name).strip() for name in proof_refs
        if str(name).strip() and str(name).strip() != root
    ))
    queue: deque[tuple[str, str, int, str]] = deque(
        (root, name, 1, "direct-proof-ref") for name in seeds
    )

    visited: set[str] = set()
    scheduled: set[str] = set()
    nodes: dict[str, dict[str, Any]] = {}
    boundaries: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    truncated = False
    reasons: list[str] = []
    batches = 0

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

        batches += 1
        names = [name for name, _ in batch]
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
            follows_body = kind in PROOF_BODY_KINDS
            nodes[name] = {
                "name": name,
                "kind": kind,
                "depth": depth,
                "inspection_ok": True,
                "type_refs": list(item.type_refs),
                "value_refs": list(item.value_refs),
                "proof_body_expanded": follows_body,
            }

            if not follows_body:
                continue
            seen: set[str] = set()
            for child in item.value_refs:
                child = str(child).strip()
                if not child or child == name or child == root or child in seen:
                    continue
                seen.add(child)
                queue.append((name, child, depth + 1, "proof-body-ref"))

    boundary_items = sorted(boundaries.values(), key=lambda item: (item["depth"], item["name"]))
    return {
        "format": "mathhub-proof-dependency-graph-v1",
        "root_name": root,
        "seed_policy": "root ConstantInfo.value refs only",
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
        "inspection_batches": batches,
        "nodes": sorted(nodes.values(), key=lambda item: (item["depth"], item["name"])),
        "boundaries": boundary_items,
        "edges": edges,
    }
