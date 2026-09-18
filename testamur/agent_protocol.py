from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping, Protocol, runtime_checkable

AGENT_PROTOCOL_VERSION = "testamur-agent-protocol-v1"


class AgentEventType(StrEnum):
    SOURCE_DISCOVERED = "SOURCE_DISCOVERED"
    SOURCE_FETCHED = "SOURCE_FETCHED"
    SOURCE_INSPECTED = "SOURCE_INSPECTED"
    CONTEXT_EXPOSED = "CONTEXT_EXPOSED"
    SOURCE_EXPLICITLY_REFERENCED = "SOURCE_EXPLICITLY_REFERENCED"
    SESSION_COMPLETED = "SESSION_COMPLETED"
    SESSION_ABORTED = "SESSION_ABORTED"


@dataclass(frozen=True)
class AgentEvent:
    protocol_version: str
    event_type: AgentEventType
    session_id: str
    occurred_at: str
    payload: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AgentEvent":
        version = str(value.get("protocol_version", ""))
        if version != AGENT_PROTOCOL_VERSION:
            raise ValueError(f"unsupported agent protocol version: {version!r}")
        session_id = str(value.get("session_id", "")).strip()
        occurred_at = str(value.get("occurred_at", "")).strip()
        if not session_id or not occurred_at:
            raise ValueError("session_id and occurred_at are required")
        if not session_id.startswith("tst:work-session:") or session_id == "tst:work-session:":
            raise ValueError("session_id must use canonical tst:work-session:<id> format")
        try:
            parsed_time = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("occurred_at must be an ISO-8601 timestamp") from exc
        if parsed_time.tzinfo is None or parsed_time.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        payload = value.get("payload", {})
        if not isinstance(payload, Mapping):
            raise ValueError("payload must be a mapping")
        return cls(protocol_version=version, event_type=AgentEventType(str(value.get("event_type", ""))), session_id=session_id, occurred_at=occurred_at, payload=dict(payload))

    def as_dict(self) -> dict[str, Any]:
        return {"protocol_version": self.protocol_version, "event_type": self.event_type.value, "session_id": self.session_id, "occurred_at": self.occurred_at, "payload": dict(self.payload)}


@dataclass(frozen=True)
class RelianceCommitRequest:
    work_session_id: str
    reconciliation_id: str
    decision_id: str
    project_ref: str | None
    project_object_ref: str
    relation_type: str
    used_for: str
    source_revision_id: str
    evidence_refs: tuple[str, ...]
    evidence_class: str
    reconciliation_policy: str
    actor_ref: str
    idempotency_key: str | None = None


@dataclass(frozen=True)
class RelianceCommitResult:
    reliance_id: str


@dataclass(frozen=True)
class WatchRegistrationRequest:
    watch_candidate_id: str
    work_session_id: str
    reliance_id: str
    source_revision_id: str
    locator: str


@dataclass(frozen=True)
class WatchRegistrationResult:
    watch_ref: str


@runtime_checkable
class RelianceSink(Protocol):
    def commit_reliance(self, request: RelianceCommitRequest) -> RelianceCommitResult: ...


@runtime_checkable
class WatchSink(Protocol):
    def register_watch(self, request: WatchRegistrationRequest) -> WatchRegistrationResult: ...


class CanonicalSourceRevisionResolver:
    """Resolve an exact W4 revision through the canonical Testamur Source store."""
    def __init__(self, source_store: Any) -> None:
        self.source_store = source_store

    def __call__(self, source_revision_id: str) -> str:
        revision_id = str(source_revision_id).strip()
        if not revision_id:
            raise ValueError("source_revision_id must not be empty")
        if not revision_id.startswith("tst:revision:") or revision_id == "tst:revision:":
            raise ValueError("source_revision_id must use canonical tst:revision:<id> format")
        revision = self.source_store.get_revision(revision_id)
        if revision is None:
            raise KeyError(revision_id)
        resolved_revision_id = str(revision.get("revision_id") or "").strip()
        source_id = str(revision.get("source_id") or "").strip()
        if resolved_revision_id != revision_id:
            raise ValueError("canonical source store returned a different revision identity")
        if not source_id:
            raise ValueError("canonical source revision has no stable source identity")
        source = self.source_store.get_source(source_id)
        if source is None:
            raise ValueError("canonical source revision points to a missing source")
        resolved_source_id = str(source.get("source_id") or "").strip()
        if resolved_source_id != source_id:
            raise ValueError("canonical source store returned a different source identity")
        return source_id


