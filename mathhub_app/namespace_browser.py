from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import server as core
from presentation import canonical_from_declaration
from registry import claim_ref


NAMESPACE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
DEFAULT_NAMESPACE_LIMIT = 80
MAX_NAMESPACE_LIMIT = 200
MAX_NAMESPACE_INDEX_ITEMS = 25_000
MAX_GLOBAL_LIBRARY_THEOREMS = 250_000
MAX_GLOBAL_LIBRARY_EDGES = 300_000
MAX_NAMESPACE_QUERY_CHARS = 160
DEFAULT_INGESTION_CHUNK_SIZE = 400
MAX_INGESTION_CHUNK_SIZE = 500
MIN_INGESTION_CHUNK_SIZE = 40
MAX_INGESTION_PLAN_ROOTS = MAX_NAMESPACE_INDEX_ITEMS

_NAMESPACE_SCAN_LOCKS_GUARD = threading.Lock()
_NAMESPACE_SCAN_LOCKS: dict[tuple[str, str, str], threading.Lock] = {}

_GLOBAL_LIBRARY_STATE_LOCK = threading.Lock()
_GLOBAL_LIBRARY_STATE: dict[str, Any] = {
    "state": "idle",
    "attempts": 0,
    "environment_hash": None,
    "started_at": None,
    "finished_at": None,
    "last_error": None,
    "last_duration_ms": None,
}


def _set_global_library_state(**updates: Any) -> None:
    with _GLOBAL_LIBRARY_STATE_LOCK:
        _GLOBAL_LIBRARY_STATE.update(updates)


def _global_library_state() -> dict[str, Any]:
    with _GLOBAL_LIBRARY_STATE_LOCK:
        return dict(_GLOBAL_LIBRARY_STATE)


def _namespace_scan_lock(environment_hash: str, imports_hash: str, namespace: str) -> threading.Lock:
    key = (environment_hash, imports_hash, namespace)
    with _NAMESPACE_SCAN_LOCKS_GUARD:
        lock = _NAMESPACE_SCAN_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _NAMESPACE_SCAN_LOCKS[key] = lock
        return lock

MATHLIB_DECL_RE = re.compile(
    r"^\s*(?P<prefix>(?:(?:@\[[^\]]*\]\s*)|(?:(?:private|protected|noncomputable|unsafe)\s+))*)"
    r"(?P<kind>theorem|lemma)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*)\b"
)
MATHLIB_NAMESPACE_RE = re.compile(
    r"^\s*namespace\s+([A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*)\s*$"
)
MATHLIB_SECTION_RE = re.compile(
    r"^\s*section(?:\s+([A-Za-z_][A-Za-z0-9_']*))?\s*$"
)
MATHLIB_END_RE = re.compile(
    r"^\s*end(?:\s+([A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*))?\s*$"
)


def _mathlib_source_root() -> Path:
    override = os.environ.get("MATHHUB_MATHLIB_SOURCE_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (core.ROOT / ".lake" / "packages" / "mathlib" / "Mathlib").resolve()


def _strip_lean_comments(lines: list[str]) -> list[str]:
    """Remove Lean line/nested block comments while preserving line structure."""
    cleaned: list[str] = []
    block_depth = 0
    for raw in lines:
        out: list[str] = []
        i = 0
        in_string = False
        escaped = False
        while i < len(raw):
            if block_depth:
                if raw.startswith("/-", i):
                    block_depth += 1
                    i += 2
                    continue
                if raw.startswith("-/", i):
                    block_depth -= 1
                    i += 2
                    continue
                i += 1
                continue
            ch = raw[i]
            if in_string:
                out.append(ch)
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                i += 1
                continue
            if raw.startswith("--", i):
                break
            if raw.startswith("/-", i):
                block_depth = 1
                i += 2
                continue
            if ch == '"':
                in_string = True
            out.append(ch)
            i += 1
        cleaned.append("".join(out))
    return cleaned


def _declaration_source(lines: list[str], start: int, *, max_lines: int = 48) -> str:
    """Capture a readable theorem/lemma declaration header without its proof."""
    parts: list[str] = []
    for raw in lines[start : min(len(lines), start + max_lines)]:
        line = raw.rstrip()
        parts.append(line)
        joined = "\n".join(parts)
        marker = joined.find(":=")
        if marker >= 0:
            return joined[:marker].strip()
        if len(parts) > 1 and re.match(r"^\s*(?:theorem|lemma|def|namespace|section|end)\b", line):
            parts.pop()
            break
    return "\n".join(parts).strip()


def _scan_mathlib_source_tree() -> tuple[list[dict[str, Any]], int, int]:
    """Index public theorem/lemma declarations from the installed Mathlib sources.

    This is deliberately a source-file navigation index. It does not invoke Lean
    and does not claim kernel verification; explicit Import remains that boundary.
    """
    root = _mathlib_source_root()
    if not root.is_dir():
        raise FileNotFoundError(f"Mathlib source tree not found: {root}")

    entries: list[dict[str, Any]] = []
    total_seen = 0
    files_scanned = 0
    for file_path in sorted(root.rglob("*.lean")):
        try:
            original = file_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        files_scanned += 1
        code = _strip_lean_comments(original)
        block_stack: list[tuple[str, tuple[str, ...]]] = []
        namespace_parts: list[str] = []

        for index, line in enumerate(code):
            ns_match = MATHLIB_NAMESPACE_RE.match(line)
            if ns_match:
                parts = tuple(part for part in ns_match.group(1).split(".") if part)
                namespace_parts.extend(parts)
                block_stack.append(("namespace", parts))
                continue
            section_match = MATHLIB_SECTION_RE.match(line)
            if section_match:
                block_stack.append(("section", ()))
                continue
            if MATHLIB_END_RE.match(line):
                if block_stack:
                    kind, parts = block_stack.pop()
                    if kind == "namespace" and parts:
                        del namespace_parts[-len(parts):]
                continue

            decl = MATHLIB_DECL_RE.match(line)
            if not decl:
                continue
            prefix = str(decl.group("prefix") or "")
            if re.search(r"\bprivate\s+", prefix):
                continue
            raw_name = str(decl.group("name"))
            full_name = ".".join([*namespace_parts, raw_name]) if namespace_parts else raw_name
            total_seen += 1
            if len(entries) >= MAX_GLOBAL_LIBRARY_THEOREMS:
                continue

            relative = file_path.relative_to(root)
            module_parts = ("Mathlib", *relative.with_suffix("").parts)
            entries.append(
                {
                    "lean_name": full_name,
                    "namespace": full_name.rsplit(".", 1)[0] if "." in full_name else "",
                    "short_name": full_name.rsplit(".", 1)[-1],
                    "source_module": ".".join(module_parts),
                    "source_path": str(Path("Mathlib") / relative),
                    "source_line": index + 1,
                    "declaration_source": _declaration_source(original, index),
                }
            )

    # A duplicated declaration name cannot coexist in one Lean environment. If
    # source parsing encounters generated/shadowed text, retain one deterministic
    # source location rather than inventing multiple library nodes.
    deduped: dict[str, dict[str, Any]] = {}
    for entry in entries:
        deduped.setdefault(str(entry["lean_name"]), entry)
    return list(deduped.values()), total_seen, files_scanned


LEAN_REFERENCE_TOKEN_RE = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*\b"
)
LEAN_REFERENCE_IGNORED = {
    "theorem", "lemma", "private", "protected", "noncomputable", "unsafe",
    "where", "by", "let", "fun", "forall", "if", "then", "else", "match",
    "with", "Type", "Prop", "Sort", "True", "False",
}


