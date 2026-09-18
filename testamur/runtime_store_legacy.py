from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from threading import local
from typing import Any, Iterator

from .runtime_protocol import (
    IdempotencyConflict,
    MemoryProtocolLedger,
    RunState,
    RunStateConflict,
    RuntimeEvent,
    RuntimeRun,
    SequenceConflict,
    canonical_json,
    protocol_for_run_id,
)


class LegacyRuntimeProtocolLedger(MemoryProtocolLedger):
    """SQLite-backed compatibility ledger over historical runtime table names.

    The implementation is Testamur-owned. Historical ``witness_runtime_*`` table
    names are deliberately retained because they are persisted schema identifiers,
    not a live Python/runtime namespace. This class is an internal storage
    compatibility layer; public callers should import ``TestamurProtocolLedger``
    from ``testamur.runtime_store``.
    """

    def __init__(self, store: Any) -> None:
        self.store = store
        self._transaction = local()
        self._init_schema()

    def _init_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS witness_runtime_runs(
          run_id TEXT PRIMARY KEY,
          client_id TEXT NOT NULL,
          begin_payload_json TEXT NOT NULL,
          created_record_digest TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS witness_runtime_events(
          event_id TEXT PRIMARY KEY,
          run_id TEXT NOT NULL REFERENCES witness_runtime_runs(run_id),
          sequence INTEGER NOT NULL,
          operation TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          UNIQUE(run_id, sequence)
        );

        CREATE TABLE IF NOT EXISTS witness_runtime_idempotency(
          client_id TEXT NOT NULL,
          idempotency_key TEXT NOT NULL,
          request_fingerprint TEXT NOT NULL,
          response_json TEXT NOT NULL,
          PRIMARY KEY(client_id, idempotency_key)
        );

        CREATE TABLE IF NOT EXISTS witness_runtime_cache(
          cache_key TEXT PRIMARY KEY,
          value_json TEXT NOT NULL
        );

        CREATE TRIGGER IF NOT EXISTS witness_runtime_runs_no_update
        BEFORE UPDATE ON witness_runtime_runs BEGIN
          SELECT RAISE(ABORT, 'Legacy runtime run roots are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS witness_runtime_runs_no_delete
        BEFORE DELETE ON witness_runtime_runs BEGIN
          SELECT RAISE(ABORT, 'Legacy runtime run roots are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS witness_runtime_events_no_update
        BEFORE UPDATE ON witness_runtime_events BEGIN
          SELECT RAISE(ABORT, 'Legacy runtime events are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS witness_runtime_events_no_delete
        BEFORE DELETE ON witness_runtime_events BEGIN
          SELECT RAISE(ABORT, 'Legacy runtime events are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS witness_runtime_idempotency_no_update
        BEFORE UPDATE ON witness_runtime_idempotency BEGIN
          SELECT RAISE(ABORT, 'Legacy runtime idempotency bindings are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS witness_runtime_idempotency_no_delete
        BEFORE DELETE ON witness_runtime_idempotency BEGIN
          SELECT RAISE(ABORT, 'Legacy runtime idempotency bindings are immutable');
        END;
        """
        with self.store.connect() as conn:
            conn.executescript(schema)

    def _active_connection(self) -> sqlite3.Connection | None:
        return getattr(self._transaction, "connection", None)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        active = self._active_connection()
        if active is not None:
            yield active
            return
        with self.store.connect() as conn:
            yield conn

    @contextmanager
    def atomic(self) -> Iterator["LegacyRuntimeProtocolLedger"]:
        if self._active_connection() is not None:
            yield self
            return

        conn = self.store.connect()
        self._transaction.connection = conn
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield self
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            try:
                del self._transaction.connection
            except AttributeError:
                pass
            conn.close()

    @staticmethod
    def _state_for(events: tuple[RuntimeEvent, ...]) -> str:
        if not events:
            return RunState.ACTIVE
        last = events[-1].operation
        if last == "finish_run":
            return RunState.FINISHED
        if last == "fail_run":
            return RunState.FAILED
        if last == "cancel_run":
            return RunState.CANCELLED
        return RunState.ACTIVE

    def get_run(self, run_id: str) -> RuntimeRun | None:
        with self._connection() as conn:
            root = conn.execute(
                "SELECT * FROM witness_runtime_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if root is None:
                return None
            rows = conn.execute(
                "SELECT * FROM witness_runtime_events WHERE run_id=? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        events = tuple(
            RuntimeEvent(
                event_id=str(row["event_id"]),
                run_id=str(row["run_id"]),
                sequence=int(row["sequence"]),
                operation=str(row["operation"]),
                payload=json.loads(str(row["payload_json"])),
            )
            for row in rows
        )
        return RuntimeRun(
            run_id=str(root["run_id"]),
            client_id=str(root["client_id"]),
            begin_payload=json.loads(str(root["begin_payload_json"])),
            state=self._state_for(events),
            events=events,
            protocol=protocol_for_run_id(str(root["run_id"])),
        )

    def put_run(self, run: RuntimeRun) -> None:
        if self._active_connection() is None:
            with self.atomic():
                self.put_run(run)
            return

        previous = self.get_run(run.run_id)
        if previous is not None:
            if previous.client_id != run.client_id or previous.begin_payload != run.begin_payload:
                raise RunStateConflict("runtime run identity is already bound to another root")
            if len(run.events) < len(previous.events):
                raise RunStateConflict("runtime history cannot be shortened")
            if run.events[: len(previous.events)] != previous.events:
                raise RunStateConflict("runtime history cannot be rewritten")
            if previous.state != RunState.ACTIVE and run != previous:
                raise RunStateConflict("terminal runtime run is immutable")

        with self._connection() as conn:
            try:
                if previous is None:
                    empty = RuntimeRun(
                        run_id=run.run_id,
                        client_id=run.client_id,
                        begin_payload=run.begin_payload,
                        state=RunState.ACTIVE,
                        events=(),
                        protocol=run.protocol,
                    )
                    conn.execute(
                        """INSERT INTO witness_runtime_runs(
                             run_id,client_id,begin_payload_json,created_record_digest
                           ) VALUES(?,?,?,?)""",
                        (
                            run.run_id,
                            run.client_id,
                            canonical_json(run.begin_payload),
                            empty.record_digest,
                        ),
                    )
                    start = 0
                else:
                    start = len(previous.events)
                for event in run.events[start:]:
                    conn.execute(
                        """INSERT INTO witness_runtime_events(
                             event_id,run_id,sequence,operation,payload_json
                           ) VALUES(?,?,?,?,?)""",
                        (
                            event.event_id,
                            event.run_id,
                            event.sequence,
                            event.operation,
                            canonical_json(event.payload),
                        ),
                    )
            except sqlite3.IntegrityError as exc:
                current = self.get_run(run.run_id)
                if current is not None and len(current.events) >= len(run.events):
                    raise SequenceConflict(
                        f"runtime sequence conflict for run {run.run_id}"
                    ) from exc
                raise RunStateConflict(
                    f"runtime run commit conflict for {run.run_id}"
                ) from exc

    def get_idempotency(self, client_id: str, key: str) -> tuple[str, dict[str, Any]] | None:
        with self._connection() as conn:
            row = conn.execute(
                """SELECT request_fingerprint,response_json
                   FROM witness_runtime_idempotency
                   WHERE client_id=? AND idempotency_key=?""",
                (client_id, key),
            ).fetchone()
        if row is None:
            return None
        return str(row["request_fingerprint"]), json.loads(str(row["response_json"]))

    def put_idempotency(
        self, client_id: str, key: str, fingerprint: str, response: dict[str, Any]
    ) -> None:
        if self._active_connection() is None:
            with self.atomic():
                self.put_idempotency(client_id, key, fingerprint, response)
            return

        previous = self.get_idempotency(client_id, key)
        if previous is not None:
            if previous[0] != fingerprint:
                raise IdempotencyConflict(
                    "idempotency key is already bound to another request"
                )
            return
        try:
            with self._connection() as conn:
                conn.execute(
                    """INSERT INTO witness_runtime_idempotency(
                         client_id,idempotency_key,request_fingerprint,response_json
                       ) VALUES(?,?,?,?)""",
                    (client_id, key, fingerprint, canonical_json(response)),
                )
        except sqlite3.IntegrityError as exc:
            previous = self.get_idempotency(client_id, key)
            if previous is not None and previous[0] == fingerprint:
                return
            raise IdempotencyConflict(
                "idempotency key is already bound to another request"
            ) from exc

    def cache_put(self, cache_key: str, value: dict[str, Any]) -> None:
        """Write disposable acceleration state; never evidence/history."""
        with self.store.connect() as conn:
            conn.execute(
                """INSERT INTO witness_runtime_cache(cache_key,value_json)
                   VALUES(?,?)
                   ON CONFLICT(cache_key) DO UPDATE SET value_json=excluded.value_json""",
                (str(cache_key), canonical_json(value)),
            )

    def cache_get(self, cache_key: str) -> dict[str, Any] | None:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT value_json FROM witness_runtime_cache WHERE cache_key=?",
                (str(cache_key),),
            ).fetchone()
        if row is None:
            return None
        return json.loads(str(row["value_json"]))

    def purge_cache(self) -> int:
        """Delete only disposable cache rows, preserving protocol evidence/history."""
        with self.store.connect() as conn:
            count = int(
                conn.execute("SELECT COUNT(*) AS n FROM witness_runtime_cache").fetchone()["n"]
            )
            conn.execute("DELETE FROM witness_runtime_cache")
        return count

    def evidence_counts(self) -> dict[str, int]:
        """Small invariant probe used by GC/tests/doctor-like callers."""
        with self.store.connect() as conn:
            runs = int(
                conn.execute("SELECT COUNT(*) AS n FROM witness_runtime_runs").fetchone()["n"]
            )
            events = int(
                conn.execute("SELECT COUNT(*) AS n FROM witness_runtime_events").fetchone()["n"]
            )
            idempotency = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM witness_runtime_idempotency"
                ).fetchone()["n"]
            )
        return {"runs": runs, "events": events, "idempotency": idempotency}


__all__ = ["LegacyRuntimeProtocolLedger"]
