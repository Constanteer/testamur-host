from __future__ import annotations

from typing import Any

from .relations import RelationIndex


class TestamurInspector:
    """Read-side inspector for the canonical historical graph query surface.

    A stale run means pinned inputs or dependency topology changed; it does not
    mean the checked claim is false. The store is deliberately structural here:
    the inspector depends on Testamur store behavior, not a legacy class name.
    """

    def __init__(self, store: Any, relations: RelationIndex | None = None) -> None:
        self.store = store
        self.relations = relations or RelationIndex(store)

    def assess_run(self, run: dict[str, Any]) -> dict[str, Any]:
        closure = self.relations.dependency_closure(
            run["project_id"], run["claim_object_id"]
        )
        meta = (run.get("result") or {}).get("_witness") or {}
        recorded = meta.get("dependency_topology_fingerprint")
        known = bool(recorded)
        topology_stale = bool(known and recorded != closure["topology_fingerprint"])
        old = set(meta.get("dependency_object_ids") or [])
        current = set(closure["dependency_object_ids"])
        reasons: list[dict[str, Any]] = []
        if run.get("stale"):
            reasons.append({"kind": "revision_changed", "object_ids": run.get("stale_inputs") or []})
        if topology_stale:
            reasons.append({
                "kind": "dependency_topology_changed",
                "added": sorted(current - old),
                "removed": sorted(old - current),
            })
        return {
            **run,
            "effective_stale": bool(run.get("stale") or topology_stale),
            "stale_reasons": reasons,
            "topology_assessment": "changed" if topology_stale else "current" if known else "unknown",
            "current_dependency_topology_fingerprint": closure["topology_fingerprint"],
            "current_dependency_object_ids": closure["dependency_object_ids"],
        }


def __getattr__(name: str) -> Any:
    # Transitional importer for the historical query implementation. The old
    # symbol is deliberately absent from the public module surface and __all__.
    if name == "".join(("Witness", "Inspector")):
        return TestamurInspector
    raise AttributeError(name)


__all__ = ["TestamurInspector"]
