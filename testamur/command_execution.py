from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .runtime_protocol import PROTOCOL_VERSION, RunState, RuntimeProtocol


COMMAND_EXECUTION_VERSION = "testamur-command-v0.1"
DEFAULT_CAPTURE_BYTES = 100_000
DEFAULT_TIMEOUT_SECONDS = 120
MAX_TIMEOUT_SECONDS = 86_400


@dataclass(frozen=True)
class CommandRequest:
    client_id: str
    request_id: str
    idempotency_key: str
    argv: tuple[str, ...]
    cwd: str
    timeout_seconds: int

    @staticmethod
    def _text(value: Any, label: str) -> str:
        result = str(value or "").strip()
        if not result:
            raise ValueError(f"{label} is required")
        return result

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "CommandRequest":
        if not isinstance(raw, Mapping):
            raise ValueError("command request must be a JSON object")
        data = dict(raw)
        allowed = {
            "client_id", "request_id", "idempotency_key", "argv", "cwd", "timeout_seconds"
        }
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ValueError(f"unknown command request fields: {', '.join(unknown)}")
        argv_raw = data.get("argv")
        if not isinstance(argv_raw, list) or not argv_raw:
            raise ValueError("argv must be a non-empty JSON array")
        if any(not isinstance(item, str) or not item for item in argv_raw):
            raise ValueError("every argv item must be a non-empty string")
        cwd_path = Path(str(data.get("cwd") or ".")).expanduser().resolve()
        if not cwd_path.exists():
            raise ValueError(f"cwd does not exist: {cwd_path}")
        if not cwd_path.is_dir():
            raise ValueError(f"cwd is not a directory: {cwd_path}")
        try:
            timeout = int(data.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        except (TypeError, ValueError) as exc:
            raise ValueError("timeout_seconds must be an integer") from exc
        if timeout < 1 or timeout > MAX_TIMEOUT_SECONDS:
            raise ValueError(f"timeout_seconds must be between 1 and {MAX_TIMEOUT_SECONDS}")
        return cls(
            client_id=cls._text(data.get("client_id"), "client_id"),
            request_id=cls._text(data.get("request_id"), "request_id"),
            idempotency_key=cls._text(data.get("idempotency_key"), "idempotency_key"),
            argv=tuple(argv_raw), cwd=str(cwd_path), timeout_seconds=timeout,
        )

    def begin_payload(self) -> dict[str, Any]:
        return {
            "purpose": "command_execution", "execution_api": COMMAND_EXECUTION_VERSION,
            "argv": list(self.argv), "cwd": self.cwd, "timeout_seconds": self.timeout_seconds,
            "shell": False, "network_side_effect": False,
        }


class CommandExecutionService:
    """Local argv execution above the canonical Testamur Runtime Protocol.

    Process execution is recorded evidence only. Success does not imply
    verification, correctness, reliance, or universal truth.
    """

    def __init__(self, protocol: RuntimeProtocol, *, capture_bytes: int = DEFAULT_CAPTURE_BYTES) -> None:
        if int(capture_bytes) < 1:
            raise ValueError("capture_bytes must be at least 1")
        self.protocol = protocol
        self.capture_bytes = int(capture_bytes)
        self._lock = threading.RLock()

    @staticmethod
    def _error(request_id: str, code: str, message: str, **details: Any) -> dict[str, Any]:
        error: dict[str, Any] = {"code": str(code), "message": str(message)}
        if details:
            error["details"] = details
        return {"execution_api": COMMAND_EXECUTION_VERSION, "request_id": str(request_id or ""), "ok": False, "error": error}

    @staticmethod
    def _protocol_request(request: CommandRequest, operation: str, suffix: str, *, run_id: str | None = None, sequence: int | None = None, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "protocol": PROTOCOL_VERSION, "client_id": request.client_id,
            "request_id": f"{request.request_id}:{suffix}",
            "idempotency_key": f"command:{request.idempotency_key}:{suffix}",
            "operation": operation, "payload": dict(payload or {}),
        }
        if run_id is not None: result["run_id"] = str(run_id)
        if sequence is not None: result["sequence"] = int(sequence)
        return result

    def _protocol_call(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self.protocol.safe_call(request)

    def _capture(self, raw: bytes | str | None) -> dict[str, Any]:
        encoded = b"" if raw is None else raw if isinstance(raw, bytes) else str(raw).encode("utf-8", errors="replace")
        visible = encoded[: self.capture_bytes]
        return {"text": visible.decode("utf-8", errors="replace"), "sha256": hashlib.sha256(encoded).hexdigest(), "byte_size": len(encoded), "captured_bytes": len(visible), "truncated": len(encoded) > self.capture_bytes}

    def _run_process(self, request: CommandRequest) -> tuple[dict[str, Any], str]:
        started = time.perf_counter(); timed_out = False
        executable = shutil.which(request.argv[0]) or request.argv[0]
        try:
            completed = subprocess.run(list(request.argv), cwd=request.cwd, capture_output=True, timeout=request.timeout_seconds, check=False, shell=False, env=os.environ.copy())
            returncode, stdout, stderr = int(completed.returncode), completed.stdout, completed.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True; returncode, stdout, stderr = 124, exc.stdout, exc.stderr
        except OSError as exc:
            returncode, stdout, stderr = 127, b"", str(exc).encode("utf-8", errors="replace")
        process = {"argv": list(request.argv), "cwd": request.cwd, "resolved_executable": str(executable), "shell": False, "timeout_seconds": request.timeout_seconds, "returncode": returncode, "timed_out": timed_out, "duration_ms": round((time.perf_counter()-started)*1000), "stdout": self._capture(stdout), "stderr": self._capture(stderr)}
        return process, "timed_out" if timed_out else "success" if returncode == 0 else "process_failed"

    @staticmethod
    def _terminal_process(run: Any) -> dict[str, Any] | None:
        if not run.events: return None
        terminal = run.events[-1]
        if terminal.operation not in {"finish_run", "fail_run"}: return None
        payload = terminal.payload if isinstance(terminal.payload, dict) else {}
        process = payload.get("process")
        return dict(process) if isinstance(process, dict) else None

    def _completed_response(self, request: CommandRequest, run: Any, *, replayed: bool) -> dict[str, Any]:
        process = self._terminal_process(run)
        if process is None:
            return self._error(request.request_id, "execution_record_missing", "terminal command run does not contain a process observation", run_id=run.run_id)
        return {"execution_api": COMMAND_EXECUTION_VERSION, "request_id": request.request_id, "ok": True, "replayed": bool(replayed), "result": {"run_id": run.run_id, "state": str(run.state), "record_digest": run.record_digest, "process": process, "verification_implied": False, "network_used": False}}

    def execute(self, raw_request: Mapping[str, Any]) -> dict[str, Any]:
        request_id = str(raw_request.get("request_id") or "") if isinstance(raw_request, Mapping) else ""
        try: request = CommandRequest.from_mapping(raw_request)
        except (TypeError, ValueError) as exc: return self._error(request_id, "invalid_execute_request", str(exc))
        with self._lock:
            begin = self._protocol_call(self._protocol_request(request, "begin_run", "begin", payload=request.begin_payload()))
            if not begin.get("ok"): return self._error(request.request_id, str((begin.get("error") or {}).get("code") or "protocol_error"), str((begin.get("error") or {}).get("message") or "begin_run failed"))
            run_id = str(begin["result"]["run_id"]); run = self.protocol.ledger.get_run(run_id)
            if run is None: return self._error(request.request_id, "execution_state_missing", "begin_run succeeded but the protocol ledger cannot load the run", run_id=run_id)
            if run.state != RunState.ACTIVE: return self._completed_response(request, run, replayed=True)
            if run.events: return self._error(request.request_id, "execution_incomplete", "command intent is committed without a terminal process observation; refusing to re-run", run_id=run_id, next_sequence=run.next_sequence)
            intent = self._protocol_call(self._protocol_request(request, "execute", "execute", run_id=run_id, sequence=1, payload={"argv": list(request.argv), "cwd": request.cwd, "timeout_seconds": request.timeout_seconds, "shell": False, "execution_owner": COMMAND_EXECUTION_VERSION, "observation_pending": True}))
            if not intent.get("ok"): return self._error(request.request_id, str((intent.get("error") or {}).get("code") or "protocol_error"), str((intent.get("error") or {}).get("message") or "execute intent failed"), run_id=run_id)
            process, outcome = self._run_process(request)
            terminal = self._protocol_call(self._protocol_request(request, "finish_run" if outcome == "success" else "fail_run", "terminal", run_id=run_id, sequence=2, payload={"outcome": outcome, "process": process, "verification_implied": False}))
            if not terminal.get("ok"): return self._error(request.request_id, "terminal_record_failed", str((terminal.get("error") or {}).get("message") or "terminal event could not be recorded"), run_id=run_id, process=process)
            completed = self.protocol.ledger.get_run(run_id)
            if completed is None: return self._error(request.request_id, "execution_state_missing", "terminal event committed but the protocol ledger cannot load the run", run_id=run_id)
            return self._completed_response(request, completed, replayed=False)


__all__ = ["COMMAND_EXECUTION_VERSION", "DEFAULT_CAPTURE_BYTES", "DEFAULT_TIMEOUT_SECONDS", "MAX_TIMEOUT_SECONDS", "CommandRequest", "CommandExecutionService"]
