from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

import server as core
from batch_import import MAX_BATCH_IMPORT, import_existing_declarations
from claim_import import IMPORTABLE_LEAN_NAME
from dependency_import import (
    MAX_AUTO_DEPTH,
    MAX_CLASSIFY_BATCH,
    _attach_external_argument_evidence,
    _classify_unbound,
    _inspect_batch,
)
from registry import utc_now


DEFAULT_TASK_MAX_DEPTH = 2
DEFAULT_TASK_MAX_CLAIMS = 160
MAX_TASK_DEPTH = MAX_AUTO_DEPTH
MAX_TASK_CLAIMS = 5000
MAX_TASK_ROOTS = 500
TASK_PREFIX = "IMT-"
STATE_VERSION = 2


def _uniq_names(raw: Any, *, limit: int = MAX_TASK_ROOTS) -> list[str]:
    if not isinstance(raw, list):
        raise ValueError("declaration_names must be a list")
    out: list[str] = []
    for value in raw:
        name = str(value or "").strip()
        if not name or not IMPORTABLE_LEAN_NAME.fullmatch(name):
            raise ValueError(f"invalid importable Lean declaration name: {name!r}")
        if name not in out:
            out.append(name)
    if not out:
        raise ValueError("declaration_names must contain at least one theorem")
    if len(out) > limit:
        raise ValueError(f"import task accepts at most {limit} root theorems")
    return out


