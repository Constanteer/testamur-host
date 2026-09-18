from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .receipt_store import TestamurReceiptStore


def _validate_depth(max_depth: int) -> int:
    value = int(max_depth)
    if value < 1:
        raise ValueError("max_depth must be at least 1")
    return value


def _revision_relation(expected_hash: Any, observed_hash: Any) -> str:
    if expected_hash is None or observed_hash is None:
        return "not_assessable"
    return "same_revision" if str(expected_hash) == str(observed_hash) else "different_revision"


def _run_entry(
    store: TestamurReceiptStore,
    run_id: str,
    *,
    depth: int,
    via_artifact: str,
    observation_role: str,
    via_observation_id: str | None,
    revision_relation: str,
) -> dict[str, Any] | None:
    receipt = store.get(run_id)
    if receipt is None:
        return None
    return {
        "run_id": run_id,
        "depth": depth,
        "via_artifact": via_artifact,
        "via_observation_id": via_observation_id,
        "observation_role": observation_role,
        "revision_relation": revision_relation,
        "command": receipt.get("argv") or [],
        "inputs": receipt.get("input_artifacts") or [],
        "git_context": receipt.get("git_context") or {},
        "verification_implied": bool(receipt.get("verification_implied", False)),
        "causal_attribution_implied": False,
    }


