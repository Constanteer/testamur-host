from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from build_selection import ACTIVE_ARGUMENT_BUILD_INDEX, ensure_active_argument_build_index
from catalog import Catalog, PATH_DEPTH
from dependency_index import PROOF_DEPENDENCY_ROUTE_INDEX, ensure_proof_dependency_route_index
from formal_binding_index import FORMAL_BINDING_NAME_INDEX, ensure_formal_binding_name_index
from registry import build_ref, claim_ref, parse_ref, proof_ref, version_ref


GRAPH_PATH_SCAN_BUDGET = 400
GRAPH_PATH_BATCH_SIZE = 100
GRAPH_PATH_EXECUTION = "batched-route-exists-v1"
GRAPH_QUERY_OWNED_INDEXES = ("idx_graph_proofs_version",)
GRAPH_QUERY_REQUIRED_INDEXES = (
    *GRAPH_QUERY_OWNED_INDEXES,
    ACTIVE_ARGUMENT_BUILD_INDEX,
    PROOF_DEPENDENCY_ROUTE_INDEX,
    FORMAL_BINDING_NAME_INDEX,
)
# Backward-compatible public name: these are indexes required by graph-query
# execution, not indexes whose lifecycle is owned by GraphQueryCatalog.
GRAPH_QUERY_INDEXES = GRAPH_QUERY_REQUIRED_INDEXES


