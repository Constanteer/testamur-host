from __future__ import annotations

import secrets
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .artifacts import DEFAULT_IGNORES, snapshot_path
from .command_execution import CommandRequest
from .runtime_protocol import PROTOCOL_VERSION, canonical_hash


AGENT_WRAPPER_VERSION = "testamur-agent-wrapper-v0.4"
DEFAULT_WORKTREE_MAX_ENTRIES = 5_000
DEFAULT_WORKTREE_MAX_CHANGES = 500
_GIT_TIMEOUT_SECONDS = 2.0
_TERMINAL_OPERATIONS = {"finish_run", "fail_run", "cancel_run"}
_PYTHON_NO_VALUE_OPTIONS = {
    "-b", "-bb", "-B", "-d", "-E", "-h", "-i", "-I", "-O", "-OO",
    "-P", "-q", "-R", "-s", "-S", "-u", "-v", "-V", "-x",
    "--help", "--version", "--help-env", "--help-xoptions", "--help-all",
}
_PYTHON_VALUE_OPTIONS = {"-W", "-X", "--check-hash-based-pycs"}


class CommandRuntimeClient(Protocol):
    def execute_command(self, request: Mapping[str, Any]) -> dict[str, Any]: ...


CommandPolicy = Callable[[tuple[str, ...], Path], bool | None]


def _key(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(12)}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_declared_path(cwd: Path, raw: str | Path) -> Path:
    candidate = Path(raw).expanduser()
    return candidate if candidate.is_absolute() else cwd / candidate


def _artifact_shell(
    cwd: Path,
    raw: str | Path,
    *,
    role: str,
    required: bool,
    status: str,
) -> dict[str, Any]:
    return {
        "role": role,
        "declared_path": str(raw),
        "resolved_path": str(_resolve_declared_path(cwd, raw)),
        "required": bool(required),
        "observation_phase": "pre_execution" if role == "input" else "post_execution",
        "causal_attribution_implied": False,
        "status": status,
        "snapshot": None,
    }


def _artifact_observation(
    cwd: Path,
    raw: str | Path,
    *,
    role: str,
    required: bool,
) -> dict[str, Any]:
    resolved = _resolve_declared_path(cwd, raw)
    observation = _artifact_shell(cwd, raw, role=role, required=required, status="pending")
    if not resolved.exists() and not resolved.is_symlink():
        observation["status"] = "missing"
        return observation
    try:
        observation["snapshot"] = snapshot_path(resolved, root=cwd)
        observation["status"] = "captured"
    except (OSError, ValueError) as exc:
        observation["status"] = "capture_failed"
        observation["error"] = {"code": type(exc).__name__, "message": str(exc)}
    return observation


def _capture_artifacts(
    cwd: Path,
    paths: Sequence[str | Path],
    *,
    role: str,
    required: bool,
) -> list[dict[str, Any]]:
    return [_artifact_observation(cwd, raw, role=role, required=required) for raw in paths]


def _artifact_placeholders(
    cwd: Path,
    paths: Sequence[str | Path],
    *,
    role: str,
    required: bool,
    status: str,
) -> list[dict[str, Any]]:
    return [
        _artifact_shell(cwd, raw, role=role, required=required, status=status)
        for raw in paths
    ]


def _require_inputs(observations: Sequence[Mapping[str, Any]]) -> None:
    failures = [item for item in observations if item.get("status") != "captured"]
    if not failures:
        return
    details = ", ".join(
        f"{item.get('declared_path')} ({item.get('status')})" for item in failures
    )
    raise ValueError(f"declared input artifacts must be capturable before execution: {details}")


def _execution_error(response: Mapping[str, Any]) -> RuntimeError:
    error = response.get("error")
    if not isinstance(error, Mapping):
        return RuntimeError("command runtime returned an error without details")
    return RuntimeError(
        f"{str(error.get('code') or 'runtime_error')}: {str(error.get('message') or '')}"
    )


