from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

import server as core
from registry import claim_ref


NAMESPACE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
DEFAULT_NAMESPACE_LIMIT = 80
MAX_NAMESPACE_LIMIT = 200
MAX_NAMESPACE_INDEX_ITEMS = 25_000
MAX_GLOBAL_LIBRARY_THEOREMS = 60_000
MAX_GLOBAL_LIBRARY_EDGES = 300_000
MAX_NAMESPACE_QUERY_CHARS = 160
DEFAULT_INGESTION_CHUNK_SIZE = 400
MAX_INGESTION_CHUNK_SIZE = 500
MIN_INGESTION_CHUNK_SIZE = 40
MAX_INGESTION_PLAN_ROOTS = MAX_NAMESPACE_INDEX_ITEMS

_NAMESPACE_SCAN_LOCKS_GUARD = threading.Lock()
_NAMESPACE_SCAN_LOCKS: dict[tuple[str, str, str], threading.Lock] = {}


def _namespace_scan_lock(environment_hash: str, imports_hash: str, namespace: str) -> threading.Lock:
    key = (environment_hash, imports_hash, namespace)
    with _NAMESPACE_SCAN_LOCKS_GUARD:
        lock = _NAMESPACE_SCAN_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _NAMESPACE_SCAN_LOCKS[key] = lock
        return lock

LEAN_GLOBAL_LIBRARY_COMMAND = r'''
open Lean Elab Command

private def mathhubLibraryProofConstants (info : ConstantInfo) : Array Name :=
  match info with
  | .thmInfo v => v.value.getUsedConstants
  | .defnInfo v => v.value.getUsedConstants
  | .opaqueInfo v => v.value.getUsedConstants
  | _ => #[]

elab "#mathhub_scan_library" : command => do
  let env ← getEnv
  let mut theoremNames : Array Name := #[]
  for (name, info) in env.constants do
    if !name.isInternal then
      match info with
      | .thmInfo _ => theoremNames := theoremNames.push name
      | _ => pure ()
  let sortedNames := theoremNames.qsort Name.quickLt
  logInfo m!"MATHHUB_LIBRARY_COUNT:{sortedNames.size}"
  let mut emitted := 0
  let mut emittedEdges := 0
  for name in sortedNames do
    if emitted < __MATHHUB_GLOBAL_THEOREM_LIMIT__ then
      match env.find? name with
      | some info =>
          logInfo m!"MATHHUB_LIBRARY_ITEM:{name}"
          emitted := emitted + 1
          let mut refs := info.type.getUsedConstants
          for dep in mathhubLibraryProofConstants info do
            if !refs.contains dep then refs := refs.push dep
          for dep in refs do
            if emittedEdges < __MATHHUB_GLOBAL_EDGE_LIMIT__ && dep != name && !dep.isInternal then
              match env.find? dep with
              | some (.thmInfo _) =>
                  logInfo m!"MATHHUB_LIBRARY_EDGE:{name}|||{dep}"
                  emittedEdges := emittedEdges + 1
              | _ => pure ()
      | none => pure ()
  logInfo m!"MATHHUB_LIBRARY_EDGE_COUNT:{emittedEdges}"
'''

