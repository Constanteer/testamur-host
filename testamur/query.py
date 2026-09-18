from __future__ import annotations

from collections import Counter, deque
from typing import Any, Iterable

from .inspector import TestamurInspector
from .model import EdgeKind, ObjectKind, canonical_hash, normalize_edge_kind
from .relations import RelationIndex
from .store import TestamurStore


class TestamurQuery:
    """Read-only graph/query primitives over domain-independent Testamur graph objects.

    Queries operate on the active graph by default. Passing ``at_revision`` binds
    the query to one immutable project-state revision reconstructed from CAS, so
    later object revisions, relation changes, or verification runs cannot alter
    the answer.
    """

    DEFAULT_PROVENANCE_OUTGOING_KINDS = (EdgeKind.DERIVED_FROM.value,)
    DEFAULT_PROVENANCE_INCOMING_KINDS = (
        EdgeKind.SUPPORTS.value,
        EdgeKind.VERIFIES.value,
    )
    CHALLENGE_KINDS = (
        EdgeKind.CONTRADICTS.value,
        EdgeKind.INVALIDATES.value,
    )

    def __init__(
        self,
        store: TestamurStore,
        relations: RelationIndex | None = None,
        inspector: TestamurInspector | None = None,
    ) -> None:
        self.store = store
        self.relations = relations or RelationIndex(store)
        self.inspector = inspector or TestamurInspector(store, self.relations)

    @staticmethod
    def _normalize_kinds(kinds: Iterable[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(normalize_edge_kind(kind) for kind in kinds))

    def _project_object(self, project: str, object_id: str) -> tuple[str, dict[str, Any]]:
        project_id = self.store._project_id(project)
        obj = self.store.get_object(object_id)
        if obj["project_id"] != project_id:
            raise ValueError("object is not part of this project")
        return project_id, obj

    def _historical_context(self, project: str, at_revision: str) -> dict[str, Any]:
        if not hasattr(self.store, "reconstruct_revision"):
            raise ValueError("this Testamur store does not support historical reconstruction")
        project_id = self.store._project_id(project)
        state = self.store.reconstruct_revision(at_revision)
        state_project = state.get("project") or {}
        if str(state_project.get("id") or "") != project_id:
            raise ValueError("state revision is not part of this project")
        objects = {str(item["id"]): item for item in state.get("objects", [])}
        retracted = {
            str(item["edge_id"])
            for item in state.get("edge_retractions", [])
            if item.get("edge_id")
        }
        active_edges = [
            edge for edge in state.get("edges", []) if str(edge.get("id")) not in retracted
        ]
        return {
            "project_id": project_id,
            "at_revision": at_revision,
            "state_revision": state.get("state_revision"),
            "state": state,
            "objects": objects,
            "active_edges": active_edges,
            "verification_runs": list(state.get("verification_runs", [])),
        }

    @staticmethod
    def _historical_object(context: dict[str, Any], object_id: str) -> dict[str, Any]:
        obj = context["objects"].get(object_id)
        if obj is None:
            raise ValueError("object did not exist at the requested state revision")
        return obj

    def _dependency_from_edges(
        self,
        root_object_id: str,
        edges: Iterable[dict[str, Any]],
        *,
        kinds: Iterable[str],
        max_nodes: int,
    ) -> dict[str, Any]:
        if int(max_nodes) < 1:
            raise ValueError("max_nodes must be at least 1")
        kind_filter = set(self._normalize_kinds(kinds))
        outgoing: dict[str, list[dict[str, str]]] = {}
        for edge in sorted(edges, key=lambda item: str(item["id"])):
            if str(edge["kind"]) not in kind_filter:
                continue
            outgoing.setdefault(str(edge["source_object_id"]), []).append(
                {
                    "edge_id": str(edge["id"]),
                    "target": str(edge["target_object_id"]),
                    "kind": str(edge["kind"]),
                }
            )
        queue = deque([root_object_id])
        seen = {root_object_id}
        traversed: list[dict[str, str]] = []
        truncated = False
        while queue:
            source = queue.popleft()
            for edge in outgoing.get(source, []):
                traversed.append({"source": source, **edge})
                target = edge["target"]
                if target not in seen:
                    if len(seen) >= int(max_nodes):
                        truncated = True
                        continue
                    seen.add(target)
                    queue.append(target)
        dependency_ids = sorted(seen - {root_object_id})
        canonical_edges = sorted(
            (edge["source"], edge["kind"], edge["target"], edge["edge_id"])
            for edge in traversed
        )
        return {
            "root_object_id": root_object_id,
            "dependency_object_ids": dependency_ids,
            "edges": traversed,
            "truncated": truncated,
            "topology_fingerprint": canonical_hash(canonical_edges),
        }

    def _impact_from_edges(
        self,
        changed_object_id: str,
        edges: Iterable[dict[str, Any]],
        object_kinds: dict[str, str],
        *,
        kinds: Iterable[str],
        max_nodes: int,
    ) -> dict[str, Any]:
        if int(max_nodes) < 1:
            raise ValueError("max_nodes must be at least 1")
        kind_filter = set(self._normalize_kinds(kinds))
        incoming: dict[str, list[dict[str, str]]] = {}
        for edge in sorted(edges, key=lambda item: str(item["id"])):
            if str(edge["kind"]) not in kind_filter:
                continue
            incoming.setdefault(str(edge["target_object_id"]), []).append(
                {
                    "edge_id": str(edge["id"]),
                    "source": str(edge["source_object_id"]),
                    "kind": str(edge["kind"]),
                }
            )
        queue = deque([changed_object_id])
        seen = {changed_object_id}
        traversed: list[dict[str, str]] = []
        while queue and len(seen) < int(max_nodes):
            target = queue.popleft()
            for edge in incoming.get(target, []):
                traversed.append({"target": target, **edge})
                source = edge["source"]
                if source not in seen:
                    seen.add(source)
                    queue.append(source)
        dependents = sorted(seen - {changed_object_id})
        claims = [item for item in dependents if object_kinds.get(item) == ObjectKind.CLAIM.value]
        return {
            "changed_object_id": changed_object_id,
            "dependent_object_ids": dependents,
            "affected_claim_ids": claims,
            "edges": traversed,
            "truncated": bool(queue),
        }

    @staticmethod
    def _forward_paths(
        root_object_id: str, edges: Iterable[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Return deterministic shortest paths in literal dependency direction."""
        outgoing: dict[str, list[dict[str, Any]]] = {}
        for edge in edges:
            outgoing.setdefault(str(edge["source"]), []).append(edge)
        for values in outgoing.values():
            values.sort(key=lambda edge: (edge["kind"], edge["target"], edge["edge_id"]))
        paths: dict[str, dict[str, Any]] = {
            root_object_id: {
                "depth": 0,
                "path_object_ids": [root_object_id],
                "path_edge_ids": [],
            }
        }
        queue = deque([root_object_id])
        while queue:
            source = queue.popleft()
            base = paths[source]
            for edge in outgoing.get(source, []):
                target = str(edge["target"])
                if target in paths:
                    continue
                paths[target] = {
                    "depth": int(base["depth"]) + 1,
                    "path_object_ids": [*base["path_object_ids"], target],
                    "path_edge_ids": [*base["path_edge_ids"], str(edge["edge_id"])],
                }
                queue.append(target)
        return paths

    @staticmethod
    def _reverse_paths(
        root_object_id: str, edges: Iterable[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Return deterministic dependent -> ... -> root shortest paths."""
        incoming: dict[str, list[dict[str, Any]]] = {}
        for edge in edges:
            incoming.setdefault(str(edge["target"]), []).append(edge)
        for values in incoming.values():
            values.sort(key=lambda edge: (edge["kind"], edge["source"], edge["edge_id"]))
        paths: dict[str, dict[str, Any]] = {
            root_object_id: {
                "depth": 0,
                "path_object_ids": [root_object_id],
                "path_edge_ids": [],
            }
        }
        queue = deque([root_object_id])
        while queue:
            target = queue.popleft()
            base = paths[target]
            for edge in incoming.get(target, []):
                source = str(edge["source"])
                if source in paths:
                    continue
                paths[source] = {
                    "depth": int(base["depth"]) + 1,
                    "path_object_ids": [source, *base["path_object_ids"]],
                    "path_edge_ids": [str(edge["edge_id"]), *base["path_edge_ids"]],
                }
                queue.append(source)
        return paths

    def provenance(
        self,
        project: str,
        object_id: str,
        *,
        outgoing_kinds: Iterable[str] = DEFAULT_PROVENANCE_OUTGOING_KINDS,
        incoming_kinds: Iterable[str] = DEFAULT_PROVENANCE_INCOMING_KINDS,
        max_nodes: int = 5000,
        at_revision: str | None = None,
    ) -> dict[str, Any]:
        """Trace provenance at the current state or an immutable historical state."""
        if int(max_nodes) < 1:
            raise ValueError("max_nodes must be at least 1")
        if at_revision is None:
            project_id, root = self._project_object(project, object_id)
            object_lookup = self.store.get_object
            active_edges = self.relations.edges(project_id)
            state_revision = None
        else:
            context = self._historical_context(project, at_revision)
            project_id = context["project_id"]
            root = self._historical_object(context, object_id)
            object_lookup = lambda item: self._historical_object(context, item)
            active_edges = context["active_edges"]
            state_revision = context["state_revision"]

        outgoing_filter = set(self._normalize_kinds(outgoing_kinds))
        incoming_filter = set(self._normalize_kinds(incoming_kinds))
        outgoing: dict[str, list[dict[str, Any]]] = {}
        incoming: dict[str, list[dict[str, Any]]] = {}
        for edge in active_edges:
            if edge["kind"] in outgoing_filter:
                outgoing.setdefault(str(edge["source_object_id"]), []).append(edge)
            if edge["kind"] in incoming_filter:
                incoming.setdefault(str(edge["target_object_id"]), []).append(edge)

        queue = deque([object_id])
        seen = {object_id}
        traversed: dict[str, dict[str, Any]] = {}
        truncated = False

        def follow(current: str, edge: dict[str, Any], next_id: str, direction: str) -> None:
            nonlocal truncated
            if next_id not in seen:
                if len(seen) >= int(max_nodes):
                    truncated = True
                    return
                seen.add(next_id)
                queue.append(next_id)
            traversed[str(edge["id"])] = {
                "edge_id": str(edge["id"]),
                "source_object_id": str(edge["source_object_id"]),
                "target_object_id": str(edge["target_object_id"]),
                "kind": str(edge["kind"]),
                "payload": edge.get("payload") or {},
                "traversal_direction": direction,
                "followed_from": current,
                "followed_to": next_id,
            }

        while queue:
            current = queue.popleft()
            for edge in outgoing.get(current, []):
                follow(current, edge, str(edge["target_object_id"]), "outgoing")
            for edge in incoming.get(current, []):
                follow(current, edge, str(edge["source_object_id"]), "incoming")

        provenance_ids = sorted(seen - {object_id})
        edges = sorted(
            traversed.values(),
            key=lambda edge: (
                edge["source_object_id"],
                edge["kind"],
                edge["target_object_id"],
                edge["edge_id"],
            ),
        )
        fingerprint_material = [
            (
                edge["source_object_id"],
                edge["kind"],
                edge["target_object_id"],
                edge["edge_id"],
                edge["traversal_direction"],
            )
            for edge in edges
        ]
        return {
            "project_id": project_id,
            "at_revision": at_revision,
            "state_revision": state_revision,
            "root_object_id": object_id,
            "root_object": root,
            "provenance_object_ids": provenance_ids,
            "provenance_objects": [object_lookup(item) for item in provenance_ids],
            "edges": edges,
            "truncated": truncated,
            "topology_fingerprint": canonical_hash(fingerprint_material),
            "semantics": {
                "outgoing_kinds": sorted(outgoing_filter),
                "incoming_kinds": sorted(incoming_filter),
                "active_edges_only": True,
                "historical_snapshot": at_revision is not None,
            },
        }

    def dependency_closure(
        self,
        project: str,
        object_id: str,
        *,
        kinds: Iterable[str] = RelationIndex.DEFAULT_DEPENDENCY_KINDS,
        max_nodes: int = 5000,
        at_revision: str | None = None,
    ) -> dict[str, Any]:
        if at_revision is None:
            project_id, root = self._project_object(project, object_id)
            result = self.relations.dependency_closure(
                project_id, object_id, kinds=kinds, max_nodes=max_nodes
            )
            dependency_objects = [
                self.store.get_object(item) for item in result["dependency_object_ids"]
            ]
            state_revision = None
        else:
            context = self._historical_context(project, at_revision)
            project_id = context["project_id"]
            root = self._historical_object(context, object_id)
            result = self._dependency_from_edges(
                object_id,
                context["active_edges"],
                kinds=kinds,
                max_nodes=max_nodes,
            )
            dependency_objects = [
                self._historical_object(context, item)
                for item in result["dependency_object_ids"]
            ]
            state_revision = context["state_revision"]

        paths = self._forward_paths(object_id, result["edges"])
        dependency_nodes = [
            {
                "object": obj,
                **paths.get(
                    str(obj["id"]),
                    {"depth": None, "path_object_ids": [], "path_edge_ids": []},
                ),
            }
            for obj in dependency_objects
        ]
        dependency_nodes.sort(
            key=lambda item: (
                item["depth"] if item["depth"] is not None else 10**9,
                item["object"]["id"],
            )
        )
        revision_fingerprint = canonical_hash(
            {
                str(root["id"]): str(root["revision_id"]),
                **{
                    str(obj["id"]): str(obj["revision_id"])
                    for obj in dependency_objects
                },
            }
        )
        return {
            **result,
            "project_id": project_id,
            "at_revision": at_revision,
            "state_revision": state_revision,
            "root_object": root,
            "dependency_objects": dependency_objects,
            "dependency_nodes": dependency_nodes,
            "revision_fingerprint": revision_fingerprint,
        }

    def reverse_impact(
        self,
        project: str,
        object_id: str,
        *,
        kinds: Iterable[str] = RelationIndex.DEFAULT_DEPENDENCY_KINDS,
        max_nodes: int = 5000,
        at_revision: str | None = None,
    ) -> dict[str, Any]:
        if at_revision is None:
            project_id, changed = self._project_object(project, object_id)
            result = self.relations.impact(project_id, object_id, kinds=kinds, max_nodes=max_nodes)
            dependent_objects = [
                self.store.get_object(item) for item in result["dependent_object_ids"]
            ]
            affected_claims = [self.store.get_object(item) for item in result["affected_claim_ids"]]
            affected_claim_verification = [
                self.verification_status(project_id, claim["id"]) for claim in affected_claims
            ]
            state_revision = None
        else:
            context = self._historical_context(project, at_revision)
            project_id = context["project_id"]
            changed = self._historical_object(context, object_id)
            object_kinds = {item: str(obj["kind"]) for item, obj in context["objects"].items()}
            result = self._impact_from_edges(
                object_id,
                context["active_edges"],
                object_kinds,
                kinds=kinds,
                max_nodes=max_nodes,
            )
            dependent_objects = [
                self._historical_object(context, item)
                for item in result["dependent_object_ids"]
            ]
            affected_claims = [
                self._historical_object(context, item)
                for item in result["affected_claim_ids"]
            ]
            affected_claim_verification = [
                self.verification_status(project_id, claim["id"], at_revision=at_revision)
                for claim in affected_claims
            ]
            state_revision = context["state_revision"]

        paths = self._reverse_paths(object_id, result["edges"])
        dependent_nodes = [
            {
                "object": obj,
                **paths.get(
                    str(obj["id"]),
                    {"depth": None, "path_object_ids": [], "path_edge_ids": []},
                ),
            }
            for obj in dependent_objects
        ]
        dependent_nodes.sort(
            key=lambda item: (
                item["depth"] if item["depth"] is not None else 10**9,
                item["object"]["id"],
            )
        )
        return {
            **result,
            "project_id": project_id,
            "at_revision": at_revision,
            "state_revision": state_revision,
            "changed_object": changed,
            "dependent_objects": dependent_objects,
            "dependent_nodes": dependent_nodes,
            "direct_dependent_object_ids": sorted(
                item["object"]["id"] for item in dependent_nodes if item["depth"] == 1
            ),
            "affected_claims": affected_claims,
            "affected_claim_verification": affected_claim_verification,
        }

    def why_believe(
        self,
        project: str,
        object_id: str,
        *,
        max_nodes: int = 5000,
        at_revision: str | None = None,
    ) -> dict[str, Any]:
        """Answer why a record is believed without inventing a confidence scalar."""
        dependency = self.dependency_closure(
            project, object_id, max_nodes=max_nodes, at_revision=at_revision
        )
        project_id = dependency["project_id"]
        target = dependency["root_object"]
        provenance = self.provenance(
            project_id, object_id, max_nodes=max_nodes, at_revision=at_revision
        )
        verification = self.verification_status(
            project_id, object_id, at_revision=at_revision
        )
        if at_revision is None:
            active_edges = self.relations.edges(project_id)
            object_lookup = self.store.get_object
        else:
            context = self._historical_context(project_id, at_revision)
            active_edges = context["active_edges"]
            object_lookup = lambda item: self._historical_object(context, item)
        challenge_edges = [
            edge
            for edge in active_edges
            if str(edge["target_object_id"]) == object_id
            and str(edge["kind"]) in self.CHALLENGE_KINDS
        ]
        challenge_ids = sorted(
            {str(edge["source_object_id"]) for edge in challenge_edges}
        )
        return {
            "question": "why_do_we_believe_this",
            "project_id": project_id,
            "at_revision": at_revision,
            "state_revision": dependency["state_revision"],
            "target": target,
            "dependency_graph": dependency,
            "provenance": provenance,
            "challenges": {
                "edges": challenge_edges,
                "source_objects": [object_lookup(item) for item in challenge_ids],
            },
            "verification": verification,
            "semantics": {
                "confidence_aggregation": "none",
                "dependency_relations": list(RelationIndex.DEFAULT_DEPENDENCY_KINDS),
                "historical_snapshot": at_revision is not None,
                "note": (
                    "Recorded provenance, dependencies, challenges and typed checks are shown "
                    "separately; Witness does not collapse them into a universal truth score."
                ),
            },
        }

    def what_produced(
        self,
        project: str,
        object_id: str,
        *,
        max_nodes: int = 5000,
        at_revision: str | None = None,
    ) -> dict[str, Any]:
        """Answer what produced an object using only active ``derived_from`` lineage."""
        lineage = self.provenance(
            project,
            object_id,
            outgoing_kinds=(EdgeKind.DERIVED_FROM.value,),
            incoming_kinds=(),
            max_nodes=max_nodes,
            at_revision=at_revision,
        )
        target = lineage["root_object"]
        relation_edges = [
            {
                "source": edge["source_object_id"],
                "target": edge["target_object_id"],
                "kind": edge["kind"],
                "edge_id": edge["edge_id"],
            }
            for edge in lineage["edges"]
        ]
        paths = self._forward_paths(object_id, relation_edges)
        producer_nodes = [
            {
                "object": obj,
                **paths.get(
                    str(obj["id"]),
                    {"depth": None, "path_object_ids": [], "path_edge_ids": []},
                ),
            }
            for obj in lineage["provenance_objects"]
        ]
        producer_nodes.sort(
            key=lambda item: (
                item["depth"] if item["depth"] is not None else 10**9,
                item["object"]["id"],
            )
        )
        has_upstream = {str(edge["source_object_id"]) for edge in lineage["edges"]}
        terminal_ids = sorted(
            item["object"]["id"]
            for item in producer_nodes
            if item["object"]["id"] not in has_upstream
        )
        revision_fingerprint = canonical_hash(
            {
                str(target["id"]): str(target["revision_id"]),
                **{
                    str(item["object"]["id"]): str(item["object"]["revision_id"])
                    for item in producer_nodes
                },
            }
        )
        return {
            "question": "what_produced_this",
            "project_id": lineage["project_id"],
            "at_revision": at_revision,
            "state_revision": lineage["state_revision"],
            "target": target,
            "producer_object_ids": [item["object"]["id"] for item in producer_nodes],
            "producer_nodes": producer_nodes,
            "terminal_producer_ids": terminal_ids,
            "derivation_edges": lineage["edges"],
            "topology_fingerprint": lineage["topology_fingerprint"],
            "revision_fingerprint": revision_fingerprint,
            "truncated": lineage["truncated"],
            "semantics": (
                "Core direction is A --derived_from--> B: A is derived from B. "
                "No other relation kind is treated as production lineage."
            ),
        }

    def invalidation_impact(
        self,
        project: str,
        object_id: str,
        *,
        max_nodes: int = 5000,
        at_revision: str | None = None,
    ) -> dict[str, Any]:
        """Answer what requires reconsideration if X is invalidated."""
        impact = self.reverse_impact(
            project, object_id, max_nodes=max_nodes, at_revision=at_revision
        )
        project_id = impact["project_id"]
        target = impact["changed_object"]
        claim_ids_requiring_reconsideration = set(impact["affected_claim_ids"])
        if target["kind"] == ObjectKind.CLAIM.value:
            claim_ids_requiring_reconsideration.add(object_id)

        if at_revision is None:
            raw_runs = self.store.list_verification_runs(project_id)
            assess = self.inspector.assess_run
        else:
            context = self._historical_context(project_id, at_revision)
            raw_runs = context["verification_runs"]
            assess = lambda run: self._assess_historical_run(context, run)

        affected_runs: list[dict[str, Any]] = []
        for run in raw_runs:
            reasons: list[str] = []
            if object_id in (run.get("input_revisions") or {}):
                reasons.append("pins_invalidated_object_revision")
            if str(run["claim_object_id"]) in claim_ids_requiring_reconsideration:
                reasons.append("claim_requires_reconsideration")
            if not reasons:
                continue
            affected_runs.append(
                {
                    **assess(run),
                    "impact_reasons": reasons,
                    "would_require_reverification": True,
                }
            )

        invalidated_revision = {
            "object_id": object_id,
            "revision_id": target["revision_id"],
            "revision_no": target["revision_no"],
            "content_hash": target["content_hash"],
        }
        impact_fingerprint = canonical_hash(
            {
                "invalidated_revision": invalidated_revision,
                "edge_ids": sorted(edge["edge_id"] for edge in impact["edges"]),
                "dependent_revision_ids": sorted(
                    item["object"]["revision_id"] for item in impact["dependent_nodes"]
                ),
            }
        )
        return {
            **impact,
            "question": "what_breaks_if_x_is_invalidated",
            "invalidated_object": target,
            "invalidated_revision": invalidated_revision,
            "claim_ids_requiring_reconsideration": sorted(
                claim_ids_requiring_reconsideration
            ),
            "affected_verification_runs": affected_runs,
            "impact_fingerprint": impact_fingerprint,
            "semantics": (
                "Potential impact only: active depends_on / assumes / derived_from edges are "
                "walked in reverse. Every listed dependent requires reconsideration, not "
                "automatic rejection."
            ),
        }

    def what_breaks_if_invalidated(
        self,
        project: str,
        object_id: str,
        *,
        max_nodes: int = 5000,
        at_revision: str | None = None,
    ) -> dict[str, Any]:
        return self.invalidation_impact(
            project, object_id, max_nodes=max_nodes, at_revision=at_revision
        )

    @staticmethod
    def _decorate_revision_dag(
        revisions: list[dict[str, Any]], current_revision_id: str
    ) -> tuple[list[dict[str, Any]], list[str]]:
        known = {str(item["id"]) for item in revisions}
        children: dict[str, list[str]] = {item: [] for item in known}
        for revision in revisions:
            child = str(revision["id"])
            for parent in revision.get("parent_revision_ids", []):
                if parent in children:
                    children[parent].append(child)
        decorated = []
        for revision in revisions:
            item = dict(revision)
            parents = list(item.get("parent_revision_ids") or [])
            child_ids = sorted(children.get(str(item["id"]), []))
            item["parent_revision_ids"] = parents
            item["child_revision_ids"] = child_ids
            item["supersedes_revision_id"] = parents[0] if len(parents) == 1 else None
            item["is_current"] = str(item["id"]) == current_revision_id
            item["is_head"] = not child_ids
            decorated.append(item)
        heads = sorted(item["id"] for item in decorated if item["is_head"])
        return decorated, heads

    def revision_history(
        self, project: str, object_id: str, *, at_revision: str | None = None
    ) -> dict[str, Any]:
        if at_revision is None:
            project_id, current = self._project_object(project, object_id)
            with self.store.connect() as conn:
                rows = conn.execute(
                    "SELECT id FROM witness_revisions WHERE object_id=? ORDER BY revision_no,id",
                    (object_id,),
                ).fetchall()
            revisions = [self.store.get_object_revision(str(row["id"])) for row in rows]
            current_revision_id = str(current["revision_id"])
            object_kind = str(current["kind"])
            state_revision = None
        else:
            context = self._historical_context(project, at_revision)
            current = self._historical_object(context, object_id)
            project_id = context["project_id"]
            revisions = [
                dict(item)
                for item in context["state"].get("object_revisions", [])
                if str(item["object_id"]) == object_id
            ]
            revisions.sort(key=lambda item: (int(item["revision_no"]), str(item["id"])))
            current_revision_id = str(current["revision_id"])
            object_kind = str(current["kind"])
            state_revision = context["state_revision"]
        decorated, heads = self._decorate_revision_dag(revisions, current_revision_id)
        current_revision = next(
            item for item in decorated if str(item["id"]) == current_revision_id
        )
        return {
            "project_id": project_id,
            "at_revision": at_revision,
            "state_revision": state_revision,
            "object_id": object_id,
            "object_kind": object_kind,
            "history_semantics": "revision_dag",
            "current_revision_id": current_revision_id,
            "current_revision_no": int(current_revision["revision_no"]),
            "revision_heads": heads,
            "revision_count": len(decorated),
            "revisions": decorated,
        }

    def _assess_historical_run(
        self, context: dict[str, Any], run: dict[str, Any]
    ) -> dict[str, Any]:
        pinned = run.get("input_revisions") or {}
        stale_inputs: list[str] = []
        for object_id, snapshot in pinned.items():
            current = context["objects"].get(str(object_id))
            if current is None or str(current.get("revision_id")) != str(snapshot.get("revision_id")):
                stale_inputs.append(str(object_id))
        closure = self._dependency_from_edges(
            str(run["claim_object_id"]),
            context["active_edges"],
            kinds=RelationIndex.DEFAULT_DEPENDENCY_KINDS,
            max_nodes=5000,
        )
        meta = (run.get("result") or {}).get("_witness") or {}
        recorded = meta.get("dependency_topology_fingerprint")
        known = bool(recorded)
        topology_stale = bool(known and recorded != closure["topology_fingerprint"])
        old = set(meta.get("dependency_object_ids") or [])
        current_deps = set(closure["dependency_object_ids"])
        reasons: list[dict[str, Any]] = []
        if stale_inputs:
            reasons.append({"kind": "revision_changed", "object_ids": sorted(stale_inputs)})
        if topology_stale:
            reasons.append(
                {
                    "kind": "dependency_topology_changed",
                    "added": sorted(current_deps - old),
                    "removed": sorted(old - current_deps),
                }
            )
        return {
            **run,
            "stale": bool(stale_inputs),
            "stale_inputs": sorted(stale_inputs),
            "effective_stale": bool(stale_inputs or topology_stale),
            "stale_reasons": reasons,
            "topology_assessment": "changed" if topology_stale else "current" if known else "unknown",
            "current_dependency_topology_fingerprint": closure["topology_fingerprint"],
            "current_dependency_object_ids": closure["dependency_object_ids"],
            "assessed_at_revision": context["at_revision"],
        }

    def verification_status(
        self, project: str, object_id: str, *, at_revision: str | None = None
    ) -> dict[str, Any]:
        """Return effective verification status at current or historical project state."""
        if at_revision is None:
            project_id, obj = self._project_object(project, object_id)
            with self.store.connect() as conn:
                if obj["kind"] == ObjectKind.CLAIM.value:
                    rows = conn.execute(
                        """SELECT * FROM witness_verification_runs
                           WHERE project_id=? AND claim_object_id=? ORDER BY rowid""",
                        (project_id, object_id),
                    ).fetchall()
                    runs = [
                        self.inspector.assess_run(self.store._serialize_run(conn, row)) for row in rows
                    ]
                else:
                    rows = conn.execute(
                        "SELECT * FROM witness_verification_runs WHERE project_id=? ORDER BY rowid",
                        (project_id,),
                    ).fetchall()
                    runs = []
                    for row in rows:
                        run = self.store._serialize_run(conn, row)
                        if object_id in run["input_revisions"]:
                            runs.append(self.inspector.assess_run(run))
            state_revision = None
        else:
            context = self._historical_context(project, at_revision)
            project_id = context["project_id"]
            obj = self._historical_object(context, object_id)
            if obj["kind"] == ObjectKind.CLAIM.value:
                raw_runs = [
                    run
                    for run in context["verification_runs"]
                    if str(run["claim_object_id"]) == object_id
                ]
            else:
                raw_runs = [
                    run
                    for run in context["verification_runs"]
                    if object_id in (run.get("input_revisions") or {})
                ]
            runs = [self._assess_historical_run(context, run) for run in raw_runs]
            state_revision = context["state_revision"]

        if obj["kind"] == ObjectKind.CLAIM.value:
            latest = runs[-1] if runs else None
            effective_status = (
                "unverified"
                if latest is None
                else "stale"
                if latest["effective_stale"]
                else str(latest["status"])
            )
            status_counts = Counter(
                "stale" if run["effective_stale"] else str(run["status"]) for run in runs
            )
            return {
                "project_id": project_id,
                "at_revision": at_revision,
                "state_revision": state_revision,
                "object_id": object_id,
                "object_kind": obj["kind"],
                "is_verification_target": True,
                "effective_status": effective_status,
                "needs_verification": latest is None or bool(latest["effective_stale"]),
                "latest_run": latest,
                "verification_runs": runs,
                "status_counts": dict(status_counts),
            }

        return {
            "project_id": project_id,
            "at_revision": at_revision,
            "state_revision": state_revision,
            "object_id": object_id,
            "object_kind": obj["kind"],
            "is_verification_target": False,
            "effective_status": "not_verification_target",
            "needs_verification": False,
            "latest_run": None,
            "verification_runs": [],
            "pinned_by_runs": runs,
            "pinned_by_run_count": len(runs),
        }