def _runtime_subcommand_is_run(args: Sequence[str]) -> bool:
    index = 0
    values = [str(item) for item in args]
    while index < len(values):
        item = values[index]
        if item == "--json":
            index += 1
            continue
        if item in {"--db", "--endpoint"}:
            if index + 1 >= len(values):
                return False
            index += 2
            continue
        if item.startswith("--db=") or item.startswith("--endpoint="):
            index += 1
            continue
        # The public CLI treats a bare separator as shorthand for
        # `testamur run -- <command>`; allowing it here would recursively wrap
        # Testamur inside its own command service.
        if item == "--":
            return True
        if item.startswith("-"):
            return False
        return item == "run"
    return False


def _is_python_executable(executable: str) -> tuple[bool, bool]:
    """Return (is_python_interpreter, is_windows_py_launcher) conservatively."""
    name = executable.lower()
    if name in {"py", "py.exe"}:
        return True, True
    if name.endswith(".exe"):
        name = name[:-4]
    for prefix in ("python", "pythonw", "pypy"):
        if name == prefix:
            return True, False
        if not name.startswith(prefix):
            continue
        suffix = name[len(prefix) :]
        if suffix and suffix[0].isdigit() and all(ch.isdigit() or ch == "." for ch in suffix):
            return True, False
    return False, False


def _python_module_invocation(
    args: Sequence[str],
    *,
    py_launcher: bool,
) -> tuple[str, list[str]] | None:
    values = [str(item) for item in args]
    index = 0
    while index < len(values):
        item = values[index]
        if item == "-m":
            if index + 1 >= len(values):
                return None
            return values[index + 1], values[index + 2 :]
        if item in {"-c", "-", "--"}:
            return None
        if item in _PYTHON_VALUE_OPTIONS:
            if index + 1 >= len(values):
                return None
            index += 2
            continue
        if (item.startswith("-W") and item != "-W") or (
            item.startswith("-X") and item != "-X"
        ):
            index += 1
            continue
        if item in _PYTHON_NO_VALUE_OPTIONS:
            index += 1
            continue
        if py_launcher and len(item) > 1 and item[0] == "-" and item[1].isdigit():
            index += 1
            continue
        if item.startswith("-"):
            return None
        # First non-option token is a script/path. Later tokens belong to it.
        return None
    return None


def _looks_like_recursive_testamur_run(argv: Sequence[str]) -> bool:
    if not argv:
        return False
    executable = Path(str(argv[0])).name.lower()
    rest = [str(item) for item in argv[1:]]
    if executable in {"testamur", "testamur.exe", "witness", "witness.exe"}:
        return _runtime_subcommand_is_run(rest)
    is_python, py_launcher = _is_python_executable(executable)
    if not is_python:
        return False
    invocation = _python_module_invocation(rest, py_launcher=py_launcher)
    if invocation is None:
        return False
    module, module_args = invocation
    return module in {
        "testamur",
        "testamur.__main__",
        "testamur.entrypoint",
        "testamur.front_router",
        "witness",
        "witness.runtime_cli",
    } and _runtime_subcommand_is_run(module_args)


def _environment_metadata() -> dict[str, Any]:
    return {
        "status": "not_captured",
        "policy": "environment_values_never_captured",
        "captured_values": {},
        "full_environment_captured": False,
    }


def _client_filesystem_scope(client: CommandRuntimeClient) -> str:
    scope = str(getattr(client, "execution_filesystem_scope", "caller_managed") or "caller_managed")
    return scope


def _run_git(cwd: Path, *args: str) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return int(completed.returncode), completed.stdout.strip()