def _int(value: Any, *, default: int, low: int, high: int, name: str) -> int:
    try:
        parsed = int(default if value is None else value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    return max(low, min(parsed, high))


def _json(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


def _sort_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(entries, key=lambda item: (int(item.get("depth") or 0), str(item.get("name") or "")))


class ImportTaskStore:
    """Durable, bounded execution state for Lean theorem ingestion.

    This table is operational state only. Mathematical truth remains in
    Claim/Proof/Build/FormalBinding/ArgumentIndex. A step processes at most one
    <=40-theorem frontier batch (or one <=200-constant classification batch) and
    persists before returning. Tasks are pinned to the exact Lean environment in
    which they were created and cannot silently resume across an environment
    change.
    """

    SUMMARY_COLUMNS: dict[str, str] = {
        "environment_hash": "TEXT NOT NULL DEFAULT ''",
        "processed_count": "INTEGER NOT NULL DEFAULT 0",
        "scheduled_count": "INTEGER NOT NULL DEFAULT 0",
        "frontier_count": "INTEGER NOT NULL DEFAULT 0",
        "deferred_count": "INTEGER NOT NULL DEFAULT 0",
        "classification_pending_count": "INTEGER NOT NULL DEFAULT 0",
        "imported_count": "INTEGER NOT NULL DEFAULT 0",
        "existing_count": "INTEGER NOT NULL DEFAULT 0",
        "failed_count": "INTEGER NOT NULL DEFAULT 0",
        "lean_invocations": "INTEGER NOT NULL DEFAULT 0",
        "lean_duration_ms": "INTEGER NOT NULL DEFAULT 0",
        "steps": "INTEGER NOT NULL DEFAULT 0",
        "truncated": "INTEGER NOT NULL DEFAULT 0",
        "substrate_ref_count": "INTEGER NOT NULL DEFAULT 0",
    }

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS import_tasks(
                  task_id TEXT PRIMARY KEY,
                  status TEXT NOT NULL CHECK(status IN('active','paused','completed','cancelled')),
                  roots_json TEXT NOT NULL,
                  imports_json TEXT NOT NULL,
                  environment_hash TEXT NOT NULL DEFAULT '',
                  max_depth INTEGER NOT NULL,
                  max_claims INTEGER NOT NULL,
                  state_json TEXT NOT NULL,
                  lease_token TEXT,
                  lease_started_at TEXT,
                  processed_count INTEGER NOT NULL DEFAULT 0,
                  scheduled_count INTEGER NOT NULL DEFAULT 0,
                  frontier_count INTEGER NOT NULL DEFAULT 0,
                  deferred_count INTEGER NOT NULL DEFAULT 0,
                  classification_pending_count INTEGER NOT NULL DEFAULT 0,
                  imported_count INTEGER NOT NULL DEFAULT 0,
                  existing_count INTEGER NOT NULL DEFAULT 0,
                  failed_count INTEGER NOT NULL DEFAULT 0,
                  lean_invocations INTEGER NOT NULL DEFAULT 0,
                  lean_duration_ms INTEGER NOT NULL DEFAULT 0,
                  steps INTEGER NOT NULL DEFAULT 0,
                  truncated INTEGER NOT NULL DEFAULT 0,
                  substrate_ref_count INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_import_tasks_updated
                  ON import_tasks(updated_at DESC);
                """
            )
            present = {str(row["name"]) for row in conn.execute("PRAGMA table_info(import_tasks)")}
            for name, declaration in self.SUMMARY_COLUMNS.items():
                if name not in present:
                    conn.execute(f"ALTER TABLE import_tasks ADD COLUMN {name} {declaration}")

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @staticmethod
    def _new_id() -> str:
        return TASK_PREFIX + uuid.uuid4().hex[:16].upper()

    @staticmethod
    def _initial_state(roots: list[str], payload: dict[str, Any], environment_hash: str) -> dict[str, Any]:
        return {
            "state_version": STATE_VERSION,
            "environment_hash": environment_hash,
            "frontier": [{"name": name, "depth": 0} for name in roots],
            "classification_frontier": [],
            "deferred": [],
            "scheduled": list(roots),
            "processed": [],
            "items": {},
            "dependency_parents": {},
            "substrate_refs": [],
            "unimportable_refs": [],
            "classification_errors": [],
            "lean_invocations": 0,
            "lean_duration_ms": 0,
            "steps": 0,
            "truncated": False,
            "last_error": "",
            "area": str(payload.get("area") or "").strip(),
            "claim_author": str(payload.get("claim_author") or "").strip(),
            "proof_author": str(payload.get("proof_author") or "").strip(),
        }

    @staticmethod
    def _summary(state: dict[str, Any]) -> dict[str, int]:
        items = state.get("items") or {}
        values = list(items.values())
        deferred = list(state.get("deferred") or [])
        return {
            "processed_count": len(state.get("processed") or []),
            "scheduled_count": len(state.get("scheduled") or []),
            "frontier_count": len(state.get("frontier") or []),
            "deferred_count": len(deferred),
            "classification_pending_count": len(state.get("classification_frontier") or []),
            "imported_count": sum(1 for item in values if item.get("status") == "imported"),
            "existing_count": sum(1 for item in values if item.get("status") == "already_registered"),
            "failed_count": sum(1 for item in values if item.get("status") == "failed"),
            "lean_invocations": int(state.get("lean_invocations") or 0),
            "lean_duration_ms": int(state.get("lean_duration_ms") or 0),
            "steps": int(state.get("steps") or 0),
            "truncated": 1 if deferred else 0,
            "substrate_ref_count": len(state.get("substrate_refs") or []),
        }

    def _write_state(
        self,
        conn: sqlite3.Connection,
        task_id: str,
        state: dict[str, Any],
        *,
        status: str | None = None,
        max_depth: int | None = None,
        max_claims: int | None = None,
        clear_lease: bool = False,
        lease_token: str | None = None,
    ) -> None:
        summary = self._summary(state)
        assignments = [
            "state_json=?", "processed_count=?", "scheduled_count=?", "frontier_count=?",
            "deferred_count=?", "classification_pending_count=?", "imported_count=?",
            "existing_count=?", "failed_count=?", "lean_invocations=?", "lean_duration_ms=?",
            "steps=?", "truncated=?", "substrate_ref_count=?", "updated_at=?",
        ]
        values: list[Any] = [
            json.dumps(state), summary["processed_count"], summary["scheduled_count"],
            summary["frontier_count"], summary["deferred_count"], summary["classification_pending_count"],
            summary["imported_count"], summary["existing_count"], summary["failed_count"],
            summary["lean_invocations"], summary["lean_duration_ms"], summary["steps"],
            summary["truncated"], summary["substrate_ref_count"], utc_now(),
        ]
        if status is not None:
            assignments.append("status=?"); values.append(status)
        if max_depth is not None:
            assignments.append("max_depth=?"); values.append(max_depth)
        if max_claims is not None:
            assignments.append("max_claims=?"); values.append(max_claims)
        if clear_lease:
            assignments.extend(["lease_token=NULL", "lease_started_at=NULL"])
        sql = f"UPDATE import_tasks SET {','.join(assignments)} WHERE task_id=?"
        values.append(task_id)
        if lease_token is not None:
            sql += " AND lease_token=?"; values.append(lease_token)
        cur = conn.execute(sql, values)
        if lease_token is not None and cur.rowcount != 1:
            raise ValueError("import task worker lease changed while step was running")

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        roots = _uniq_names(payload.get("declaration_names") or payload.get("names"))
        imports = core.validate_imports(payload.get("imports") or ["Mathlib"])
        max_depth = _int(payload.get("max_depth"), default=DEFAULT_TASK_MAX_DEPTH, low=0, high=MAX_TASK_DEPTH, name="max_depth")
        max_claims = _int(payload.get("max_claims"), default=max(DEFAULT_TASK_MAX_CLAIMS, len(roots)), low=len(roots), high=MAX_TASK_CLAIMS, name="max_claims")
        environment = core.runtime_status()
        if not environment.get("ready"):
            raise ValueError(str(environment.get("message") or "Lean environment unavailable"))
        environment_hash = str(environment.get("environment_hash") or "")
        if not environment_hash:
            raise ValueError("pinned Lean environment has no environment hash")
        task_id, now = self._new_id(), utc_now()
        state = self._initial_state(roots, payload, environment_hash)
        summary = self._summary(state)
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO import_tasks(
                   task_id,status,roots_json,imports_json,environment_hash,max_depth,max_claims,state_json,
                   processed_count,scheduled_count,frontier_count,deferred_count,classification_pending_count,
                   imported_count,existing_count,failed_count,lean_invocations,lean_duration_ms,steps,truncated,
                   substrate_ref_count,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    task_id,"active",json.dumps(roots),json.dumps(imports),environment_hash,max_depth,max_claims,json.dumps(state),
                    summary["processed_count"],summary["scheduled_count"],summary["frontier_count"],summary["deferred_count"],
                    summary["classification_pending_count"],summary["imported_count"],summary["existing_count"],summary["failed_count"],
                    summary["lean_invocations"],summary["lean_duration_ms"],summary["steps"],summary["truncated"],
                    summary["substrate_ref_count"],now,now,
                ),
            )
        return self.get(task_id)

    @staticmethod
    def _row(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM import_tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return row

    @staticmethod
    def _decode(row: sqlite3.Row) -> tuple[list[str], list[str], dict[str, Any]]:
        return list(_json(row["roots_json"], [])), list(_json(row["imports_json"], [])), dict(_json(row["state_json"], {}))

    @staticmethod
    def _base_snapshot(row: sqlite3.Row) -> dict[str, Any]:
        roots = list(_json(row["roots_json"], []))
        imports = list(_json(row["imports_json"], []))
        processed = int(row["processed_count"] or 0)
        return {
            "task_id": str(row["task_id"]), "status": str(row["status"]),
            "roots": roots, "imports": imports, "environment_hash": str(row["environment_hash"] or ""),
            "max_depth": int(row["max_depth"]), "max_claims": int(row["max_claims"]),
            "frontier_count": int(row["frontier_count"] or 0), "deferred_count": int(row["deferred_count"] or 0),
            "classification_pending_count": int(row["classification_pending_count"] or 0),
            "scheduled_claims": int(row["scheduled_count"] or 0), "processed_claims": processed,
            "imported": int(row["imported_count"] or 0), "already_registered": int(row["existing_count"] or 0),
            "failed": int(row["failed_count"] or 0), "auto_dependency_claims": max(0, processed - min(processed, len(roots))),
            "lean_invocations": int(row["lean_invocations"] or 0), "lean_duration_ms": int(row["lean_duration_ms"] or 0),
            "steps": int(row["steps"] or 0), "truncated": bool(row["truncated"]),
            "substrate_ref_count": int(row["substrate_ref_count"] or 0),
            "leased": bool(row["lease_token"]), "lease_started_at": row["lease_started_at"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def _detail_snapshot(self, row: sqlite3.Row) -> dict[str, Any]:
        result = self._base_snapshot(row)
        roots, _, state = self._decode(row)
        items = state.get("items") or {}
        processed = list(state.get("processed") or [])
        root_items = [items[name] for name in roots if name in items]
        recent_items = [items[name] for name in processed[-40:] if name in items]
        failures = [item for item in items.values() if item.get("status") == "failed"]
        result.update(
            {
                "state_version": int(state.get("state_version") or 1),
                "last_error": str(state.get("last_error") or ""),
                "root_items": root_items,
                "recent_items": recent_items,
                "failed_items": sorted(failures, key=lambda x: str(x.get("declaration_name") or ""))[:80],
                "failed_items_truncated": len(failures) > 80,
                "substrate_ref_sample": sorted(state.get("substrate_refs") or [])[:80],
                "unimportable_ref_count": len(state.get("unimportable_refs") or []),
                "unimportable_ref_sample": sorted(state.get("unimportable_refs") or [])[:40],
                "classification_errors": list(state.get("classification_errors") or [])[-20:],
            }
        )
        return result

    def get(self, task_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            return self._detail_snapshot(self._row(conn, task_id))

    def list(self, *, limit: int = 24) -> dict[str, Any]:
        limit = max(1, min(int(limit), 100))
        columns = "task_id,status,roots_json,imports_json,environment_hash,max_depth,max_claims,lease_token,lease_started_at,processed_count,scheduled_count,frontier_count,deferred_count,classification_pending_count,imported_count,existing_count,failed_count,lean_invocations,lean_duration_ms,steps,truncated,substrate_ref_count,created_at,updated_at"
        with self.connect() as conn:
            rows = conn.execute(f"SELECT {columns} FROM import_tasks ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return {"tasks": [self._base_snapshot(row) for row in rows], "limit": limit}

    def pause(self, task_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = self._row(conn, task_id)
            if row["status"] in {"completed", "cancelled"}:
                return self._detail_snapshot(row)
            conn.execute("UPDATE import_tasks SET status='paused',updated_at=? WHERE task_id=?", (utc_now(), task_id))
        return self.get(task_id)

    def resume(self, task_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = self._row(conn, task_id)
            if row["status"] in {"completed", "cancelled"}:
                raise ValueError(f"{row['status']} import tasks cannot be resumed")
            if row["lease_token"]:
                raise ValueError("an import step is still leased; wait for it to finish or recover a stale lease")
            conn.execute("UPDATE import_tasks SET status='active',updated_at=? WHERE task_id=?", (utc_now(), task_id))
        return self.get(task_id)

    def cancel(self, task_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = self._row(conn, task_id)
            if row["status"] == "completed":
                return self._detail_snapshot(row)
            conn.execute("UPDATE import_tasks SET status='cancelled',updated_at=? WHERE task_id=?", (utc_now(), task_id))
        return self.get(task_id)

    def recover(self, task_id: str) -> dict[str, Any]:
        """Explicitly clear a stale worker lease after a process interruption."""
        with self.connect() as conn:
            row = self._row(conn, task_id)
            if row["status"] in {"completed", "cancelled"}:
                return self._detail_snapshot(row)
            conn.execute("UPDATE import_tasks SET lease_token=NULL,lease_started_at=NULL,updated_at=? WHERE task_id=?", (utc_now(), task_id))
        return self.get(task_id)

    def retry_failed(self, task_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = self._row(conn, task_id)
            if row["status"] == "cancelled":
                raise ValueError("cancelled import tasks cannot retry failures")
            if row["lease_token"]:
                raise ValueError("cannot retry failures while an import step is running")
            _, _, state = self._decode(row)
            items = state.get("items") or {}
            frontier = list(state.get("frontier") or [])
            queued = {str(entry.get("name") or "") for entry in frontier}
            retried_names: set[str] = set()
            for name, item in list(items.items()):
                if item.get("status") != "failed" or name in queued:
                    continue
                frontier.append({"name": name, "depth": int(item.get("depth") or 0)})
                items.pop(name, None); retried_names.add(name)
            state["frontier"] = _sort_entries(frontier); state["items"] = items
            state["processed"] = [name for name in state.get("processed") or [] if name not in retried_names]
            state["last_error"] = ""
            status = "active" if frontier else str(row["status"])
            self._write_state(conn, task_id, state, status=status)
        result = self.get(task_id); result["retried"] = len(retried_names); return result

    def extend(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as conn:
            row = self._row(conn, task_id)
            if row["status"] == "cancelled":
                raise ValueError("cancelled import tasks cannot be extended")
            if row["lease_token"]:
                raise ValueError("cannot change task budgets while an import step is running")
            _, _, state = self._decode(row)
            new_depth = _int(payload.get("max_depth"), default=int(row["max_depth"]), low=int(row["max_depth"]), high=MAX_TASK_DEPTH, name="max_depth")
            new_claims = _int(payload.get("max_claims"), default=int(row["max_claims"]), low=int(row["max_claims"]), high=MAX_TASK_CLAIMS, name="max_claims")
            frontier = list(state.get("frontier") or []); deferred = list(state.get("deferred") or [])
            scheduled = list(state.get("scheduled") or []); scheduled_set = set(scheduled); still_deferred: list[dict[str, Any]] = []
            for entry in deferred:
                name, depth = str(entry.get("name") or ""), int(entry.get("depth") or 0)
                if not name or depth > new_depth or len(scheduled) >= new_claims:
                    still_deferred.append(entry); continue
                if name not in scheduled_set:
                    scheduled.append(name); scheduled_set.add(name)
                if not any(str(x.get("name") or "") == name for x in frontier):
                    frontier.append({"name": name, "depth": depth})
            state["frontier"] = _sort_entries(frontier); state["deferred"] = still_deferred; state["scheduled"] = scheduled; state["truncated"] = bool(still_deferred); state["last_error"] = ""
            active_work = bool(frontier or state.get("classification_frontier"))
            status = "active" if active_work else str(row["status"])
            self._write_state(conn, task_id, state, status=status, max_depth=new_depth, max_claims=new_claims)
        return self.get(task_id)

    def _claim_lease(self, task_id: str) -> str:
        token, now = uuid.uuid4().hex, utc_now()
        with self.connect() as conn:
            row = self._row(conn, task_id)
            if str(row["status"]) != "active":
                raise ValueError(f"import task is {row['status']}, not active")
            cur = conn.execute(
                "UPDATE import_tasks SET lease_token=?,lease_started_at=?,updated_at=? WHERE task_id=? AND status='active' AND lease_token IS NULL",
                (token, now, now, task_id),
            )
            if cur.rowcount != 1:
                raise ValueError("another import step is already running for this task")
        return token

    def _pause_after_step_error(self, task_id: str, token: str, message: str) -> None:
        with self.connect() as conn:
            row = self._row(conn, task_id)
            if str(row["lease_token"] or "") != token:
                return
            _, _, state = self._decode(row); state["last_error"] = message
            live_status = str(row["status"]); status = live_status if live_status in {"paused", "cancelled"} else "paused"
            self._write_state(conn, task_id, state, status=status, clear_lease=True, lease_token=token)

    @staticmethod
    def _add_parent(state: dict[str, Any], child: str, parent: str) -> None:
        mapping = state.setdefault("dependency_parents", {}); parents = list(mapping.get(child) or [])
        if parent not in parents: parents.append(parent)
        mapping[child] = parents

    @staticmethod
    def _enqueue_theorems(state: dict[str, Any], names: list[str], depth: int, max_depth: int, max_claims: int) -> None:
        frontier = list(state.get("frontier") or []); deferred = list(state.get("deferred") or []); scheduled = list(state.get("scheduled") or [])
        scheduled_set = set(scheduled); frontier_names = {str(x.get("name") or "") for x in frontier}; deferred_names = {str(x.get("name") or "") for x in deferred}
        for name in names:
            if name in scheduled_set: continue
            if depth > max_depth or len(scheduled) >= max_claims:
                if name not in deferred_names:
                    deferred.append({"name": name, "depth": depth}); deferred_names.add(name)
                continue
            scheduled.append(name); scheduled_set.add(name)
            if name not in frontier_names:
                frontier.append({"name": name, "depth": depth}); frontier_names.add(name)
        state["frontier"] = _sort_entries(frontier); state["deferred"] = _sort_entries(deferred); state["scheduled"] = scheduled; state["truncated"] = bool(deferred)

    def _classify_entries(self, imports: list[str], entries: list[dict[str, Any]], state: dict[str, Any], max_depth: int, max_claims: int) -> None:
        if not entries: return
        names = [str(entry.get("name") or "") for entry in entries if str(entry.get("name") or "")]
        classifications, calls, duration, errors = _classify_unbound(imports, names)
        state["lean_invocations"] = int(state.get("lean_invocations") or 0) + calls
        state["lean_duration_ms"] = int(state.get("lean_duration_ms") or 0) + duration
        state.setdefault("classification_errors", []).extend(errors)
        substrate = set(state.get("substrate_refs") or []); unimportable = set(state.get("unimportable_refs") or [])
        retry: list[dict[str, Any]] = []; theorem_by_depth: dict[int, list[str]] = {}
        for entry in entries:
            name, depth = str(entry.get("name") or ""), int(entry.get("depth") or 0); kind = classifications.get(name)
            if kind == "theorem": theorem_by_depth.setdefault(depth, []).append(name)
            elif kind in {"substrate", "internal"}: substrate.add(name)
            elif kind == "missing": unimportable.add(name)
            else: retry.append({"name": name, "depth": depth})
        state["substrate_refs"] = sorted(substrate); state["unimportable_refs"] = sorted(unimportable)
        existing_pending = list(state.get("classification_frontier") or [])
        pending_names = {str(item.get("name") or "") for item in existing_pending}
        for entry in retry:
            if entry["name"] not in pending_names:
                existing_pending.append(entry); pending_names.add(entry["name"])
        state["classification_frontier"] = _sort_entries(existing_pending)
        for depth, theorem_names in sorted(theorem_by_depth.items()):
            self._enqueue_theorems(state, theorem_names, depth, max_depth, max_claims)

    def _finish_step(self, task_id: str, token: str, state: dict[str, Any]) -> dict[str, Any]:
        active_work = bool(state.get("frontier") or state.get("classification_frontier")); computed = "active" if active_work else "completed"
        state["truncated"] = bool(state.get("deferred")); state["last_error"] = ""
        with self.connect() as conn:
            live = self._row(conn, task_id)
            if str(live["lease_token"] or "") != token:
                raise ValueError("import task worker lease changed while step was running")
            live_status = str(live["status"]); final = live_status if live_status in {"paused", "cancelled"} else computed
            self._write_state(conn, task_id, state, status=final, clear_lease=True, lease_token=token)
        return self.get(task_id)

    def step(self, task_id: str) -> dict[str, Any]:
        token = self._claim_lease(task_id)
        try:
            environment = core.runtime_status()
            if not environment.get("ready"):
                raise ValueError(str(environment.get("message") or "Lean environment unavailable"))
            current_environment_hash = str(environment.get("environment_hash") or "")
            with self.connect() as conn:
                row = self._row(conn, task_id); roots, imports, state = self._decode(row); max_depth, max_claims = int(row["max_depth"]), int(row["max_claims"]); task_environment_hash = str(row["environment_hash"] or state.get("environment_hash") or "")
            if not current_environment_hash or current_environment_hash != task_environment_hash:
                raise ValueError("Lean environment changed since this task was created; create a new task instead of mixing environments")

            frontier = list(state.get("frontier") or [])
            classification_frontier = list(state.get("classification_frontier") or [])
            if not frontier and classification_frontier:
                chunk, rest = classification_frontier[:MAX_CLASSIFY_BATCH], classification_frontier[MAX_CLASSIFY_BATCH:]
                state["classification_frontier"] = rest; self._classify_entries(imports, chunk, state, max_depth, max_claims); state["steps"] = int(state.get("steps") or 0) + 1
                return self._finish_step(task_id, token, state)
            if not frontier:
                return self._finish_step(task_id, token, state)

            first_depth = int(frontier[0].get("depth") or 0); current: list[str] = []; remaining: list[dict[str, Any]] = []
            for entry in frontier:
                if len(current) < MAX_BATCH_IMPORT and int(entry.get("depth") or 0) == first_depth: current.append(str(entry.get("name") or ""))
                else: remaining.append(entry)
            current = [name for name in current if name]; state["frontier"] = remaining
            if not current:
                return self._finish_step(task_id, token, state)

            inspections, inspection_errors, inspect_calls, inspect_ms = _inspect_batch(imports, current, current_environment_hash)
            batch = import_existing_declarations({"declaration_names": current, "imports": imports, "area": state.get("area"), "claim_author": state.get("claim_author"), "proof_author": state.get("proof_author")})
            state["lean_invocations"] = int(state.get("lean_invocations") or 0) + inspect_calls + int(batch.get("lean_invocations") or 0)
            state["lean_duration_ms"] = int(state.get("lean_duration_ms") or 0) + inspect_ms + int(batch.get("inspect_duration_ms") or 0) + int(batch.get("audit_duration_ms") or 0)
            state["steps"] = int(state.get("steps") or 0) + 1
            items = dict(state.get("items") or {}); processed = list(state.get("processed") or []); processed_set = set(processed)
            batch_items = {str(item.get("declaration_name") or ""): dict(item) for item in batch.get("items", [])}
            candidates: list[dict[str, Any]] = []; seen_candidates: set[str] = set()
            scheduled_set = set(state.get("scheduled") or []); deferred_set = {str(x.get("name") or "") for x in state.get("deferred") or []}; classify_set = {str(x.get("name") or "") for x in state.get("classification_frontier") or []}
            substrate_known = set(state.get("substrate_refs") or []); unimportable_known = set(state.get("unimportable_refs") or [])

            for name in current:
                item = batch_items.get(name, {"declaration_name": name, "status": "failed", "error": "batch import returned no result"}); item["depth"] = first_depth
                if name in inspection_errors: item.setdefault("argument_warning", inspection_errors[name])
                inspected = inspections.get(name)
                if inspected and item.get("status") in {"imported", "already_registered"}:
                    _attach_external_argument_evidence(name, inspected, environment)
                    refs = list(dict.fromkeys(str(x) for x in inspected.get("proof_dependencies") or [] if str(x))); item["source_proof_dependencies"] = refs; item["argument_evidence_origin"] = "external-declaration"
                    for ref in refs:
                        if ref == name: continue
                        self._add_parent(state, ref, name)
                        if not IMPORTABLE_LEAN_NAME.fullmatch(ref):
                            unimportable_known.add(ref); continue
                        if ref in scheduled_set or ref in deferred_set or ref in classify_set or ref in substrate_known or ref in unimportable_known or ref in seen_candidates: continue
                        candidates.append({"name": ref, "depth": first_depth + 1}); seen_candidates.add(ref)
                items[name] = item
                if name not in processed_set: processed.append(name); processed_set.add(name)
            state["items"] = items; state["processed"] = processed; state["unimportable_refs"] = sorted(unimportable_known)

            known_entries: list[dict[str, Any]] = []; unknown_entries: list[dict[str, Any]] = []
            for entry in candidates:
                if core.FORMAL.claim_for_name(entry["name"], current_environment_hash) is not None: known_entries.append(entry)
                else: unknown_entries.append(entry)
            for depth in sorted({int(x["depth"]) for x in known_entries}):
                self._enqueue_theorems(state, [x["name"] for x in known_entries if int(x["depth"]) == depth], depth, max_depth, max_claims)

            immediate_unknown = unknown_entries[:MAX_CLASSIFY_BATCH]
            delayed_unknown = unknown_entries[MAX_CLASSIFY_BATCH:]
            if delayed_unknown:
                pending = list(state.get("classification_frontier") or []); pending_names = {str(x.get("name") or "") for x in pending}
                for entry in delayed_unknown:
                    if entry["name"] not in pending_names:
                        pending.append(entry); pending_names.add(entry["name"])
                state["classification_frontier"] = _sort_entries(pending)
            self._classify_entries(imports, immediate_unknown, state, max_depth, max_claims)

            with core.ARGUMENT.connect() as conn: core.ARGUMENT._reconcile(conn, current_environment_hash)
            return self._finish_step(task_id, token, state)
        except Exception as exc:
            self._pause_after_step_error(task_id, token, str(exc)); raise
