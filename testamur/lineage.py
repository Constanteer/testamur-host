from __future__ import annotations

import json
import sqlite3
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from .component_identity import canonical_extension_id, canonical_json


class LineageRelationType(StrEnum):
    DERIVED_FROM = "DERIVED_FROM"
    CONTAINS = "CONTAINS"
    PATCHED_FROM = "PATCHED_FROM"
    TRANSFORMS = "TRANSFORMS"
    SUPERSEDES = "SUPERSEDES"
    EQUIVALENT_TO = "EQUIVALENT_TO"


class LineageEvidenceClass(StrEnum):
    OBSERVED = "OBSERVED"
    DERIVED = "DERIVED"
    DECLARED = "DECLARED"


class IncompleteLineageTraversalError(RuntimeError):
    """Raised when a decision-oriented lineage query hits a resource bound."""

    def __init__(self, traversal: Mapping[str, Any]) -> None:
        self.traversal = dict(traversal)
        reasons = ", ".join(str(item) for item in traversal.get("truncation_reasons") or [])
        super().__init__(
            "lineage traversal incomplete"
            + (f" ({reasons})" if reasons else "")
            + "; use lineage_walk() to inspect bounded partial results"
        )


AFFECTEDNESS_PROPAGATING_RELATIONS = frozenset(
    {
        LineageRelationType.DERIVED_FROM.value,
        LineageRelationType.CONTAINS.value,
        LineageRelationType.PATCHED_FROM.value,
        LineageRelationType.TRANSFORMS.value,
        LineageRelationType.EQUIVALENT_TO.value,
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _required(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    return text


def _normalize_relation(value: str | LineageRelationType) -> str:
    try:
        return LineageRelationType(str(value)).value
    except ValueError as exc:
        allowed = ", ".join(item.value for item in LineageRelationType)
        raise ValueError(
            f"unsupported lineage relation {value!r}; expected one of: {allowed}"
        ) from exc


def _normalize_evidence(
    evidence: Iterable[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in evidence or ():
        if not isinstance(raw, Mapping):
            raise ValueError("lineage evidence entries must be mappings")
        item = dict(raw)
        item["ref"] = _required(item.get("ref"), field="evidence.ref")
        try:
            item["evidence_class"] = LineageEvidenceClass(
                str(item.get("evidence_class"))
            ).value
        except ValueError as exc:
            raise ValueError(
                "evidence.evidence_class must be OBSERVED, DERIVED, or DECLARED"
            ) from exc
        if item["evidence_class"] == LineageEvidenceClass.DERIVED.value:
            item["analyzer"] = _required(
                item.get("analyzer"), field="evidence.analyzer"
            )
            item["analyzer_version"] = _required(
                item.get("analyzer_version"), field="evidence.analyzer_version"
            )
        canonical_json(item)
        result.append(item)
    if not result:
        raise ValueError("lineage relations require explicit evidence")
    return result


def _normalize_scope(scope: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if scope is None:
        return None
    if not isinstance(scope, Mapping):
        raise ValueError("scope must be a mapping")
    normalized = dict(scope)
    canonical_json(normalized)
    if not normalized:
        raise ValueError("scope must not be empty when supplied")
    return normalized


class TestamurLineageStore:
    """Append-only typed lineage graph over exact artifact/revision references."""

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS testamur_lineage_edges(
          edge_id TEXT PRIMARY KEY,
          relation_type TEXT NOT NULL,
          upstream_ref TEXT NOT NULL,
          downstream_ref TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          recorded_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS testamur_lineage_upstream_idx ON testamur_lineage_edges(upstream_ref, relation_type, edge_id);
        CREATE INDEX IF NOT EXISTS testamur_lineage_downstream_idx ON testamur_lineage_edges(downstream_ref, relation_type, edge_id);
        CREATE INDEX IF NOT EXISTS testamur_lineage_relation_idx ON testamur_lineage_edges(relation_type, edge_id);
        CREATE TRIGGER IF NOT EXISTS testamur_lineage_no_update BEFORE UPDATE ON testamur_lineage_edges BEGIN
          SELECT RAISE(ABORT, 'Testamur lineage edges are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS testamur_lineage_no_delete BEFORE DELETE ON testamur_lineage_edges BEGIN
          SELECT RAISE(ABORT, 'Testamur lineage edges are immutable');
        END;
        """
        with self.connect() as conn:
            conn.executescript(schema)

    def record_lineage(
        self,
        subject_revision: str,
        relation: str | LineageRelationType,
        upstream_revision: str,
        *,
        evidence: Iterable[Mapping[str, Any]],
        scope: Mapping[str, Any] | None = None,
        component_mapping: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        downstream_ref = _required(subject_revision, field="subject_revision")
        upstream_ref = _required(upstream_revision, field="upstream_revision")
        if downstream_ref == upstream_ref:
            raise ValueError("lineage endpoints must be distinct")
        relation_type = _normalize_relation(relation)
        normalized_evidence = _normalize_evidence(evidence)
        normalized_scope = _normalize_scope(scope)
        if (
            relation_type == LineageRelationType.EQUIVALENT_TO.value
            and normalized_scope is None
        ):
            raise ValueError("EQUIVALENT_TO requires an explicit equivalence scope")
        if (
            relation_type == LineageRelationType.SUPERSEDES.value
            and normalized_scope is None
        ):
            raise ValueError("SUPERSEDES requires an explicit replacement scope")
        normalized_mapping = None
        if component_mapping is not None:
            if not isinstance(component_mapping, Mapping):
                raise ValueError("component_mapping must be a mapping")
            normalized_mapping = dict(component_mapping)
            canonical_json(normalized_mapping)
        normalized_metadata: dict[str, Any] = {}
        if metadata is not None:
            if not isinstance(metadata, Mapping):
                raise ValueError("metadata must be a mapping")
            normalized_metadata = dict(metadata)
            canonical_json(normalized_metadata)
        normalized_created_by = None
        if created_by is not None:
            normalized_created_by = _required(created_by, field="created_by")
        identity = {
            "relation_type": relation_type,
            "upstream_ref": upstream_ref,
            "downstream_ref": downstream_ref,
            "scope": normalized_scope,
            "component_mapping": normalized_mapping,
            "evidence": normalized_evidence,
            "metadata": normalized_metadata,
            "created_by": normalized_created_by,
        }
        edge_id = canonical_extension_id("lineage", identity)
        recorded_at = _utc_now()
        payload = {
            "edge_id": edge_id,
            **identity,
            "recorded_at": recorded_at,
            "semantics": {
                "lineage_is_revision_pinned_when_refs_are_revision_pinned": True,
                "lineage_reference_identity_is_type_stable": True,
                "lineage_implies_advisory_applicability": False,
                "derivation_implies_equivalence": False,
                "equivalence_is_scope_limited": relation_type
                == LineageRelationType.EQUIVALENT_TO.value,
                "equivalence_is_symmetric_within_scope": relation_type
                == LineageRelationType.EQUIVALENT_TO.value,
                "supersedes_is_scope_limited": relation_type
                == LineageRelationType.SUPERSEDES.value,
                "supersedes_implies_semantic_equivalence": False,
                "canonical_extension_identity": True,
            },
        }
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT payload_json FROM testamur_lineage_edges WHERE edge_id=?",
                (edge_id,),
            ).fetchone()
            if existing is not None:
                return json.loads(str(existing["payload_json"]))
            conn.execute(
                "INSERT INTO testamur_lineage_edges(edge_id,relation_type,upstream_ref,downstream_ref,payload_json,recorded_at) VALUES(?,?,?,?,?,?)",
                (
                    edge_id,
                    relation_type,
                    upstream_ref,
                    downstream_ref,
                    canonical_json(payload),
                    recorded_at,
                ),
            )
        return payload

    def get_lineage(self, edge_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM testamur_lineage_edges WHERE edge_id=?",
                (str(edge_id),),
            ).fetchone()
        return None if row is None else json.loads(str(row["payload_json"]))

    def edges_for(
        self,
        ref: str,
        *,
        direction: str = "both",
        relation_filter: Iterable[str | LineageRelationType] | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        target = _required(ref, field="ref")
        if direction not in {"incoming", "outgoing", "both"}:
            raise ValueError("direction must be incoming, outgoing, or both")
        selected = None
        if relation_filter is not None:
            selected = {_normalize_relation(item) for item in relation_filter}
            if not selected:
                return []
        bounded = max(1, min(int(limit), 5001))
        clauses: list[str] = []
        params: list[Any] = []
        if direction in {"outgoing", "both"}:
            clauses.append("upstream_ref=?")
            params.append(target)
        if direction in {"incoming", "both"}:
            clauses.append("downstream_ref=?")
            params.append(target)
        where = "(" + " OR ".join(clauses) + ")"
        if selected is not None:
            placeholders = ",".join("?" for _ in selected)
            where += f" AND relation_type IN ({placeholders})"
            params.extend(sorted(selected))
        params.append(bounded)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT payload_json FROM testamur_lineage_edges WHERE {where} ORDER BY relation_type, upstream_ref, downstream_ref, edge_id LIMIT ?",
                params,
            ).fetchall()
        return [json.loads(str(row["payload_json"])) for row in rows]

    def _traversal_edges(
        self, node: str, selected: set[str]
    ) -> tuple[list[tuple[dict[str, Any], str, bool]], bool]:
        """Return material-successor edges and whether more than 5000 exist."""

        result: list[tuple[dict[str, Any], str, bool]] = []
        outgoing = self.edges_for(
            node,
            direction="outgoing",
            relation_filter=selected,
            limit=5001,
        )
        outgoing_capped = len(outgoing) > 5000
        for edge in outgoing[:5000]:
            result.append((edge, str(edge["downstream_ref"]), False))
        maybe_capped = outgoing_capped
        if LineageRelationType.EQUIVALENT_TO.value in selected:
            incoming = self.edges_for(
                node,
                direction="incoming",
                relation_filter=[LineageRelationType.EQUIVALENT_TO],
                limit=5001,
            )
            incoming_capped = len(incoming) > 5000
            maybe_capped = maybe_capped or incoming_capped
            for edge in incoming[:5000]:
                result.append((edge, str(edge["upstream_ref"]), True))
        result.sort(
            key=lambda item: (
                item[0]["relation_type"],
                item[1],
                item[0]["edge_id"],
                item[2],
            )
        )
        return result, maybe_capped

    def lineage_walk(
        self,
        upstream_revision: str,
        downstream_revision: str | None = None,
        *,
        relation_filter: Iterable[str | LineageRelationType] | None = None,
        max_depth: int = 16,
        max_paths: int = 1000,
    ) -> dict[str, Any]:
        """Return bounded material-lineage paths plus explicit completion metadata.

        This is the explicit exploratory traversal surface: callers may inspect
        partial paths together with ``traversal.complete`` and truncation reasons.
        A resource bound is never evidence that no additional descendant or path
        exists.
        """

        origin = _required(upstream_revision, field="upstream_revision")
        destination = (
            None
            if downstream_revision is None
            else _required(downstream_revision, field="downstream_revision")
        )
        depth_limit = max(1, min(int(max_depth), 64))
        path_limit = max(1, min(int(max_paths), 10000))
        selected = (
            set(AFFECTEDNESS_PROPAGATING_RELATIONS)
            if relation_filter is None
            else {_normalize_relation(item) for item in relation_filter}
        )
        if not selected:
            return {
                "paths": [],
                "traversal": {
                    "origin": origin,
                    "destination": destination,
                    "complete": True,
                    "truncated": False,
                    "truncation_reasons": [],
                    "max_depth": depth_limit,
                    "max_paths": path_limit,
                    "expansions": 0,
                    "returned_paths": 0,
                    "selected_relations": [],
                },
            }

        expansion_limit = max(64, min(100000, path_limit * depth_limit * 4))
        expansions = 0
        truncation_reasons: set[str] = set()
        queue: deque[
            tuple[
                str,
                tuple[dict[str, Any], ...],
                frozenset[str],
                str | None,
                dict[str, Any] | None,
            ]
        ] = deque([(origin, (), frozenset({origin}), None, None)])
        results: list[dict[str, Any]] = []

        while queue:
            if "max_paths" in truncation_reasons:
                break
            if expansions >= expansion_limit:
                truncation_reasons.add("expansion_limit")
                break

            node, path, seen, equivalence_scope_key, equivalence_scope = queue.popleft()
            if len(path) >= depth_limit:
                remaining_edges, maybe_capped = self._traversal_edges(node, selected)
                if remaining_edges:
                    truncation_reasons.add("max_depth")
                if maybe_capped:
                    truncation_reasons.add("per_node_edge_limit")
                continue

            traversal_edges, maybe_capped = self._traversal_edges(node, selected)
            if maybe_capped:
                truncation_reasons.add("per_node_edge_limit")
            for edge, next_ref, reversed_equivalence in traversal_edges:
                if expansions >= expansion_limit:
                    truncation_reasons.add("expansion_limit")
                    break
                expansions += 1
                if next_ref in seen:
                    continue
                next_scope_key = equivalence_scope_key
                next_scope = equivalence_scope
                if edge["relation_type"] == LineageRelationType.EQUIVALENT_TO.value:
                    edge_scope = edge.get("scope")
                    if not isinstance(edge_scope, Mapping) or not edge_scope:
                        continue
                    edge_scope_dict = dict(edge_scope)
                    edge_scope_key = canonical_json(edge_scope_dict)
                    if (
                        equivalence_scope_key is not None
                        and edge_scope_key != equivalence_scope_key
                    ):
                        continue
                    next_scope_key = edge_scope_key
                    next_scope = edge_scope_dict

                traversed_edge = dict(edge)
                traversed_edge["traversal_reversed"] = reversed_equivalence
                next_path = (*path, traversed_edge)
                if destination is None or next_ref == destination:
                    results.append(
                        {
                            "upstream_ref": origin,
                            "downstream_ref": next_ref,
                            "length": len(next_path),
                            "edge_ids": [item["edge_id"] for item in next_path],
                            "edges": list(next_path),
                            "equivalence_scope": next_scope,
                            "semantics": {
                                "candidate_material_path": True,
                                "affectedness_verdict_implied": False,
                                "bounded_traversal": True,
                                "scoped_equivalence_symmetric": True,
                                "equivalence_scope_chaining_requires_exact_match": True,
                            },
                        }
                    )
                    if len(results) > path_limit:
                        truncation_reasons.add("max_paths")
                        break
                if destination is None or next_ref != destination:
                    queue.append(
                        (
                            next_ref,
                            next_path,
                            seen | {next_ref},
                            next_scope_key,
                            next_scope,
                        )
                    )

        complete = not truncation_reasons
        reasons = sorted(truncation_reasons)
        returned_results = results[:path_limit]
        returned_results.sort(
            key=lambda item: (
                int(item["length"]),
                str(item["downstream_ref"]),
                tuple(item["edge_ids"]),
                tuple(
                    bool(edge.get("traversal_reversed")) for edge in item["edges"]
                ),
            )
        )
        for item in returned_results:
            item["semantics"]["traversal_complete"] = complete
            item["semantics"]["truncation_reasons"] = reasons
        return {
            "paths": returned_results,
            "traversal": {
                "origin": origin,
                "destination": destination,
                "complete": complete,
                "truncated": not complete,
                "truncation_reasons": reasons,
                "max_depth": depth_limit,
                "max_paths": path_limit,
                "expansion_limit": expansion_limit,
                "expansions": expansions,
                "returned_paths": len(returned_results),
                "selected_relations": sorted(selected),
                "resource_bound_implies_no_more_lineage": False,
            },
        }

    def lineage_paths(
        self,
        upstream_revision: str,
        downstream_revision: str | None = None,
        *,
        relation_filter: Iterable[str | LineageRelationType] | None = None,
        max_depth: int = 16,
        max_paths: int = 1000,
        allow_partial: bool = False,
    ) -> list[dict[str, Any]]:
        """Return material-lineage paths, failing closed on incomplete traversal.

        Use ``lineage_walk`` when bounded partial results are intentionally useful.
        Decision-oriented callers should keep the default ``allow_partial=False``;
        otherwise a resource bound could be misread as evidence that no additional
        descendant exists.
        """

        report = self.lineage_walk(
            upstream_revision,
            downstream_revision,
            relation_filter=relation_filter,
            max_depth=max_depth,
            max_paths=max_paths,
        )
        traversal = report["traversal"]
        if not bool(traversal.get("complete")) and not allow_partial:
            raise IncompleteLineageTraversalError(traversal)
        return report["paths"]

    def descendant_refs(
        self,
        upstream_revision: str,
        *,
        relation_filter: Iterable[str | LineageRelationType] | None = None,
        max_depth: int = 16,
        max_paths: int = 1000,
        allow_partial: bool = False,
    ) -> list[str]:
        paths = self.lineage_paths(
            upstream_revision,
            relation_filter=relation_filter,
            max_depth=max_depth,
            max_paths=max_paths,
            allow_partial=allow_partial,
        )
        return sorted({str(path["downstream_ref"]) for path in paths})

    def stats(self) -> dict[str, int]:
        with self.connect() as conn:
            total = int(
                conn.execute("SELECT COUNT(*) FROM testamur_lineage_edges").fetchone()[0]
            )
        return {"lineage_edges": total}