def _git_context(cwd: Path) -> dict[str, Any]:
    code, root = _run_git(cwd, "rev-parse", "--show-toplevel")
    if code != 0 or not root:
        return {
            "status": "not_repository",
            "repo_root": None,
            "head_sha": None,
            "dirty": None,
            "branch": None,
        }
    _, head = _run_git(cwd, "rev-parse", "HEAD")
    branch_code, branch = _run_git(cwd, "symbolic-ref", "--short", "-q", "HEAD")
    status_code, status = _run_git(
        cwd,
        "status",
        "--porcelain=v1",
        "--untracked-files=normal",
        "--ignore-submodules=dirty",
    )
    return {
        "status": "captured",
        "repo_root": str(Path(root).resolve()),
        "head_sha": head or None,
        "dirty": None if status_code != 0 else bool(status),
        "branch": branch if branch_code == 0 and branch else None,
    }


def _worktree_state(cwd: Path, *, max_entries: int) -> dict[str, Any]:
    try:
        snapshot = snapshot_path(
            cwd,
            root=cwd.parent,
            ignore_names=set(DEFAULT_IGNORES) | {".witness"},
            max_entries=max_entries,
        )
    except (OSError, ValueError) as exc:
        return {
            "status": "capture_failed",
            "digest": None,
            "entry_count": None,
            "entries": {},
            "error": {"code": type(exc).__name__, "message": str(exc)},
        }
    entries: dict[str, str] = {}
    manifest = snapshot.get("manifest")
    if isinstance(manifest, list):
        for item in manifest:
            if isinstance(item, Mapping) and str(item.get("path") or ""):
                entries[str(item["path"])] = canonical_hash(dict(item))
    return {
        "status": "captured",
        "digest": "sha256:" + str(snapshot.get("content_hash") or ""),
        "entry_count": len(entries),
        "entries": entries,
    }


def _worktree_delta(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    max_changes: int,
) -> dict[str, Any]:
    if before.get("status") != "captured" or after.get("status") != "captured":
        return {
            "status": "capture_failed",
            "before_status": before.get("status"),
            "after_status": after.get("status"),
            "before_digest": before.get("digest"),
            "after_digest": after.get("digest"),
            "added": [], "removed": [], "changed": [],
            "truncated": False, "omitted_change_count": 0,
        }
    before_entries = dict(before.get("entries") or {})
    after_entries = dict(after.get("entries") or {})
    added = sorted(set(after_entries) - set(before_entries))
    removed = sorted(set(before_entries) - set(after_entries))
    changed = sorted(
        path
        for path in set(before_entries).intersection(after_entries)
        if before_entries[path] != after_entries[path]
    )
    total = len(added) + len(removed) + len(changed)
    remaining = int(max_changes)

    def take(items: list[str]) -> list[str]:
        nonlocal remaining
        selected = items[: max(remaining, 0)]
        remaining -= len(selected)
        return selected

    limited_added = take(added)
    limited_removed = take(removed)
    limited_changed = take(changed)
    returned = len(limited_added) + len(limited_removed) + len(limited_changed)
    return {
        "status": "captured",
        "before_digest": before.get("digest"),
        "after_digest": after.get("digest"),
        "before_entry_count": before.get("entry_count"),
        "after_entry_count": after.get("entry_count"),
        "added": limited_added,
        "removed": limited_removed,
        "changed": limited_changed,
        "truncated": returned < total,
        "omitted_change_count": total - returned,
    }


def _predicted_run_id(command_request: Mapping[str, Any]) -> str:
    request = CommandRequest.from_mapping(command_request)
    identity = {
        "protocol": PROTOCOL_VERSION,
        "client_id": request.client_id,
        "idempotency_key": f"command:{request.idempotency_key}:begin",
        "payload": request.begin_payload(),
    }
    return f"tst:run:{canonical_hash(identity)}"


def _terminal_process_from_run(
    result: Mapping[str, Any],
) -> tuple[str | None, dict[str, Any] | None]:
    events = result.get("events")
    if not isinstance(events, list) or not events:
        return None, None
    terminal = events[-1]
    if not isinstance(terminal, Mapping):
        return None, None
    operation = str(terminal.get("operation") or "")
    if operation not in _TERMINAL_OPERATIONS:
        return None, None
    payload = terminal.get("payload")
    if not isinstance(payload, Mapping):
        return operation, None
    process = payload.get("process")
    return operation, dict(process) if isinstance(process, Mapping) else None


