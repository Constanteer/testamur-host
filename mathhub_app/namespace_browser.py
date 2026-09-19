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