LEAN_NAMESPACE_COMMAND = r'''
open Lean Elab Command

elab "#mathhub_list_namespace " id:ident : command => do
  let nsPrefix := id.getId
  let env ← getEnv
  let mut names : Array Name := #[]
  for (name, info) in env.constants do
    if nsPrefix.isPrefixOf name then
      if name == nsPrefix then
        pure ()
      else if name.isInternal then
        pure ()
      else
        match info with
        | .thmInfo _ => names := names.push name
        | _ => pure ()
  let sortedNames := names.qsort Name.quickLt
  logInfo m!"MATHHUB_NAMESPACE_COUNT:{sortedNames.size}"
  let mut emitted := 0
  for name in sortedNames do
    if emitted < __MATHHUB_LIMIT__ then
      logInfo m!"MATHHUB_NAMESPACE_ITEM:{name}"
      emitted := emitted + 1
'''


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def imports_fingerprint(imports: list[str]) -> str:
    payload = json.dumps(imports, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def global_library_source(imports: list[str]) -> str:
    import_lines = "\n".join(f"import {module}" for module in imports)
    command = (
        LEAN_GLOBAL_LIBRARY_COMMAND
        .replace("__MATHHUB_GLOBAL_THEOREM_LIMIT__", str(MAX_GLOBAL_LIBRARY_THEOREMS))
        .replace("__MATHHUB_GLOBAL_EDGE_LIMIT__", str(MAX_GLOBAL_LIBRARY_EDGES))
    )
    return f"{import_lines}\n\n{command}\n#mathhub_scan_library\n"


def parse_global_library(output: str) -> tuple[list[str], list[tuple[str, str]], int]:
    names = sorted(set(core.parse_marker(output, "MATHHUB_LIBRARY_ITEM:")))
    edge_matches = re.findall(r"MATHHUB_LIBRARY_EDGE:([^\s|]+)\|\|\|([^\s]+)", output or "")
    edges: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for source, target in edge_matches:
        pair = (source.strip(), target.strip())
        if pair[0] and pair[1] and pair[0] != pair[1] and pair not in seen:
            seen.add(pair)
            edges.append(pair)
    count_match = re.search(r"MATHHUB_LIBRARY_COUNT:([0-9]+)", output or "")
    total = int(count_match.group(1)) if count_match else len(names)
    return names, edges, total


def _ensure_global_cache_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS library_global_scans (
            environment_hash TEXT PRIMARY KEY,
            imports_hash TEXT NOT NULL,
            total_theorems INTEGER NOT NULL,
            indexed_theorems INTEGER NOT NULL,
            indexed_edges INTEGER NOT NULL,
            complete INTEGER NOT NULL CHECK(complete IN (0,1)),
            duration_ms INTEGER NOT NULL DEFAULT 0,
            scanned_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS library_global_theorems (
            environment_hash TEXT NOT NULL,
            lean_name TEXT NOT NULL,
            namespace TEXT NOT NULL,
            short_name TEXT NOT NULL,
            PRIMARY KEY(environment_hash, lean_name)
        );
        CREATE INDEX IF NOT EXISTS idx_library_global_namespace
            ON library_global_theorems(environment_hash, namespace, lean_name);
        CREATE TABLE IF NOT EXISTS library_global_edges (
            environment_hash TEXT NOT NULL,
            source_name TEXT NOT NULL,
            target_name TEXT NOT NULL,
            PRIMARY KEY(environment_hash, source_name, target_name)
        );
        CREATE INDEX IF NOT EXISTS idx_library_global_edge_source
            ON library_global_edges(environment_hash, source_name);
        CREATE INDEX IF NOT EXISTS idx_library_global_edge_target
            ON library_global_edges(environment_hash, target_name);
        """
    )


def _global_scan_record(environment_hash: str) -> dict[str, Any] | None:
    with core.FORMAL.connect() as conn:
        _ensure_global_cache_schema(conn)
        row = conn.execute(
            "SELECT * FROM library_global_scans WHERE environment_hash=?",
            (environment_hash,),
        ).fetchone()
    return dict(row) if row is not None else None


def ensure_global_library_scan(*, refresh: bool = False) -> dict[str, Any]:
    environment = core.runtime_status()
    if not environment.get("ready"):
        return {"ok": False, "ready": False, "error": str(environment.get("message") or "Lean environment unavailable")}
    environment_hash = str(environment.get("environment_hash") or "")
    if not environment_hash:
        return {"ok": False, "ready": False, "error": "pinned Lean environment has no environment hash"}
    imports = ["Mathlib"]
    imports_hash = imports_fingerprint(imports)
    existing = None if refresh else _global_scan_record(environment_hash)
    if existing is not None:
        return {"ok": True, "ready": True, "cache_hit": True, **existing}

    lock = _namespace_scan_lock(environment_hash, imports_hash, "__GLOBAL__")
    with lock:
        existing = None if refresh else _global_scan_record(environment_hash)
        if existing is not None:
            return {"ok": True, "ready": True, "cache_hit": True, **existing}
        run = core.invoke_lean(
            global_library_source(imports),
            timeout_seconds=max(core.INDEX_TIMEOUT_SECONDS, 300),
        )
        if not run.get("ok"):
            return {
                "ok": False,
                "ready": False,
                "cache_hit": False,
                "error": str(run.get("engine_error") or run.get("output") or "Lean library scan failed")[-4000:],
                "duration_ms": int(run.get("duration_ms") or 0),
            }
        names, edges, total = parse_global_library(str(run.get("output") or ""))
        names = names[:MAX_GLOBAL_LIBRARY_THEOREMS]
        name_set = set(names)
        edges = [(a, b) for a, b in edges if a in name_set and b in name_set][:MAX_GLOBAL_LIBRARY_EDGES]
        now = utc_now()
        with core.FORMAL.connect() as conn:
            _ensure_global_cache_schema(conn)
            conn.execute("DELETE FROM library_global_theorems WHERE environment_hash=?", (environment_hash,))
            conn.execute("DELETE FROM library_global_edges WHERE environment_hash=?", (environment_hash,))
            if names:
                conn.executemany(
                    """INSERT INTO library_global_theorems(environment_hash,lean_name,namespace,short_name)
                       VALUES(?,?,?,?)""",
                    [
                        (
                            environment_hash,
                            name,
                            name.rsplit(".", 1)[0] if "." in name else "",
                            name.rsplit(".", 1)[-1],
                        )
                        for name in names
                    ],
                )
            if edges:
                conn.executemany(
                    """INSERT OR IGNORE INTO library_global_edges(environment_hash,source_name,target_name)
                       VALUES(?,?,?)""",
                    [(environment_hash, a, b) for a, b in edges],
                )
            conn.execute(
                """INSERT INTO library_global_scans(
                       environment_hash,imports_hash,total_theorems,indexed_theorems,indexed_edges,
                       complete,duration_ms,scanned_at
                   ) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(environment_hash) DO UPDATE SET
                       imports_hash=excluded.imports_hash,
                       total_theorems=excluded.total_theorems,
                       indexed_theorems=excluded.indexed_theorems,
                       indexed_edges=excluded.indexed_edges,
                       complete=excluded.complete,
                       duration_ms=excluded.duration_ms,
                       scanned_at=excluded.scanned_at""",
                (
                    environment_hash,
                    imports_hash,
                    total,
                    len(names),
                    len(edges),
                    1 if total <= len(names) else 0,
                    int(run.get("duration_ms") or 0),
                    now,
                ),
            )
        return {
            "ok": True,
            "ready": True,
            "cache_hit": False,
            "environment_hash": environment_hash,
            "imports_hash": imports_hash,
            "total_theorems": total,
            "indexed_theorems": len(names),
            "indexed_edges": len(edges),
            "complete": total <= len(names),
            "duration_ms": int(run.get("duration_ms") or 0),
            "scanned_at": now,
        }


def global_library_status() -> dict[str, Any]:
    environment = core.runtime_status()
    environment_hash = str(environment.get("environment_hash") or "")
    record = _global_scan_record(environment_hash) if environment_hash else None
    return {
        "ok": True,
        "runtime_ready": bool(environment.get("ready")),
        "environment_hash": environment_hash,
        "indexed": record is not None,
        "scan": record,
    }


def _top_namespace(name: str) -> str:
    return name.split(".", 1)[0] if "." in name else "(root)"


def global_library_world() -> dict[str, Any]:
    status = global_library_status()
    environment_hash = str(status.get("environment_hash") or "")
    if not status.get("indexed") or not environment_hash:
        return {"ok": True, "indexed": False, "nodes": [], "edges": [], "theorem_count": 0, "cluster_count": 0}
    with core.FORMAL.connect() as conn:
        _ensure_global_cache_schema(conn)
        rows = conn.execute(
            """SELECT lean_name FROM library_global_theorems
               WHERE environment_hash=? ORDER BY lean_name""",
            (environment_hash,),
        ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        key = _top_namespace(str(row["lean_name"]))
        counts[key] = counts.get(key, 0) + 1
    nodes = [
        {
            "id": f"LIBNS:{namespace}",
            "kind": "cluster",
            "cluster_source": "mathlib-library",
            "cluster_key": f"libns:{namespace}",
            "namespace_path": namespace,
            "title": namespace,
            "claim_count": count,
            "library_count": count,
        }
        for namespace, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    return {
        "ok": True,
        "indexed": True,
        "library_mode": True,
        "nodes": nodes,
        "edges": [],
        "theorem_count": sum(counts.values()),
        "claim_count": 0,
        "cluster_count": len(nodes),
    }


def global_library_cluster(namespace: str, *, limit: int = 100) -> dict[str, Any]:
    status = global_library_status()
    environment_hash = str(status.get("environment_hash") or "")
    if not status.get("indexed") or not environment_hash:
        return {"ok": True, "indexed": False, "nodes": [], "edges": []}
    namespace = str(namespace or "").strip()
    prefix = "" if namespace == "(root)" else namespace
    limit = max(1, min(int(limit), 200))
    with core.FORMAL.connect() as conn:
        _ensure_global_cache_schema(conn)
        if prefix:
            rows = conn.execute(
                """SELECT lean_name,namespace,short_name FROM library_global_theorems
                   WHERE environment_hash=? AND lean_name LIKE ?
                   ORDER BY lean_name""",
                (environment_hash, f"{prefix}.%"),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT lean_name,namespace,short_name FROM library_global_theorems
                   WHERE environment_hash=? AND lean_name NOT LIKE '%.%'
                   ORDER BY lean_name""",
                (environment_hash,),
            ).fetchall()
    child_counts: dict[str, int] = {}
    direct: list[str] = []
    for row in rows:
        name = str(row["lean_name"])
        if prefix:
            rest = name[len(prefix) + 1 :] if name.startswith(prefix + ".") else name
        else:
            rest = name
        if "." in rest:
            child = rest.split(".", 1)[0]
            full = f"{prefix}.{child}" if prefix else child
            child_counts[full] = child_counts.get(full, 0) + 1
        else:
            direct.append(name)
    nodes: list[dict[str, Any]] = [
        {
            "id": f"LIBNS:{child}",
            "kind": "cluster",
            "cluster_source": "mathlib-library",
            "cluster_key": f"libns:{child}",
            "namespace_path": child,
            "title": child,
            "claim_count": count,
            "library_count": count,
            "parent_cluster_id": f"LIBNS:{namespace}",
        }
        for child, count in sorted(child_counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]
    remaining = max(0, limit - len(nodes))
    theorem_names = direct[:remaining]
    nodes.extend(
        {
            "id": f"LIB:{name}",
            "kind": "library-theorem",
            "lean_name": name,
            "title": name.rsplit(".", 1)[-1],
            "namespace_path": name.rsplit(".", 1)[0] if "." in name else "",
            "parent_cluster_id": f"LIBNS:{namespace}",
            "status": "library",
        }
        for name in theorem_names
    )
    node_names = set(theorem_names)
    edges: list[dict[str, Any]] = []
    if node_names:
        placeholders = ",".join("?" for _ in node_names)
        with core.FORMAL.connect() as conn:
            edge_rows = conn.execute(
                f"""SELECT source_name,target_name FROM library_global_edges
                    WHERE environment_hash=?
                      AND source_name IN({placeholders})
                      AND target_name IN({placeholders})""",
                (environment_hash, *node_names, *node_names),
            ).fetchall()
        edges = [
            {
                "from": f"LIB:{row['source_name']}",
                "to": f"LIB:{row['target_name']}",
                "evidence_kind": "mathlib-direct-ref",
                "proof_count": 0,
                "proof_ids": [],
            }
            for row in edge_rows
        ]
    return {"ok": True, "indexed": True, "library_mode": True, "nodes": nodes, "edges": edges}


def namespace_source(imports: list[str], namespace: str, limit: int) -> str:
    import_lines = "\n".join(f"import {module}" for module in imports)
    command = LEAN_NAMESPACE_COMMAND.replace("__MATHHUB_LIMIT__", str(limit))
    return f"{import_lines}\n\n{command}\n#mathhub_list_namespace {namespace}\n"


def parse_namespace_count(output: str) -> int:
    match = re.search(r"MATHHUB_NAMESPACE_COUNT:([0-9]+)", output)
    return int(match.group(1)) if match else 0


def _ensure_cache_schema(conn: sqlite3.Connection) -> None:
    # Mutable derived cache keyed by immutable Lean environment/import context.
    # This remains navigation/read state, not mathematical source of truth.
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS library_namespace_scans (
            environment_hash TEXT NOT NULL,
            imports_hash TEXT NOT NULL,
            namespace TEXT NOT NULL,
            total_theorems INTEGER NOT NULL,
            indexed_theorems INTEGER NOT NULL,
            complete INTEGER NOT NULL CHECK(complete IN (0,1)),
            duration_ms INTEGER NOT NULL DEFAULT 0,
            scanned_at TEXT NOT NULL,
            PRIMARY KEY(environment_hash, imports_hash, namespace)
        );
        CREATE TABLE IF NOT EXISTS library_theorem_index (
            environment_hash TEXT NOT NULL,
            imports_hash TEXT NOT NULL,
            namespace TEXT NOT NULL,
            lean_name TEXT NOT NULL,
            short_name TEXT NOT NULL,
            PRIMARY KEY(environment_hash, imports_hash, namespace, lean_name)
        );
        CREATE INDEX IF NOT EXISTS idx_library_theorem_namespace_name
            ON library_theorem_index(environment_hash, imports_hash, namespace, lean_name);
        CREATE INDEX IF NOT EXISTS idx_library_theorem_namespace_short
            ON library_theorem_index(environment_hash, imports_hash, namespace, short_name);
        """
    )


def _registered_names(names: list[str], environment_hash: str) -> dict[str, str]:
    if not names or not environment_hash:
        return {}
    placeholders = ",".join("?" for _ in names)
    with core.FORMAL.connect() as conn:
        rows = conn.execute(
            f"""SELECT lean_name,claim_id FROM formal_bindings
                WHERE environment_hash=? AND lean_name IN({placeholders})
                ORDER BY id DESC""",
            (environment_hash, *names),
        ).fetchall()
    result: dict[str, str] = {}
    for row in rows:
        name = str(row["lean_name"])
        result.setdefault(name, claim_ref(int(row["claim_id"])))
    return result


def _scan_namespace(
    *,
    imports: list[str],
    namespace: str,
    environment_hash: str,
    imports_hash: str,
) -> dict[str, Any]:
    run = core.invoke_lean(
        namespace_source(imports, namespace, MAX_NAMESPACE_INDEX_ITEMS),
        timeout_seconds=core.INDEX_TIMEOUT_SECONDS,
    )
    if run.get("engine_error"):
        return {"error": run["engine_error"], **run}
    output = str(run.get("output") or "")
    if not run.get("ok"):
        return {"error": "Lean namespace enumeration failed", **run}

    names = sorted(core.parse_marker(output, "MATHHUB_NAMESPACE_ITEM:"))
    total = parse_namespace_count(output)
    complete = total <= len(names)
    with core.FORMAL.connect() as conn:
        _ensure_cache_schema(conn)
        conn.execute(
            """DELETE FROM library_theorem_index
               WHERE environment_hash=? AND imports_hash=? AND namespace=?""",
            (environment_hash, imports_hash, namespace),
        )
        if names:
            conn.executemany(
                """INSERT INTO library_theorem_index(
                       environment_hash,imports_hash,namespace,lean_name,short_name
                   ) VALUES(?,?,?,?,?)""",
                [
                    (
                        environment_hash,
                        imports_hash,
                        namespace,
                        name,
                        name.rsplit(".", 1)[-1],
                    )
                    for name in names
                ],
            )
        conn.execute(
            """INSERT INTO library_namespace_scans(
                   environment_hash,imports_hash,namespace,total_theorems,
                   indexed_theorems,complete,duration_ms,scanned_at
               ) VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(environment_hash,imports_hash,namespace) DO UPDATE SET
                   total_theorems=excluded.total_theorems,
                   indexed_theorems=excluded.indexed_theorems,
                   complete=excluded.complete,
                   duration_ms=excluded.duration_ms,
                   scanned_at=excluded.scanned_at""",
            (
                environment_hash,
                imports_hash,
                namespace,
                total,
                len(names),
                1 if complete else 0,
                int(run.get("duration_ms") or 0),
                utc_now(),
            ),
        )
    return {
        "ok": True,
        "total_theorems": total,
        "indexed_theorems": len(names),
        "index_complete": complete,
        "duration_ms": int(run.get("duration_ms") or 0),
    }


def _scan_record(
    environment_hash: str,
    imports_hash: str,
    namespace: str,
) -> dict[str, Any] | None:
    with core.FORMAL.connect() as conn:
        _ensure_cache_schema(conn)
        row = conn.execute(
            """SELECT * FROM library_namespace_scans
               WHERE environment_hash=? AND imports_hash=? AND namespace=?""",
            (environment_hash, imports_hash, namespace),
        ).fetchone()
    return dict(row) if row is not None else None


def _search_cached(
    *,
    environment_hash: str,
    imports_hash: str,
    namespace: str,
    query: str,
    limit: int,
) -> tuple[list[str], int]:
    q = query.casefold()
    with core.FORMAL.connect() as conn:
        _ensure_cache_schema(conn)
        if q:
            pattern = f"%{q}%"
            prefix = f"{q}%"
            exact = q
            count = conn.execute(
                """SELECT COUNT(*) n FROM library_theorem_index
                   WHERE environment_hash=? AND imports_hash=? AND namespace=?
                     AND (lower(lean_name) LIKE ? OR lower(short_name) LIKE ?)""",
                (environment_hash, imports_hash, namespace, pattern, pattern),
            ).fetchone()
            rows = conn.execute(
                """SELECT lean_name FROM library_theorem_index
                   WHERE environment_hash=? AND imports_hash=? AND namespace=?
                     AND (lower(lean_name) LIKE ? OR lower(short_name) LIKE ?)
                   ORDER BY CASE
                       WHEN lower(short_name)=? THEN 0
                       WHEN lower(short_name) LIKE ? THEN 1
                       ELSE 2 END,
                     length(short_name), lean_name
                   LIMIT ?""",
                (
                    environment_hash,
                    imports_hash,
                    namespace,
                    pattern,
                    pattern,
                    exact,
                    prefix,
                    limit,
                ),
            ).fetchall()
        else:
            count = conn.execute(
                """SELECT COUNT(*) n FROM library_theorem_index
                   WHERE environment_hash=? AND imports_hash=? AND namespace=?""",
                (environment_hash, imports_hash, namespace),
            ).fetchone()
            rows = conn.execute(
                """SELECT lean_name FROM library_theorem_index
                   WHERE environment_hash=? AND imports_hash=? AND namespace=?
                   ORDER BY lean_name LIMIT ?""",
                (environment_hash, imports_hash, namespace, limit),
            ).fetchall()
    return [str(row["lean_name"]) for row in rows], int(count["n"] if count else 0)


def _ingestion_plan_cached(
    *,
    environment_hash: str,
    imports_hash: str,
    namespace: str,
    scan: dict[str, Any],
    chunk_size: int,
) -> dict[str, Any]:
    total = int(scan.get("total_theorems") or 0)
    indexed = int(scan.get("indexed_theorems") or 0)
    if not bool(scan.get("complete")):
        return {
            "ok": False,
            "mode": "ingestion-plan",
            "error": (
                "namespace theorem index is incomplete; choose a narrower Lean namespace "
                "before creating durable import tasks"
            ),
            "namespace": namespace,
            "total_theorems": total,
            "indexed_theorems": indexed,
            "index_complete": False,
        }

    with core.FORMAL.connect() as conn:
        _ensure_cache_schema(conn)
        rows = conn.execute(
            """SELECT i.lean_name
               FROM library_theorem_index i
               WHERE i.environment_hash=? AND i.imports_hash=? AND i.namespace=?
                 AND NOT EXISTS (
                   SELECT 1 FROM formal_bindings f
                   WHERE f.environment_hash=i.environment_hash
                     AND f.lean_name=i.lean_name
                 )
               ORDER BY i.lean_name
               LIMIT ?""",
            (
                environment_hash,
                imports_hash,
                namespace,
                MAX_INGESTION_PLAN_ROOTS + 1,
            ),
        ).fetchall()
    names = [str(row["lean_name"]) for row in rows]
    if len(names) > MAX_INGESTION_PLAN_ROOTS:
        return {
            "ok": False,
            "mode": "ingestion-plan",
            "error": "namespace ingestion plan exceeds the hard root cap; choose a narrower namespace",
            "namespace": namespace,
            "total_theorems": total,
            "indexed_theorems": indexed,
            "index_complete": True,
        }

    chunks = [names[offset : offset + chunk_size] for offset in range(0, len(names), chunk_size)]
    registered = max(0, indexed - len(names))
    suggested_budget = min(5000, max(chunk_size, chunk_size * 3))
    return {
        "ok": True,
        "mode": "ingestion-plan",
        "namespace": namespace,
        "total_theorems": total,
        "indexed_theorems": indexed,
        "index_complete": True,
        "already_registered": registered,
        "unregistered_roots": len(names),
        "chunk_size": chunk_size,
        "task_count": len(chunks),
        "suggested_claim_budget_per_task": suggested_budget,
        "root_chunks": chunks,
    }


def _list_namespace_from_global_cache(
    *,
    environment_hash: str,
    namespace: str,
    query: str,
    limit: int,
) -> dict[str, Any] | None:
    scan = _global_scan_record(environment_hash)
    if scan is None:
        return None
    prefix = f"{namespace}.%"
    q = query.casefold()
    with core.FORMAL.connect() as conn:
        _ensure_global_cache_schema(conn)
        if q:
            pattern = f"%{q}%"
            count = conn.execute(
                """SELECT COUNT(*) n FROM library_global_theorems
                   WHERE environment_hash=? AND lean_name LIKE ?
                     AND (lower(lean_name) LIKE ? OR lower(short_name) LIKE ?)""",
                (environment_hash, prefix, pattern, pattern),
            ).fetchone()
            rows = conn.execute(
                """SELECT lean_name FROM library_global_theorems
                   WHERE environment_hash=? AND lean_name LIKE ?
                     AND (lower(lean_name) LIKE ? OR lower(short_name) LIKE ?)
                   ORDER BY CASE
                     WHEN lower(short_name)=? THEN 0
                     WHEN lower(short_name) LIKE ? THEN 1
                     ELSE 2 END,
                     length(short_name), lean_name
                   LIMIT ?""",
                (
                    environment_hash,
                    prefix,
                    pattern,
                    pattern,
                    q,
                    f"{q}%",
                    limit,
                ),
            ).fetchall()
        else:
            count = conn.execute(
                """SELECT COUNT(*) n FROM library_global_theorems
                   WHERE environment_hash=? AND lean_name LIKE ?""",
                (environment_hash, prefix),
            ).fetchone()
            rows = conn.execute(
                """SELECT lean_name FROM library_global_theorems
                   WHERE environment_hash=? AND lean_name LIKE ?
                   ORDER BY lean_name LIMIT ?""",
                (environment_hash, prefix, limit),
            ).fetchall()
    names = [str(row["lean_name"]) for row in rows]
    matched = int(count["n"] if count else 0)
    registered = _registered_names(names, environment_hash)
    items = [
        {
            "declaration_name": name,
            "short_name": name.rsplit(".", 1)[-1],
            "namespace": name.rsplit(".", 1)[0] if "." in name else "",
            "registered": name in registered,
            "claim_id": registered.get(name),
        }
        for name in names
    ]
    return {
        "ok": True,
        "namespace": namespace,
        "imports": ["Mathlib"],
        "query": query,
        "total_theorems": matched if not query else int(
            _global_namespace_count(environment_hash, namespace)
        ),
        "indexed_theorems": int(_global_namespace_count(environment_hash, namespace)),
        "matched_theorems": matched,
        "index_complete": True,
        "returned": len(items),
        "limit": limit,
        "truncated": matched > len(items),
        "items": items,
        "cache_hit": True,
        "lean_invocations": 0,
        "duration_ms": 0,
        "environment_hash": environment_hash,
        "imports_hash": str(scan.get("imports_hash") or ""),
        "scanned_at": str(scan.get("scanned_at") or ""),
        "global_cache": True,
    }


def _global_namespace_count(environment_hash: str, namespace: str) -> int:
    with core.FORMAL.connect() as conn:
        _ensure_global_cache_schema(conn)
        row = conn.execute(
            """SELECT COUNT(*) n FROM library_global_theorems
               WHERE environment_hash=? AND lean_name LIKE ?""",
            (environment_hash, f"{namespace}.%"),
        ).fetchone()
    return int(row["n"] if row else 0)


def list_namespace_declarations(payload: dict[str, Any]) -> dict[str, Any]:
    namespace = str(payload.get("namespace") or payload.get("prefix") or "").strip()
    if not namespace or not NAMESPACE_RE.fullmatch(namespace):
        raise ValueError("namespace must be a conservative qualified Lean namespace")
    imports = core.validate_imports(payload.get("imports") or ["Mathlib"])
    query = str(payload.get("query") or payload.get("q") or "").strip()[:MAX_NAMESPACE_QUERY_CHARS]
    try:
        limit = int(payload.get("limit") or DEFAULT_NAMESPACE_LIMIT)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    limit = max(1, min(limit, MAX_NAMESPACE_LIMIT))
    try:
        chunk_size = int(payload.get("chunk_size") or DEFAULT_INGESTION_CHUNK_SIZE)
    except (TypeError, ValueError) as exc:
        raise ValueError("chunk_size must be an integer") from exc
    chunk_size = max(MIN_INGESTION_CHUNK_SIZE, min(chunk_size, MAX_INGESTION_CHUNK_SIZE))

    environment = core.runtime_status()
    if not environment.get("ready"):
        return {
            "ok": False,
            "error": str(environment.get("message") or "Lean environment unavailable"),
        }
    environment_hash = str(environment.get("environment_hash") or "")
    if not environment_hash:
        raise ValueError("pinned Lean environment has no environment hash")
    imports_hash = imports_fingerprint(imports)

    if imports == ["Mathlib"] and not payload.get("refresh") and not payload.get("ingestion_plan"):
        global_cached = _list_namespace_from_global_cache(
            environment_hash=environment_hash,
            namespace=namespace,
            query=query,
            limit=limit,
        )
        if global_cached is not None:
            return global_cached

    scan = None if payload.get("refresh") else _scan_record(environment_hash, imports_hash, namespace)
    cache_hit = scan is not None
    scan_duration_ms = 0
    if scan is None:
        with _namespace_scan_lock(environment_hash, imports_hash, namespace):
            # A background warmup and a first browser request may arrive together.
            # Recheck after acquiring the lock so they never launch duplicate Lean scans.
            scan = None if payload.get("refresh") else _scan_record(environment_hash, imports_hash, namespace)
            cache_hit = scan is not None
            if scan is None:
                scanned = _scan_namespace(
                    imports=imports,
                    namespace=namespace,
                    environment_hash=environment_hash,
                    imports_hash=imports_hash,
                )
                if not scanned.get("ok"):
                    return scanned
                scan_duration_ms = int(scanned.get("duration_ms") or 0)
                scan = _scan_record(environment_hash, imports_hash, namespace)
                if scan is None:
                    raise RuntimeError("namespace index scan was not persisted")

    if payload.get("ingestion_plan"):
        plan = _ingestion_plan_cached(
            environment_hash=environment_hash,
            imports_hash=imports_hash,
            namespace=namespace,
            scan=scan,
            chunk_size=chunk_size,
        )
        plan.update(
            {
                "imports": imports,
                "cache_hit": cache_hit,
                "lean_invocations": 0 if cache_hit else 1,
                "duration_ms": scan_duration_ms,
                "environment_hash": environment_hash,
                "imports_hash": imports_hash,
                "scanned_at": str(scan.get("scanned_at") or ""),
            }
        )
        return plan

    names, matched = _search_cached(
        environment_hash=environment_hash,
        imports_hash=imports_hash,
        namespace=namespace,
        query=query,
        limit=limit,
    )
    registered = _registered_names(names, environment_hash)
    items = [
        {
            "declaration_name": name,
            "short_name": name.rsplit(".", 1)[-1],
            "namespace": name.rsplit(".", 1)[0] if "." in name else "",
            "registered": name in registered,
            "claim_id": registered.get(name),
        }
        for name in names
    ]
    total = int(scan.get("total_theorems") or 0)
    indexed = int(scan.get("indexed_theorems") or 0)
    index_complete = bool(scan.get("complete"))
    return {
        "ok": True,
        "namespace": namespace,
        "imports": imports,
        "query": query,
        "total_theorems": total,
        "indexed_theorems": indexed,
        "matched_theorems": matched,
        "index_complete": index_complete,
        "returned": len(items),
        "limit": limit,
        "truncated": matched > len(items) or (not query and total > len(items)),
        "items": items,
        "cache_hit": cache_hit,
        "lean_invocations": 0 if cache_hit else 1,
        "duration_ms": scan_duration_ms,
        "environment_hash": environment_hash,
        "imports_hash": imports_hash,
        "scanned_at": str(scan.get("scanned_at") or ""),
    }
