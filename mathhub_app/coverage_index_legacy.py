from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

from registry import parse_ref


KINDS = {"theorem", "substrate", "internal", "missing"}


def imports_fingerprint(imports: list[str]) -> str:
    payload = json.dumps(imports, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class CoverageIndex:
    """Derived dependency/registry coverage read model.

    Mathematical truth remains ProofDependency. This index only remembers exact
    Lean ConstantInfo classifications in one environment/import visibility set so
    raw proof constants can be explained as theorem dependencies or formal
    substrate without launching Lean on every page read.

    Namespace registry coverage is a separate scale-level signal: it compares a
    complete cached theorem index for one Lean namespace with exact FormalBindings
    already registered on current Claim revisions in the same environment. It
    never substitutes for Proof dependency coverage.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS formal_constant_kinds(
                  environment_hash TEXT NOT NULL,
                  imports_hash TEXT NOT NULL,
                  constant_name TEXT NOT NULL,
                  constant_kind TEXT NOT NULL CHECK(constant_kind IN('theorem','substrate','internal','missing')),
                  observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  PRIMARY KEY(environment_hash,imports_hash,constant_name)
                );
                CREATE INDEX IF NOT EXISTS idx_formal_constant_kind
                  ON formal_constant_kinds(environment_hash,imports_hash,constant_kind,constant_name);
                """
            )

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def record(self, environment_hash: str, imports: list[str], classifications: dict[str, str]) -> None:
        environment_hash = str(environment_hash or "")
        if not environment_hash or not classifications:
            return
        ih = imports_fingerprint(imports)
        rows = [
            (environment_hash, ih, str(name), str(kind))
            for name, kind in classifications.items()
            if str(name) and str(kind) in KINDS
        ]
        if not rows:
            return
        with self.connect() as conn:
            conn.executemany(
                """INSERT INTO formal_constant_kinds(environment_hash,imports_hash,constant_name,constant_kind)
                   VALUES(?,?,?,?)
                   ON CONFLICT(environment_hash,imports_hash,constant_name) DO UPDATE SET
                     constant_kind=excluded.constant_kind,
                     observed_at=CURRENT_TIMESTAMP""",
                rows,
            )

    def kinds(self, environment_hash: str, imports: list[str], names: list[str]) -> dict[str, str]:
        names = list(dict.fromkeys(str(name) for name in names if str(name)))
        if not names or not environment_hash:
            return {}
        ih = imports_fingerprint(imports)
        result: dict[str, str] = {}
        with self.connect() as conn:
            for offset in range(0, len(names), 400):
                chunk = names[offset : offset + 400]
                marks = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"""SELECT constant_name,constant_kind FROM formal_constant_kinds
                        WHERE environment_hash=? AND imports_hash=? AND constant_name IN({marks})""",
                    (environment_hash, ih, *chunk),
                ).fetchall()
                for row in rows:
                    result[str(row["constant_name"])] = str(row["constant_kind"])
        return result

    def _argument_environment(self, argument_build_id: str) -> str:
        build_id = parse_ref(str(argument_build_id), "BLD")
        with self.connect() as conn:
            row = conn.execute("SELECT environment_hash FROM builds WHERE id=?", (build_id,)).fetchone()
        return str(row["environment_hash"] or "") if row is not None else ""

    def _self_constants(self, claim_id: int, environment_hash: str) -> set[str]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT lean_name FROM formal_bindings
                   WHERE claim_id=? AND environment_hash=?""",
                (claim_id, environment_hash),
            ).fetchall()
        return {str(row["lean_name"]) for row in rows}

    @staticmethod
    def _unavailable() -> dict[str, Any]:
        return {
            "status": "unavailable",
            "percent": None,
            "mapped_current": 0,
            "stale_mapped": 0,
            "unresolved_theorems": 0,
            "substrate_refs": 0,
            "missing_refs": 0,
            "unclassified_refs": 0,
            "theorem_dependencies": 0,
        }

    def proof_coverage(self, route: dict[str, Any]) -> dict[str, Any]:
        if not route.get("argument_index_complete") or not route.get("argument_build_id"):
            return self._unavailable()

        environment_hash = self._argument_environment(str(route["argument_build_id"]))
        if not environment_hash:
            return self._unavailable()
        imports = list(route.get("imports") or ["Mathlib"])
        claim_id = parse_ref(str(route["claim_id"]), "CLM")
        raw = set(str(x) for x in (route.get("raw_refs") or {}).get("proof", []) if str(x))
        raw -= self._self_constants(claim_id, environment_hash)

        active_evidence: set[str] = set()
        stale_evidence: set[str] = set()
        for dep in route.get("depends_on") or []:
            target = active_evidence if dep.get("active", True) else stale_evidence
            target.update(str(x) for x in dep.get("evidence_constants") or [] if str(x))
        active_evidence &= raw
        stale_evidence &= raw
        # Reconciliation may retain a historical dependency row and also add a new
        # current-revision row for the same evidence constant. Current mapping wins.
        stale_evidence -= active_evidence
        remaining = sorted(raw - active_evidence - stale_evidence)
        known = self.kinds(environment_hash, imports, remaining)

        unresolved = sorted(name for name in remaining if known.get(name) == "theorem")
        substrate = sorted(name for name in remaining if known.get(name) in {"substrate", "internal"})
        missing = sorted(name for name in remaining if known.get(name) == "missing")
        unclassified = sorted(name for name in remaining if name not in known)

        blockers = len(unresolved) + len(stale_evidence) + len(missing) + len(unclassified)
        status = "complete" if blockers == 0 else "partial"
        theorem_total = len(active_evidence) + len(stale_evidence) + len(unresolved)
        percent = None if unclassified or missing else (100 if theorem_total == 0 else round(100 * len(active_evidence) / theorem_total))
        return {
            "status": status,
            "percent": percent,
            "mapped_current": len(active_evidence),
            "stale_mapped": len(stale_evidence),
            "unresolved_theorems": len(unresolved),
            "substrate_refs": len(substrate),
            "missing_refs": len(missing),
            "unclassified_refs": len(unclassified),
            "theorem_dependencies": theorem_total,
            "argument_environment_hash": environment_hash,
            "mapped_current_sample": sorted(active_evidence)[:40],
            "stale_mapped_sample": sorted(stale_evidence)[:40],
            "unresolved_theorem_sample": unresolved[:40],
            "substrate_sample": substrate[:40],
            "missing_sample": missing[:40],
            "unclassified_sample": unclassified[:40],
        }

    def decorate_argument(self, argument: dict[str, Any]) -> dict[str, Any]:
        proofs = list(argument.get("proofs") or [])
        ranked: list[tuple[int, int, dict[str, Any]]] = []
        for proof in proofs:
            coverage = self.proof_coverage(proof)
            proof["coverage"] = coverage
            rank = {"complete": 2, "partial": 1, "unavailable": 0}.get(str(coverage.get("status")), 0)
            pct = int(coverage.get("percent")) if coverage.get("percent") is not None else -1
            ranked.append((rank, pct, proof))
        if ranked:
            best = max(ranked, key=lambda x: (x[0], x[1]))[2]
            argument["coverage"] = {
                **dict(best.get("coverage") or {}),
                "best_proof_id": best.get("proof_id"),
                "proofs_complete": sum(1 for p in proofs if (p.get("coverage") or {}).get("status") == "complete"),
                "proofs_partial": sum(1 for p in proofs if (p.get("coverage") or {}).get("status") == "partial"),
                "proofs_unavailable": sum(1 for p in proofs if (p.get("coverage") or {}).get("status") == "unavailable"),
            }
        else:
            argument["coverage"] = {**self._unavailable(), "best_proof_id": None, "proofs_complete": 0, "proofs_partial": 0, "proofs_unavailable": 0}
        return argument

    @staticmethod
    def _coverage_from_counts(
        namespace: str,
        source_namespace: str,
        total: int,
        indexed: int,
        registered: int,
        scanned_at: str,
    ) -> dict[str, Any]:
        percent = 100.0 if total == 0 else round(100.0 * min(registered, total) / total, 1)
        status = "complete" if registered >= total else ("empty" if registered == 0 else "partial")
        return {
            "kind": "registry",
            "status": status,
            "percent": percent,
            "registered_theorems": registered,
            "total_theorems": total,
            "remaining_theorems": max(0, total - registered),
            "indexed_theorems": indexed,
            "index_complete": True,
            "index_source_namespace": source_namespace,
            "namespace": namespace,
            "scanned_at": scanned_at,
        }

    def namespace_coverages(
        self,
        cluster_keys: list[str],
        environment_hash: str,
        imports: list[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Return exact registry coverage for namespace clusters.

        If an exact namespace scan does not exist, a complete ancestor scan can be
        filtered deterministically to derive an exact child denominator. Incomplete
        ancestor scans are never used to manufacture a percentage. A theorem only
        contributes to the numerator when its FormalBinding targets that Claim's
        current revision in the same environment.
        """
        namespaces = list(dict.fromkeys(
            key.partition(":")[2]
            for key in cluster_keys
            if str(key).startswith("ns:") and key.partition(":")[2]
        ))
        if not namespaces or not environment_hash:
            return {}
        imports = list(imports or ["Mathlib"])
        ih = imports_fingerprint(imports)
        result: dict[str, dict[str, Any]] = {}
        with self.connect() as conn:
            required = {
                str(row["name"])
                for row in conn.execute(
                    """SELECT name FROM sqlite_master WHERE type='table'
                       AND name IN('library_namespace_scans','library_theorem_index','formal_bindings','claim_versions')"""
                ).fetchall()
            }
            if required != {"library_namespace_scans", "library_theorem_index", "formal_bindings", "claim_versions"}:
                return {}

            for namespace in namespaces:
                source = conn.execute(
                    """SELECT namespace,total_theorems,indexed_theorems,complete,scanned_at
                       FROM library_namespace_scans
                       WHERE environment_hash=? AND imports_hash=? AND complete=1
                         AND (?=namespace OR ? LIKE namespace || '.%')
                       ORDER BY CASE WHEN namespace=? THEN 0 ELSE 1 END,
                                length(namespace) DESC
                       LIMIT 1""",
                    (environment_hash, ih, namespace, namespace, namespace),
                ).fetchone()
                if source is None:
                    result[f"ns:{namespace}"] = {
                        "kind": "registry",
                        "status": "unavailable",
                        "percent": None,
                        "index_complete": False,
                    }
                    continue

                source_namespace = str(source["namespace"])
                if source_namespace == namespace:
                    total = int(source["total_theorems"] or 0)
                    indexed = int(source["indexed_theorems"] or 0)
                    name_filter = "1=1"
                    filter_args: tuple[Any, ...] = ()
                else:
                    name_filter = "i.lean_name LIKE ?"
                    filter_args = (namespace + ".%",)
                    count_row = conn.execute(
                        """SELECT COUNT(DISTINCT lean_name) n FROM library_theorem_index
                           WHERE environment_hash=? AND imports_hash=? AND namespace=?
                             AND lean_name LIKE ?""",
                        (environment_hash, ih, source_namespace, namespace + ".%"),
                    ).fetchone()
                    total = int(count_row["n"] if count_row else 0)
                    indexed = total

                count = conn.execute(
                    f"""SELECT COUNT(DISTINCT CASE WHEN fb.lean_name IS NOT NULL THEN i.lean_name END) registered_count
                        FROM library_theorem_index i
                        LEFT JOIN (
                          SELECT DISTINCT fb.lean_name
                          FROM formal_bindings fb
                          JOIN claim_versions cv ON cv.id=fb.claim_version_id
                          WHERE fb.environment_hash=?
                            AND cv.version_no=(
                              SELECT MAX(x.version_no) FROM claim_versions x
                              WHERE x.claim_id=fb.claim_id
                            )
                        ) fb ON fb.lean_name=i.lean_name
                        WHERE i.environment_hash=? AND i.imports_hash=? AND i.namespace=?
                          AND {name_filter}""",
                    (environment_hash, environment_hash, ih, source_namespace, *filter_args),
                ).fetchone()
                registered = int(count["registered_count"] if count else 0)
                result[f"ns:{namespace}"] = self._coverage_from_counts(
                    namespace,
                    source_namespace,
                    total,
                    indexed,
                    registered,
                    str(source["scanned_at"] or ""),
                )
        return result

    def decorate_graph_clusters(
        self,
        graph: dict[str, Any],
        environment_hash: str,
        imports: list[str] | None = None,
    ) -> dict[str, Any]:
        nodes = graph.get("nodes") if isinstance(graph, dict) else None
        if not isinstance(nodes, list) or not nodes:
            return graph
        keys = [str(node.get("cluster_key") or "") for node in nodes if isinstance(node, dict) and node.get("kind") == "cluster"]
        coverages = self.namespace_coverages(keys, environment_hash, imports)
        if not coverages:
            return graph
        decorated = []
        for node in nodes:
            if not isinstance(node, dict):
                decorated.append(node); continue
            key = str(node.get("cluster_key") or "")
            coverage = coverages.get(key)
            decorated.append({**node, "registry_coverage": coverage} if coverage is not None else node)
        return {**graph, "nodes": decorated}


def install_classifier_cache(index: CoverageIndex, dependency_module: Any, task_module: Any, runtime_status: Callable[[], dict[str, Any]]) -> None:
    """Share exact Lean classifications with the coverage read model.

    Import tasks copied `_classify_unbound` at module import time, so install the
    same wrapper into both module namespaces. This changes execution caching only;
    classification truth still comes from the original Lean metacommand.
    """
    original = dependency_module._classify_unbound
    if getattr(original, "_mathhub_coverage_cached", False):
        return

    def cached(imports: list[str], names: list[str]):
        names = list(dict.fromkeys(str(name) for name in names if str(name)))
        environment_hash = str((runtime_status() or {}).get("environment_hash") or "")
        known = index.kinds(environment_hash, imports, names)
        unknown = [name for name in names if name not in known]
        fresh: dict[str, str] = {}
        calls = duration = 0
        errors: list[str] = []
        if unknown:
            fresh, calls, duration, errors = original(imports, unknown)
            index.record(environment_hash, imports, fresh)
        return {**known, **fresh}, calls, duration, errors

    cached._mathhub_coverage_cached = True  # type: ignore[attr-defined]
    dependency_module._classify_unbound = cached
    task_module._classify_unbound = cached
