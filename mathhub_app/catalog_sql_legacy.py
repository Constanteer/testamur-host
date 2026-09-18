from __future__ import annotations

import json
import re
import shlex
import sqlite3
from pathlib import Path
from typing import Any

from build_selection import active_argument_build_id_sql, ensure_active_argument_build_index
from formal_index import TYPE_PARTITION_VERSION, TYPE_SHAPE_VERSION
from registry import build_ref, claim_ref, parse_ref, proof_ref, version_ref
from trust import classify_axioms


ALLOWED_STATUSES = {"", "all", "open", "partial", "verified", "failed"}
PATH_DEPTH = 2


class Catalog:
    """Read-optimized latest-Claim index with cursor pagination.

    Claim status is scoped to the current Claim Version. Historical Builds never
    make a new Version verified. A current Version is verified if any of its Build
    records is verified; the corresponding Build is its verification witness.

    When FormalBinding data is available, imported external declaration names are
    searchable and become the catalog display Lean name. The stable MathHub alias
    remains available as ``formal_lean_name`` provenance.

    Structural filters are deterministic reads over recorded Lean/math graph evidence.
    Positive graph filters read reconciled current ProofDependency rows. Negative
    graph filters are deliberately stronger than absence of a mapped edge: they require
    complete raw proof-Expr evidence and exact environment-scoped FormalBindings before
    establishing that a route does not use a Claim.

    ``not-uses:X`` is Claim-level universal exclusion across every current Proof route.
    ``route-uses:X`` and ``route-not-uses:Y`` are grouped under one existential Proof
    quantifier, so all route-scoped clauses must be witnessed by the same current Proof.
    All source-Proof-scoped filters use the active complete argument Build on the
    current Claim revision.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        with self.connect() as conn:
            ensure_active_argument_build_index(conn)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None

    @staticmethod
    def _external_name_sql(conn: sqlite3.Connection, version_alias: str) -> str:
        if not Catalog._table_exists(conn, "formal_bindings"):
            return "NULL"
        return f"""(
          SELECT fb.lean_name FROM formal_bindings fb
          WHERE fb.claim_version_id={version_alias}.id
            AND fb.origin='imported-declaration'
          ORDER BY fb.id DESC LIMIT 1
        )"""

    @staticmethod
    def _one_constant(token: str, label: str) -> str:
        value = token.split(":", 1)[1].strip()
        if not value or any(ch.isspace() for ch in value):
            raise ValueError(f"{label} filter requires one exact Lean constant name")
        return value

    @staticmethod
    def _canonical_graph_value(value: str, label: str) -> str:
        value = str(value or "").strip()
        if not value or any(ch.isspace() for ch in value):
            raise ValueError(f"{label} filter requires one Claim ref or exact Lean declaration name")
        if value.lower().startswith("clm-"):
            try:
                return claim_ref(parse_ref(value, "CLM"))
            except ValueError as exc:
                raise ValueError(f"{label} filter Claim identifiers must be CLM-<digits>") from exc
        return value

    @staticmethod
    def _graph_selector(token: str, label: str) -> str:
        return Catalog._canonical_graph_value(token.split(":", 1)[1], label)

    @staticmethod
    def _uses_selector(token: str) -> str:
        return Catalog._graph_selector(token, "uses")

    @staticmethod
    def _path_selector(token: str) -> dict[str, Any]:
        parts = token.split(":", 2)
        if len(parts) != 3 or not parts[1].isdigit():
            raise ValueError("path filter syntax is path:2:<Claim ref or exact Lean declaration name>")
        depth = int(parts[1])
        if depth != PATH_DEPTH:
            raise ValueError("path filter currently supports exact depth 2 only")
        target = Catalog._canonical_graph_value(parts[2], "path")
        return {"depth": depth, "target": target}

    @staticmethod
    def parse_query(raw: str) -> dict[str, Any]:
        raw = str(raw or "").strip()[:760]
        try:
            tokens = shlex.split(raw)
        except ValueError as exc:
            raise ValueError(f"invalid search query: {exc}") from exc
        namespace = ""
        head = ""
        binders: int | None = None
        statement_refs: list[str] = []
        proof_refs: list[str] = []
        uses: list[str] = []
        used_by: list[str] = []
        paths: list[dict[str, Any]] = []
        not_uses: list[str] = []
        route_uses: list[str] = []
        route_not_uses: list[str] = []
        binder_domains: list[str] = []
        result_refs: list[str] = []
        text: list[str] = []
        for token in tokens:
            lower = token.lower()
            if lower.startswith("namespace:"):
                value = token.split(":", 1)[1].strip()
                if not value or any(ch.isspace() for ch in value):
                    raise ValueError("namespace filter requires one Lean namespace prefix")
                if namespace and namespace != value:
                    raise ValueError("use at most one namespace filter")
                namespace = value
            elif lower.startswith("statement-ref:"):
                value = Catalog._one_constant(token, "statement-ref")
                if value not in statement_refs:
                    statement_refs.append(value)
            elif lower.startswith("proof-ref:"):
                value = Catalog._one_constant(token, "proof-ref")
                if value not in proof_refs:
                    proof_refs.append(value)
            elif lower.startswith("route-not-uses:"):
                value = Catalog._graph_selector(token, "route-not-uses")
                if value not in route_not_uses:
                    route_not_uses.append(value)
            elif lower.startswith("route-uses:"):
                value = Catalog._graph_selector(token, "route-uses")
                if value not in route_uses:
                    route_uses.append(value)
            elif lower.startswith("not-uses:"):
                value = Catalog._graph_selector(token, "not-uses")
                if value not in not_uses:
                    not_uses.append(value)
            elif lower.startswith("used-by:"):
                value = Catalog._graph_selector(token, "used-by")
                if value not in used_by:
                    used_by.append(value)
            elif lower.startswith("uses:"):
                value = Catalog._uses_selector(token)
                if value not in uses:
                    uses.append(value)
            elif lower.startswith("path:"):
                value = Catalog._path_selector(token)
                if value not in paths:
                    paths.append(value)
            elif lower.startswith("binder-domain:"):
                value = Catalog._one_constant(token, "binder-domain")
                if value not in binder_domains:
                    binder_domains.append(value)
            elif lower.startswith("result-ref:"):
                value = Catalog._one_constant(token, "result-ref")
                if value not in result_refs:
                    result_refs.append(value)
            elif lower.startswith("head:"):
                value = Catalog._one_constant(token, "head")
                if head and head != value:
                    raise ValueError("use at most one head filter")
                head = value
            elif lower.startswith("binders:"):
                value = token.split(":", 1)[1].strip()
                if not value or not value.isdigit():
                    raise ValueError("binders filter requires one non-negative integer")
                parsed = int(value)
                if binders is not None and binders != parsed:
                    raise ValueError("use at most one binders filter")
                binders = parsed
            else:
                text.append(token)
        return {
            "raw": raw,
            "text": " ".join(text).strip(),
            "namespace": namespace,
            "statement_refs": statement_refs[:8],
            "proof_refs": proof_refs[:8],
            "uses": uses[:8],
            "used_by": used_by[:8],
            "paths": paths[:4],
            "not_uses": not_uses[:8],
            "route_uses": route_uses[:8],
            "route_not_uses": route_not_uses[:8],
            "binder_domains": binder_domains[:8],
            "result_refs": result_refs[:8],
            "head": head,
            "binders": binders,
        }

    @staticmethod
    def _active_argument_build_sql(proof_alias: str) -> str:
        return active_argument_build_id_sql(proof_alias)

    @staticmethod
    def _negative_resolution_sql(build_alias: str, selector: str, prefix: str) -> tuple[str, list[Any]]:
        if re.fullmatch(r"CLM-\d+", selector, re.IGNORECASE):
            target_claim_id = parse_ref(selector, "CLM")
            return (
                f"""EXISTS(
                  SELECT 1 FROM formal_bindings {prefix}fb
                  WHERE {prefix}fb.claim_id=?
                    AND {prefix}fb.claim_version_id=(
                      SELECT cv.id FROM claim_versions cv
                      WHERE cv.claim_id={prefix}fb.claim_id
                      ORDER BY cv.version_no DESC LIMIT 1
                    )
                    AND {prefix}fb.environment_hash={build_alias}.environment_hash
                )""",
                [target_claim_id],
            )
        return (
            f"""EXISTS(
              SELECT 1 FROM formal_bindings {prefix}fb
              WHERE {prefix}fb.lean_name=?
                AND {prefix}fb.claim_version_id=(
                  SELECT cv.id FROM claim_versions cv
                  WHERE cv.claim_id={prefix}fb.claim_id
                  ORDER BY cv.version_no DESC LIMIT 1
                )
                AND {prefix}fb.environment_hash={build_alias}.environment_hash
            )""",
            [selector],
        )

    @staticmethod
    def _negative_raw_use_sql(build_alias: str, selector: str, prefix: str) -> tuple[str, list[Any]]:
        if re.fullmatch(r"CLM-\d+", selector, re.IGNORECASE):
            target_claim_id = parse_ref(selector, "CLM")
            return (
                f"""EXISTS(
                  SELECT 1 FROM build_constant_refs {prefix}cr
                  JOIN formal_bindings {prefix}rawfb
                    ON {prefix}rawfb.lean_name={prefix}cr.constant_name
                   AND {prefix}rawfb.environment_hash={build_alias}.environment_hash
                  WHERE {prefix}cr.build_id={build_alias}.id
                    AND {prefix}cr.ref_kind='proof'
                    AND {prefix}rawfb.claim_id=?
                    AND {prefix}rawfb.claim_version_id=(
                      SELECT cv.id FROM claim_versions cv
                      WHERE cv.claim_id={prefix}rawfb.claim_id
                      ORDER BY cv.version_no DESC LIMIT 1
                    )
                )""",
                [target_claim_id],
            )
        return (
            f"""EXISTS(
              SELECT 1 FROM build_constant_refs {prefix}cr
              JOIN formal_bindings {prefix}rawfb
                ON {prefix}rawfb.lean_name={prefix}cr.constant_name
               AND {prefix}rawfb.environment_hash={build_alias}.environment_hash
              JOIN formal_bindings {prefix}seedfb
                ON {prefix}seedfb.claim_id={prefix}rawfb.claim_id
               AND {prefix}seedfb.claim_version_id={prefix}rawfb.claim_version_id
               AND {prefix}seedfb.environment_hash={build_alias}.environment_hash
               AND {prefix}seedfb.lean_name=?
              WHERE {prefix}cr.build_id={build_alias}.id
                AND {prefix}cr.ref_kind='proof'
                AND {prefix}rawfb.claim_version_id=(
                  SELECT cv.id FROM claim_versions cv
                  WHERE cv.claim_id={prefix}rawfb.claim_id
                  ORDER BY cv.version_no DESC LIMIT 1
                )
            )""",
            [selector],
        )

    @staticmethod
    def _positive_route_dependency_sql(proof_alias: str, build_alias: str, selector: str, prefix: str) -> tuple[str, list[Any]]:
        if re.fullmatch(r"CLM-\d+", selector, re.IGNORECASE):
            target_claim_id = parse_ref(selector, "CLM")
            return (
                f"""EXISTS(
                  SELECT 1 FROM proof_claim_dependencies {prefix}d
                  WHERE {prefix}d.proof_id={proof_alias}.id
                    AND {prefix}d.source_build_id={build_alias}.id
                    AND {prefix}d.target_claim_id=?
                    AND {prefix}d.target_claim_version_id=(
                      SELECT cv.id FROM claim_versions cv
                      WHERE cv.claim_id={prefix}d.target_claim_id
                      ORDER BY cv.version_no DESC LIMIT 1
                    )
                )""",
                [target_claim_id],
            )
        return (
            f"""EXISTS(
              SELECT 1 FROM proof_claim_dependencies {prefix}d
              JOIN formal_bindings {prefix}fb
                ON {prefix}fb.claim_id={prefix}d.target_claim_id
               AND {prefix}fb.claim_version_id={prefix}d.target_claim_version_id
               AND {prefix}fb.environment_hash={prefix}d.environment_hash
               AND {prefix}fb.lean_name=?
              WHERE {prefix}d.proof_id={proof_alias}.id
                AND {prefix}d.source_build_id={build_alias}.id
                AND {prefix}d.target_claim_version_id=(
                  SELECT cv.id FROM claim_versions cv
                  WHERE cv.claim_id={prefix}d.target_claim_id
                  ORDER BY cv.version_no DESC LIMIT 1
                )
            )""",
            [selector],
        )

    def page(
        self,
        *,
        limit: int = 40,
        before_internal_id: int | None = None,
        query: str = "",
        status: str = "all",
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), 100))
        parsed = self.parse_query(query)
        status = str(status or "all").strip().lower()
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"unsupported Claim status filter: {status}")
        if before_internal_id is not None and before_internal_id <= 0:
            raise ValueError("cursor must resolve to a positive Claim key")

        text_query = str(parsed["text"])
        q_like = f"%{text_query.lower()}%"
        namespace = str(parsed["namespace"])
        statement_refs = list(parsed["statement_refs"])
        proof_refs = list(parsed["proof_refs"])
        uses = list(parsed["uses"])
        used_by = list(parsed["used_by"])
        paths = list(parsed["paths"])
        not_uses = list(parsed["not_uses"])
        route_uses = list(parsed["route_uses"])
        route_not_uses = list(parsed["route_not_uses"])
        binder_domains = list(parsed["binder_domains"])
        result_refs = list(parsed["result_refs"])
        head = str(parsed["head"])
        binders = parsed["binders"]

        with self.connect() as conn:
            external_name = self._external_name_sql(conn, "l")
            constant_ref_available = self._table_exists(conn, "build_constant_refs")
            statement_ref_available = constant_ref_available
            proof_ref_available = constant_ref_available
            dependency_available = self._table_exists(conn, "proof_claim_dependencies")
            formal_binding_available = self._table_exists(conn, "formal_bindings")
            uses_available = dependency_available
            uses_lean_name_available = dependency_available and formal_binding_available
            used_by_available = dependency_available
            used_by_lean_name_available = dependency_available and formal_binding_available
            path_available = dependency_available
            path_lean_name_available = dependency_available and formal_binding_available
            negative_available = dependency_available and formal_binding_available and constant_ref_available
            route_query_available = dependency_available
            route_uses_lean_name_available = dependency_available and formal_binding_available
            route_uses_need_binding = any(
                not re.fullmatch(r"CLM-\d+", selector, re.IGNORECASE)
                for selector in route_uses
            )
            route_negative_available = negative_available
            head_available = self._table_exists(conn, "formal_type_shapes")
            partition_available = (
                self._table_exists(conn, "formal_type_partitions")
                and self._table_exists(conn, "formal_type_partition_refs")
            )

            structural_clauses: list[str] = []
            structural_args: list[Any] = []
            if namespace:
                structural_clauses.append(
                    "(lower(COALESCE(indexed.external_lean_name,indexed.lean_name))=lower(?) "
                    "OR lower(COALESCE(indexed.external_lean_name,indexed.lean_name)) LIKE lower(?))"
                )
                structural_args.extend([namespace, namespace + ".%"])

            if statement_refs:
                if not statement_ref_available:
                    structural_clauses.append("0=1")
                else:
                    active_argument_build = self._active_argument_build_sql("sp")
                    for ref in statement_refs:
                        structural_clauses.append(
                            f"""EXISTS(
                              SELECT 1 FROM proofs sp
                              JOIN build_constant_refs sr
                                ON sr.build_id={active_argument_build}
                               AND sr.ref_kind='statement'
                              WHERE sp.claim_version_id=indexed.id
                                AND sr.constant_name=?
                            )"""
                        )
                        structural_args.append(ref)

            if proof_refs:
                if not proof_ref_available:
                    structural_clauses.append("0=1")
                else:
                    active_proof_build = self._active_argument_build_sql("pp")
                    for ref in proof_refs:
                        structural_clauses.append(
                            f"""EXISTS(
                              SELECT 1 FROM proofs pp
                              JOIN build_constant_refs pr
                                ON pr.build_id={active_proof_build}
                               AND pr.ref_kind='proof'
                              WHERE pp.claim_version_id=indexed.id
                                AND pr.constant_name=?
                            )"""
                        )
                        structural_args.append(ref)

            if uses:
                if not uses_available:
                    structural_clauses.append("0=1")
                else:
                    active_uses_build = self._active_argument_build_sql("up")
                    for selector in uses:
                        if re.fullmatch(r"CLM-\d+", selector, re.IGNORECASE):
                            target_claim_id = parse_ref(selector, "CLM")
                            structural_clauses.append(
                                f"""EXISTS(
                                  SELECT 1 FROM proofs up
                                  JOIN proof_claim_dependencies ud
                                    ON ud.proof_id=up.id
                                   AND ud.source_build_id={active_uses_build}
                                  WHERE up.claim_version_id=indexed.id
                                    AND ud.target_claim_id=?
                                    AND ud.target_claim_version_id=(
                                      SELECT ucv.id FROM claim_versions ucv
                                      WHERE ucv.claim_id=ud.target_claim_id
                                      ORDER BY ucv.version_no DESC LIMIT 1
                                    )
                                )"""
                            )
                            structural_args.append(target_claim_id)
                        elif not uses_lean_name_available:
                            structural_clauses.append("0=1")
                        else:
                            structural_clauses.append(
                                f"""EXISTS(
                                  SELECT 1 FROM proofs up
                                  JOIN proof_claim_dependencies ud
                                    ON ud.proof_id=up.id
                                   AND ud.source_build_id={active_uses_build}
                                  JOIN formal_bindings ufb
                                    ON ufb.claim_id=ud.target_claim_id
                                   AND ufb.claim_version_id=ud.target_claim_version_id
                                   AND ufb.environment_hash=ud.environment_hash
                                   AND ufb.lean_name=?
                                  WHERE up.claim_version_id=indexed.id
                                    AND ud.target_claim_version_id=(
                                      SELECT ucv.id FROM claim_versions ucv
                                      WHERE ucv.claim_id=ud.target_claim_id
                                      ORDER BY ucv.version_no DESC LIMIT 1
                                    )
                                )"""
                            )
                            structural_args.append(selector)

            if used_by:
                if not used_by_available:
                    structural_clauses.append("0=1")
                else:
                    active_used_by_build = self._active_argument_build_sql("ubp")
                    for selector in used_by:
                        if re.fullmatch(r"CLM-\d+", selector, re.IGNORECASE):
                            source_claim_id = parse_ref(selector, "CLM")
                            structural_clauses.append(
                                f"""EXISTS(
                                  SELECT 1 FROM proofs ubp
                                  JOIN proof_claim_dependencies ubd
                                    ON ubd.proof_id=ubp.id
                                   AND ubd.source_build_id={active_used_by_build}
                                  WHERE ubp.claim_id=?
                                    AND ubp.claim_version_id=(
                                      SELECT scv.id FROM claim_versions scv
                                      WHERE scv.claim_id=ubp.claim_id
                                      ORDER BY scv.version_no DESC LIMIT 1
                                    )
                                    AND ubd.target_claim_id=indexed.claim_id
                                    AND ubd.target_claim_version_id=indexed.id
                                )"""
                            )
                            structural_args.append(source_claim_id)
                        elif not used_by_lean_name_available:
                            structural_clauses.append("0=1")
                        else:
                            structural_clauses.append(
                                f"""EXISTS(
                                  SELECT 1 FROM proofs ubp
                                  JOIN proof_claim_dependencies ubd
                                    ON ubd.proof_id=ubp.id
                                   AND ubd.source_build_id={active_used_by_build}
                                  JOIN formal_bindings usfb
                                    ON usfb.claim_id=ubp.claim_id
                                   AND usfb.claim_version_id=ubp.claim_version_id
                                   AND usfb.environment_hash=ubd.environment_hash
                                   AND usfb.lean_name=?
                                  WHERE ubp.claim_version_id=(
                                      SELECT scv.id FROM claim_versions scv
                                      WHERE scv.claim_id=ubp.claim_id
                                      ORDER BY scv.version_no DESC LIMIT 1
                                    )
                                    AND ubd.target_claim_id=indexed.claim_id
                                    AND ubd.target_claim_version_id=indexed.id
                                )"""
                            )
                            structural_args.append(selector)

            if paths:
                if not path_available:
                    structural_clauses.append("0=1")
                else:
                    active_path_source_build = self._active_argument_build_sql("p1")
                    active_path_middle_build = self._active_argument_build_sql("p2")
                    for path in paths:
                        selector = str(path["target"])
                        if re.fullmatch(r"CLM-\d+", selector, re.IGNORECASE):
                            target_claim_id = parse_ref(selector, "CLM")
                            structural_clauses.append(
                                f"""EXISTS(
                                  SELECT 1
                                  FROM proofs p1
                                  JOIN proof_claim_dependencies d1
                                    ON d1.proof_id=p1.id
                                   AND d1.source_build_id={active_path_source_build}
                                  JOIN proofs p2
                                    ON p2.claim_id=d1.target_claim_id
                                   AND p2.claim_version_id=d1.target_claim_version_id
                                  JOIN proof_claim_dependencies d2
                                    ON d2.proof_id=p2.id
                                   AND d2.source_build_id={active_path_middle_build}
                                  WHERE p1.claim_version_id=indexed.id
                                    AND d1.target_claim_version_id=(
                                      SELECT mcv.id FROM claim_versions mcv
                                      WHERE mcv.claim_id=d1.target_claim_id
                                      ORDER BY mcv.version_no DESC LIMIT 1
                                    )
                                    AND d2.target_claim_id=?
                                    AND d2.target_claim_version_id=(
                                      SELECT tcv.id FROM claim_versions tcv
                                      WHERE tcv.claim_id=d2.target_claim_id
                                      ORDER BY tcv.version_no DESC LIMIT 1
                                    )
                                )"""
                            )
                            structural_args.append(target_claim_id)
                        elif not path_lean_name_available:
                            structural_clauses.append("0=1")
                        else:
                            structural_clauses.append(
                                f"""EXISTS(
                                  SELECT 1
                                  FROM proofs p1
                                  JOIN proof_claim_dependencies d1
                                    ON d1.proof_id=p1.id
                                   AND d1.source_build_id={active_path_source_build}
                                  JOIN proofs p2
                                    ON p2.claim_id=d1.target_claim_id
                                   AND p2.claim_version_id=d1.target_claim_version_id
                                  JOIN proof_claim_dependencies d2
                                    ON d2.proof_id=p2.id
                                   AND d2.source_build_id={active_path_middle_build}
                                  JOIN formal_bindings pfb
                                    ON pfb.claim_id=d2.target_claim_id
                                   AND pfb.claim_version_id=d2.target_claim_version_id
                                   AND pfb.environment_hash=d2.environment_hash
                                   AND pfb.lean_name=?
                                  WHERE p1.claim_version_id=indexed.id
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
                                )"""
                            )
                            structural_args.append(selector)

            if not_uses:
                if not negative_available:
                    structural_clauses.append("0=1")
                else:
                    active_negative_build = self._active_argument_build_sql("np")
                    missing_negative_build = self._active_argument_build_sql("npm")
                    structural_clauses.append(
                        f"""EXISTS(
                          SELECT 1 FROM proofs np
                          WHERE np.claim_version_id=indexed.id
                            AND {active_negative_build} IS NOT NULL
                        )"""
                    )
                    structural_clauses.append(
                        f"""NOT EXISTS(
                          SELECT 1 FROM proofs npm
                          WHERE npm.claim_version_id=indexed.id
                            AND {missing_negative_build} IS NULL
                        )"""
                    )
                    for index, selector in enumerate(not_uses):
                        route_alias = f"nu{index}"
                        build_alias = f"nub{index}"
                        active_build = self._active_argument_build_sql(route_alias)
                        resolution_sql, resolution_args = self._negative_resolution_sql(build_alias, selector, f"nur{index}")
                        raw_use_sql, raw_use_args = self._negative_raw_use_sql(build_alias, selector, f"nux{index}")
                        structural_clauses.append(
                            f"""NOT EXISTS(
                              SELECT 1 FROM proofs {route_alias}
                              JOIN builds {build_alias} ON {build_alias}.id={active_build}
                              WHERE {route_alias}.claim_version_id=indexed.id
                                AND NOT ({resolution_sql})
                            )"""
                        )
                        structural_args.extend(resolution_args)
                        structural_clauses.append(
                            f"""NOT EXISTS(
                              SELECT 1 FROM proofs {route_alias}
                              JOIN builds {build_alias} ON {build_alias}.id={active_build}
                              WHERE {route_alias}.claim_version_id=indexed.id
                                AND ({raw_use_sql})
                            )"""
                        )
                        structural_args.extend(raw_use_args)

            if route_uses or route_not_uses:
                if (
                    not route_query_available
                    or (route_uses_need_binding and not route_uses_lean_name_available)
                    or (route_not_uses and not route_negative_available)
                ):
                    structural_clauses.append("0=1")
                else:
                    route_alias = "rq"
                    build_alias = "rqb"
                    active_route_build = self._active_argument_build_sql(route_alias)
                    route_conditions: list[str] = []
                    route_args: list[Any] = []
                    for index, selector in enumerate(route_uses):
                        condition, args = self._positive_route_dependency_sql(route_alias, build_alias, selector, f"rqp{index}")
                        route_conditions.append(condition)
                        route_args.extend(args)
                    for index, selector in enumerate(route_not_uses):
                        resolution_sql, resolution_args = self._negative_resolution_sql(build_alias, selector, f"rqn{index}")
                        raw_use_sql, raw_use_args = self._negative_raw_use_sql(build_alias, selector, f"rqx{index}")
                        route_conditions.append(resolution_sql)
                        route_conditions.append(f"NOT ({raw_use_sql})")
                        route_args.extend(resolution_args)
                        route_args.extend(raw_use_args)
                    route_sql = " AND ".join(route_conditions) if route_conditions else "1=1"
                    structural_clauses.append(
                        f"""EXISTS(
                          SELECT 1 FROM proofs {route_alias}
                          JOIN builds {build_alias} ON {build_alias}.id={active_route_build}
                          WHERE {route_alias}.claim_version_id=indexed.id
                            AND {route_sql}
                        )"""
                    )
                    structural_args.extend(route_args)

            if head:
                if not head_available:
                    structural_clauses.append("0=1")
                else:
                    active_head_build = self._active_argument_build_sql("hp")
                    structural_clauses.append(
                        f"""EXISTS(
                          SELECT 1 FROM proofs hp
                          JOIN formal_type_shapes hs
                            ON hs.source_build_id={active_head_build}
                           AND hs.shape_version=?
                          WHERE hp.claim_version_id=indexed.id
                            AND hs.result_head=?
                        )"""
                    )
                    structural_args.extend([TYPE_SHAPE_VERSION, head])

            if binders is not None:
                if not head_available:
                    structural_clauses.append("0=1")
                else:
                    active_binder_build = self._active_argument_build_sql("bp")
                    structural_clauses.append(
                        f"""EXISTS(
                          SELECT 1 FROM proofs bp
                          JOIN formal_type_shapes bs
                            ON bs.source_build_id={active_binder_build}
                           AND bs.shape_version=?
                          WHERE bp.claim_version_id=indexed.id
                            AND bs.result_binder_count=?
                        )"""
                    )
                    structural_args.extend([TYPE_SHAPE_VERSION, int(binders)])

            if binder_domains:
                if not partition_available:
                    structural_clauses.append("0=1")
                else:
                    active_domain_build = self._active_argument_build_sql("dp")
                    for ref in binder_domains:
                        structural_clauses.append(
                            f"""EXISTS(
                              SELECT 1 FROM proofs dp
                              JOIN formal_type_partitions tp
                                ON tp.source_build_id={active_domain_build}
                               AND tp.partition_version=?
                              JOIN formal_type_partition_refs tr
                                ON tr.partition_id=tp.id
                               AND tr.ref_kind='binder-domain-head'
                              WHERE dp.claim_version_id=indexed.id
                                AND tr.constant_name=?
                            )"""
                        )
                        structural_args.extend([TYPE_PARTITION_VERSION, ref])

            if result_refs:
                if not partition_available:
                    structural_clauses.append("0=1")
                else:
                    active_result_build = self._active_argument_build_sql("rp")
                    for ref in result_refs:
                        structural_clauses.append(
                            f"""EXISTS(
                              SELECT 1 FROM proofs rp
                              JOIN formal_type_partitions tp
                                ON tp.source_build_id={active_result_build}
                               AND tp.partition_version=?
                              JOIN formal_type_partition_refs tr
                                ON tr.partition_id=tp.id
                               AND tr.ref_kind='result-ref'
                              WHERE rp.claim_version_id=indexed.id
                                AND tr.constant_name=?
                            )"""
                        )
                        structural_args.extend([TYPE_PARTITION_VERSION, ref])

            structural_sql = "" if not structural_clauses else " AND " + " AND ".join(structural_clauses)
            sql = f"""
            WITH latest AS (
                SELECT cv.* FROM claim_versions cv JOIN (
                    SELECT claim_id, MAX(version_no) AS version_no
                    FROM claim_versions GROUP BY claim_id
                ) x ON x.claim_id=cv.claim_id AND x.version_no=cv.version_no
            ), indexed AS (
                SELECT c.id AS claim_id,c.created_at AS claim_created_at,l.*,
                    {external_name} AS external_lean_name,
                    CASE
                      WHEN EXISTS(SELECT 1 FROM builds b JOIN proofs p ON p.id=b.proof_id WHERE p.claim_version_id=l.id AND b.status='verified') THEN 'verified'
                      WHEN EXISTS(SELECT 1 FROM builds b JOIN proofs p ON p.id=b.proof_id WHERE p.claim_version_id=l.id AND b.status='partial') THEN 'partial'
                      WHEN EXISTS(SELECT 1 FROM proofs p WHERE p.claim_version_id=l.id AND NOT EXISTS(SELECT 1 FROM builds b WHERE b.proof_id=p.id)) THEN 'partial'
                      WHEN EXISTS(SELECT 1 FROM builds b JOIN proofs p ON p.id=b.proof_id WHERE p.claim_version_id=l.id AND b.status='failed') THEN 'failed'
                      WHEN EXISTS(SELECT 1 FROM proofs p WHERE p.claim_version_id=l.id) THEN 'partial'
                      ELSE 'open' END AS claim_status,
                    (SELECT b.id FROM builds b JOIN proofs p ON p.id=b.proof_id WHERE p.claim_version_id=l.id ORDER BY b.id DESC LIMIT 1) AS latest_build_id,
                    CASE
                      WHEN EXISTS(SELECT 1 FROM builds b JOIN proofs p ON p.id=b.proof_id WHERE p.claim_version_id=l.id AND b.status='verified') THEN
                        (SELECT b.id FROM builds b JOIN proofs p ON p.id=b.proof_id WHERE p.claim_version_id=l.id AND b.status='verified' ORDER BY b.id DESC LIMIT 1)
                      WHEN EXISTS(SELECT 1 FROM builds b JOIN proofs p ON p.id=b.proof_id WHERE p.claim_version_id=l.id AND b.status='partial') THEN
                        (SELECT b.id FROM builds b JOIN proofs p ON p.id=b.proof_id WHERE p.claim_version_id=l.id AND b.status='partial' ORDER BY b.id DESC LIMIT 1)
                      WHEN EXISTS(SELECT 1 FROM proofs p WHERE p.claim_version_id=l.id AND NOT EXISTS(SELECT 1 FROM builds b WHERE b.proof_id=p.id)) THEN NULL
                      WHEN EXISTS(SELECT 1 FROM builds b JOIN proofs p ON p.id=b.proof_id WHERE p.claim_version_id=l.id AND b.status='failed') THEN
                        (SELECT b.id FROM builds b JOIN proofs p ON p.id=b.proof_id WHERE p.claim_version_id=l.id AND b.status='failed' ORDER BY b.id DESC LIMIT 1)
                      ELSE NULL END AS verification_build_id
                FROM claims c JOIN latest l ON l.claim_id=c.id
            )
            SELECT indexed.*,vb.axioms_json AS verification_axioms_json,
                   vb.audit_ok AS verification_audit_ok
            FROM indexed LEFT JOIN builds vb ON vb.id=indexed.verification_build_id
            WHERE (? IS NULL OR indexed.claim_id < ?)
              AND (?='' OR lower(indexed.title) LIKE ? OR lower(indexed.statement) LIKE ?
                   OR lower(indexed.formal_declaration) LIKE ? OR lower(indexed.area) LIKE ?
                   OR lower(indexed.claim_author) LIKE ? OR lower(indexed.lean_name) LIKE ?
                   OR lower(COALESCE(indexed.external_lean_name,'')) LIKE ?)
              AND (? IN ('','all') OR indexed.claim_status=?)
              {structural_sql}
            ORDER BY indexed.claim_id DESC LIMIT ?
            """
            args = [
                before_internal_id,before_internal_id,text_query,
                q_like,q_like,q_like,q_like,q_like,q_like,q_like,
                status,status,*structural_args,limit+1,
            ]
            rows = conn.execute(sql, args).fetchall()

        has_more = len(rows) > limit
        visible = rows[:limit]
        items = [self._serialize(row) for row in visible]
        return {
            "claims": items,
            "next_cursor": claim_ref(int(visible[-1]["claim_id"])) if has_more and visible else None,
            "has_more": has_more,
            "page_size": len(items),
            "query": parsed["raw"],
            "text_query": text_query,
            "status": status or "all",
            "structural_filters": {
                "namespace": namespace or None,
                "statement_refs": statement_refs,
                "proof_refs": proof_refs,
                "uses": uses,
                "used_by": used_by,
                "paths": paths,
                "not_uses": not_uses,
                "route_uses": route_uses,
                "route_not_uses": route_not_uses,
                "head": head or None,
                "binders": binders,
                "binder_domains": binder_domains,
                "result_refs": result_refs,
            },
            "statement_ref_search_available": statement_ref_available,
            "proof_ref_search_available": proof_ref_available,
            "uses_search_available": uses_available,
            "uses_lean_name_search_available": uses_lean_name_available,
            "used_by_search_available": used_by_available,
            "used_by_lean_name_search_available": used_by_lean_name_available,
            "path_search_available": path_available,
            "path_lean_name_search_available": path_lean_name_available,
            "path_supported_depths": [PATH_DEPTH] if path_available else [],
            "not_uses_search_available": negative_available,
            "route_uses_search_available": route_query_available,
            "route_uses_lean_name_search_available": route_uses_lean_name_available,
            "route_not_uses_search_available": route_negative_available,
            "head_search_available": head_available,
            "binder_count_search_available": head_available,
            "binder_domain_search_available": partition_available,
            "result_ref_search_available": partition_available,
            "type_shape_version": TYPE_SHAPE_VERSION if head_available else None,
            "type_partition_version": TYPE_PARTITION_VERSION if partition_available else None,
        }

    def _current_routes(self, conn: sqlite3.Connection, claim_id: int) -> list[sqlite3.Row]:
        active = self._active_argument_build_sql("p")
        return conn.execute(
            f"""SELECT p.id proof_id,p.claim_version_id,b.id build_id,b.environment_hash
                FROM proofs p
                LEFT JOIN builds b ON b.id={active}
                WHERE p.claim_id=?
                  AND p.claim_version_id=(
                    SELECT cv.id FROM claim_versions cv
                    WHERE cv.claim_id=p.claim_id ORDER BY cv.version_no DESC LIMIT 1
                  )
                ORDER BY p.id DESC""",
            (claim_id,),
        ).fetchall()

    @staticmethod
    def _resolved_claim_ids(conn: sqlite3.Connection, environment_hash: str, selector: str) -> list[int]:
        if re.fullmatch(r"CLM-\d+", selector, re.IGNORECASE):
            claim_id = parse_ref(selector, "CLM")
            row = conn.execute(
                """SELECT 1 FROM formal_bindings fb
                   WHERE fb.claim_id=? AND fb.environment_hash=?
                     AND fb.claim_version_id=(
                       SELECT cv.id FROM claim_versions cv
                       WHERE cv.claim_id=fb.claim_id ORDER BY cv.version_no DESC LIMIT 1
                     ) LIMIT 1""",
                (claim_id, environment_hash),
            ).fetchone()
            return [claim_id] if row else []
        rows = conn.execute(
            """SELECT DISTINCT fb.claim_id FROM formal_bindings fb
               WHERE fb.lean_name=? AND fb.environment_hash=?
                 AND fb.claim_version_id=(
                   SELECT cv.id FROM claim_versions cv
                   WHERE cv.claim_id=fb.claim_id ORDER BY cv.version_no DESC LIMIT 1
                 )
               ORDER BY fb.claim_id""",
            (selector, environment_hash),
        ).fetchall()
        return [int(row["claim_id"]) for row in rows]

    @staticmethod
    def _raw_used_claim_ids(conn: sqlite3.Connection, build_id: int, environment_hash: str) -> set[int]:
        rows = conn.execute(
            """SELECT DISTINCT fb.claim_id
               FROM build_constant_refs cr
               JOIN formal_bindings fb
                 ON fb.lean_name=cr.constant_name
                AND fb.environment_hash=?
               WHERE cr.build_id=? AND cr.ref_kind='proof'
                 AND fb.claim_version_id=(
                   SELECT cv.id FROM claim_versions cv
                   WHERE cv.claim_id=fb.claim_id ORDER BY cv.version_no DESC LIMIT 1
                 )""",
            (environment_hash, build_id),
        ).fetchall()
        return {int(row["claim_id"]) for row in rows}

    @staticmethod
    def _route_positive_match(conn: sqlite3.Connection, proof_id: int, build_id: int, selector: str) -> dict[str, Any] | None:
        if re.fullmatch(r"CLM-\d+", selector, re.IGNORECASE):
            target_claim_id = parse_ref(selector, "CLM")
            row = conn.execute(
                """SELECT d.target_claim_id,d.evidence_constant
                   FROM proof_claim_dependencies d
                   WHERE d.proof_id=? AND d.source_build_id=? AND d.target_claim_id=?
                     AND d.target_claim_version_id=(
                       SELECT cv.id FROM claim_versions cv
                       WHERE cv.claim_id=d.target_claim_id ORDER BY cv.version_no DESC LIMIT 1
                     )
                   ORDER BY d.id DESC LIMIT 1""",
                (proof_id, build_id, target_claim_id),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT d.target_claim_id,d.evidence_constant
                   FROM proof_claim_dependencies d
                   JOIN formal_bindings fb
                     ON fb.claim_id=d.target_claim_id
                    AND fb.claim_version_id=d.target_claim_version_id
                    AND fb.environment_hash=d.environment_hash
                    AND fb.lean_name=?
                   WHERE d.proof_id=? AND d.source_build_id=?
                     AND d.target_claim_version_id=(
                       SELECT cv.id FROM claim_versions cv
                       WHERE cv.claim_id=d.target_claim_id ORDER BY cv.version_no DESC LIMIT 1
                     )
                   ORDER BY d.id DESC LIMIT 1""",
                (selector, proof_id, build_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "selector": selector,
            "target_claim_id": claim_ref(int(row["target_claim_id"])),
            "evidence_constant": row["evidence_constant"],
        }

    def quantifier_witness(
        self,
        source_claim_id: int,
        *,
        not_uses: list[str] | None = None,
        route_uses: list[str] | None = None,
        route_not_uses: list[str] | None = None,
    ) -> dict[str, Any]:
        """Audit Claim-level universal and same-Proof-route dependency predicates."""
        not_uses = [self._canonical_graph_value(x, "not-uses") for x in (not_uses or [])][:8]
        route_uses = [self._canonical_graph_value(x, "route-uses") for x in (route_uses or [])][:8]
        route_not_uses = [self._canonical_graph_value(x, "route-not-uses") for x in (route_not_uses or [])][:8]
        with self.connect() as conn:
            dependency_available = self._table_exists(conn, "proof_claim_dependencies")
            formal_binding_available = self._table_exists(conn, "formal_bindings")
            constant_ref_available = self._table_exists(conn, "build_constant_refs")
            needs_negative_evidence = bool(not_uses or route_not_uses)
            needs_route_binding = any(
                not re.fullmatch(r"CLM-\d+", selector, re.IGNORECASE)
                for selector in route_uses
            )
            required = (
                dependency_available
                and (not needs_route_binding or formal_binding_available)
                and (
                    not needs_negative_evidence
                    or (formal_binding_available and constant_ref_available)
                )
            )
            if not required:
                return {
                    "available": False,
                    "source_claim_id": claim_ref(source_claim_id),
                    "universal": [],
                    "route_group": None,
                }
            routes = self._current_routes(conn, source_claim_id)
            universal_rows: list[dict[str, Any]] = []
            universal_ok = bool(routes)
            for selector in not_uses:
                selector_ok = bool(routes)
                checked = 0
                for route in routes:
                    if route["build_id"] is None:
                        selector_ok = False
                        break
                    env = str(route["environment_hash"] or "")
                    resolved = self._resolved_claim_ids(conn, env, selector)
                    if not resolved:
                        selector_ok = False
                        break
                    raw_used = self._raw_used_claim_ids(conn, int(route["build_id"]), env)
                    if any(claim_id in raw_used for claim_id in resolved):
                        selector_ok = False
                        break
                    checked += 1
                universal_rows.append(
                    {
                        "selector": selector,
                        "matched": selector_ok,
                        "complete_routes_checked": checked,
                    }
                )
                universal_ok = universal_ok and selector_ok

            route_group = None
            if route_uses or route_not_uses:
                for route in routes:
                    if route["build_id"] is None:
                        continue
                    proof_id = int(route["proof_id"])
                    build_id = int(route["build_id"])
                    env = str(route["environment_hash"] or "")
                    positives: list[dict[str, Any]] = []
                    positive_ok = True
                    for selector in route_uses:
                        match = self._route_positive_match(conn, proof_id, build_id, selector)
                        if match is None:
                            positive_ok = False
                            break
                        positives.append(match)
                    if not positive_ok:
                        continue
                    negatives: list[dict[str, Any]] = []
                    negative_ok = True
                    raw_used = (
                        self._raw_used_claim_ids(conn, build_id, env)
                        if route_not_uses
                        else set()
                    )
                    for selector in route_not_uses:
                        resolved = self._resolved_claim_ids(conn, env, selector)
                        if not resolved or any(claim_id in raw_used for claim_id in resolved):
                            negative_ok = False
                            break
                        negatives.append(
                            {
                                "selector": selector,
                                "resolved_claim_ids": [claim_ref(claim_id) for claim_id in resolved],
                            }
                        )
                    if not negative_ok:
                        continue
                    route_group = {
                        "matched": True,
                        "proof_id": proof_ref(proof_id),
                        "build_id": build_ref(build_id),
                        "environment_hash": env,
                        "positive": positives,
                        "negative": negatives,
                    }
                    break
                if route_group is None:
                    route_group = {"matched": False, "positive": [], "negative": []}

        return {
            "available": True,
            "source_claim_id": claim_ref(source_claim_id),
            "universal_matched": universal_ok if not_uses else None,
            "universal": universal_rows,
            "route_group": route_group,
        }

    def path_witness(self, source_claim_id: int, *, depth: int, target: str) -> dict[str, Any]:
        """Return one deterministic exact two-hop current ProofDependency witness."""
        if int(depth) != PATH_DEPTH:
            raise ValueError("path witness currently supports exact depth 2 only")
        selector = self._canonical_graph_value(target, "path")
        with self.connect() as conn:
            dependency_available = self._table_exists(conn, "proof_claim_dependencies")
            formal_binding_available = self._table_exists(conn, "formal_bindings")
            if not dependency_available:
                return {
                    "available": False,
                    "matched": False,
                    "depth": PATH_DEPTH,
                    "source_claim_id": claim_ref(source_claim_id),
                    "target_selector": selector,
                    "steps": [],
                }
            active_source_build = self._active_argument_build_sql("p1")
            active_middle_build = self._active_argument_build_sql("p2")
            args: list[Any] = [source_claim_id]
            binding_join = ""
            target_clause = ""
            if re.fullmatch(r"CLM-\d+", selector, re.IGNORECASE):
                target_clause = "AND d2.target_claim_id=?"
                args.append(parse_ref(selector, "CLM"))
            else:
                if not formal_binding_available:
                    return {
                        "available": False,
                        "matched": False,
                        "depth": PATH_DEPTH,
                        "source_claim_id": claim_ref(source_claim_id),
                        "target_selector": selector,
                        "steps": [],
                    }
                binding_join = """JOIN formal_bindings pfb
                                    ON pfb.claim_id=d2.target_claim_id
                                   AND pfb.claim_version_id=d2.target_claim_version_id
                                   AND pfb.environment_hash=d2.environment_hash
                                   AND pfb.lean_name=?"""
                args.insert(0, selector)

            row = conn.execute(
                f"""SELECT
                       p1.claim_id source_claim_id,
                       p1.claim_version_id source_version_id,
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
                     {binding_join}
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
                       {target_clause}
                     ORDER BY p1.id DESC,d1.id DESC,p2.id DESC,d2.id DESC
                     LIMIT 1""",
                args,
            ).fetchone()

        base = {
            "available": True,
            "matched": row is not None,
            "depth": PATH_DEPTH,
            "source_claim_id": claim_ref(source_claim_id),
            "target_selector": selector,
            "steps": [],
        }
        if row is None:
            return base
        base["target_claim_id"] = claim_ref(int(row["target_claim_id"]))
        base["target_claim_version_id"] = version_ref(int(row["target_version_id"]))
        base["target_title"] = row["target_title"]
        base["target_lean_name"] = row["target_lean_name"]
        base["steps"] = [
            {
                "from_claim_id": claim_ref(int(row["source_claim_id"])),
                "proof_id": proof_ref(int(row["source_proof_id"])),
                "build_id": build_ref(int(row["source_build_id"])),
                "to_claim_id": claim_ref(int(row["middle_claim_id"])),
                "to_claim_version_id": version_ref(int(row["middle_version_id"])),
                "to_title": row["middle_title"],
                "to_lean_name": row["middle_lean_name"],
                "evidence_constant": row["first_evidence"],
            },
            {
                "from_claim_id": claim_ref(int(row["middle_claim_id"])),
                "proof_id": proof_ref(int(row["middle_proof_id"])),
                "build_id": build_ref(int(row["middle_build_id"])),
                "to_claim_id": claim_ref(int(row["target_claim_id"])),
                "to_claim_version_id": version_ref(int(row["target_version_id"])),
                "to_title": row["target_title"],
                "to_lean_name": row["target_lean_name"],
                "evidence_constant": row["second_evidence"],
            },
        ]
        return base

    def _serialize(self, row: sqlite3.Row) -> dict[str, Any]:
        latest = row["latest_build_id"]
        witness = row["verification_build_id"]
        trust = None
        if witness is not None:
            try:
                axioms = json.loads(row["verification_axioms_json"] or "[]")
            except json.JSONDecodeError:
                axioms = []
            trust = classify_axioms(axioms)
        stored_name = str(row["lean_name"] or "")
        external_name = str(row["external_lean_name"] or "").strip()
        display_name = external_name or stored_name
        result = {
            "claim_id": claim_ref(int(row["claim_id"])),
            "version_id": version_ref(int(row["id"])),
            "version": int(row["version_no"]),
            "title": row["title"],
            "statement": row["statement"],
            "latex": row["latex"],
            "area": row["area"],
            "claim_author": row["claim_author"],
            "formal_declaration": row["formal_declaration"],
            "lean_name": display_name,
            "content_hash": row["content_hash"],
            "created_at": row["claim_created_at"],
            "version_created_at": row["created_at"],
            "status": row["claim_status"],
            "latest_build_id": build_ref(int(latest)) if latest is not None else None,
            "verification_build_id": build_ref(int(witness)) if witness is not None else None,
            "verification_audit_ok": None if row["verification_audit_ok"] is None else bool(row["verification_audit_ok"]),
            "trust_class": trust["trust_class"] if trust else None,
            "compiler_trust_axioms": trust["compiler_trust_axioms"] if trust else [],
            "nonstandard_axioms": trust["nonstandard_axioms"] if trust else [],
            "lean_name_source": "imported-declaration" if external_name else "claim",
        }
        if external_name and external_name != stored_name:
            result["formal_lean_name"] = stored_name
        return result
