from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from typing import Any

from .runtime_protocol import (
    EVENT_OPERATIONS,
    LEGACY_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    TERMINAL_OPERATIONS,
    Operation,
    RunState,
    RuntimeEvent,
    RuntimeRun,
    canonical_hash,
    protocol_for_run_id,
)


class RuntimeIntegrityError(sqlite3.DatabaseError):
    """Stored runtime evidence cannot be reconstructed without contradiction."""


def state_for_events(events: Iterable[RuntimeEvent]) -> str:
    state = RunState.ACTIVE
    for event in events:
        operation = Operation(event.operation)
        if operation is Operation.FINISH_RUN:
            state = RunState.FINISHED
        elif operation is Operation.FAIL_RUN:
            state = RunState.FAILED
        elif operation is Operation.CANCEL_RUN:
            state = RunState.CANCELLED
    return state


def event_id_for(
    *,
    run_id: str,
    sequence: int,
    operation: str,
    payload: Mapping[str, Any],
    protocol: str | None = None,
) -> str:
    resolved_protocol = protocol or protocol_for_run_id(run_id)
    identity = {
        "protocol": resolved_protocol,
        "run_id": str(run_id),
        "sequence": int(sequence),
        "operation": str(operation),
        "payload": dict(payload),
    }
    prefix = "wtn" if resolved_protocol == LEGACY_PROTOCOL_VERSION else "tst"
    return f"{prefix}:event:{canonical_hash(identity)}"


def binding_digest(
    client_id: str,
    key: str,
    fingerprint: str,
    response: Mapping[str, Any],
) -> str:
    return canonical_hash(
        {
            "client_id": str(client_id),
            "idempotency_key": str(key),
            "request_fingerprint": str(fingerprint),
            "response": dict(response),
        }
    )


def validate_idempotency_binding(
    client_id: str,
    key: str,
    fingerprint: str,
    response: Mapping[str, Any],
    stored_digest: str | None,
) -> None:
    if not stored_digest:
        raise RuntimeIntegrityError(
            f"missing runtime idempotency integrity record for {client_id!r}/{key!r}"
        )
    actual = binding_digest(client_id, key, fingerprint, response)
    if actual != str(stored_digest):
        raise RuntimeIntegrityError(
            f"runtime idempotency binding integrity mismatch for {client_id!r}/{key!r}"
        )


def _checkpoint_map(rows: Iterable[Mapping[str, Any]]) -> dict[int, str]:
    result: dict[int, str] = {}
    for row in rows:
        sequence = int(row["sequence"])
        if sequence in result:
            raise RuntimeIntegrityError(f"duplicate runtime checkpoint at sequence {sequence}")
        result[sequence] = str(row["record_digest"])
    return result


def reconstruct_run(
    root: Mapping[str, Any],
    event_rows: Iterable[Mapping[str, Any]],
    checkpoint_rows: Iterable[Mapping[str, Any]],
    *,
    require_checkpoints: bool = True,
) -> RuntimeRun:
    run_id = str(root["run_id"])
    protocol = protocol_for_run_id(run_id)
    client_id = str(root["client_id"])
    try:
        begin_payload = json.loads(str(root["begin_payload_json"]))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeIntegrityError(f"invalid runtime root payload for {run_id}") from exc
    if not isinstance(begin_payload, dict):
        raise RuntimeIntegrityError(f"runtime root payload is not an object for {run_id}")

    empty = RuntimeRun(
        run_id=run_id,
        client_id=client_id,
        begin_payload=begin_payload,
        state=RunState.ACTIVE,
        events=(),
        protocol=protocol,
    )
    if str(root["created_record_digest"]) != empty.record_digest:
        raise RuntimeIntegrityError(f"runtime root digest mismatch for {run_id}")

    events: list[RuntimeEvent] = []
    terminal_seen = False
    for expected_sequence, row in enumerate(event_rows, start=1):
        row_run_id = str(row["run_id"])
        sequence = int(row["sequence"])
        operation_text = str(row["operation"])
        try:
            payload = json.loads(str(row["payload_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeIntegrityError(
                f"invalid runtime event payload at {run_id}:{sequence}"
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeIntegrityError(
                f"runtime event payload is not an object at {run_id}:{sequence}"
            )
        if row_run_id != run_id:
            raise RuntimeIntegrityError(
                f"runtime event {row['event_id']} is bound to the wrong run"
            )
        if sequence != expected_sequence:
            raise RuntimeIntegrityError(
                f"runtime sequence gap for {run_id}: expected {expected_sequence}, got {sequence}"
            )
        try:
            operation = Operation(operation_text)
        except ValueError as exc:
            raise RuntimeIntegrityError(
                f"unknown persisted runtime operation {operation_text!r}"
            ) from exc
        if operation not in EVENT_OPERATIONS:
            raise RuntimeIntegrityError(
                f"non-event operation persisted in runtime history: {operation.value}"
            )
        if terminal_seen:
            raise RuntimeIntegrityError(f"runtime event exists after terminal state for {run_id}")
        expected_event_id = event_id_for(
            run_id=run_id,
            sequence=sequence,
            operation=operation.value,
            payload=payload,
            protocol=protocol,
        )
        if str(row["event_id"]) != expected_event_id:
            raise RuntimeIntegrityError(
                f"runtime event identity mismatch at {run_id}:{sequence}"
            )
        event = RuntimeEvent(
            event_id=expected_event_id,
            run_id=run_id,
            sequence=sequence,
            operation=operation.value,
            payload=payload,
        )
        events.append(event)
        terminal_seen = operation in TERMINAL_OPERATIONS

    run = RuntimeRun(
        run_id=run_id,
        client_id=client_id,
        begin_payload=begin_payload,
        state=state_for_events(events),
        events=tuple(events),
        protocol=protocol,
    )

    checkpoints = _checkpoint_map(checkpoint_rows)
    if require_checkpoints or checkpoints:
        expected_sequences = set(range(0, len(events) + 1))
        if set(checkpoints) != expected_sequences:
            raise RuntimeIntegrityError(
                f"runtime checkpoint/event tail mismatch for {run_id}: "
                f"checkpoints={sorted(checkpoints)}, events={len(events)}"
            )
        if checkpoints[0] != empty.record_digest:
            raise RuntimeIntegrityError(f"runtime root checkpoint mismatch for {run_id}")
        prefix: list[RuntimeEvent] = []
        for sequence, event in enumerate(events, start=1):
            prefix.append(event)
            prefix_run = RuntimeRun(
                run_id=run_id,
                client_id=client_id,
                begin_payload=begin_payload,
                state=state_for_events(prefix),
                events=tuple(prefix),
                protocol=protocol,
            )
            if checkpoints[sequence] != prefix_run.record_digest:
                raise RuntimeIntegrityError(
                    f"runtime checkpoint digest mismatch at {run_id}:{sequence}"
                )
    return run
