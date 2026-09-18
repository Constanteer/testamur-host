from __future__ import annotations

import json
import sqlite3
from typing import Any

from .runtime_store_legacy import LegacyRuntimeProtocolLedger as _LegacyLedger
from .runtime_protocol import (
    IdempotencyConflict,
    RunState,
    RunStateConflict,
    RuntimeRun,
    SequenceConflict,
    canonical_json,
)
from .runtime_integrity import (
    RuntimeIntegrityError,
    binding_digest,
    reconstruct_run,
    state_for_events,
    validate_idempotency_binding,
)


class TestamurProtocolLedger(_LegacyLedger):
    """Canonical runtime ledger with legacy-schema compatibility.

    The implementation and public Python identity are Testamur-owned. Historical
    ``witness_runtime_*`` table names are retained so existing databases reopen
    without a destructive cosmetic migration.
    """

    def _init_schema(self) -> None:
        with self.store.connect() as conn:
            checkpoints_existed = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='witness_runtime_checkpoints'"
            ).fetchone() is not None
            bindings_existed = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='witness_runtime_idempotency_integrity'"
            ).fetchone() is not None

        super()._init_schema()
        with self.store.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS witness_runtime_checkpoints(
                  run_id TEXT NOT NULL REFERENCES witness_runtime_runs(run_id),
                  sequence INTEGER NOT NULL,
                  record_digest TEXT NOT NULL,
                  PRIMARY KEY(run_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS witness_runtime_idempotency_integrity(
                  client_id TEXT NOT NULL,
                  idempotency_key TEXT NOT NULL,
                  binding_digest TEXT NOT NULL,
                  PRIMARY KEY(client_id, idempotency_key),
                  FOREIGN KEY(client_id, idempotency_key)
                    REFERENCES witness_runtime_idempotency(client_id, idempotency_key)
                );
                CREATE TRIGGER IF NOT EXISTS witness_runtime_checkpoints_no_update
                BEFORE UPDATE ON witness_runtime_checkpoints BEGIN
                  SELECT RAISE(ABORT, 'Runtime checkpoints are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS witness_runtime_checkpoints_no_delete
                BEFORE DELETE ON witness_runtime_checkpoints BEGIN
                  SELECT RAISE(ABORT, 'Runtime checkpoints are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS witness_runtime_idempotency_integrity_no_update
                BEFORE UPDATE ON witness_runtime_idempotency_integrity BEGIN
                  SELECT RAISE(ABORT, 'Runtime integrity bindings are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS witness_runtime_idempotency_integrity_no_delete
                BEFORE DELETE ON witness_runtime_idempotency_integrity BEGIN
                  SELECT RAISE(ABORT, 'Runtime integrity bindings are immutable');
                END;
                """
            )
            if not checkpoints_existed:
                self._backfill_checkpoints(conn)
            if not bindings_existed:
                self._backfill_binding_digests(conn)

    def _backfill_checkpoints(self, conn: sqlite3.Connection) -> None:
        for root in conn.execute("SELECT * FROM witness_runtime_runs ORDER BY run_id").fetchall():
            rows = conn.execute(
                "SELECT * FROM witness_runtime_events WHERE run_id=? ORDER BY sequence",
                (root["run_id"],),
            ).fetchall()
            run = reconstruct_run(root, rows, (), require_checkpoints=False)
            empty = RuntimeRun(
                run_id=run.run_id,
                client_id=run.client_id,
                begin_payload=run.begin_payload,
                state=RunState.ACTIVE,
                events=(),
                protocol=run.protocol,
            )
            conn.execute(
                "INSERT INTO witness_runtime_checkpoints VALUES(?,?,?)",
                (run.run_id, 0, empty.record_digest),
            )
            prefix = []
            for event in run.events:
                prefix.append(event)
                prefix_run = RuntimeRun(
                    run_id=run.run_id,
                    client_id=run.client_id,
                    begin_payload=run.begin_payload,
                    state=state_for_events(prefix),
                    events=tuple(prefix),
                    protocol=run.protocol,
                )
                conn.execute(
                    "INSERT INTO witness_runtime_checkpoints VALUES(?,?,?)",
                    (run.run_id, event.sequence, prefix_run.record_digest),
                )

    def _backfill_binding_digests(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            "SELECT client_id,idempotency_key,request_fingerprint,response_json FROM witness_runtime_idempotency"
        ).fetchall()
        for row in rows:
            response = json.loads(str(row["response_json"]))
            if not isinstance(response, dict):
                raise RuntimeIntegrityError("persisted idempotency response is not an object")
            conn.execute(
                "INSERT INTO witness_runtime_idempotency_integrity VALUES(?,?,?)",
                (
                    str(row["client_id"]),
                    str(row["idempotency_key"]),
                    binding_digest(
                        str(row["client_id"]),
                        str(row["idempotency_key"]),
                        str(row["request_fingerprint"]),
                        response,
                    ),
                ),
            )

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
            checkpoints = conn.execute(
                "SELECT sequence,record_digest FROM witness_runtime_checkpoints WHERE run_id=? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        return reconstruct_run(root, rows, checkpoints)

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
                        "INSERT INTO witness_runtime_runs(run_id,client_id,begin_payload_json,created_record_digest) VALUES(?,?,?,?)",
                        (run.run_id, run.client_id, canonical_json(run.begin_payload), empty.record_digest),
                    )
                    conn.execute(
                        "INSERT INTO witness_runtime_checkpoints VALUES(?,?,?)",
                        (run.run_id, 0, empty.record_digest),
                    )
                    start = 0
                    prefix = []
                else:
                    start = len(previous.events)
                    prefix = list(previous.events)

                for event in run.events[start:]:
                    conn.execute(
                        "INSERT INTO witness_runtime_events(event_id,run_id,sequence,operation,payload_json) VALUES(?,?,?,?,?)",
                        (event.event_id, event.run_id, event.sequence, event.operation, canonical_json(event.payload)),
                    )
                    prefix.append(event)
                    prefix_run = RuntimeRun(
                        run_id=run.run_id,
                        client_id=run.client_id,
                        begin_payload=run.begin_payload,
                        state=state_for_events(prefix),
                        events=tuple(prefix),
                        protocol=run.protocol,
                    )
                    conn.execute(
                        "INSERT INTO witness_runtime_checkpoints VALUES(?,?,?)",
                        (run.run_id, event.sequence, prefix_run.record_digest),
                    )
            except sqlite3.IntegrityError as exc:
                if run.events:
                    occupied = conn.execute(
                        "SELECT 1 FROM witness_runtime_events WHERE run_id=? AND sequence=?",
                        (run.run_id, run.events[-1].sequence),
                    ).fetchone()
                    if occupied is not None:
                        raise SequenceConflict(
                            f"runtime sequence conflict for run {run.run_id}"
                        ) from exc
                raise RunStateConflict(f"runtime run commit conflict for {run.run_id}") from exc

    def get_idempotency(self, client_id: str, key: str) -> tuple[str, dict[str, Any]] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT request_fingerprint,response_json FROM witness_runtime_idempotency WHERE client_id=? AND idempotency_key=?",
                (client_id, key),
            ).fetchone()
            if row is None:
                return None
            integrity = conn.execute(
                "SELECT binding_digest FROM witness_runtime_idempotency_integrity WHERE client_id=? AND idempotency_key=?",
                (client_id, key),
            ).fetchone()
        response = json.loads(str(row["response_json"]))
        if not isinstance(response, dict):
            raise RuntimeIntegrityError("runtime idempotency response is not an object")
        validate_idempotency_binding(
            client_id,
            key,
            str(row["request_fingerprint"]),
            response,
            None if integrity is None else str(integrity["binding_digest"]),
        )
        return str(row["request_fingerprint"]), response

    def put_idempotency(
        self,
        client_id: str,
        key: str,
        fingerprint: str,
        response: dict[str, Any],
    ) -> None:
        if self._active_connection() is None:
            with self.atomic():
                self.put_idempotency(client_id, key, fingerprint, response)
            return

        previous = self.get_idempotency(client_id, key)
        if previous is not None:
            if previous[0] != fingerprint:
                raise IdempotencyConflict("idempotency key is already bound to another request")
            return

        with self._connection() as conn:
            try:
                conn.execute(
                    "INSERT INTO witness_runtime_idempotency(client_id,idempotency_key,request_fingerprint,response_json) VALUES(?,?,?,?)",
                    (client_id, key, fingerprint, canonical_json(response)),
                )
                conn.execute(
                    "INSERT INTO witness_runtime_idempotency_integrity VALUES(?,?,?)",
                    (client_id, key, binding_digest(client_id, key, fingerprint, response)),
                )
            except sqlite3.IntegrityError as exc:
                current = self.get_idempotency(client_id, key)
                if current is not None and current[0] == fingerprint:
                    return
                raise IdempotencyConflict(
                    "idempotency key is already bound to another request"
                ) from exc


__all__ = ["TestamurProtocolLedger"]