def trace(
    store: TestamurReceiptStore,
    target: str,
    *,
    root: Path,
    recursive: bool = False,
    max_depth: int = 8,
) -> dict[str, Any]:
    """Trace observed provenance upstream without asserting causal production.

    Recursive traversal walks the persisted bipartite observation graph:
    artifact <- producer observation <- run <- input observation <- artifact.
    Edges are revision-aware: when both sides have content hashes, different
    revisions are recorded as historical mismatches and are not traversed.
    Missing hashes remain explicitly not assessable rather than being silently
    treated as equal. Historical files are never re-snapshotted.
    """

    limit = _validate_depth(max_depth)
    explanation = store.explain(target, root=root)
    immediate: list[dict[str, Any]] = []
    transitive: list[dict[str, Any]] = []
    mismatched: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    queue: deque[tuple[str, int]] = deque()
    seen_runs: set[str] = set()
    seen_mismatches: set[tuple[str, str | None]] = set()
    truncated = False

    def add_producer(
        producer_observation: dict[str, Any],
        *,
        consumer_run_id: str | None,
        consumer_input_observation_id: str | None,
        via_artifact: str,
        expected_hash: Any,
        depth: int,
    ) -> None:
        nonlocal truncated
        producer_run_id = str(producer_observation.get("run_id") or "")
        if not producer_run_id:
            return
        relation = _revision_relation(expected_hash, producer_observation.get("content_hash"))
        edge = {
            "producer_run_id": producer_run_id,
            "consumer_run_id": consumer_run_id,
            "artifact": via_artifact,
            "producer_observation_id": producer_observation.get("observation_id"),
            "consumer_input_observation_id": consumer_input_observation_id,
            "producer_observation_role": producer_observation.get("role"),
            "revision_relation": relation,
            "depth": depth,
            "causal_attribution_implied": False,
        }
        edges.append(edge)

        if relation == "different_revision":
            key = (
                str(producer_observation.get("observation_id") or producer_run_id),
                consumer_input_observation_id,
            )
            if key not in seen_mismatches:
                seen_mismatches.add(key)
                entry = _run_entry(
                    store,
                    producer_run_id,
                    depth=depth,
                    via_artifact=via_artifact,
                    observation_role=str(producer_observation.get("role") or "observation"),
                    via_observation_id=(
                        None
                        if producer_observation.get("observation_id") is None
                        else str(producer_observation.get("observation_id"))
                    ),
                    revision_relation=relation,
                )
                if entry is not None:
                    mismatched.append(entry)
            return

        if producer_run_id in seen_runs:
            return
        seen_runs.add(producer_run_id)
        entry = _run_entry(
            store,
            producer_run_id,
            depth=depth,
            via_artifact=via_artifact,
            observation_role=str(producer_observation.get("role") or "observation"),
            via_observation_id=(
                None
                if producer_observation.get("observation_id") is None
                else str(producer_observation.get("observation_id"))
            ),
            revision_relation=relation,
        )
        if entry is None:
            return
        if depth == 1:
            immediate.append(entry)
        else:
            transitive.append(entry)
        if recursive:
            if depth < limit:
                queue.append((producer_run_id, depth))
            else:
                # A depth-limited traversal is only marked truncated when a
                # compatible or unassessable upstream edge actually exists.
                for input_observation in store.observations_for_run(producer_run_id):
                    if input_observation.get("role") != "input":
                        continue
                    producers = [
                        item
                        for item in store.observations_for_path(
                            str(
                                input_observation.get("resolved_path")
                                or input_observation.get("declared_path")
                                or ""
                            ),
                            root=root,
                        )
                        if item.get("role") in {"output", "worktree_change"}
                    ]
                    if any(
                        str(item.get("run_id") or "") not in seen_runs
                        and _revision_relation(
                            input_observation.get("content_hash"),
                            item.get("content_hash"),
                        )
                        != "different_revision"
                        for item in producers
                    ):
                        truncated = True
                        break

    if explanation.get("kind") == "run":
        target_run_id = str(explanation.get("target") or target)
        seen_runs.add(target_run_id)
        target_inputs = [
            item for item in store.observations_for_run(target_run_id) if item.get("role") == "input"
        ]
        for input_observation in target_inputs:
            artifact = str(
                input_observation.get("declared_path")
                or input_observation.get("resolved_path")
                or ""
            )
            lookup = str(input_observation.get("resolved_path") or artifact)
            for producer in store.observations_for_path(lookup, root=root):
                if producer.get("role") not in {"output", "worktree_change"}:
                    continue
                add_producer(
                    producer,
                    consumer_run_id=target_run_id,
                    consumer_input_observation_id=str(input_observation.get("observation_id") or ""),
                    via_artifact=artifact,
                    expected_hash=input_observation.get("content_hash"),
                    depth=1,
                )
    else:
        current_hash = explanation.get("current_content_hash")
        observed_producers = [
            *(explanation.get("observed_as_declared_output") or []),
            *(explanation.get("observed_worktree_changes") or []),
        ]
        for producer in observed_producers:
            add_producer(
                producer,
                consumer_run_id=None,
                consumer_input_observation_id=None,
                via_artifact=str(explanation.get("target") or target),
                expected_hash=current_hash,
                depth=1,
            )

    while queue:
        consumer_run_id, consumer_depth = queue.popleft()
        for input_observation in store.observations_for_run(consumer_run_id):
            if input_observation.get("role") != "input":
                continue
            artifact = str(
                input_observation.get("declared_path")
                or input_observation.get("resolved_path")
                or ""
            )
            lookup = str(input_observation.get("resolved_path") or artifact)
            for producer in store.observations_for_path(lookup, root=root):
                if producer.get("role") not in {"output", "worktree_change"}:
                    continue
                add_producer(
                    producer,
                    consumer_run_id=consumer_run_id,
                    consumer_input_observation_id=str(input_observation.get("observation_id") or ""),
                    via_artifact=artifact,
                    expected_hash=input_observation.get("content_hash"),
                    depth=consumer_depth + 1,
                )

    return {
        **explanation,
        "upstream_observed_runs": immediate,
        "transitive_upstream_runs": transitive if recursive else [],
        "revision_mismatched_producers": mismatched,
        "trace_edges": edges,
        "traversal": {
            "recursive": bool(recursive),
            "max_depth": limit,
            "truncated": truncated,
            "runs_observed": len(immediate) + len(transitive),
            "revision_mismatches": len(mismatched),
            "edges_observed": len(edges),
        },
        "semantics": {
            "recursive_trace": bool(recursive),
            "trace_uses_persisted_observations": True,
            "revision_aware_traversal": True,
            "different_revisions_are_not_traversed": True,
            "missing_revision_hash_is_not_assessable": True,
            "causal_attribution_implied": False,
        },
    }