class CanonicalWatchSink:
    """Promote only reconciled W4 watch candidates into canonical Watches.

    Canonical Watch identity and mutable Watch configuration are deliberately
    separate. W1 stores the W4 ownership marker on the initial WatchRevision,
    not the immutable Watch row. Recovery follows the immutable revision chain
    back to that initial marker so later user configuration changes do not make
    an already-created monitoring obligation unrecoverable.
    """
    def __init__(self, source_store: Any, watch_store: Any) -> None:
        self.source_store = source_store
        self.watch_store = watch_store
        self.resolve_source = CanonicalSourceRevisionResolver(source_store)

    @staticmethod
    def _required_request_text(value: Any, field: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"watch registration requires {field}")
        return text

    @staticmethod
    def _required_testamur_id(value: Any, field: str, prefix: str) -> str:
        text = CanonicalWatchSink._required_request_text(value, field)
        if not text.startswith(prefix) or text == prefix:
            raise ValueError(f"watch registration requires {field} to use {prefix}<id> format")
        return text

    @staticmethod
    def _watch_id(watch_candidate_id: str) -> str:
        candidate_id = str(watch_candidate_id).strip()
        if not candidate_id:
            raise ValueError("watch_candidate_id must not be empty")
        return "tst:watch:" + hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()

    def _registration_marker_matches(
        self,
        watch_id: str,
        latest_revision: Mapping[str, Any],
        registration_label: str,
    ) -> bool:
        """Verify ownership from immutable WatchRevision history.

        The current label is mutable configuration. The W4 marker is evidence of
        creation ownership only on the initial revision, so a legitimate later
        label change must not strand create-before-local-ack recovery.
        """
        revision: Mapping[str, Any] = latest_revision
        seen: set[str] = set()
        while True:
            revision_id = str(revision.get("revision_id") or "").strip()
            if not revision_id or revision_id in seen:
                raise ValueError("watch revision history is malformed or cyclic")
            seen.add(revision_id)
            if str(revision.get("watch_id") or "") != watch_id:
                raise ValueError("watch revision belongs to a different watch")
            parent_id = str(revision.get("parent_revision_id") or "").strip()
            ordinal = revision.get("ordinal")
            if not parent_id:
                if ordinal is not None and int(ordinal) != 1:
                    raise ValueError("watch revision history has a non-initial root")
                return str(revision.get("label") or "") == registration_label
            getter = getattr(self.watch_store, "get_watch_revision", None)
            if not callable(getter):
                # Compatibility for simple adapters: a matching current marker
                # is sufficient, but changed configuration requires history API.
                return str(revision.get("label") or "") == registration_label
            parent = getter(parent_id)
            if parent is None:
                raise ValueError("watch revision history is incomplete")
            revision = parent

    def _validate_existing(
        self,
        watch_id: str,
        existing: Mapping[str, Any],
        *,
        source_id: str,
        registration_label: str,
    ) -> None:
        if str(existing.get("watch_id") or "") != watch_id:
            raise ValueError("canonical watch store returned a different watch identity")
        if str(existing.get("source_id") or "") != source_id:
            raise ValueError("watch candidate is already bound to a different source")

        latest_revision = self.watch_store.latest_watch_revision(watch_id)
        if latest_revision is None:
            raise ValueError("existing watch has no configuration revision")
        if not self._registration_marker_matches(watch_id, latest_revision, registration_label):
            raise ValueError("watch candidate is already bound to a different reliance")

    def register_watch(self, request: WatchRegistrationRequest) -> WatchRegistrationResult:
        candidate_id = self._required_testamur_id(
            request.watch_candidate_id, "watch_candidate_id", "tst:watch-candidate:"
        )
        self._required_testamur_id(request.work_session_id, "work_session_id", "tst:work-session:")
        reliance_id = self._required_testamur_id(request.reliance_id, "reliance_id", "tst:reliance:")
        revision_id = self._required_testamur_id(
            request.source_revision_id, "source_revision_id", "tst:revision:"
        )
        self._required_request_text(request.locator, "locator")

        source_id = self.resolve_source(revision_id)
        watch_id = self._watch_id(candidate_id)
        registration_label = f"WorkSession reliance {reliance_id}"
        existing = self.watch_store.get_watch(watch_id)
        if existing is not None:
            self._validate_existing(
                watch_id,
                existing,
                source_id=source_id,
                registration_label=registration_label,
            )
            return WatchRegistrationResult(watch_ref=watch_id)

        try:
            created = self.watch_store.create_watch(
                self.source_store,
                source_id=source_id,
                watch_id=watch_id,
                label=registration_label,
            )
        except ValueError:
            existing = self.watch_store.get_watch(watch_id)
            if existing is None:
                raise
            self._validate_existing(
                watch_id,
                existing,
                source_id=source_id,
                registration_label=registration_label,
            )
            return WatchRegistrationResult(watch_ref=watch_id)

        watch = created.get("watch", {})
        watch_revision = created.get("revision", {})
        if str(watch.get("watch_id") or "") != watch_id:
            raise RuntimeError("canonical watch store returned a different watch identity")
        if str(watch.get("source_id") or "") != source_id:
            raise RuntimeError("canonical watch store lost WorkSession source binding")
        if str(watch_revision.get("watch_id") or "") != watch_id:
            raise RuntimeError("canonical watch store returned a revision for a different watch")
        if str(watch_revision.get("label") or "") != registration_label:
            raise RuntimeError("canonical watch revision lost WorkSession reliance binding")
        return WatchRegistrationResult(watch_ref=watch_id)