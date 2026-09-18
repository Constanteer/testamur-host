from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ._work_session_types import UsageState, WorkSessionStatus
from .agent_capture import AgentCapture
from .runtime_protocol import canonical_hash
from .source_store import TestamurSourceStore
from .work_session import WorkSessionStore

CODEX_GATEWAY_ADAPTER = {
    "adapter_id": "testamur-codex",
    "adapter_version": "0.3.0",
    "family": "coding_agent",
    "fidelity": "NATIVE",
    "exact_content_capture": True,
    "model_context_exposure_signal": False,
    "human_access_handoff": False,
}
_GATEWAY_CAPTURE_SCHEMA = "testamur.source-gateway.capture.v1"
_GATEWAY_SOURCE_TOKENS = ("fetch", "revalidate", "watch_refresh")
_BINDING_SCHEMA = """
CREATE TABLE IF NOT EXISTS testamur_codex_session_bindings(
    host_session_hash TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    work_session_id TEXT NOT NULL UNIQUE,
    closed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    closed_at TEXT,
    PRIMARY KEY(host_session_hash, ordinal)
);
"""

class CodexHookError(ValueError):
    pass

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def _required_text(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise CodexHookError(f"{field} is required")
    return text

def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}

def resolve_database_path(raw: Mapping[str, Any], *, environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    explicit = str(env.get("TESTAMUR_DB") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return Path(str(raw.get("cwd") or ".")).expanduser() / ".testamur" / "evidence.db"

def _host_session_hash(raw: Mapping[str, Any]) -> str:
    return canonical_hash({
        "adapter": "testamur-codex",
        "host_session_id": _required_text(raw.get("session_id"), field="session_id"),
    })

def _safe_locator(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = urllib.parse.urlsplit(raw)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname + (f":{port}" if port is not None else "")
    return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path or "/", "", ""))

def _gateway_capture(value: Any, *, depth: int = 0) -> Mapping[str, Any] | None:
    if depth > 4 or not isinstance(value, Mapping):
        return None
    if str(value.get("schema") or "") == _GATEWAY_CAPTURE_SCHEMA:
        return value
    for key in ("structuredContent", "result", "capture", "gateway_capture"):
        nested = value.get(key)
        if isinstance(nested, Mapping):
            found = _gateway_capture(nested, depth=depth + 1)
            if found is not None:
                return found
    return None

def _successful_tool_response(value: Any) -> bool:
    return not isinstance(value, Mapping) or value.get("isError") is not True

def _looks_like_gateway_source_tool(tool_name: str) -> bool:
    lowered = tool_name.lower()
    return "testamur" in lowered and any(token in lowered for token in _GATEWAY_SOURCE_TOKENS)

class CodexGatewayHookBridge:
    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.sessions = WorkSessionStore(self.path)
        self.capture = AgentCapture(self.sessions)
        self.sources = TestamurSourceStore(self.path)
        with self._connect() as conn:
            conn.executescript(_BINDING_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _latest_binding(self, host_hash: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM testamur_codex_session_bindings WHERE host_session_hash=? ORDER BY ordinal DESC LIMIT 1",
                (host_hash,),
            ).fetchone()

    def _open_session(self, raw: Mapping[str, Any]) -> tuple[str, bool]:
        host_hash = _host_session_hash(raw)
        row = self._latest_binding(host_hash)
        if row is not None and not bool(row["closed"]):
            sid = str(row["work_session_id"])
            try:
                if self.sessions.get_session(sid).status is WorkSessionStatus.OPEN:
                    return sid, False
            except KeyError:
                pass
        ordinal = 1 if row is None else int(row["ordinal"]) + 1
        sid = "tst:work-session:" + canonical_hash({
            "adapter": "testamur-codex", "host_session_hash": host_hash, "ordinal": ordinal
        })
        self.sessions.start_session(
            initiating_actor_ref="codex:hook",
            host_environment="codex-hooks",
            agent_ref="codex",
            capture_policy_ref="testamur:codex-hooks:v1",
            visibility="LOCAL",
            session_id=sid,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO testamur_codex_session_bindings VALUES(?,?,?,?,?,NULL)",
                (host_hash, ordinal, sid, 0, _now()),
            )
        return sid, True

    def _current_session(self, raw: Mapping[str, Any]) -> str | None:
        row = self._latest_binding(_host_session_hash(raw))
        return None if row is None or bool(row["closed"]) else str(row["work_session_id"])

    def _candidate(self, session_id: str, locator: str, *, metadata: Mapping[str, Any], observed_at: str | None = None):
        for item in self.sessions.list_candidates(session_id):
            if item.locator == locator:
                return item, False
        return self.capture.discover(
            session_id, locator=locator, metadata=metadata, observed_at=observed_at
        ), True

    def _validated_gateway_capture(self, value: Mapping[str, Any]):
        source_hint, snapshot_hint, revision_hint = (
            _mapping(value.get("source")), _mapping(value.get("snapshot")), _mapping(value.get("revision"))
        )
        source_id = str(source_hint.get("source_id") or snapshot_hint.get("source_id") or "").strip()
        snapshot_id = str(snapshot_hint.get("snapshot_id") or "").strip()
        revision_id = str(revision_hint.get("revision_id") or snapshot_hint.get("revision_id") or "").strip()
        if not source_id or not snapshot_id or not revision_id:
            return None
        source = self.sources.get_source(source_id)
        snapshot = self.sources.get_snapshot(snapshot_id)
        revision = self.sources.get_revision(revision_id)
        if source is None or snapshot is None or revision is None:
            return None
        if str(snapshot.get("source_id")) != source_id or str(snapshot.get("revision_id")) != revision_id:
            return None
        if str(revision.get("source_id")) != source_id:
            return None
        if str(snapshot.get("content_hash")) != str(revision.get("content_hash")):
            return None
        return source, snapshot, revision

    def _bind_exact(self, raw: Mapping[str, Any], *, session_id: str, tool_name: str, tool_use_id: str, capture: Mapping[str, Any]):
        resolved = self._validated_gateway_capture(capture)
        if resolved is None:
            return None
        source, snapshot, revision = resolved
        locator = _safe_locator(source.get("initial_locator"))
        if locator is None:
            return None
        candidate, _ = self._candidate(
            session_id, locator,
            metadata={
                "adapter": "testamur-codex",
                "source_id": source["source_id"],
                "host_tool_name": tool_name,
                "gateway_managed": True,
                "raw_tool_payload_persisted": False,
            },
            observed_at=str(snapshot["observed_at"]),
        )
        evidence_ref = "tst:codex-tool:" + canonical_hash({
            "session_id": session_id,
            "tool_use_id": tool_use_id,
            "snapshot_id": snapshot["snapshot_id"],
            "source_revision_id": revision["revision_id"],
        })
        for obs in self.sessions.observations(session_id, candidate_id=candidate.candidate_id):
            if evidence_ref in obs.evidence_refs:
                return {
                    "status": "idempotent_replay",
                    "session_id": session_id,
                    "candidate_id": candidate.candidate_id,
                    "source_revision_id": revision["revision_id"],
                    "observation_id": obs.observation_id,
                    "exact_revision_resolved": True,
                    "durable_reliance_implied": False,
                }
        obs = self.capture.fetched(
            session_id, candidate.candidate_id,
            source_revision_id=str(revision["revision_id"]),
            retrieval_receipt_ref=evidence_ref,
            created_at=str(snapshot["observed_at"]),
        )
        return {
            "status": "recorded",
            "session_id": session_id,
            "candidate_id": candidate.candidate_id,
            "source_revision_id": revision["revision_id"],
            "snapshot_id": snapshot["snapshot_id"],
            "observation_id": obs.observation_id,
            "usage_state": obs.usage_state.value,
            "exact_revision_resolved": True,
            "durable_reliance_implied": False,
        }

    def _discover_locator_only(self, raw: Mapping[str, Any], *, session_id: str, tool_name: str):
        tool_input = _mapping(raw.get("tool_input"))
        locator = next((_safe_locator(tool_input.get(k)) for k in ("locator", "url", "uri") if _safe_locator(tool_input.get(k)) is not None), None)
        if locator is None:
            return {
                "status": "observed_unmapped",
                "session_id": session_id,
                "host_tool_name": tool_name,
                "semantic_record_created": False,
                "raw_tool_payload_persisted": False,
            }
        candidate, created = self._candidate(
            session_id, locator,
            metadata={
                "adapter": "testamur-codex",
                "host_tool_name": tool_name,
                "exact_content_capture": False,
                "raw_tool_payload_persisted": False,
            },
        )
        return {
            "status": "discovered" if created else "idempotent_replay",
            "session_id": session_id,
            "candidate_id": candidate.candidate_id,
            "locator": locator,
            "source_revision_id": None,
            "usage_state": UsageState.DISCOVERED.value,
            "exact_revision_resolved": False,
            "durable_reliance_implied": False,
        }

    def handle(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        event_name = _required_text(raw.get("hook_event_name"), field="hook_event_name")
        if event_name == "SessionStart":
            sid, created = self._open_session(raw)
            return {"status": "session_started" if created else "session_ready", "session_id": sid, "adapter": dict(CODEX_GATEWAY_ADAPTER)}
        if event_name == "PostToolUse":
            sid = self._current_session(raw)
            late_attach = False
            if sid is None:
                sid, _ = self._open_session(raw)
                late_attach = True
            tool_name = _required_text(raw.get("tool_name"), field="tool_name")
            tool_use_id = _required_text(raw.get("tool_use_id"), field="tool_use_id")
            response = raw.get("tool_response")
            structured = _gateway_capture(response) if _successful_tool_response(response) and _looks_like_gateway_source_tool(tool_name) else None
            result = self._bind_exact(raw, session_id=sid, tool_name=tool_name, tool_use_id=tool_use_id, capture=structured) if structured is not None else None
            if result is None:
                result = self._discover_locator_only(raw, session_id=sid, tool_name=tool_name)
            result["late_attach"] = late_attach
            return result
        if event_name == "SessionEnd":
            sid = self._current_session(raw)
            if sid is None:
                return {"status": "no_open_session"}
            session = self.sessions.get_session(sid)
            if session.status is WorkSessionStatus.OPEN:
                session = self.sessions.complete(sid)
            with self._connect() as conn:
                conn.execute(
                    "UPDATE testamur_codex_session_bindings SET closed=1,closed_at=? WHERE host_session_hash=? AND work_session_id=?",
                    (_now(), _host_session_hash(raw), sid),
                )
            return {"status": "session_completed", "session_id": sid, "work_session_status": session.status.value}
        return {"status": "ignored_hook_event", "hook_event_name": event_name, "semantic_record_created": False}

def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        raw = json.load(sys.stdin)
        if not isinstance(raw, Mapping):
            raise CodexHookError("Codex hook input must be a JSON object")
        result = CodexGatewayHookBridge(resolve_database_path(raw)).handle(raw)
        if os.environ.get("TESTAMUR_CODEX_DEBUG") == "1":
            print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"Testamur Codex capture failed: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