def impact(
    store: TestamurReceiptStore,
    target: str,
    *,
    root: Path,
    recursive: bool = False,
    max_depth: int = 8,
) -> dict[str, Any]:
    """Trace downstream evidence that requires revalidation after target change.

    The first hop compares current bytes with persisted input observations.
    Recursive hops propagate epistemic uncertainty only across compatible
    persisted revisions. Different known revisions are recorded but not crossed.
    A propagated edge means dependent evidence should be checked again; it does
    not mean the downstream conclusion is false or causally determined by the
    changed input.
    """

    limit = _validate_depth(max_depth)
    observations = store.observations_for_path(target, root=root)
    if not observations:
        raise KeyError(target)

    current_hash = store.current_content_hash(target, root=root)
    immediate_consumers: list[dict[str, Any]] = []
    transitive_consumers: list[dict[str, Any]] = []
    affected: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    mismatched_edges: list[dict[str, Any]] = []
    queue: deque[tuple[dict[str, Any], int, str]] = deque()
    seen_consumer_observations: set[str] = set()
    expanded_artifacts: set[tuple[str, str | None]] = set()
    truncated = False

    root_candidate = Path(target).expanduser()
    root_resolved = str(
        root_candidate.resolve()
        if root_candidate.is_absolute()
        else (root / root_candidate).resolve()
    )
    expanded_artifacts.add((root_resolved, None if current_hash is None else str(current_hash)))

    def reason_for_root(observed_hash: Any) -> str:
        if current_hash is None:
            return "current_target_not_assessable_or_missing"
        if observed_hash is None:
            return "historical_input_revision_not_assessable"
        return "input_revision_changed"

    def add_outputs(run_id: str, *, depth: int, source_artifact: str) -> None:
        for output in store.observations_for_run(run_id):
            role = str(output.get("role") or "")
            if role == "input":
                continue
            item = {
                "run_id": run_id,
                "depth": depth,
                "path": output.get("declared_path") or output.get("resolved_path"),
                "resolved_path": output.get("resolved_path"),
                "content_hash": output.get("content_hash"),
                "source_artifact": source_artifact,
                "observation_role": role,
                "observation_id": output.get("observation_id"),
                "state": "potentially_stale_requires_revalidation",
                "causal_attribution_implied": False,
            }
            affected.append(item)
            if recursive:
                queue.append((output, depth, run_id))

    for input_observation in observations:
        if input_observation.get("role") != "input":
            continue
        observation_id = str(input_observation.get("observation_id") or "")
        if observation_id in seen_consumer_observations:
            continue
        seen_consumer_observations.add(observation_id)
        observed_hash = input_observation.get("content_hash")
        changed = (
            current_hash is None
            or observed_hash is None
            or str(observed_hash) != str(current_hash)
        )
        run_id = str(input_observation.get("run_id") or "")
        receipt = store.get(run_id)
        state = "requires_revalidation" if changed else "input_matches_observation"
        consumer = {
            "run_id": run_id,
            "depth": 1,
            "via_artifact": target,
            "input_observation_id": observation_id,
            "observed_content_hash": observed_hash,
            "current_content_hash": current_hash,
            "revision_relation": _revision_relation(current_hash, observed_hash),
            "state": state,
            "reason": reason_for_root(observed_hash) if changed else "input_revision_matches",
            "command": [] if receipt is None else receipt.get("argv") or [],
            "causal_attribution_implied": False,
        }
        immediate_consumers.append(consumer)
        edges.append(
            {
                "source_run_id": None,
                "consumer_run_id": run_id,
                "artifact": target,
                "consumer_input_observation_id": observation_id,
                "revision_relation": consumer["revision_relation"],
                "depth": 1,
                "state": state,
                "causal_attribution_implied": False,
            }
        )
        if changed and receipt is not None:
            add_outputs(run_id, depth=1, source_artifact=target)

    while queue:
        source_output, producer_depth, source_run_id = queue.popleft()
        next_depth = producer_depth + 1
        lookup = str(
            source_output.get("resolved_path")
            or source_output.get("declared_path")
            or ""
        )
        if not lookup:
            continue
        candidate = Path(lookup).expanduser()
        resolved = str(candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve())
        source_hash = source_output.get("content_hash")
        expansion_key = (resolved, None if source_hash is None else str(source_hash))
        if expansion_key in expanded_artifacts:
            continue
        expanded_artifacts.add(expansion_key)

        downstream_inputs = [
            item
            for item in store.observations_for_path(lookup, root=root)
            if item.get("role") == "input"
        ]
        compatible_inputs = [
            item
            for item in downstream_inputs
            if _revision_relation(source_hash, item.get("content_hash")) != "different_revision"
        ]
        if next_depth > limit:
            if compatible_inputs:
                truncated = True
            continue

        display_artifact = str(
            source_output.get("declared_path")
            or source_output.get("resolved_path")
            or lookup
        )
        for input_observation in downstream_inputs:
            relation = _revision_relation(source_hash, input_observation.get("content_hash"))
            if relation == "different_revision":
                mismatched_edges.append(
                    {
                        "source_run_id": source_run_id,
                        "consumer_run_id": input_observation.get("run_id"),
                        "artifact": display_artifact,
                        "source_observation_id": source_output.get("observation_id"),
                        "consumer_input_observation_id": input_observation.get("observation_id"),
                        "revision_relation": relation,
                        "depth": next_depth,
                        "causal_attribution_implied": False,
                    }
                )
                continue

            observation_id = str(input_observation.get("observation_id") or "")
            if observation_id in seen_consumer_observations:
                continue
            seen_consumer_observations.add(observation_id)
            run_id = str(input_observation.get("run_id") or "")
            receipt = store.get(run_id)
            consumer = {
                "run_id": run_id,
                "depth": next_depth,
                "via_artifact": display_artifact,
                "input_observation_id": observation_id,
                "revision_relation": relation,
                "state": "requires_revalidation_due_to_upstream",
                "reason": "upstream_evidence_requires_revalidation",
                "command": [] if receipt is None else receipt.get("argv") or [],
                "causal_attribution_implied": False,
            }
            transitive_consumers.append(consumer)
            edges.append(
                {
                    "source_run_id": source_run_id,
                    "consumer_run_id": run_id,
                    "artifact": display_artifact,
                    "source_observation_id": source_output.get("observation_id"),
                    "consumer_input_observation_id": observation_id,
                    "revision_relation": relation,
                    "depth": next_depth,
                    "state": "requires_revalidation_due_to_upstream",
                    "causal_attribution_implied": False,
                }
            )
            if receipt is not None:
                add_outputs(run_id, depth=next_depth, source_artifact=display_artifact)

    return {
        "target": target,
        "kind": "artifact",
        "current_content_hash": current_hash,
        "consumers": immediate_consumers,
        "transitive_consumers": transitive_consumers if recursive else [],
        "potentially_affected_observations": affected,
        "impact_edges": edges,
        "revision_mismatched_edges": mismatched_edges,
        "traversal": {
            "recursive": bool(recursive),
            "max_depth": limit,
            "truncated": truncated,
            "immediate_consumers": len(immediate_consumers),
            "transitive_consumers": len(transitive_consumers),
            "affected_observations": len(affected),
            "revision_mismatches": len(mismatched_edges),
            "edges_observed": len(edges),
        },
        "semantics": {
            "change_implies_invalidation": False,
            "revalidation_required_when_input_hash_differs": True,
            "upstream_uncertainty_propagates_revalidation": bool(recursive),
            "revision_aware_traversal": True,
            "different_revisions_are_not_traversed": True,
            "missing_revision_hash_is_not_assessable": True,
            "causal_attribution_implied": False,
            "recursive_impact": bool(recursive),
            "impact_uses_persisted_observations": True,
        },
    }
