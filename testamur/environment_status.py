from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .receipt_store import TestamurReceiptStore


def revalidation_status(
    store: TestamurReceiptStore,
    *,
    root: Path,
    detail_limit: int = 12,
    graph_path_limit: int = 32,
    graph_max_depth: int = 8,
) -> dict[str, Any]:
    """Compare current inputs and propagate revalidation through persisted evidence.

    Direct hash mismatches identify runs whose observed input bytes changed.
    A bounded, revision-aware graph walk then propagates the need to revalidate
    dependent evidence. Neither direct nor transitive revalidation means a
    conclusion is false, and no universal trust score is computed.
    """

    with store.connect() as conn:
        rows = conn.execute(
            """SELECT run_id,declared_path,resolved_path,status,content_hash,observation_json
               FROM testamur_artifact_observations
               WHERE role='input'
               ORDER BY rowid DESC"""
        ).fetchall()

    by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_path[str(row["resolved_path"])].append(
            {
                "run_id": str(row["run_id"]),
                "declared_path": str(row["declared_path"]),
                "resolved_path": str(row["resolved_path"]),
                "status": str(row["status"]),
                "content_hash": None if row["content_hash"] is None else str(row["content_hash"]),
                "observation": json.loads(str(row["observation_json"])),
            }
        )

    details: list[dict[str, Any]] = []
    stale_runs: set[str] = set()
    current_runs: set[str] = set()
    unknown_runs: set[str] = set()

    for resolved_path, observations in sorted(by_path.items()):
        target = Path(resolved_path)
        exists = target.exists() or target.is_symlink()
        current_hash = store.current_content_hash(target, root=root) if exists else None
        stale: list[str] = []
        current: list[str] = []
        unknown: list[str] = []

        for observation in observations:
            run_id = str(observation["run_id"])
            observed_hash = observation.get("content_hash")
            observed_status = str(observation.get("status") or "unknown")
            if observed_status != "captured" or not observed_hash:
                unknown.append(run_id)
                unknown_runs.add(run_id)
            elif not exists:
                stale.append(run_id)
                stale_runs.add(run_id)
            elif current_hash is None:
                unknown.append(run_id)
                unknown_runs.add(run_id)
            elif str(current_hash) == str(observed_hash):
                current.append(run_id)
                current_runs.add(run_id)
            else:
                stale.append(run_id)
                stale_runs.add(run_id)

        if stale:
            state = "requires_revalidation"
        elif unknown:
            state = "not_assessable"
        else:
            state = "current"
        details.append(
            {
                "path": observations[0]["declared_path"] or resolved_path,
                "resolved_path": resolved_path,
                "exists": exists,
                "current_content_hash": current_hash,
                "state": state,
                "runs_requiring_revalidation": sorted(set(stale)),
                "runs_current": sorted(set(current)),
                "runs_not_assessable": sorted(set(unknown)),
            }
        )

    requiring = [item for item in details if item["state"] == "requires_revalidation"]
    unknown = [item for item in details if item["state"] == "not_assessable"]
    current = [item for item in details if item["state"] == "current"]

    transitive_runs: set[str] = set()
    affected_observation_ids: set[str] = set()
    affected_paths: set[str] = set()
    graph_truncated = False
    traversed_paths = 0
    path_budget = max(0, int(graph_path_limit))
    depth_budget = max(1, int(graph_max_depth))

    for item in requiring[:path_budget]:
        traversed_paths += 1
        impact = store.impact(
            str(item["resolved_path"]),
            root=root,
            recursive=True,
            max_depth=depth_budget,
        )
        transitive_for_path = {
            str(consumer.get("run_id"))
            for consumer in impact.get("transitive_consumers") or []
            if consumer.get("run_id")
        }
        transitive_runs.update(transitive_for_path)
        for observation in impact.get("potentially_affected_observations") or []:
            observation_id = observation.get("observation_id")
            if observation_id:
                affected_observation_ids.add(str(observation_id))
            path = observation.get("resolved_path") or observation.get("path")
            if path:
                affected_paths.add(str(path))
        traversal = impact.get("traversal") or {}
        graph_truncated = graph_truncated or bool(traversal.get("truncated"))
        item["transitive_runs_requiring_revalidation"] = sorted(transitive_for_path)
        item["affected_observations_requiring_revalidation"] = int(
            traversal.get("affected_observations") or 0
        )

    if len(requiring) > path_budget:
        graph_truncated = True

    transitive_only = transitive_runs - stale_runs
    total_revalidation_runs = stale_runs | transitive_runs
    ordered = [*requiring, *unknown, *current]
    limit = max(0, int(detail_limit))

    return {
        "observed_input_paths": len(details),
        "paths_current": len(current),
        "paths_requiring_revalidation": len(requiring),
        "paths_not_assessable": len(unknown),
        # Backward-compatible direct count.
        "runs_requiring_revalidation": len(stale_runs),
        "direct_runs_requiring_revalidation": len(stale_runs),
        "transitive_runs_requiring_revalidation": len(transitive_only),
        "total_runs_requiring_revalidation": len(total_revalidation_runs),
        "affected_observations_requiring_revalidation": len(affected_observation_ids),
        "affected_paths_requiring_revalidation": len(affected_paths),
        "runs_current_for_observed_inputs": len(current_runs - stale_runs),
        "runs_not_assessable": len(unknown_runs - stale_runs),
        "details": ordered[:limit],
        "details_truncated": len(ordered) > limit,
        "dependency_graph": {
            "paths_traversed": traversed_paths,
            "path_limit": path_budget,
            "max_depth": depth_budget,
            "truncated": graph_truncated,
            "revision_aware": True,
        },
        "semantics": {
            "hash_mismatch_means": "observed input bytes changed; dependent evidence requires revalidation",
            "hash_mismatch_implies_false": False,
            "transitive_revalidation_implies_false": False,
            "transitive_revalidation_uses_persisted_revision_aware_graph": True,
            "unknown_is_not_failure": True,
            "universal_trust_score": None,
        },
    }