def _inspect_interrupted_run(
    client: CommandRuntimeClient,
    command_request: Mapping[str, Any],
) -> dict[str, Any]:
    predicted = _predicted_run_id(command_request)
    call = getattr(client, "call", None)
    if not callable(call):
        return {
            "run_id": None, "predicted_run_id": predicted,
            "record_status": "unconfirmed_transport", "state": None,
            "record_digest": None, "next_sequence": None,
            "terminal_operation": None, "process": None,
        }
    inspect_request = {
        "protocol": PROTOCOL_VERSION,
        "client_id": str(command_request["client_id"]),
        "request_id": _key("req-agent-inspect"),
        # GET_RUN is temporal. A reused idempotency key would replay stale state.
        "idempotency_key": _key("agent-inspect"),
        "operation": "get_run",
        "run_id": predicted,
        "payload": {},
    }
    try:
        response = call(inspect_request)
    except Exception as exc:
        return {
            "run_id": None, "predicted_run_id": predicted,
            "record_status": "inspection_failed", "state": None,
            "record_digest": None, "next_sequence": None,
            "terminal_operation": None, "process": None,
            "inspection_error": {"code": type(exc).__name__, "message": str(exc)},
        }
    if not isinstance(response, Mapping) or not response.get("ok"):
        return {
            "run_id": None, "predicted_run_id": predicted,
            "record_status": "not_observed", "state": None,
            "record_digest": None, "next_sequence": None,
            "terminal_operation": None, "process": None,
        }
    result = response.get("result")
    if not isinstance(result, Mapping):
        return {
            "run_id": None, "predicted_run_id": predicted,
            "record_status": "invalid_inspection_response", "state": None,
            "record_digest": None, "next_sequence": None,
            "terminal_operation": None, "process": None,
        }
    state = str(result.get("state") or "")
    terminal_operation, process = _terminal_process_from_run(result)
    return {
        "run_id": predicted,
        "predicted_run_id": predicted,
        "record_status": "active_incomplete" if state == "active" else "terminal_observed",
        "state": state,
        "record_digest": result.get("record_digest"),
        "next_sequence": result.get("next_sequence"),
        "terminal_operation": terminal_operation,
        "process": process,
    }


def _existing_run_prevents_execution(
    client: CommandRuntimeClient,
    command_request: Mapping[str, Any],
) -> bool:
    observed = _inspect_interrupted_run(client, command_request)
    if observed.get("record_status") == "terminal_observed":
        return True
    if observed.get("record_status") != "active_incomplete":
        return False
    try:
        return int(observed.get("next_sequence") or 0) > 1
    except (TypeError, ValueError):
        return False


def _signal_metadata(returncode: int, timed_out: bool) -> tuple[int | None, str | None]:
    if timed_out or returncode >= 0:
        return None, None
    number = -returncode
    try:
        name = signal.Signals(number).name
    except (ValueError, AttributeError):
        name = f"SIG{number}"
    return number, name


