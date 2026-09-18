from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping, Sequence

WORK_SESSION_VERSION = "testamur-work-session-v1"


class WorkSessionStatus(StrEnum):
    OPEN = "OPEN"
    COMPLETED_UNRECONCILED = "COMPLETED_UNRECONCILED"
    RECONCILED = "RECONCILED"
    PUSHED = "PUSHED"
    ATTESTED = "ATTESTED"
    ABORTED = "ABORTED"


class UsageState(StrEnum):
    DISCOVERED = "DISCOVERED"
    FETCHED = "FETCHED"
    INSPECTED = "INSPECTED"
    EXPOSED_TO_MODEL = "EXPOSED_TO_MODEL"
    EXPLICITLY_REFERENCED = "EXPLICITLY_REFERENCED"
    RELIED_ON_BY_PROJECT_OBJECT = "RELIED_ON_BY_PROJECT_OBJECT"


class ReconciliationPolicy(StrEnum):
    TRUST_AGENT_DECLARATION = "TRUST_AGENT_DECLARATION"
    ASK_USER_TO_CONFIRM = "ASK_USER_TO_CONFIRM"
    MECHANICAL_ONLY = "MECHANICAL_ONLY"
    DISABLED = "DISABLED"


class RelianceDecision(StrEnum):
    YES = "yes"
    NO = "no"
    UNCERTAIN = "uncertain"


class EvidenceClass(StrEnum):
    AGENT_DECLARED = "AGENT_DECLARED"
    HUMAN_CONFIRMED = "HUMAN_CONFIRMED"
    MECHANICAL = "MECHANICAL"


@dataclass(frozen=True)
class WorkSession:
    session_id: str
    project_ref: str | None
    workspace_ref: str | None
    initiating_actor_ref: str
    agent_ref: str | None
    agent_version: str | None
    host_environment: str
    capture_policy_ref: str | None
    reconciliation_policy: ReconciliationPolicy
    visibility: str
    started_at: str
    status: WorkSessionStatus
    ended_at: str | None


@dataclass(frozen=True)
class WorkInputCandidate:
    candidate_id: str
    session_id: str
    locator: str
    title: str | None
    observed_at: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class AgentObservation:
    observation_id: str
    session_id: str
    candidate_id: str
    usage_state: UsageState
    source_revision_id: str | None
    evidence_class: EvidenceClass
    evidence_refs: tuple[str, ...]
    generation_or_step_id: str | None
    created_at: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class ReconciliationRun:
    reconciliation_id: str
    session_id: str
    policy: ReconciliationPolicy
    actor_ref: str
    created_at: str


@dataclass(frozen=True)
class ReconciliationDecisionRecord:
    decision_id: str
    reconciliation_id: str
    session_id: str
    candidate_id: str
    source_revision_id: str | None
    used: RelianceDecision
    relation_type: str | None
    used_for: str | None
    project_object_ref: str | None
    evidence_class: EvidenceClass
    evidence_refs: tuple[str, ...]
    notes: str | None
    created_at: str


@dataclass(frozen=True)
class RelianceExport:
    decision_id: str
    reliance_id: str
    created_at: str


@dataclass(frozen=True)
class WatchCandidate:
    watch_candidate_id: str
    decision_id: str
    reliance_id: str
    session_id: str
    source_revision_id: str
    locator: str
    created_at: str


_ALLOWED_TRANSITIONS = {
    WorkSessionStatus.OPEN: {WorkSessionStatus.COMPLETED_UNRECONCILED, WorkSessionStatus.ABORTED},
    WorkSessionStatus.COMPLETED_UNRECONCILED: {WorkSessionStatus.RECONCILED},
    WorkSessionStatus.RECONCILED: {WorkSessionStatus.PUSHED},
    WorkSessionStatus.PUSHED: {WorkSessionStatus.ATTESTED},
    WorkSessionStatus.ATTESTED: set(),
    WorkSessionStatus.ABORTED: set(),
}
_USAGE_ORDER = {
    UsageState.DISCOVERED: 0,
    UsageState.FETCHED: 1,
    UsageState.INSPECTED: 1,
    UsageState.EXPOSED_TO_MODEL: 2,
    UsageState.EXPLICITLY_REFERENCED: 3,
    UsageState.RELIED_ON_BY_PROJECT_OBJECT: 4,
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sid(kind: str, value: Any) -> str:
    return f"tst:{kind}:{hashlib.sha256(_json(value).encode()).hexdigest()}"


def _time(value: str | None, field: str) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    raw = str(value).strip()
    if not raw:
        raise ValueError(f"{field} must not be empty")
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _required(value: str | None, field: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _refs(values: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(x).strip() for x in values or () if str(x).strip()))


def _payload(row: sqlite3.Row) -> dict[str, Any]:
    return json.loads(str(row["payload_json"]))
