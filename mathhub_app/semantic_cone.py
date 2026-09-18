from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Callable


# A semantic cone explains the meaning of a statement. It is deliberately not a
# proof dependency graph. In particular, theorem proof bodies are never traversed.
DEFINITIONAL_KINDS = frozenset({
    "definition",
    "abbrev",
    "opaque",
    "instance",
    "structure",
    "constructor",
    "recursor",
    "inductive",
    "class",
})

THEOREM_KINDS = frozenset({"theorem", "lemma"})


@dataclass(frozen=True)
class ConstantInspection:
    """Deterministic structural facts for one Lean constant.

    ``type_refs`` are constants used by the declaration's type.
    ``value_refs`` are constants used by its value/body when a body exists.

    A Lean adapter can construct this from ``ConstantInfo``. Tests can provide a
    pure in-memory inspector, keeping graph semantics independent of Lean process
    management.
    """

    name: str
    kind: str
    type_refs: tuple[str, ...] = ()
    value_refs: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, name: str, payload: dict[str, Any]) -> "ConstantInspection":
        def uniq(values: Any) -> tuple[str, ...]:
            if not isinstance(values, (list, tuple)):
                return ()
            return tuple(dict.fromkeys(str(v) for v in values if str(v)))

        return cls(
            name=name,
            kind=str(payload.get("kind") or "unknown").strip().lower(),
            type_refs=uniq(payload.get("type_refs") or payload.get("statement_refs")),
            value_refs=uniq(payload.get("value_refs") or payload.get("body_refs")),
        )


def build_semantic_cone(
    root_name: str,
    statement_refs: list[str] | tuple[str, ...],
    inspect_constant: Callable[[str], ConstantInspection | dict[str, Any]],
    *,
    max_depth: int = 8,
    max_nodes: int = 500,
) -> dict[str, Any]:
    """Expand a Claim statement's semantic dependency cone.

    The root declaration's *statement refs* seed the traversal. For every
    reachable constant, type references are followed because they participate in
    the constant's meaning. Value/body references are followed only for
    definitional/formal-substrate constants. Theorem/lemma proof bodies are never
    traversed; doing so would silently turn this into a proof dependency graph.

    Budgets are hard semantic boundaries. If either is reached, ``complete`` is
    false and ``truncated`` is true. Callers must not present a bounded cone as a
    complete semantic expansion.
    """
    root = str(root_name or "").strip()
    if not root:
        raise ValueError("root_name is required")
    if max_depth < 1:
        raise ValueError("max_depth must be >= 1")
    if max_nodes < 1:
        raise ValueError("max_nodes must be >= 1")

    seeds = list(dict.fromkeys(str(x) for x in statement_refs if str(x) and str(x) != root))
    queue: deque[tuple[str, str, int, str]] = deque(
        (root, name, 1, "statement-ref") for name in seeds
    )
    visited: set[str] = set()
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    truncated = False
    truncation_reasons: list[str] = []

    while queue:
        parent, name, depth, relation = queue.popleft()
        if depth > max_depth:
            truncated = True
            if "max-depth" not in truncation_reasons:
                truncation_reasons.append("max-depth")
            continue

        # Preserve every observed edge, even when a node was inspected earlier.
        edges.append({
            "from": parent,
            "to": name,
            "depth": depth,
            "relation": relation,
        })

        if name in visited:
            continue
        if len(visited) >= max_nodes:
            truncated = True
            if "max-nodes" not in truncation_reasons:
                truncation_reasons.append("max-nodes")
            continue

        visited.add(name)
        try:
            raw = inspect_constant(name)
            inspection = raw if isinstance(raw, ConstantInspection) else ConstantInspection.from_mapping(name, raw)
        except Exception as exc:  # inspection failure is evidence, not silent omission
            nodes[name] = {
                "name": name,
                "kind": "unknown",
                "depth": depth,
                "inspection_ok": False,
            }
            errors.append({"constant": name, "error": str(exc)})
            continue

        kind = inspection.kind or "unknown"
        nodes[name] = {
            "name": name,
            "kind": kind,
            "depth": depth,
            "inspection_ok": True,
            "type_refs": list(inspection.type_refs),
            "value_refs": list(inspection.value_refs),
            "value_expanded": kind in DEFINITIONAL_KINDS,
        }

        # Type references are semantic for every constant.
        children: list[tuple[str, str]] = [
            (child, "type-ref")
            for child in inspection.type_refs
            if child and child != name and child != root
        ]

        # Definition bodies participate in meaning. Proof bodies do not.
        if kind in DEFINITIONAL_KINDS:
            children.extend(
                (child, "definition-body-ref")
                for child in inspection.value_refs
                if child and child != name and child != root
            )

        seen_children: set[tuple[str, str]] = set()
        for child, child_relation in children:
            key = (child, child_relation)
            if key in seen_children:
                continue
            seen_children.add(key)
            queue.append((name, child, depth + 1, child_relation))

    return {
        "format": "mathhub-semantic-cone-v1",
        "root_name": root,
        "complete": not truncated and not errors,
        "truncated": truncated,
        "truncation_reasons": truncation_reasons,
        "inspection_errors": errors,
        "budgets": {"max_depth": max_depth, "max_nodes": max_nodes},
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": sorted(nodes.values(), key=lambda item: (item["depth"], item["name"])),
        "edges": edges,
    }