def _process_fields(process: Mapping[str, Any]) -> dict[str, Any]:
    stdout_map = process.get("stdout") if isinstance(process.get("stdout"), Mapping) else {}
    stderr_map = process.get("stderr") if isinstance(process.get("stderr"), Mapping) else {}
    returncode = int(process.get("returncode", 127))
    timed_out = bool(process.get("timed_out", False))
    signal_number, signal_name = _signal_metadata(returncode, timed_out)
    execution_status = (
        "timed_out" if timed_out else
        "signaled" if signal_number is not None else
        "success" if returncode == 0 else "failed"
    )
    return {
        "execution_status": execution_status,
        "command_success": returncode == 0 and not timed_out,
        "exit_code": returncode,
        "timed_out": timed_out,
        "signal_number": signal_number,
        "signal_name": signal_name,
        "resolved_executable": process.get("resolved_executable"),
        "process_duration_ms": int(process.get("duration_ms", 0)),
        "stdout": str(stdout_map.get("text") or ""),
        "stderr": str(stderr_map.get("text") or ""),
        "stdout_preview": str(stdout_map.get("text") or ""),
        "stderr_preview": str(stderr_map.get("text") or ""),
        "stdout_digest": "sha256:" + str(stdout_map["sha256"]) if stdout_map.get("sha256") else None,
        "stderr_digest": "sha256:" + str(stderr_map["sha256"]) if stderr_map.get("sha256") else None,
        "stdout_byte_count": int(stdout_map["byte_size"]) if stdout_map.get("byte_size") is not None else None,
        "stderr_byte_count": int(stderr_map["byte_size"]) if stderr_map.get("byte_size") is not None else None,
        "stdout_captured_bytes": int(stdout_map["captured_bytes"]) if stdout_map.get("captured_bytes") is not None else None,
        "stderr_captured_bytes": int(stderr_map["captured_bytes"]) if stderr_map.get("captured_bytes") is not None else None,
        "stdout_truncated": bool(stdout_map.get("truncated", False)),
        "stderr_truncated": bool(stderr_map.get("truncated", False)),
    }


def _unknown_process_fields() -> dict[str, Any]:
    return {
        "execution_status": "interrupted", "command_success": False,
        "exit_code": None, "timed_out": False,
        "signal_number": None, "signal_name": None,
        "resolved_executable": None, "process_duration_ms": None,
        "stdout": "", "stderr": "", "stdout_preview": "", "stderr_preview": "",
        "stdout_digest": None, "stderr_digest": None,
        "stdout_byte_count": None, "stderr_byte_count": None,
        "stdout_captured_bytes": None, "stderr_captured_bytes": None,
        "stdout_truncated": False, "stderr_truncated": False,
    }


def _replay_artifact_observations(
    cwd: Path,
    paths: Sequence[str | Path],
    *,
    role: str,
) -> list[dict[str, Any]]:
    return _artifact_placeholders(
        cwd,
        paths,
        role=role,
        required=role == "input",
        status="not_attributed_replay" if role == "input" else "not_recaptured_replay",
    )


def _post_execution_context(
    workdir: Path,
    *,
    capture_git_context: bool,
    capture_worktree_delta: bool,
    worktree_before: Mapping[str, Any],
    worktree_max_entries: int,
    worktree_max_changes: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    git_after = _git_context(workdir) if capture_git_context else {"status": "disabled"}
    if not capture_worktree_delta:
        return git_after, {"status": "disabled"}
    worktree_after = _worktree_state(workdir, max_entries=worktree_max_entries)
    return git_after, _worktree_delta(worktree_before, worktree_after, max_changes=worktree_max_changes)


def _post_execution_observations(
    workdir: Path,
    output_artifacts: Sequence[str | Path],
    *,
    capture_git_context: bool,
    capture_worktree_delta: bool,
    worktree_before: Mapping[str, Any],
    worktree_max_entries: int,
    worktree_max_changes: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], str | None]:
    try:
        outputs = _capture_artifacts(workdir, output_artifacts, role="output", required=False)
    except KeyboardInterrupt:
        return (
            _artifact_placeholders(
                workdir, output_artifacts, role="output", required=False,
                status="not_captured_caller_interrupt",
            ),
            {"status": "not_attributed_caller_interrupt"},
            {"status": "not_attributed_caller_interrupt" if capture_worktree_delta else "disabled"},
            "output_artifact_capture",
        )
    try:
        git_after, worktree_delta = _post_execution_context(
            workdir,
            capture_git_context=capture_git_context,
            capture_worktree_delta=capture_worktree_delta,
            worktree_before=worktree_before,
            worktree_max_entries=worktree_max_entries,
            worktree_max_changes=worktree_max_changes,
        )
    except KeyboardInterrupt:
        return (
            outputs,
            {"status": "not_captured_caller_interrupt"},
            {"status": "not_attributed_caller_interrupt" if capture_worktree_delta else "disabled"},
            "post_execution_context",
        )
    return outputs, git_after, worktree_delta, None


