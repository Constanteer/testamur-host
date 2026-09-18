from __future__ import annotations

import copy
import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from threading import RLock
from typing import Any, ContextManager, Iterator, Mapping, Protocol


# New protocol envelopes and newly-created runtime identities are Testamur-owned.
# The historical Witness wire token remains readable only when reconstructing
# persisted legacy runs; it is not accepted as the protocol for new writes.
PROTOCOL_VERSION = "testamur-runtime-v0.1"
LEGACY_PROTOCOL_VERSION = "witness-runtime-v0.1"


class ProtocolError(ValueError):
    """Base error for transport-neutral protocol violations."""

    code = "protocol_error"

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self)}


class InvalidRequest(ProtocolError):
    code = "invalid_request"


class IdempotencyConflict(ProtocolError):
    code = "idempotency_conflict"


class RunNotFound(ProtocolError):
    code = "run_not_found"


class RunStateConflict(ProtocolError):
    code = "run_state_conflict"


class SequenceConflict(ProtocolError):
    code = "sequence_conflict"


class RunState(StrEnum):
    ACTIVE = "active"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Operation(StrEnum):
    BEGIN_RUN = "begin_run"
    OBSERVE_INPUT = "observe_input"
    EXECUTE = "execute"
    OBSERVE_ARTIFACT = "observe_artifact"
    DECLARE_RELATION = "declare_relation"
    DECLARE_CLAIM = "declare_claim"
    ATTACH_EVIDENCE = "attach_evidence"
    REQUEST_VERIFICATION = "request_verification"
    FINISH_RUN = "finish_run"
    FAIL_RUN = "fail_run"
    CANCEL_RUN = "cancel_run"
    GET_RUN = "get_run"