def _resolve_source_reference(
    token: str,
    *,
    namespace: str,
    known_names: set[str],
) -> str | None:
    if token in LEAN_REFERENCE_IGNORED:
        return None
    if token in known_names:
        return token
    # Lean source commonly uses an unqualified theorem from the current or a
    # parent namespace. Resolve only those deterministic namespace candidates;
    # do not guess through imports/open namespaces, which would create fake
    # mathematical edges in a browsing index.
    if "." not in token and namespace:
        parts = namespace.split(".")
        for end in range(len(parts), 0, -1):
            candidate = ".".join([*parts[:end], token])
            if candidate in known_names:
                return candidate
    return None


def _build_source_reference_edges(
    entries: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    """Build conservative theorem-statement reference edges without running Lean.

    These are navigation/source-index edges only. They intentionally remain
    distinct from verified ProofDependency evidence created by explicit Import.
    """
    known_names = {str(entry.get("lean_name") or "") for entry in entries}
    known_names.discard("")
    edges: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        source = str(entry.get("lean_name") or "")
        if not source:
            continue
        declaration = str(entry.get("declaration_source") or "")
        namespace = str(entry.get("namespace") or "")
        per_source = 0
        for token in LEAN_REFERENCE_TOKEN_RE.findall(declaration):
            target = _resolve_source_reference(
                token, namespace=namespace, known_names=known_names
            )
            if not target or target == source:
                continue
            edge = (source, target)
            if edge in seen:
                continue
            seen.add(edge)
            edges.append(edge)
            per_source += 1
            if len(edges) >= MAX_GLOBAL_LIBRARY_EDGES:
                return edges
            # One giant theorem signature should not monopolize the global edge
            # budget or turn a local expansion into a hairball.
            if per_source >= 24:
                break
    return edges


def _rebuild_global_edges_from_cached_index(environment_hash: str) -> int:
    """Backfill source-reference edges for pre-edge global indexes in place."""
    with core.FORMAL.connect() as conn:
        _ensure_global_cache_schema(conn)
        rows = conn.execute(
            """SELECT lean_name,namespace,declaration_source
               FROM library_global_theorems
               WHERE environment_hash=?
               ORDER BY lean_name""",
            (environment_hash,),
        ).fetchall()
        entries = [dict(row) for row in rows]
        edges = _build_source_reference_edges(entries)
        conn.execute(
            "DELETE FROM library_global_edges WHERE environment_hash=?",
            (environment_hash,),
        )
        if edges:
            conn.executemany(
                """INSERT OR IGNORE INTO library_global_edges(
                       environment_hash,source_name,target_name
                   ) VALUES(?,?,?)""",
                [(environment_hash, source, target) for source, target in edges],
            )
        conn.execute(
            """UPDATE library_global_scans
               SET indexed_edges=?
               WHERE environment_hash=?""",
            (len(edges), environment_hash),
        )
    return len(edges)


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
            source_module TEXT NOT NULL DEFAULT '',
            source_path TEXT NOT NULL DEFAULT '',
            source_line INTEGER NOT NULL DEFAULT 0,
            declaration_source TEXT NOT NULL DEFAULT '',
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
        CREATE TABLE IF NOT EXISTS library_theorem_details (
            environment_hash TEXT NOT NULL,
            lean_name TEXT NOT NULL,
            detail_json TEXT NOT NULL,
            inspected_at TEXT NOT NULL,
            PRIMARY KEY(environment_hash, lean_name)
        );
        CREATE INDEX IF NOT EXISTS idx_library_global_edge_source
            ON library_global_edges(environment_hash, source_name);
        CREATE INDEX IF NOT EXISTS idx_library_global_edge_target
            ON library_global_edges(environment_hash, target_name);
        """
    )
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(library_global_theorems)").fetchall()
    }
    for name, ddl in (
        ("source_module", "TEXT NOT NULL DEFAULT ''"),
        ("source_path", "TEXT NOT NULL DEFAULT ''"),
        ("source_line", "INTEGER NOT NULL DEFAULT 0"),
        ("declaration_source", "TEXT NOT NULL DEFAULT ''"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE library_global_theorems ADD COLUMN {name} {ddl}")


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
        error = str(environment.get("message") or "Mathlib environment unavailable")
        _set_global_library_state(state="idle", last_error=error)
        return {"ok": False, "ready": False, "error": error}
    environment_hash = str(environment.get("environment_hash") or "")
    if not environment_hash:
        error = "pinned Mathlib environment has no environment hash"
        _set_global_library_state(state="error", last_error=error)
        return {"ok": False, "ready": False, "error": error}

    imports_hash = imports_fingerprint(["Mathlib"])
    existing = None if refresh else _global_scan_record(environment_hash)
    if existing is not None:
        if int(existing.get("indexed_edges") or 0) == 0 and int(existing.get("indexed_theorems") or 0) > 0:
            existing = {
                **existing,
                "indexed_edges": _rebuild_global_edges_from_cached_index(environment_hash),
            }
        _set_global_library_state(
            state="ready",
            environment_hash=environment_hash,
            finished_at=existing.get("scanned_at"),
            last_error=None,
            last_duration_ms=int(existing.get("duration_ms") or 0),
        )
        return {
            "ok": True,
            "ready": True,
            "cache_hit": True,
            "index_method": "mathlib-source-files",
            "lean_invocations": 0,
            **existing,
        }

    lock = _namespace_scan_lock(environment_hash, imports_hash, "__GLOBAL__")
    with lock:
        existing = None if refresh else _global_scan_record(environment_hash)
        if existing is not None:
            if int(existing.get("indexed_edges") or 0) == 0 and int(existing.get("indexed_theorems") or 0) > 0:
                existing = {
                    **existing,
                    "indexed_edges": _rebuild_global_edges_from_cached_index(environment_hash),
                }
            return {
                "ok": True,
                "ready": True,
                "cache_hit": True,
                "index_method": "mathlib-source-files",
                "lean_invocations": 0,
                **existing,
            }

        state = _global_library_state()
        _set_global_library_state(
            state="scanning",
            attempts=int(state.get("attempts") or 0) + 1,
            environment_hash=environment_hash,
            started_at=utc_now(),
            finished_at=None,
            last_error=None,
        )
        started = time.perf_counter()
        try:
            entries, total, files_scanned = _scan_mathlib_source_tree()
        except (OSError, ValueError) as exc:
            duration_ms = round((time.perf_counter() - started) * 1000)
            error = str(exc)
            _set_global_library_state(
                state="error",
                finished_at=utc_now(),
                last_error=error,
                last_duration_ms=duration_ms,
            )
            return {
                "ok": False,
                "ready": False,
                "cache_hit": False,
                "index_method": "mathlib-source-files",
                "lean_invocations": 0,
                "error": error,
                "duration_ms": duration_ms,
            }

        edges = _build_source_reference_edges(entries)
        duration_ms = round((time.perf_counter() - started) * 1000)
        now = utc_now()
        with core.FORMAL.connect() as conn:
            _ensure_global_cache_schema(conn)
            conn.execute(
                "DELETE FROM library_global_theorems WHERE environment_hash=?",
                (environment_hash,),
            )
            conn.execute(
                "DELETE FROM library_global_edges WHERE environment_hash=?",
                (environment_hash,),
            )
            if entries:
                conn.executemany(
                    """INSERT OR REPLACE INTO library_global_theorems(
                           environment_hash,lean_name,namespace,short_name,
                           source_module,source_path,source_line,declaration_source
                       ) VALUES(?,?,?,?,?,?,?,?)""",
                    [
                        (
                            environment_hash,
                            entry["lean_name"],
                            entry["namespace"],
                            entry["short_name"],
                            entry["source_module"],
                            entry["source_path"],
                            int(entry["source_line"]),
                            entry["declaration_source"],
                        )
                        for entry in entries
                    ],
                )
            if edges:
                conn.executemany(
                    """INSERT OR IGNORE INTO library_global_edges(
                           environment_hash,source_name,target_name
                       ) VALUES(?,?,?)""",
                    [(environment_hash, source, target) for source, target in edges],
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
                    len(entries),
                    len(edges),
                    1 if total <= len(entries) else 0,
                    duration_ms,
                    now,
                ),
            )

        _set_global_library_state(
            state="ready",
            environment_hash=environment_hash,
            finished_at=now,
            last_error=None,
            last_duration_ms=duration_ms,
        )
        return {
            "ok": True,
            "ready": True,
            "cache_hit": False,
            "index_method": "mathlib-source-files",
            "lean_invocations": 0,
            "environment_hash": environment_hash,
            "imports_hash": imports_hash,
            "total_theorems": total,
            "indexed_theorems": len(entries),
            "indexed_edges": len(edges),
            "files_scanned": files_scanned,
            "complete": total <= len(entries),
            "duration_ms": duration_ms,
            "scanned_at": now,
        }


def global_library_status() -> dict[str, Any]:
    environment = core.runtime_status()
    environment_hash = str(environment.get("environment_hash") or "")
    record = _global_scan_record(environment_hash) if environment_hash else None
    lifecycle = _global_library_state()
    if record is not None:
        lifecycle = {
            **lifecycle,
            "state": "ready",
            "environment_hash": environment_hash,
            "finished_at": record.get("scanned_at"),
            "last_error": None,
            "last_duration_ms": int(record.get("duration_ms") or 0),
        }
    return {
        "ok": True,
        "runtime_ready": bool(environment.get("ready")),
        "environment_hash": environment_hash,
        "indexed": record is not None,
        "scan": record,
        "index_method": "mathlib-source-files",
        "lean_invocations": 0,
        "source_root": str(_mathlib_source_root()),
        **lifecycle,
    }

def _top_namespace(name: str) -> str:
    return name.split(".", 1)[0] if "." in name else "(root)"


def _module_path_from_source(source_module: str) -> str:
    module = str(source_module or "").strip()
    if module == "Mathlib":
        return ""
    return module.removeprefix("Mathlib.")


def global_library_world() -> dict[str, Any]:
    """Return the Mathlib root plus its first source-module layer.

    Containment edges are explicit so the first screen is a graph rather than a
    cloud of unrelated circles. The root is navigational; theorem dependency
    edges appear when theorem-bearing branches are expanded.
    """
    status = global_library_status()
    environment_hash = str(status.get("environment_hash") or "")
    if not status.get("indexed") or not environment_hash:
        return {"ok": True, "indexed": False, "nodes": [], "edges": [], "theorem_count": 0, "cluster_count": 0}

    with core.FORMAL.connect() as conn:
        _ensure_global_cache_schema(conn)
        rows = conn.execute(
            """SELECT source_module FROM library_global_theorems
               WHERE environment_hash=?""",
            (environment_hash,),
        ).fetchall()

    counts: dict[str, int] = {}
    for row in rows:
        module_path = _module_path_from_source(str(row["source_module"] or ""))
        if not module_path:
            continue
        root = module_path.split(".", 1)[0]
        counts[root] = counts.get(root, 0) + 1

    root_id = "LIBMOD:(root)"
    nodes: list[dict[str, Any]] = [
        {
            "id": root_id,
            "kind": "cluster",
            "cluster_source": "mathlib-module",
            "cluster_key": "libmod:(root)",
            "module_path": "(root)",
            "namespace_path": "(root)",
            "title": "Mathlib",
            "claim_count": len(rows),
            "library_count": len(rows),
        }
    ]
    for module, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        nodes.append(
            {
                "id": f"LIBMOD:{module}",
                "kind": "cluster",
                "cluster_source": "mathlib-module",
                "cluster_key": f"libmod:{module}",
                "module_path": module,
                "namespace_path": module,
                "title": module,
                "claim_count": count,
                "library_count": count,
                "parent_cluster_id": root_id,
            }
        )

    edges = [
        {
            "from": root_id,
            "to": node["id"],
            "evidence_kind": "module-contains",
            "proof_count": 0,
            "proof_ids": [],
        }
        for node in nodes
        if node["id"] != root_id
    ]
    return {
        "ok": True,
        "indexed": True,
        "library_mode": True,
        "hierarchy": "module",
        "nodes": nodes,
        "edges": edges,
        "theorem_count": len(rows),
        "claim_count": 0,
        "cluster_count": len(nodes),
    }


def global_library_module(module_path: str, *, limit: int = 36) -> dict[str, Any]:
    """Expand one Mathlib source-module node by one level in the current canvas."""
    status = global_library_status()
    environment_hash = str(status.get("environment_hash") or "")
    if not status.get("indexed") or not environment_hash:
        return {"ok": True, "indexed": False, "nodes": [], "edges": []}

    module_path = str(module_path or "").strip()
    prefix = "" if module_path == "(root)" else module_path
    full_prefix = "Mathlib" if not prefix else f"Mathlib.{prefix}"
    limit = max(8, min(int(limit), 48))

    with core.FORMAL.connect() as conn:
        _ensure_global_cache_schema(conn)
        if prefix:
            rows = conn.execute(
                """SELECT lean_name,namespace,short_name,source_module
                   FROM library_global_theorems
                   WHERE environment_hash=?
                     AND (source_module=? OR source_module LIKE ?)
                   ORDER BY source_module,lean_name""",
                (environment_hash, full_prefix, f"{full_prefix}.%"),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT lean_name,namespace,short_name,source_module
                   FROM library_global_theorems
                   WHERE environment_hash=?
                   ORDER BY source_module,lean_name""",
                (environment_hash,),
            ).fetchall()

    child_counts: dict[str, int] = {}
    direct: list[str] = []
    for row in rows:
        source_module = str(row["source_module"] or "")
        source_path = _module_path_from_source(source_module)
        if prefix:
            if source_path == prefix:
                direct.append(str(row["lean_name"]))
                continue
            rest = source_path[len(prefix) + 1 :] if source_path.startswith(prefix + ".") else source_path
        else:
            if not source_path:
                direct.append(str(row["lean_name"]))
                continue
            rest = source_path
        if "." in rest:
            child = rest.split(".", 1)[0]
            child_path = f"{prefix}.{child}" if prefix else child
            child_counts[child_path] = child_counts.get(child_path, 0) + 1
        elif rest:
            child_path = f"{prefix}.{rest}" if prefix else rest
            child_counts[child_path] = child_counts.get(child_path, 0) + 1

    ordered_children = sorted(child_counts.items(), key=lambda item: (-item[1], item[0]))
    child_slice = ordered_children[:limit]
    parent_id = f"LIBMOD:{module_path}"
    nodes: list[dict[str, Any]] = [
        {
            "id": f"LIBMOD:{child}",
            "kind": "cluster",
            "cluster_source": "mathlib-module",
            "cluster_key": f"libmod:{child}",
            "module_path": child,
            "namespace_path": child,
            "title": child.split(".")[-1],
            "claim_count": count,
            "library_count": count,
            "parent_cluster_id": parent_id,
        }
        for child, count in child_slice
    ]

    remaining = max(0, limit - len(nodes))
    theorem_names = direct[:remaining]
    registered = _registered_names(theorem_names, environment_hash)
    nodes.extend(
        {
            "id": f"LIB:{name}",
            "kind": "library-theorem",
            "lean_name": name,
            "title": name.rsplit(".", 1)[-1],
            "namespace_path": name.rsplit(".", 1)[0] if "." in name else "",
            "parent_cluster_id": parent_id,
            "status": "library",
            "registered": name in registered,
            "claim_id": registered.get(name),
        }
        for name in theorem_names
    )

    # Module containment is deliberately a different edge kind from theorem
    # references. Both are visible, but only the latter expresses a statement
    # relationship.
    edges: list[dict[str, Any]] = [
        {
            "from": parent_id,
            "to": node["id"],
            "evidence_kind": "module-contains",
            "proof_count": 0,
            "proof_ids": [],
        }
        for node in nodes
    ]
    if theorem_names:
        placeholders = ",".join("?" for _ in theorem_names)
        with core.FORMAL.connect() as conn:
            edge_rows = conn.execute(
                f"""SELECT source_name,target_name FROM library_global_edges
                    WHERE environment_hash=?
                      AND source_name IN({placeholders})
                      AND target_name IN({placeholders})""",
                (environment_hash, *theorem_names, *theorem_names),
            ).fetchall()
        edges.extend(
            {
                "from": f"LIB:{row['source_name']}",
                "to": f"LIB:{row['target_name']}",
                "evidence_kind": "mathlib-source-ref",
                "proof_count": 0,
                "proof_ids": [],
            }
            for row in edge_rows
        )

    total_children = len(ordered_children)
    total_direct = len(direct)
    return {
        "ok": True,
        "indexed": True,
        "library_mode": True,
        "hierarchy": "module",
        "module_path": module_path,
        "nodes": nodes,
        "edges": edges,
        "child_module_count": total_children,
        "direct_theorem_count": total_direct,
        "shown_count": len(nodes),
        "hidden_count": max(0, total_children + total_direct - len(nodes)),
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
    registered = _registered_names(theorem_names, environment_hash)
    nodes.extend(
        {
            "id": f"LIB:{name}",
            "kind": "library-theorem",
            "lean_name": name,
            "title": name.rsplit(".", 1)[-1],
            "namespace_path": name.rsplit(".", 1)[0] if "." in name else "",
            "parent_cluster_id": f"LIBNS:{namespace}",
            "status": "library",
            "registered": name in registered,
            "claim_id": registered.get(name),
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
                "evidence_kind": "mathlib-source-ref",
                "proof_count": 0,
                "proof_ids": [],
            }
            for row in edge_rows
        ]
    return {"ok": True, "indexed": True, "library_mode": True, "nodes": nodes, "edges": edges}


def _library_theorem_exists(environment_hash: str, name: str) -> bool:
    with core.FORMAL.connect() as conn:
        _ensure_global_cache_schema(conn)
        row = conn.execute(
            """SELECT 1 FROM library_global_theorems
               WHERE environment_hash=? AND lean_name=?""",
            (environment_hash, name),
        ).fetchone()
    return row is not None


def _cached_library_theorem_detail(
    environment_hash: str,
    name: str,
) -> dict[str, Any] | None:
    with core.FORMAL.connect() as conn:
        _ensure_global_cache_schema(conn)
        row = conn.execute(
            """SELECT detail_json,inspected_at FROM library_theorem_details
               WHERE environment_hash=? AND lean_name=?""",
            (environment_hash, name),
        ).fetchone()
    if row is None:
        return None
    try:
        detail = json.loads(str(row["detail_json"]))
    except json.JSONDecodeError:
        return None
    if not isinstance(detail, dict):
        return None
    detail["cache_hit"] = True
    detail["inspected_at"] = str(row["inspected_at"])
    return detail


def library_theorem_detail(name: str, *, refresh: bool = False) -> dict[str, Any]:
    """Return read-only source detail, deterministic presentation, and source refs.

    No Lean process runs here. Source-reference edges are navigation evidence,
    not verified proof dependencies. Browsing never materializes a Claim.
    """
    name = str(name or "").strip()
    if not name or not core.LEAN_NAME_RE.fullmatch(name):
        raise ValueError("name must be a conservative Lean identifier")

    runtime = core.runtime_status()
    environment_hash = str(runtime.get("environment_hash") or "")
    if not environment_hash:
        return {"ok": False, "ready": False, "error": "missing environment hash"}

    with core.FORMAL.connect() as conn:
        _ensure_global_cache_schema(conn)
        row = conn.execute(
            """SELECT lean_name,namespace,short_name,source_module,source_path,
                      source_line,declaration_source
               FROM library_global_theorems
               WHERE environment_hash=? AND lean_name=?""",
            (environment_hash, name),
        ).fetchone()
        outgoing = conn.execute(
            """SELECT target_name FROM library_global_edges
               WHERE environment_hash=? AND source_name=?
               ORDER BY target_name LIMIT 32""",
            (environment_hash, name),
        ).fetchall()
        incoming = conn.execute(
            """SELECT source_name FROM library_global_edges
               WHERE environment_hash=? AND target_name=?
               ORDER BY source_name LIMIT 24""",
            (environment_hash, name),
        ).fetchall()
    if row is None:
        raise ValueError("theorem is not present in the indexed Mathlib sources")

    registered = _registered_names([name], environment_hash)
    declaration = str(row["declaration_source"] or "")
    presentation_text = ""
    presentation_latex = ""
    presentation_renderer_version = ""
    if declaration:
        try:
            rendered = canonical_from_declaration(declaration)
            presentation_text = str(rendered.get("text") or "")
            presentation_latex = str(rendered.get("latex") or "")
            presentation_renderer_version = str(rendered.get("renderer_version") or "")
        except (ValueError, TypeError, KeyError):
            # Some advanced Lean syntax is intentionally outside the conservative
            # presentation parser. The raw source remains available unchanged.
            pass

    source_dependencies = [str(item["target_name"]) for item in outgoing]
    source_used_by = [str(item["source_name"]) for item in incoming]
    return {
        "ok": True,
        "ready": True,
        "cache_hit": True,
        "inspection_mode": "source-index",
        "lean_invocations": 0,
        "environment_hash": environment_hash,
        "declaration_name": name,
        "namespace": str(row["namespace"] or ""),
        "short_name": str(row["short_name"] or ""),
        "source_module": str(row["source_module"] or ""),
        "source_path": str(row["source_path"] or ""),
        "source_line": int(row["source_line"] or 0),
        "source_declaration": declaration,
        "pretty_type": declaration,
        "presentation_text": presentation_text,
        "presentation_latex": presentation_latex,
        "presentation_renderer_version": presentation_renderer_version,
        "source_dependencies": source_dependencies,
        "source_dependency_count": len(source_dependencies),
        "source_used_by": source_used_by,
        "source_used_by_count": len(source_used_by),
        "axioms": [],
        "statement_dependencies": [],
        "statement_dependency_count": 0,
        "proof_dependencies": [],
        "proof_dependency_count": 0,
        "transitive_dependencies": [],
        "transitive_dependency_count": 0,
        "duration_ms": 0,
        "registered": name in registered,
        "claim_id": registered.get(name),
        "verification_note": (
            "Formula and source-reference links come from the installed Mathlib source index. "
            "Read-only browsing creates no Claim. Direct Lean dependencies are exposed separately; "
            "materialization is optional when a durable workspace record is actually needed."
        ),
    }



def library_declaration_dependencies(name: str, *, refresh: bool = False) -> dict[str, Any]:
    """Inspect one Lean declaration and return its direct dependency neighborhood.

    This is intentionally separate from the source/module browser. The first
    request may invoke Lean; subsequent requests are cached by environment hash.
    Edges mean actual declaration dependencies, never module containment.
    """
    name = str(name or "").strip()
    if not name or not core.LEAN_NAME_RE.fullmatch(name):
        raise ValueError("name must be a Lean identifier")

    runtime = core.runtime_status()
    environment_hash = str(runtime.get("environment_hash") or "")
    if not runtime.get("ready") or not environment_hash:
        return {
            "ok": False,
            "ready": False,
            "error": str(runtime.get("message") or "Lean environment unavailable"),
        }

    if not refresh:
        cached = _cached_library_theorem_detail(environment_hash, name)
        if cached and cached.get("dependency_graph_version") == "lean-direct-fast-v2":
            return cached

    with core.FORMAL.connect() as conn:
        _ensure_global_cache_schema(conn)
        indexed = conn.execute(
            """SELECT lean_name,namespace,short_name,source_module,source_path,source_line
               FROM library_global_theorems
               WHERE environment_hash=? AND lean_name=?""",
            (environment_hash, name),
        ).fetchone()

    # Import the declaration's own compiled module when known instead of the
    # Mathlib umbrella. This materially reduces startup work for interactive
    # inspection while preserving the same pinned environment.
    inspection_imports = [
        str(indexed["source_module"])
    ] if indexed is not None and str(indexed["source_module"] or "") else ["Mathlib"]

    inspected = core.inspect_direct_dependencies(
        {"imports": inspection_imports, "declaration_name": name}
    )
    if not inspected.get("ok"):
        return {
            "ok": False,
            "ready": True,
            "declaration_name": name,
            "error": str(
                inspected.get("error")
                or inspected.get("engine_error")
                or inspected.get("output")
                or "Lean dependency inspection failed"
            ),
            "duration_ms": int(inspected.get("duration_ms") or 0),
        }

    statement = [
        str(dep)
        for dep in inspected.get("statement_dependencies") or []
        if str(dep) and str(dep) != name
    ]
    proof = [
        str(dep)
        for dep in inspected.get("proof_dependencies") or []
        if str(dep) and str(dep) != name
    ]
    direct = list(dict.fromkeys([*statement, *proof]))
    # Interactive browsing intentionally discovers the transitive closure by
    # expanding direct neighbors. Computing MinImports closure for every click
    # made a single node inspection far too expensive.
    transitive: list[str] = []
    axioms: list[str] = []

    indexed_dependencies: dict[str, dict[str, Any]] = {}
    if direct:
        placeholders = ",".join("?" for _ in direct)
        with core.FORMAL.connect() as conn:
            rows = conn.execute(
                f"""SELECT lean_name,namespace,short_name,source_module,source_path,source_line
                    FROM library_global_theorems
                    WHERE environment_hash=? AND lean_name IN ({placeholders})""",
                (environment_hash, *direct),
            ).fetchall()
        indexed_dependencies = {str(row["lean_name"]): dict(row) for row in rows}

    roles: dict[str, list[str]] = {}
    for dep in statement:
        roles.setdefault(dep, []).append("statement")
    for dep in proof:
        roles.setdefault(dep, []).append("proof")

    nodes: list[dict[str, Any]] = [
        {
            "id": f"LIB:{name}",
            "kind": "library-declaration",
            "lean_name": name,
            "title": str(indexed["short_name"]) if indexed is not None else name.rsplit(".", 1)[-1],
            "namespace_path": str(indexed["namespace"]) if indexed is not None else (name.rsplit(".", 1)[0] if "." in name else ""),
            "source_module": str(indexed["source_module"]) if indexed is not None else "",
            "source_path": str(indexed["source_path"]) if indexed is not None else "",
            "source_line": int(indexed["source_line"] or 0) if indexed is not None else 0,
            "dependency_root": True,
            "dependency_depth": 0,
        }
    ]
    for dep in direct:
        dep_indexed = indexed_dependencies.get(dep)
        nodes.append(
            {
                "id": f"LIB:{dep}",
                "kind": "library-declaration",
                "lean_name": dep,
                "title": str(dep_indexed["short_name"]) if dep_indexed else dep.rsplit(".", 1)[-1],
                "namespace_path": str(dep_indexed["namespace"]) if dep_indexed else (dep.rsplit(".", 1)[0] if "." in dep else ""),
                "source_module": str(dep_indexed["source_module"]) if dep_indexed else "",
                "source_path": str(dep_indexed["source_path"]) if dep_indexed else "",
                "source_line": int(dep_indexed["source_line"] or 0) if dep_indexed else 0,
                "dependency_roles": roles.get(dep, []),
                "dependency_depth": 1,
                "is_axiom": dep in axioms,
            }
        )

    edges = [
        {
            "from": f"LIB:{name}",
            "to": f"LIB:{dep}",
            "evidence_kind": "lean-dependency",
            "dependency_roles": roles.get(dep, []),
            "proof_count": 0,
            "proof_ids": [],
        }
        for dep in direct
    ]

    result = {
        "ok": True,
        "ready": True,
        "cache_hit": False,
        "dependency_graph_version": "lean-direct-fast-v2",
        "inspection_mode": "lean-direct",
        "lean_invocations": 1,
        "inspection_imports": inspection_imports,
        "environment_hash": environment_hash,
        "declaration_name": name,
        "pretty_type": str(inspected.get("pretty_type") or ""),
        "statement_dependencies": statement,
        "proof_dependencies": proof,
        "direct_dependencies": direct,
        "direct_dependency_count": len(direct),
        "transitive_dependencies": transitive,
        "transitive_dependency_count": 0,
        "transitive_complete": False,
        "axioms": axioms,
        "nodes": nodes,
        "edges": edges,
        "duration_ms": int(inspected.get("duration_ms") or 0),
        "verification_note": (
            "Edges are direct Lean declaration/proof constants in the pinned environment. "
            "Arrow direction is dependent -> dependency. Deeper closure is discovered recursively."
        ),
    }
    now = utc_now()
    with core.FORMAL.connect() as conn:
        _ensure_global_cache_schema(conn)
        conn.execute(
            """INSERT OR REPLACE INTO library_theorem_details(
                   environment_hash,lean_name,detail_json,inspected_at
               ) VALUES(?,?,?,?)""",
            (
                environment_hash,
                name,
                json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                now,
            ),
        )
    result["inspected_at"] = now
    return result




def library_declaration_dependencies_batch(
    names: list[str], *, context_module: str = ""
) -> dict[str, Any]:
    """Prefetch direct dependency neighborhoods with at most one Lean process.

    The browser uses this as look-ahead: visible dependency nodes are inspected
    before the user opens them, so walking the graph normally becomes cache-only.
    """
    cleaned = list(
        dict.fromkeys(str(name or "").strip() for name in (names or []) if str(name or "").strip())
    )
    if not cleaned:
        return {"ok": True, "items": [], "lean_invocations": 0, "cache_hits": 0}
    if len(cleaned) > 24:
        cleaned = cleaned[:24]
    for name in cleaned:
        if not core.LEAN_NAME_RE.fullmatch(name):
            raise ValueError(f"invalid declaration name: {name}")

    runtime = core.runtime_status()
    environment_hash = str(runtime.get("environment_hash") or "")
    if not runtime.get("ready") or not environment_hash:
        return {
            "ok": False,
            "ready": False,
            "error": str(runtime.get("message") or "Lean environment unavailable"),
        }

    cached_items: dict[str, dict[str, Any]] = {}
    unresolved: list[str] = []
    for name in cleaned:
        cached = _cached_library_theorem_detail(environment_hash, name)
        if cached and cached.get("dependency_graph_version") == "lean-direct-fast-v2":
            cached_items[name] = cached
        else:
            unresolved.append(name)

    if not unresolved:
        return {
            "ok": True,
            "ready": True,
            "items": [cached_items[name] for name in cleaned],
            "lean_invocations": 0,
            "cache_hits": len(cleaned),
            "inspection_mode": "cache-only",
        }

    module = str(context_module or "").strip()
    if module and not core.LEAN_NAME_RE.fullmatch(module):
        module = ""
    inspection_imports = [module] if module else ["Mathlib"]
    inspected = core.inspect_direct_dependencies_batch(
        {"imports": inspection_imports, "declaration_names": unresolved}
    )
    if not inspected.get("ok"):
        return {
            "ok": False,
            "ready": True,
            "items": list(cached_items.values()),
            "error": str(inspected.get("error") or inspected.get("output") or "batch dependency inspection failed"),
            "duration_ms": int(inspected.get("duration_ms") or 0),
        }

    raw_items = {
        str(item.get("declaration_name") or ""): item
        for item in inspected.get("items") or []
        if item.get("ok") and item.get("declaration_name")
    }
    all_names = set(unresolved)
    for item in raw_items.values():
        all_names.update(str(dep) for dep in item.get("direct_dependencies") or [] if str(dep))

    metadata: dict[str, dict[str, Any]] = {}
    if all_names:
        placeholders = ",".join("?" for _ in all_names)
        with core.FORMAL.connect() as conn:
            _ensure_global_cache_schema(conn)
            rows = conn.execute(
                f"""SELECT lean_name,namespace,short_name,source_module,source_path,source_line
                    FROM library_global_theorems
                    WHERE environment_hash=? AND lean_name IN ({placeholders})""",
                (environment_hash, *sorted(all_names)),
            ).fetchall()
        metadata = {str(row["lean_name"]): dict(row) for row in rows}

    now = utc_now()
    created: dict[str, dict[str, Any]] = {}
    with core.FORMAL.connect() as conn:
        _ensure_global_cache_schema(conn)
        for name in unresolved:
            raw = raw_items.get(name)
            if raw is None:
                continue
            statement = [str(dep) for dep in raw.get("statement_dependencies") or [] if str(dep) and str(dep) != name]
            proof = [str(dep) for dep in raw.get("proof_dependencies") or [] if str(dep) and str(dep) != name]
            direct = list(dict.fromkeys([*statement, *proof]))
            roles: dict[str, list[str]] = {}
            for dep in statement:
                roles.setdefault(dep, []).append("statement")
            for dep in proof:
                roles.setdefault(dep, []).append("proof")

            root_meta = metadata.get(name)
            nodes: list[dict[str, Any]] = [{
                "id": f"LIB:{name}",
                "kind": "library-declaration",
                "lean_name": name,
                "title": str(root_meta["short_name"]) if root_meta else name.rsplit(".", 1)[-1],
                "namespace_path": str(root_meta["namespace"]) if root_meta else (name.rsplit(".", 1)[0] if "." in name else ""),
                "source_module": str(root_meta["source_module"]) if root_meta else "",
                "source_path": str(root_meta["source_path"]) if root_meta else "",
                "source_line": int(root_meta["source_line"] or 0) if root_meta else 0,
                "dependency_root": False,
                "dependency_depth": 0,
            }]
            for dep in direct:
                dep_meta = metadata.get(dep)
                nodes.append({
                    "id": f"LIB:{dep}",
                    "kind": "library-declaration",
                    "lean_name": dep,
                    "title": str(dep_meta["short_name"]) if dep_meta else dep.rsplit(".", 1)[-1],
                    "namespace_path": str(dep_meta["namespace"]) if dep_meta else (dep.rsplit(".", 1)[0] if "." in dep else ""),
                    "source_module": str(dep_meta["source_module"]) if dep_meta else "",
                    "source_path": str(dep_meta["source_path"]) if dep_meta else "",
                    "source_line": int(dep_meta["source_line"] or 0) if dep_meta else 0,
                    "dependency_roles": roles.get(dep, []),
                    "dependency_depth": 1,
                })
            edges = [{
                "from": f"LIB:{name}",
                "to": f"LIB:{dep}",
                "evidence_kind": "lean-dependency",
                "dependency_roles": roles.get(dep, []),
                "proof_count": 0,
                "proof_ids": [],
            } for dep in direct]
            result = {
                "ok": True,
                "ready": True,
                "cache_hit": False,
                "prefetched": True,
                "dependency_graph_version": "lean-direct-fast-v2",
                "inspection_mode": "lean-direct-batch",
                "lean_invocations": 1,
                "inspection_imports": inspection_imports,
                "environment_hash": environment_hash,
                "declaration_name": name,
                "pretty_type": str(raw.get("pretty_type") or ""),
                "statement_dependencies": statement,
                "proof_dependencies": proof,
                "direct_dependencies": direct,
                "direct_dependency_count": len(direct),
                "transitive_dependencies": [],
                "transitive_dependency_count": 0,
                "transitive_complete": False,
                "axioms": [],
                "nodes": nodes,
                "edges": edges,
                "duration_ms": int(inspected.get("duration_ms") or 0),
                "verification_note": (
                    "Prefetched direct Lean declaration/proof constants. "
                    "Arrow direction is dependent -> dependency."
                ),
            }
            created[name] = result
            conn.execute(
                """INSERT OR REPLACE INTO library_theorem_details(
                       environment_hash,lean_name,detail_json,inspected_at
                   ) VALUES(?,?,?,?)""",
                (
                    environment_hash,
                    name,
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    now,
                ),
            )

    combined = {**cached_items, **created}
    return {
        "ok": True,
        "ready": True,
        "items": [combined[name] for name in cleaned if name in combined],
        "lean_invocations": 1,
        "cache_hits": len(cached_items),
        "prefetched": len(created),
        "duration_ms": int(inspected.get("duration_ms") or 0),
        "inspection_mode": "lean-direct-batch",
    }



def random_library_theorem() -> dict[str, Any]:
    status = global_library_status()
    environment_hash = str(status.get("environment_hash") or "")
    if not status.get("indexed") or not environment_hash:
        return {"ok": False, "indexed": False, "error": "Mathlib index is not ready"}
    with core.FORMAL.connect() as conn:
        _ensure_global_cache_schema(conn)
        row = conn.execute(
            """SELECT lean_name,namespace,short_name
               FROM library_global_theorems
               WHERE environment_hash=?
               ORDER BY RANDOM()
               LIMIT 1""",
            (environment_hash,),
        ).fetchone()
    if row is None:
        return {"ok": False, "indexed": True, "error": "Mathlib index is empty"}
    return {
        "ok": True,
        "indexed": True,
        "lean_name": str(row["lean_name"]),
        "namespace": str(row["namespace"]),
        "short_name": str(row["short_name"]),
    }


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