def _truth_boundary_fields() -> dict[str, Any]:
    return {
        "verification_implied": False,
        "epistemic_acceptance_implied": False,
        "publication": {
            "mode": "not_published_by_wrapper",
            "cloud_publish_performed": False,
            "artifact_content_uploaded_by_wrapper": False,
            "transport_locality": "not_asserted",
        },
    }


def _common_receipt_fields(
    *,
    key: str,
    command: Sequence[str],
    workdir: Path,
    filesystem_scope: str,
    environment: Mapping[str, Any],
    git_before: Mapping[str, Any],
    git_after: Mapping[str, Any],
    worktree_delta: Mapping[str, Any],
    inputs: Sequence[Mapping[str, Any]],
    outputs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        **_truth_boundary_fields(),
        "idempotency_key": key,
        "idempotency_scope": "canonical_command_request; wrapper artifact observations excluded",
        "argv": list(command),
        "cwd": str(workdir),
        "execution_filesystem_scope": filesystem_scope,
        "stdout_artifact_path": None,
        "stderr_artifact_path": None,
        "stdin": {"policy": "inherited_unobserved", "captured": False, "digest": None},
        "environment": dict(environment),
        "git_context": {"before": dict(git_before), "after": dict(git_after)},
        "worktree_delta": dict(worktree_delta),
        "input_artifacts": [dict(item) for item in inputs],
        "output_artifacts": [dict(item) for item in outputs],
        "artifact_observations_persisted": False,
    }