TERMINAL_OPERATIONS = {
    Operation.FINISH_RUN,
    Operation.FAIL_RUN,
    Operation.CANCEL_RUN,
}
EVENT_OPERATIONS = {
    Operation.OBSERVE_INPUT,
    Operation.EXECUTE,
    Operation.OBSERVE_ARTIFACT,
    Operation.DECLARE_RELATION,
    Operation.DECLARE_CLAIM,
    Operation.ATTACH_EVIDENCE,
    Operation.REQUEST_VERIFICATION,
    *TERMINAL_OPERATIONS,
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def protocol_for_run_id(run_id: str) -> str:
    """Infer the immutable protocol identity of a persisted runtime run.

    Historical `wtn:*` runs remain readable without keeping a Witness package.
    Every newly-created run uses the canonical `tst:*` family.
    """

    return LEGACY_PROTOCOL_VERSION if str(run_id).startswith("wtn:") else PROTOCOL_VERSION


def _stable_id(kind: str, value: Any, *, protocol: str = PROTOCOL_VERSION) -> str:
    prefix = "wtn" if protocol == LEGACY_PROTOCOL_VERSION else "tst"
    return f"{prefix}:{kind}:{canonical_hash(value)}"


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise InvalidRequest(f"{label} must be a JSON object")
    canonical_json(value)
    return dict(value)


def _required_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise InvalidRequest(f"{label} is required")
    return text


@dataclass(frozen=True)
class RuntimeEvent:
    event_id: str
    run_id: str
    sequence: int
    operation: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "operation": self.operation,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class RuntimeRun:
    run_id: str
    client_id: str
    begin_payload: dict[str, Any]
    state: str
    events: tuple[RuntimeEvent, ...]
    protocol: str = PROTOCOL_VERSION

    @property
    def next_sequence(self) -> int:
        return len(self.events) + 1

    @property
    def record_digest(self) -> str:
        return canonical_hash(self.logical_record())

    def logical_record(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "run_id": self.run_id,
            "client_id": self.client_id,
            "begin_payload": self.begin_payload,
            "state": self.state,
            "events": [event.as_dict() for event in self.events],
        }

    def as_dict(self) -> dict[str, Any]:
        result = self.logical_record()
        result["record_digest"] = self.record_digest
        result["next_sequence"] = self.next_sequence
        return result


class ProtocolLedger(Protocol):
    """Persistence boundary for protocol semantics."""

    def atomic(self) -> ContextManager["ProtocolLedger"]: ...

    def get_run(self, run_id: str) -> RuntimeRun | None: ...

    def put_run(self, run: RuntimeRun) -> None: ...

    def get_idempotency(self, client_id: str, key: str) -> tuple[str, dict[str, Any]] | None: ...

    def put_idempotency(
        self, client_id: str, key: str, fingerprint: str, response: dict[str, Any]
    ) -> None: ...


class MemoryProtocolLedger:
    def __init__(self) -> None:
        self._runs: dict[str, RuntimeRun] = {}
        self._idempotency: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
        self._lock = RLock()

    @contextmanager
    def atomic(self) -> Iterator["MemoryProtocolLedger"]:
        with self._lock:
            runs_before = copy.deepcopy(self._runs)
            idempotency_before = copy.deepcopy(self._idempotency)
            try:
                yield self
            except BaseException:
                self._runs = runs_before
                self._idempotency = idempotency_before
                raise

    def get_run(self, run_id: str) -> RuntimeRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def put_run(self, run: RuntimeRun) -> None:
        with self._lock:
            previous = self._runs.get(run.run_id)
            if previous is not None:
                if len(run.events) < len(previous.events):
                    raise RunStateConflict("protocol ledger cannot shorten run history")
                if run.events[: len(previous.events)] != previous.events:
                    raise RunStateConflict("protocol ledger cannot rewrite committed run events")
                if previous.state != RunState.ACTIVE and run != previous:
                    raise RunStateConflict("terminal run records are immutable")
            self._runs[run.run_id] = run

    def get_idempotency(self, client_id: str, key: str) -> tuple[str, dict[str, Any]] | None:
        with self._lock:
            entry = self._idempotency.get((client_id, key))
            if entry is None:
                return None
            fingerprint, response = entry
            return fingerprint, json.loads(canonical_json(response))

    def put_idempotency(
        self, client_id: str, key: str, fingerprint: str, response: dict[str, Any]
    ) -> None:
        with self._lock:
            identity = (client_id, key)
            previous = self._idempotency.get(identity)
            if previous is not None and previous[0] != fingerprint:
                raise IdempotencyConflict("idempotency key is already bound to another request")
            self._idempotency[identity] = (fingerprint, json.loads(canonical_json(response)))


class RuntimeProtocol:
    """Executable Testamur runtime protocol with read-only legacy persistence compatibility."""

    def __init__(self, ledger: ProtocolLedger | None = None) -> None:
        self.ledger = ledger or MemoryProtocolLedger()

    @staticmethod
    def _normalize_request(request: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            raise InvalidRequest("request must be a JSON object")
        data = dict(request)
        if data.get("protocol") != PROTOCOL_VERSION:
            raise InvalidRequest(f"protocol must be {PROTOCOL_VERSION!r}")
        data["client_id"] = _required_text(data.get("client_id"), "client_id")
        data["request_id"] = _required_text(data.get("request_id"), "request_id")
        data["idempotency_key"] = _required_text(
            data.get("idempotency_key"), "idempotency_key"
        )
        try:
            data["operation"] = Operation(str(data.get("operation"))).value
        except ValueError as exc:
            raise InvalidRequest(f"unknown operation {data.get('operation')!r}") from exc
        data["payload"] = _json_object(data.get("payload"), "payload")
        if "run_id" in data and data["run_id"] is not None:
            data["run_id"] = _required_text(data["run_id"], "run_id")
        if "sequence" in data and data["sequence"] is not None:
            try:
                data["sequence"] = int(data["sequence"])
            except (TypeError, ValueError) as exc:
                raise InvalidRequest("sequence must be an integer") from exc
            if data["sequence"] < 1:
                raise InvalidRequest("sequence must be >= 1")
        return data

    @staticmethod
    def _fingerprint(request: Mapping[str, Any]) -> str:
        semantic = {key: value for key, value in request.items() if key != "request_id"}
        return canonical_hash(semantic)

    @staticmethod
    def _response(request: Mapping[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL_VERSION,
            "request_id": request["request_id"],
            "ok": True,
            "result": result,
        }

    def call(self, raw_request: Mapping[str, Any]) -> dict[str, Any]:
        request = self._normalize_request(raw_request)
        atomic = getattr(self.ledger, "atomic", None)
        if atomic is None:
            return self._call_normalized(request, self.ledger)
        with atomic() as ledger:
            return self._call_normalized(request, ledger)

    def _call_normalized(
        self, request: Mapping[str, Any], ledger: ProtocolLedger
    ) -> dict[str, Any]:
        client_id = request["client_id"]
        key = request["idempotency_key"]
        fingerprint = self._fingerprint(request)
        previous = ledger.get_idempotency(client_id, key)
        if previous is not None:
            previous_fingerprint, previous_response = previous
            if previous_fingerprint != fingerprint:
                raise IdempotencyConflict(
                    f"idempotency key {key!r} is already bound to another request"
                )
            replay = dict(previous_response)
            replay["protocol"] = PROTOCOL_VERSION
            replay["request_id"] = request["request_id"]
            replay["replayed"] = True
            return replay

        operation = Operation(request["operation"])
        if operation is Operation.BEGIN_RUN:
            result = self._begin_run(request, ledger)
        elif operation is Operation.GET_RUN:
            result = self._get_run(request, ledger)
        else:
            result = self._append_event(request, operation, ledger)
        response = self._response(request, result)
        response["replayed"] = False
        ledger.put_idempotency(client_id, key, fingerprint, response)
        return response

    def safe_call(self, raw_request: Mapping[str, Any]) -> dict[str, Any]:
        request_id = ""
        try:
            if isinstance(raw_request, Mapping):
                request_id = str(raw_request.get("request_id") or "")
            return self.call(raw_request)
        except ProtocolError as exc:
            return {
                "protocol": PROTOCOL_VERSION,
                "request_id": request_id,
                "ok": False,
                "error": exc.as_dict(),
            }

    def _begin_run(
        self, request: Mapping[str, Any], ledger: ProtocolLedger
    ) -> dict[str, Any]:
        if request.get("run_id") is not None or request.get("sequence") is not None:
            raise InvalidRequest("begin_run must not supply run_id or sequence")
        payload = request["payload"]
        if payload.get("publish") is True:
            raise InvalidRequest(
                "begin_run records locally; publication is a separate network operation"
            )
        identity = {
            "protocol": PROTOCOL_VERSION,
            "client_id": request["client_id"],
            "idempotency_key": request["idempotency_key"],
            "payload": payload,
        }
        run_id = _stable_id("run", identity, protocol=PROTOCOL_VERSION)
        empty = RuntimeRun(
            run_id=run_id,
            client_id=request["client_id"],
            begin_payload=payload,
            state=RunState.ACTIVE,
            events=(),
            protocol=PROTOCOL_VERSION,
        )
        previous = ledger.get_run(run_id)
        if previous is not None:
            if previous.client_id != empty.client_id or previous.begin_payload != empty.begin_payload:
                raise RunStateConflict(
                    "deterministic run identity is already bound to another record"
                )
        else:
            ledger.put_run(empty)
        return {
            "run_id": run_id,
            "state": empty.state,
            "record_digest": empty.record_digest,
            "next_sequence": empty.next_sequence,
            "publication": "local_only",
        }

    def _load_owned_run(
        self, request: Mapping[str, Any], ledger: ProtocolLedger
    ) -> RuntimeRun:
        run_id = _required_text(request.get("run_id"), "run_id")
        run = ledger.get_run(run_id)
        if run is None or run.client_id != request["client_id"]:
            raise RunNotFound(run_id)
        return run

    def _get_run(
        self, request: Mapping[str, Any], ledger: ProtocolLedger
    ) -> dict[str, Any]:
        if request.get("sequence") is not None:
            raise InvalidRequest("get_run must not supply sequence")
        return self._load_owned_run(request, ledger).as_dict()

    @staticmethod
    def _validate_event_payload(operation: Operation, payload: Mapping[str, Any]) -> None:
        if operation is Operation.REQUEST_VERIFICATION:
            forbidden = {"verified", "verification_status", "verification_result", "passed"}
            claimed = sorted(forbidden.intersection(payload))
            if claimed:
                raise InvalidRequest(
                    "request_verification requests verifier work; clients cannot declare "
                    f"verification outcome fields: {', '.join(claimed)}"
                )
            if not str(payload.get("target_ref") or "").strip():
                raise InvalidRequest("request_verification requires target_ref")
            if not str(payload.get("verifier_ref") or "").strip():
                raise InvalidRequest("request_verification requires verifier_ref")
        if operation is Operation.FINISH_RUN and "verification_status" in payload:
            raise InvalidRequest(
                "client completion is not verification; verification_status is forbidden on finish_run"
            )
        if payload.get("publish") is True:
            raise InvalidRequest(
                "runtime event ingestion is local recording; publication is a separate network operation"
            )

    @staticmethod
    def _state_after(operation: Operation) -> str:
        if operation is Operation.FINISH_RUN:
            return RunState.FINISHED
        if operation is Operation.FAIL_RUN:
            return RunState.FAILED
        if operation is Operation.CANCEL_RUN:
            return RunState.CANCELLED
        return RunState.ACTIVE

    @staticmethod
    def _event_for(
        run: RuntimeRun,
        operation: Operation,
        sequence: int,
        payload: dict[str, Any],
    ) -> RuntimeEvent:
        event_identity = {
            "protocol": run.protocol,
            "run_id": run.run_id,
            "sequence": sequence,
            "operation": operation.value,
            "payload": payload,
        }
        return RuntimeEvent(
            event_id=_stable_id("event", event_identity, protocol=run.protocol),
            run_id=run.run_id,
            sequence=sequence,
            operation=operation.value,
            payload=payload,
        )

    @classmethod
    def _event_result(
        cls,
        run: RuntimeRun,
        event: RuntimeEvent,
        operation: Operation,
    ) -> dict[str, Any]:
        result = {
            "run_id": run.run_id,
            "event_id": event.event_id,
            "sequence": event.sequence,
            "state": run.state,
            "record_digest": run.record_digest,
            "next_sequence": run.next_sequence,
            "publication": "local_only",
        }
        if operation is Operation.FINISH_RUN:
            result["client_outcome"] = event.payload.get("outcome", "completed")
            result["verification_implied"] = False
        elif operation is Operation.REQUEST_VERIFICATION:
            result["verification_implied"] = False
        return result

    def _append_event(
        self,
        request: Mapping[str, Any],
        operation: Operation,
        ledger: ProtocolLedger,
    ) -> dict[str, Any]:
        if operation not in EVENT_OPERATIONS:
            raise InvalidRequest(f"operation {operation.value!r} is not a run event")
        run = self._load_owned_run(request, ledger)
        if run.protocol != PROTOCOL_VERSION:
            raise RunStateConflict(
                "legacy runtime runs are read-only; begin a Testamur runtime run for new events"
            )
        sequence_value = request.get("sequence")
        if sequence_value is None:
            raise InvalidRequest(f"{operation.value} requires sequence")
        sequence = int(sequence_value)
        payload = request["payload"]
        self._validate_event_payload(operation, payload)
        event = self._event_for(run, operation, sequence, payload)

        if sequence <= len(run.events):
            existing = run.events[sequence - 1]
            if existing != event:
                raise SequenceConflict(
                    f"sequence {sequence} is already committed to another event"
                )
            prefix = RuntimeRun(
                run_id=run.run_id,
                client_id=run.client_id,
                begin_payload=run.begin_payload,
                state=self._state_after(operation),
                events=run.events[:sequence],
                protocol=run.protocol,
            )
            return self._event_result(prefix, existing, operation)

        if run.state != RunState.ACTIVE:
            raise RunStateConflict(f"run {run.run_id} is already {run.state}")
        if sequence != run.next_sequence:
            raise SequenceConflict(
                f"expected sequence {run.next_sequence}, received {sequence}"
            )
        updated = RuntimeRun(
            run_id=run.run_id,
            client_id=run.client_id,
            begin_payload=run.begin_payload,
            state=self._state_after(operation),
            events=run.events + (event,),
            protocol=run.protocol,
        )
        ledger.put_run(updated)
        return self._event_result(updated, event, operation)


class EmbeddedRuntimeAdapter:
    """Reference adapter used by embedded agent/tool integrations."""

    def __init__(self, protocol: RuntimeProtocol | None = None) -> None:
        self.protocol = protocol or RuntimeProtocol()

    def request(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self.protocol.safe_call(request)


class JsonRuntimeAdapter:
    """Wire-shaped adapter proving JSON transport does not change semantics."""

    def __init__(self, protocol: RuntimeProtocol | None = None) -> None:
        self.protocol = protocol or RuntimeProtocol()

    def request_json(self, request_json: str) -> str:
        try:
            request = json.loads(request_json)
        except json.JSONDecodeError as exc:
            response = {
                "protocol": PROTOCOL_VERSION,
                "request_id": "",
                "ok": False,
                "error": {"code": "invalid_request", "message": f"invalid JSON: {exc.msg}"},
            }
        else:
            response = self.protocol.safe_call(request)
        return canonical_json(response)


def replay_transcript(
    adapter: EmbeddedRuntimeAdapter | JsonRuntimeAdapter,
    transcript: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    responses: list[dict[str, Any]] = []
    for request in transcript:
        if isinstance(adapter, JsonRuntimeAdapter):
            response = json.loads(adapter.request_json(canonical_json(request)))
        else:
            response = adapter.request(request)
        responses.append(response)
    return responses