class GraphQueryCatalog(Catalog):
    """Canonical proof-aware graph-query catalog.

    This class owns the scoped path grammar, bounded catalog execution, exact
    lazy path witness semantics, and its proof-version query index. Shared Build,
    ProofDependency, and FormalBinding indexes are installed only through their
    storage-owner helpers; mathematical graph truth still comes exclusively from
    ``proof_claim_dependencies`` rows.

    Path clause grammar::

        path:2:C [via:B] [route-through:X ...]

    ``via:`` binds the actual middle Claim. Every ``route-through:`` selector is
    bound to the same source Proof / active Build that supplies hop 1.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.ensure_query_indexes()

    def ensure_query_indexes(self) -> None:
        """Ensure graph-query access paths without duplicating shared index DDL.

        ``build_selection``, ``ArgumentIndex``/``dependency_index``, and
        ``FormalIndex``/``formal_binding_index`` remain the lifecycle owners of
        their tables' shared indexes. GraphQueryCatalog merely delegates to those
        helpers and defines only ``idx_graph_proofs_version`` itself. Missing
        optional owner tables are skipped by the helpers, keeping partial test
        databases valid.
        """
        with self.connect() as conn:
            ensure_active_argument_build_index(conn)
            ensure_proof_dependency_route_index(conn)
            ensure_formal_binding_name_index(conn)
            if self._table_exists(conn, "proofs"):
                conn.execute(
                    """CREATE INDEX IF NOT EXISTS idx_graph_proofs_version
                       ON proofs(claim_version_id,id)"""
                )

    @classmethod
    def parse_query(cls, raw: str) -> dict[str, Any]:
        raw = str(raw or "").strip()[:760]
        try:
            tokens = shlex.split(raw)
        except ValueError as exc:
            raise ValueError(f"invalid search query: {exc}") from exc

        ordinary: list[str] = []
        paths: list[dict[str, Any]] = []
        active_path: int | None = None
        previous_kind = ""

        for token in tokens:
            lower = token.lower()
            if lower.startswith("path:"):
                paths.append({**cls._path_selector(token), "route_through": []})
                active_path = len(paths) - 1
                previous_kind = "path"
                continue

            if lower.startswith("via:"):
                if active_path is None or previous_kind != "path":
                    raise ValueError("via filter must immediately follow the path:2: clause it constrains")
                via = cls._canonical_graph_value(token.split(":", 1)[1], "via")
                if paths[active_path].get("via"):
                    raise ValueError("use at most one via filter for each path:2: clause")
                paths[active_path]["via"] = via
                previous_kind = "via"
                continue

            if lower.startswith("route-through:"):
                if active_path is None or previous_kind not in {"path", "via", "route-through"}:
                    raise ValueError(
                        "route-through filter must immediately continue the path:2: clause it constrains"
                    )
                selector = cls._canonical_graph_value(
                    token.split(":", 1)[1], "route-through"
                )
                route_through = paths[active_path]["route_through"]
                if selector not in route_through:
                    route_through.append(selector)
                if len(route_through) > 8:
                    raise ValueError("use at most 8 route-through filters for each path:2: clause")
                previous_kind = "route-through"
                continue

            ordinary.append(token)
            active_path = None
            previous_kind = ""

        base = super().parse_query(shlex.join(ordinary))
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[int, str, str, tuple[str, ...]]] = set()
        for path in paths:
            route_through = tuple(str(x) for x in path.get("route_through") or [])
            key = (
                int(path["depth"]),
                str(path["target"]),
                str(path.get("via") or ""),
                route_through,
            )
            if key in seen:
                continue
            seen.add(key)
            normalized: dict[str, Any] = {
                "depth": int(path["depth"]),
                "target": str(path["target"]),
            }
            if path.get("via"):
                normalized["via"] = str(path["via"])
            if route_through:
                normalized["route_through"] = list(route_through)
            deduped.append(normalized)

        base["raw"] = raw
        base["paths"] = deduped[:4]
        return base

    @staticmethod
    def _route_through_condition(
        *,
        proof_alias: str,
        build_expr: str,
        selector: str,
        prefix: str,
        formal_binding_available: bool,
    ) -> tuple[str, list[Any]] | None:
        dep = f"{prefix}d"
        if re.fullmatch(r"CLM-\d+", selector, re.IGNORECASE):
            return (
                f"""EXISTS(
                  SELECT 1 FROM proof_claim_dependencies {dep}
                  WHERE {dep}.proof_id={proof_alias}.id
                    AND {dep}.source_build_id={build_expr}
                    AND {dep}.target_claim_id=?
                    AND {dep}.target_claim_version_id=(
                      SELECT cv.id FROM claim_versions cv
                      WHERE cv.claim_id={dep}.target_claim_id
                      ORDER BY cv.version_no DESC LIMIT 1
                    )
                )""",
                [parse_ref(selector, "CLM")],
            )
        if not formal_binding_available:
            return None
        fb = f"{prefix}fb"
        return (
            f"""EXISTS(
              SELECT 1 FROM proof_claim_dependencies {dep}
              JOIN formal_bindings {fb}
                ON {fb}.claim_id={dep}.target_claim_id
               AND {fb}.claim_version_id={dep}.target_claim_version_id
               AND {fb}.environment_hash={dep}.environment_hash
               AND {fb}.lean_name=?
              WHERE {dep}.proof_id={proof_alias}.id
                AND {dep}.source_build_id={build_expr}
                AND {dep}.target_claim_version_id=(
                  SELECT cv.id FROM claim_versions cv
                  WHERE cv.claim_id={dep}.target_claim_id
                  ORDER BY cv.version_no DESC LIMIT 1
                )
            )""",
            [selector],
        )

    def _matching_constrained_candidate_ids(
        self,
        claim_ids: list[int],
        constrained: list[dict[str, Any]],
    ) -> set[int]:
        if not claim_ids or not constrained:
            return set(claim_ids)

        with self.connect() as conn:
            dependency_available = self._table_exists(conn, "proof_claim_dependencies")
            formal_binding_available = self._table_exists(conn, "formal_bindings")
            if not dependency_available:
                return set()

            candidate_values = ",".join("(?)" for _ in claim_ids)
            clauses: list[str] = []
            args: list[Any] = [*claim_ids]

            for index, path in enumerate(constrained):
                target = self._canonical_graph_value(str(path["target"]), "path")
                via = str(path.get("via") or "")
                route_through = [
                    self._canonical_graph_value(str(value), "route-through")
                    for value in path.get("route_through") or []
                ]
                p1 = f"gp1_{index}"
                d1 = f"gd1_{index}"
                p2 = f"gp2_{index}"
                d2 = f"gd2_{index}"
                active_source_build = self._active_argument_build_sql(p1)
                active_middle_build = self._active_argument_build_sql(p2)
                joins: list[str] = []
                join_args: list[Any] = []
                where: list[str] = []
                where_args: list[Any] = []

                if via:
                    via = self._canonical_graph_value(via, "via")
                    if re.fullmatch(r"CLM-\d+", via, re.IGNORECASE):
                        where.append(f"{d1}.target_claim_id=?")
                        where_args.append(parse_ref(via, "CLM"))
                    else:
                        if not formal_binding_available:
                            return set()
                        vfb = f"gvfb_{index}"
                        joins.append(
                            f"""JOIN formal_bindings {vfb}
                                   ON {vfb}.claim_id={d1}.target_claim_id
                                  AND {vfb}.claim_version_id={d1}.target_claim_version_id
                                  AND {vfb}.environment_hash={d1}.environment_hash
                                  AND {vfb}.lean_name=?"""
                        )
                        join_args.append(via)

                if re.fullmatch(r"CLM-\d+", target, re.IGNORECASE):
                    where.append(f"{d2}.target_claim_id=?")
                    where_args.append(parse_ref(target, "CLM"))
                else:
                    if not formal_binding_available:
                        return set()
                    tfb = f"gtfb_{index}"
                    joins.append(
                        f"""JOIN formal_bindings {tfb}
                               ON {tfb}.claim_id={d2}.target_claim_id
                              AND {tfb}.claim_version_id={d2}.target_claim_version_id
                              AND {tfb}.environment_hash={d2}.environment_hash
                              AND {tfb}.lean_name=?"""
                    )
                    join_args.append(target)

                route_conditions: list[str] = []
                route_args: list[Any] = []
                for rindex, selector in enumerate(route_through):
                    condition = self._route_through_condition(
                        proof_alias=p1,
                        build_expr=f"{d1}.source_build_id",
                        selector=selector,
                        prefix=f"gr_{index}_{rindex}_",
                        formal_binding_available=formal_binding_available,
                    )
                    if condition is None:
                        return set()
                    route_sql, route_values = condition
                    route_conditions.append(route_sql)
                    route_args.extend(route_values)

                extra_where = "" if not where else " AND " + " AND ".join(where)
                extra_route = "" if not route_conditions else " AND " + " AND ".join(route_conditions)
                clauses.append(
                    f"""EXISTS(
                      SELECT 1
                      FROM proofs {p1}
                      JOIN proof_claim_dependencies {d1}
                        ON {d1}.proof_id={p1}.id
                       AND {d1}.source_build_id={active_source_build}
                      JOIN proofs {p2}
                        ON {p2}.claim_id={d1}.target_claim_id
                       AND {p2}.claim_version_id={d1}.target_claim_version_id
                      JOIN proof_claim_dependencies {d2}
                        ON {d2}.proof_id={p2}.id
                       AND {d2}.source_build_id={active_middle_build}
                      {' '.join(joins)}
                      WHERE {p1}.claim_id=c.source_claim_id
                        AND {p1}.claim_version_id=(
                          SELECT scv.id FROM claim_versions scv
                          WHERE scv.claim_id={p1}.claim_id
                          ORDER BY scv.version_no DESC LIMIT 1
                        )
                        AND {d1}.target_claim_version_id=(
                          SELECT mcv.id FROM claim_versions mcv
                          WHERE mcv.claim_id={d1}.target_claim_id
                          ORDER BY mcv.version_no DESC LIMIT 1
                        )
                        AND {d2}.target_claim_version_id=(
                          SELECT tcv.id FROM claim_versions tcv
                          WHERE tcv.claim_id={d2}.target_claim_id
                          ORDER BY tcv.version_no DESC LIMIT 1
                        )
                        {extra_where}
                        {extra_route}
                    )"""
                )
                args.extend(join_args)
                args.extend(where_args)
                args.extend(route_args)

            sql = f"""WITH candidates(source_claim_id) AS (
                         VALUES {candidate_values}
                       )
                       SELECT c.source_claim_id
                       FROM candidates c
                       WHERE {' AND '.join(clauses)}"""
            rows = conn.execute(sql, args).fetchall()
            return {int(row[0]) for row in rows}

    def _matching_via_candidate_ids(
        self,
        claim_ids: list[int],
        constrained: list[dict[str, Any]],
    ) -> set[int]:
        return self._matching_constrained_candidate_ids(claim_ids, constrained)

    def page(
        self,
        *,
        limit: int = 40,
        before_internal_id: int | None = None,
        query: str = "",
        status: str = "all",
    ) -> dict[str, Any]:
        parsed = self.parse_query(query)
        constrained = [
            path for path in parsed["paths"]
            if path.get("via") or path.get("route_through")
        ]
        if not constrained:
            return super().page(
                limit=limit,
                before_internal_id=before_internal_id,
                query=query,
                status=status,
            )

        requested = max(1, min(int(limit), 100))
        cursor = before_internal_id
        matches: list[dict[str, Any]] = []
        scanned = 0
        last_scanned_ref: str | None = None
        exhausted = False
        metadata: dict[str, Any] | None = None
        batch_queries = 0

        while len(matches) < requested + 1 and scanned < GRAPH_PATH_SCAN_BUDGET:
            batch = super().page(
                limit=GRAPH_PATH_BATCH_SIZE,
                before_internal_id=cursor,
                query=query,
                status=status,
            )
            if metadata is None:
                metadata = dict(batch)
            claims = list(batch.get("claims") or [])
            if not claims:
                exhausted = not bool(batch.get("has_more"))
                break

            candidate_ids = [parse_ref(str(item["claim_id"]), "CLM") for item in claims]
            matching_ids = self._matching_constrained_candidate_ids(candidate_ids, constrained)
            batch_queries += 1
            consumed = 0
            for item, source_claim_id in zip(claims, candidate_ids):
                consumed += 1
                last_scanned_ref = str(item["claim_id"])
                scanned += 1
                if source_claim_id in matching_ids:
                    matches.append(item)
                    if len(matches) >= requested + 1:
                        break
                if scanned >= GRAPH_PATH_SCAN_BUDGET:
                    break

            if consumed == len(claims) and not batch.get("has_more"):
                exhausted = True
            if len(matches) >= requested + 1 or scanned >= GRAPH_PATH_SCAN_BUDGET:
                break
            if exhausted:
                break
            next_cursor = str(batch.get("next_cursor") or "")
            if not next_cursor:
                exhausted = True
                break
            cursor = parse_ref(next_cursor, "CLM")

        if metadata is None:
            metadata = super().page(
                limit=1,
                before_internal_id=before_internal_id,
                query=query,
                status=status,
            )

        visible = matches[:requested]
        more_matches = len(matches) > requested
        scan_truncated = scanned >= GRAPH_PATH_SCAN_BUDGET and not exhausted and not more_matches
        has_more = more_matches or scan_truncated
        if more_matches:
            next_cursor = str(visible[-1]["claim_id"]) if visible else last_scanned_ref
        elif scan_truncated:
            next_cursor = last_scanned_ref
        else:
            next_cursor = None

        metadata["claims"] = visible
        metadata["page_size"] = len(visible)
        metadata["has_more"] = has_more
        metadata["next_cursor"] = next_cursor
        metadata["query"] = parsed["raw"]
        metadata.setdefault("structural_filters", {})["paths"] = parsed["paths"]
        metadata["path_via_search_available"] = bool(metadata.get("path_search_available"))
        metadata["path_via_lean_name_search_available"] = bool(metadata.get("path_lean_name_search_available"))
        metadata["path_route_search_available"] = bool(metadata.get("path_search_available"))
        metadata["path_via_scan_budget"] = GRAPH_PATH_SCAN_BUDGET
        metadata["path_via_scan_truncated"] = scan_truncated
        metadata["path_via_execution"] = GRAPH_PATH_EXECUTION
        metadata["path_via_candidate_batches"] = batch_queries
        metadata["graph_path_execution"] = GRAPH_PATH_EXECUTION
        metadata["graph_path_candidate_batches"] = batch_queries
        return metadata

    def _route_through_witness(
        self,
        *,
        proof_id: int,
        build_id: int,
        selector: str,
    ) -> dict[str, Any] | None:
        selector = self._canonical_graph_value(selector, "route-through")
        with self.connect() as conn:
            if re.fullmatch(r"CLM-\d+", selector, re.IGNORECASE):
                row = conn.execute(
                    """SELECT d.target_claim_id,d.target_claim_version_id,d.evidence_constant,d.environment_hash,
                              cv.title,cv.lean_name
                       FROM proof_claim_dependencies d
                       JOIN claim_versions cv ON cv.id=d.target_claim_version_id
                       WHERE d.proof_id=? AND d.source_build_id=? AND d.target_claim_id=?
                         AND d.target_claim_version_id=(
                           SELECT current.id FROM claim_versions current
                           WHERE current.claim_id=d.target_claim_id
                           ORDER BY current.version_no DESC LIMIT 1
                         )
                       ORDER BY d.id DESC LIMIT 1""",
                    (proof_id, build_id, parse_ref(selector, "CLM")),
                ).fetchone()
            else:
                if not self._table_exists(conn, "formal_bindings"):
                    return None
                row = conn.execute(
                    """SELECT d.target_claim_id,d.target_claim_version_id,d.evidence_constant,d.environment_hash,
                              cv.title,cv.lean_name
                       FROM proof_claim_dependencies d
                       JOIN formal_bindings fb
                         ON fb.claim_id=d.target_claim_id
                        AND fb.claim_version_id=d.target_claim_version_id
                        AND fb.environment_hash=d.environment_hash
                        AND fb.lean_name=?
                       JOIN claim_versions cv ON cv.id=d.target_claim_version_id
                       WHERE d.proof_id=? AND d.source_build_id=?
                         AND d.target_claim_version_id=(
                           SELECT current.id FROM claim_versions current
                           WHERE current.claim_id=d.target_claim_id
                           ORDER BY current.version_no DESC LIMIT 1
                         )
                       ORDER BY d.id DESC LIMIT 1""",
                    (selector, proof_id, build_id),
                ).fetchone()
        if row is None:
            return None
        return {
            "selector": selector,
            "target_claim_id": claim_ref(int(row["target_claim_id"])),
            "target_claim_version_id": version_ref(int(row["target_claim_version_id"])),
            "target_title": row["title"],
            "target_lean_name": row["lean_name"],
            "evidence_constant": row["evidence_constant"],
            "environment_hash": row["environment_hash"],
        }

    def path_witness(
        self,
        source_claim_id: int,
        *,
        depth: int,
        target: str,
        via: str | None = None,
        route_through: list[str] | None = None,
    ) -> dict[str, Any]:
        selectors = list(
            dict.fromkeys(
                self._canonical_graph_value(str(value), "route-through")
                for value in (route_through or [])
            )
        )[:8]
        if not via and not selectors:
            return super().path_witness(source_claim_id, depth=depth, target=target)
        if int(depth) != PATH_DEPTH:
            raise ValueError("path witness currently supports exact depth 2 only")

        target_selector = self._canonical_graph_value(target, "path")
        via_selector = self._canonical_graph_value(via, "via") if via else ""

        with self.connect() as conn:
            dependency_available = self._table_exists(conn, "proof_claim_dependencies")
            formal_binding_available = self._table_exists(conn, "formal_bindings")
            named = [target_selector, via_selector, *selectors]
            needs_binding = any(
                value and not re.fullmatch(r"CLM-\d+", value, re.IGNORECASE)
                for value in named
            )
            if not dependency_available or (needs_binding and not formal_binding_available):
                return {
                    "available": False,
                    "matched": False,
                    "depth": PATH_DEPTH,
                    "source_claim_id": claim_ref(source_claim_id),
                    "target_selector": target_selector,
                    "via_selector": via_selector or None,
                    "route_through_selectors": selectors,
                    "steps": [],
                    "source_route_constraints": [],
                }

            active_source_build = self._active_argument_build_sql("p1")
            active_middle_build = self._active_argument_build_sql("p2")
            joins: list[str] = []
            join_args: list[Any] = []
            where: list[str] = []
            where_args: list[Any] = []

            if via_selector:
                if re.fullmatch(r"CLM-\d+", via_selector, re.IGNORECASE):
                    where.append("d1.target_claim_id=?")
                    where_args.append(parse_ref(via_selector, "CLM"))
                else:
                    joins.append(
                        """JOIN formal_bindings vfb
                             ON vfb.claim_id=d1.target_claim_id
                            AND vfb.claim_version_id=d1.target_claim_version_id
                            AND vfb.environment_hash=d1.environment_hash
                            AND vfb.lean_name=?"""
                    )
                    join_args.append(via_selector)

            if re.fullmatch(r"CLM-\d+", target_selector, re.IGNORECASE):
                where.append("d2.target_claim_id=?")
                where_args.append(parse_ref(target_selector, "CLM"))
            else:
                joins.append(
                    """JOIN formal_bindings tfb
                         ON tfb.claim_id=d2.target_claim_id
                        AND tfb.claim_version_id=d2.target_claim_version_id
                        AND tfb.environment_hash=d2.environment_hash
                        AND tfb.lean_name=?"""
                )
                join_args.append(target_selector)

            route_conditions: list[str] = []
            route_args: list[Any] = []
            for index, selector in enumerate(selectors):
                condition = self._route_through_condition(
                    proof_alias="p1",
                    build_expr="d1.source_build_id",
                    selector=selector,
                    prefix=f"wrt_{index}_",
                    formal_binding_available=formal_binding_available,
                )
                if condition is None:
                    return {
                        "available": False,
                        "matched": False,
                        "depth": PATH_DEPTH,
                        "source_claim_id": claim_ref(source_claim_id),
                        "target_selector": target_selector,
                        "via_selector": via_selector or None,
                        "route_through_selectors": selectors,
                        "steps": [],
                        "source_route_constraints": [],
                    }
                route_sql, values = condition
                route_conditions.append(route_sql)
                route_args.extend(values)

            extra_where = "" if not where else " AND " + " AND ".join(where)
            extra_route = "" if not route_conditions else " AND " + " AND ".join(route_conditions)
            row = conn.execute(
                f"""SELECT
                       p1.claim_id source_claim_id,
                       p1.id source_proof_id,
                       d1.source_build_id source_build_id,
                       d1.target_claim_id middle_claim_id,
                       d1.target_claim_version_id middle_version_id,
                       d1.evidence_constant first_evidence,
                       mv.title middle_title,
                       mv.lean_name middle_lean_name,
                       p2.id middle_proof_id,
                       d2.source_build_id middle_build_id,
                       d2.target_claim_id target_claim_id,
                       d2.target_claim_version_id target_version_id,
                       d2.evidence_constant second_evidence,
                       tv.title target_title,
                       tv.lean_name target_lean_name
                     FROM proofs p1
                     JOIN proof_claim_dependencies d1
                       ON d1.proof_id=p1.id
                      AND d1.source_build_id={active_source_build}
                     JOIN claim_versions mv ON mv.id=d1.target_claim_version_id
                     JOIN proofs p2
                       ON p2.claim_id=d1.target_claim_id
                      AND p2.claim_version_id=d1.target_claim_version_id
                     JOIN proof_claim_dependencies d2
                       ON d2.proof_id=p2.id
                      AND d2.source_build_id={active_middle_build}
                     JOIN claim_versions tv ON tv.id=d2.target_claim_version_id
                     {' '.join(joins)}
                     WHERE p1.claim_id=?
                       AND p1.claim_version_id=(
                         SELECT scv.id FROM claim_versions scv
                         WHERE scv.claim_id=p1.claim_id
                         ORDER BY scv.version_no DESC LIMIT 1
                       )
                       AND d1.target_claim_version_id=(
                         SELECT mcv.id FROM claim_versions mcv
                         WHERE mcv.claim_id=d1.target_claim_id
                         ORDER BY mcv.version_no DESC LIMIT 1
                       )
                       AND d2.target_claim_version_id=(
                         SELECT tcv.id FROM claim_versions tcv
                         WHERE tcv.claim_id=d2.target_claim_id
                         ORDER BY tcv.version_no DESC LIMIT 1
                       )
                       {extra_where}
                       {extra_route}
                     ORDER BY p1.id DESC,d1.id DESC,p2.id DESC,d2.id DESC
                     LIMIT 1""",
                [*join_args, source_claim_id, *where_args, *route_args],
            ).fetchone()

        result: dict[str, Any] = {
            "available": True,
            "matched": row is not None,
            "depth": PATH_DEPTH,
            "source_claim_id": claim_ref(source_claim_id),
            "target_selector": target_selector,
            "via_selector": via_selector or None,
            "route_through_selectors": selectors,
            "steps": [],
            "source_route_constraints": [],
        }
        if row is None:
            return result

        source_proof_id = int(row["source_proof_id"])
        source_build_id = int(row["source_build_id"])
        constraints: list[dict[str, Any]] = []
        for selector in selectors:
            witness = self._route_through_witness(
                proof_id=source_proof_id,
                build_id=source_build_id,
                selector=selector,
            )
            if witness is None:
                return result
            constraints.append(witness)

        middle_claim_id = int(row["middle_claim_id"])
        result["middle_claim_id"] = claim_ref(middle_claim_id)
        result["middle_claim_version_id"] = version_ref(int(row["middle_version_id"]))
        if via_selector:
            result["via_claim_id"] = claim_ref(middle_claim_id)
            result["via_claim_version_id"] = version_ref(int(row["middle_version_id"]))
        result["target_claim_id"] = claim_ref(int(row["target_claim_id"]))
        result["target_claim_version_id"] = version_ref(int(row["target_version_id"]))
        result["target_title"] = row["target_title"]
        result["target_lean_name"] = row["target_lean_name"]
        result["source_route_constraints"] = constraints
        result["steps"] = [
            {
                "from_claim_id": claim_ref(int(row["source_claim_id"])),
                "proof_id": proof_ref(source_proof_id),
                "build_id": build_ref(source_build_id),
                "to_claim_id": claim_ref(middle_claim_id),
                "to_claim_version_id": version_ref(int(row["middle_version_id"])),
                "to_title": row["middle_title"],
                "to_lean_name": row["middle_lean_name"],
                "evidence_constant": row["first_evidence"],
            },
            {
                "from_claim_id": claim_ref(middle_claim_id),
                "proof_id": proof_ref(int(row["middle_proof_id"])),
                "build_id": build_ref(int(row["middle_build_id"])),
                "to_claim_id": claim_ref(int(row["target_claim_id"])),
                "to_claim_version_id": version_ref(int(row["target_version_id"])),
                "to_title": row["target_title"],
                "to_lean_name": row["target_lean_name"],
                "evidence_constant": row["second_evidence"],
            },
        ]
        return result