def run_agent_command(
    client: CommandRuntimeClient,
    argv: Sequence[str],
    *,
    cwd: str | Path | None = None,
    timeout_seconds: int = 120,
    client_id: str = "testamur-agent-wrapper",
    idempotency_key: str | None = None,
    input_artifacts: Sequence[str | Path] = (),
    output_artifacts: Sequence[str | Path] = (),
    capture_git_context: bool = True,
    capture_worktree_delta: bool = False,
    worktree_max_entries: int = DEFAULT_WORKTREE_MAX_ENTRIES,
    worktree_max_changes: int = DEFAULT_WORKTREE_MAX_CHANGES,
    command_policy: CommandPolicy | None = None,
) -> dict[str, Any]:
    """Execute argv through the canonical Testamur command service and return a receipt."""
    command = tuple(str(item) for item in argv)
    if not command or any(not item for item in command):
        raise ValueError("run requires a non-empty argv")
    if _looks_like_recursive_testamur_run(command):
        raise ValueError(
            "nested 'testamur run' execution is not supported; invoke the inner "
            "command directly until an explicit parent-run contract exists"
        )

    filesystem_scope = _client_filesystem_scope(client)
    if filesystem_scope == "remote_or_unknown" and (
        input_artifacts or output_artifacts or capture_worktree_delta or capture_git_context
    ):
        raise ValueError(
            "runtime client reports remote/unknown execution filesystem; caller-side "
            "artifact, Git, and worktree observations require an explicit shared-filesystem contract"
        )

    workdir = Path(cwd or ".").expanduser().resolve()
    if not workdir.exists() or not workdir.is_dir():
        raise ValueError(f"cwd is not a directory: {workdir}")
    max_entries = int(worktree_max_entries)
    max_changes = int(worktree_max_changes)
    if max_entries < 1 or max_changes < 1:
        raise ValueError("worktree capture bounds must be positive")
    if command_policy is not None and command_policy(command, workdir) is False:
        raise PermissionError("command rejected by caller-supplied policy")

    key = str(idempotency_key or _key("agent-command"))
    request = {
        "client_id": str(client_id),
        "request_id": _key("req-agent-command"),
        "idempotency_key": key,
        "argv": list(command),
        "cwd": str(workdir),
        "timeout_seconds": int(timeout_seconds),
    }
    CommandRequest.from_mapping(request)

    known_nonexecuting_retry = bool(idempotency_key) and _existing_run_prevents_execution(
        client, request
    )
    if known_nonexecuting_retry:
        inputs = _artifact_placeholders(
            workdir,
            input_artifacts,
            role="input",
            required=True,
            status="not_read_existing_run_retry",
        )
        git_before = {"status": "not_observed_existing_run_retry"}
        worktree_before = {
            "status": "not_observed_existing_run_retry" if capture_worktree_delta else "disabled"
        }
    else:
        inputs = _capture_artifacts(workdir, input_artifacts, role="input", required=True)
        if any(item.get("status") != "captured" for item in inputs):
            # Cover a concurrent run becoming terminal/incomplete after the first probe.
            if not _existing_run_prevents_execution(client, request):
                _require_inputs(inputs)
        git_before = _git_context(workdir) if capture_git_context else {"status": "disabled"}
        worktree_before = (
            _worktree_state(workdir, max_entries=max_entries)
            if capture_worktree_delta else {"status": "disabled"}
        )

    environment = _environment_metadata()
    wrapper_started_at = _utc_now()
    wrapper_started = time.perf_counter()
    try:
        response = client.execute_command(request)
    except KeyboardInterrupt:
        wrapper_ended_at = _utc_now()
        wrapper_duration_ms = round((time.perf_counter() - wrapper_started) * 1000)
        interrupted_run = _inspect_interrupted_run(client, request)
        terminal_process = interrupted_run.get("process")
        if isinstance(terminal_process, Mapping):
            process_fields = _process_fields(terminal_process)
            outputs, git_after, worktree_delta, post_interrupt_phase = _post_execution_observations(
                workdir,
                output_artifacts,
                capture_git_context=capture_git_context,
                capture_worktree_delta=capture_worktree_delta,
                worktree_before=worktree_before,
                worktree_max_entries=max_entries,
                worktree_max_changes=max_changes,
            )
            execution_time_source = "durable_terminal_after_caller_interrupt"
            execution_started_at: str | None = wrapper_started_at
            execution_ended_at: str | None = wrapper_ended_at
            duration_ms = process_fields["process_duration_ms"]
        else:
            process_fields = _unknown_process_fields()
            outputs = _artifact_placeholders(
                workdir, output_artifacts, role="output", required=False,
                status="not_captured_interrupted",
            )
            git_after = {"status": "not_attributed_interrupted"}
            worktree_delta = {
                "status": "not_attributed_interrupted" if capture_worktree_delta else "disabled"
            }
            post_interrupt_phase = None
            execution_time_source = "unknown_process_after_caller_interrupt"
            execution_started_at = None
            execution_ended_at = None
            duration_ms = None
        return {
            "wrapper_version": AGENT_WRAPPER_VERSION,
            "run_id": interrupted_run["run_id"],
            "predicted_run_id": interrupted_run["predicted_run_id"],
            "state": interrupted_run["state"] or "interrupted",
            **{k: v for k, v in process_fields.items() if k != "process_duration_ms"},
            "record_digest": interrupted_run["record_digest"],
            "replayed": False,
            "wrapper_started_at": wrapper_started_at,
            "wrapper_ended_at": wrapper_ended_at,
            "wrapper_duration_ms": wrapper_duration_ms,
            "started_at": execution_started_at,
            "ended_at": execution_ended_at,
            "duration_ms": duration_ms,
            "execution_time_source": execution_time_source,
            "interrupted": True,
            "caller_interrupted": True,
            "caller_exit_code": 130,
            "caller_interrupt_phase": post_interrupt_phase or "command_transport_or_execution",
            "interrupt_signal_number": signal.SIGINT.value,
            "interrupt_signal_name": signal.SIGINT.name,
            **_common_receipt_fields(
                key=key, command=command, workdir=workdir,
                filesystem_scope=filesystem_scope, environment=environment,
                git_before=git_before, git_after=git_after,
                worktree_delta=worktree_delta, inputs=inputs, outputs=outputs,
            ),
            "interruption_run_record_status": interrupted_run["record_status"],
            "interruption_terminal_operation": interrupted_run["terminal_operation"],
            "interruption_next_sequence": interrupted_run["next_sequence"],
            "interruption_recording_note": (
                "caller interruption and child-process termination are distinct; the "
                "wrapper never retries a side-effecting command after KeyboardInterrupt"
            ),
        }

    if not isinstance(response, Mapping):
        raise RuntimeError("command runtime returned a non-object response")
    if not response.get("ok"):
        raise _execution_error(response)
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise RuntimeError("command runtime returned no result object")
    process = result.get("process")
    if not isinstance(process, Mapping):
        raise RuntimeError("command runtime returned no process observation")

    replayed = bool(response.get("replayed", False))
    post_interrupt_phase: str | None = None
    if replayed:
        inputs = _replay_artifact_observations(workdir, input_artifacts, role="input")
        outputs = _replay_artifact_observations(workdir, output_artifacts, role="output")
        git_before = {"status": "not_attributed_replay"}
        git_after = {"status": "not_attributed_replay"}
        worktree_delta = {
            "status": "not_attributed_replay" if capture_worktree_delta else "disabled"
        }
    else:
        outputs, git_after, worktree_delta, post_interrupt_phase = _post_execution_observations(
            workdir,
            output_artifacts,
            capture_git_context=capture_git_context,
            capture_worktree_delta=capture_worktree_delta,
            worktree_before=worktree_before,
            worktree_max_entries=max_entries,
            worktree_max_changes=max_changes,
        )

    process_fields = _process_fields(process)
    wrapper_ended_at = _utc_now()
    wrapper_duration_ms = round((time.perf_counter() - wrapper_started) * 1000)
    caller_interrupted = post_interrupt_phase is not None
    return {
        "wrapper_version": AGENT_WRAPPER_VERSION,
        "run_id": str(result.get("run_id") or ""),
        "predicted_run_id": None,
        "state": str(result.get("state") or ""),
        **{k: v for k, v in process_fields.items() if k != "process_duration_ms"},
        "record_digest": str(result.get("record_digest") or ""),
        "replayed": replayed,
        "wrapper_started_at": wrapper_started_at,
        "wrapper_ended_at": wrapper_ended_at,
        "wrapper_duration_ms": wrapper_duration_ms,
        "started_at": None if replayed else wrapper_started_at,
        "ended_at": None if replayed else wrapper_ended_at,
        "duration_ms": process_fields["process_duration_ms"],
        "execution_time_source": "not_available_replay" if replayed else "wrapper_observation",
        "interrupted": caller_interrupted,
        "caller_interrupted": caller_interrupted,
        "caller_exit_code": 130 if caller_interrupted else None,
        "caller_interrupt_phase": post_interrupt_phase,
        "interrupt_signal_number": signal.SIGINT.value if caller_interrupted else None,
        "interrupt_signal_name": signal.SIGINT.name if caller_interrupted else None,
        **_common_receipt_fields(
            key=key, command=command, workdir=workdir,
            filesystem_scope=filesystem_scope, environment=environment,
            git_before=git_before, git_after=git_after,
            worktree_delta=worktree_delta, inputs=inputs, outputs=outputs,
        ),
        "artifact_persistence_note": (
            "wrapper artifact snapshots are receipt metadata linked by run_id; Runtime "
            "Protocol v0.1 has no W4-owned persistence slot and they are not promoted "
            "to verified evidence, causal process attribution, or durable reliance"
        ),
    }


__all__ = [
    "AGENT_WRAPPER_VERSION",
    "CommandPolicy",
    "CommandRuntimeClient",
    "DEFAULT_WORKTREE_MAX_CHANGES",
    "DEFAULT_WORKTREE_MAX_ENTRIES",
    "run_agent_command",
]
