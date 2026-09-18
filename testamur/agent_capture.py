from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .agent_protocol import AgentEvent, AgentEventType
from .work_session import (
    AgentObservation,
    EvidenceClass,
    UsageState,
    WorkInputCandidate,
    WorkSession,
    WorkSessionStatus,
    WorkSessionStore,
)


_CAPTURE_EVENT_TYPES = frozenset({
    AgentEventType.SOURCE_DISCOVERED,
    AgentEventType.SOURCE_FETCHED,
    AgentEventType.SOURCE_INSPECTED,
    AgentEventType.CONTEXT_EXPOSED,
    AgentEventType.SOURCE_EXPLICITLY_REFERENCED,
})
_COMPLETION_CLOSED_STATUSES = frozenset({
    WorkSessionStatus.COMPLETED_UNRECONCILED,
    WorkSessionStatus.RECONCILED,
    WorkSessionStatus.PUSHED,
    WorkSessionStatus.ATTESTED,
})


def _normalized_time(value: str) -> str:
    raw = str(value).strip()
    if not raw:
        raise ValueError("agent event occurred_at is required")
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("agent event occurred_at must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _normalized_refs(values: Sequence[Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _canonical_revision_id(value: str) -> str:
    revision_id = str(value).strip()
    if not revision_id.startswith("tst:revision:") or revision_id == "tst:revision:":
        raise ValueError("source_revision_id must use canonical tst:revision:<id> format")
    return revision_id


class AgentCapture:
    """Host-neutral capture facade over WorkSessionStore.

    Methods record observable events only. In particular, expose_to_model records
    context exposure and never claims that a model cognitively relied on content.

    Host event delivery may be at-least-once. Once a WorkSession has closed,
    this facade therefore accepts only an exact replay of an already-persisted
    capture/lifecycle event. A delayed event that was never durably recorded is
    not backfilled after reconciliation boundaries have become possible.
    """

    def __init__(self, store: WorkSessionStore) -> None:
        self.store = store

    @staticmethod
    def _validate_discovery_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
        result = dict(metadata or {})
        reserved = sorted({"locator", "title"} & set(result))
        if reserved:
            raise ValueError(
                "SOURCE_DISCOVERED metadata cannot override reserved fields: "
                + ", ".join(reserved)
            )
        return result

    def discover(
        self,
        session_id: str,
        *,
        locator: str,
        title: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        observed_at: str | None = None,
    ) -> WorkInputCandidate:
        clean_metadata = self._validate_discovery_metadata(metadata)
        return self.store.create_candidate(
            session_id,
            locator=locator,
            title=title,
            metadata=clean_metadata,
            observed_at=observed_at,
        )

    def fetched(
        self,
        session_id: str,
        candidate_id: str,
        *,
        source_revision_id: str,
        retrieval_receipt_ref: str,
        created_at: str | None = None,
    ) -> AgentObservation:
        revision_id = _canonical_revision_id(source_revision_id)
        return self.store.record_observation(
            session_id,
            candidate_id,
            UsageState.FETCHED,
            source_revision_id=revision_id,
            evidence_class=EvidenceClass.MECHANICAL,
            evidence_refs=(retrieval_receipt_ref,),
            created_at=created_at,
        )

    def inspected(
        self,
        session_id: str,
        candidate_id: str,
        *,
        source_revision_id: str,
        inspection_ref: str,
        created_at: str | None = None,
    ) -> AgentObservation:
        revision_id = _canonical_revision_id(source_revision_id)
        return self.store.record_observation(
            session_id,
            candidate_id,
            UsageState.INSPECTED,
            source_revision_id=revision_id,
            evidence_class=EvidenceClass.MECHANICAL,
            evidence_refs=(inspection_ref,),
            created_at=created_at,
        )

    def expose_to_model(
        self,
        session_id: str,
        candidate_id: str,
        *,
        source_revision_id: str,
        generation_or_step_id: str,
        evidence_refs: Sequence[str],
        retrieval_method: str | None = None,
        context_role: str | None = None,
        created_at: str | None = None,
    ) -> AgentObservation:
        revision_id = _canonical_revision_id(source_revision_id)
        return self.store.record_observation(
            session_id,
            candidate_id,
            UsageState.EXPOSED_TO_MODEL,
            source_revision_id=revision_id,
            evidence_class=EvidenceClass.MECHANICAL,
            evidence_refs=evidence_refs,
            generation_or_step_id=generation_or_step_id,
            metadata={
                "retrieval_method": retrieval_method,
                "context_role": context_role,
                "cognition_inferred": False,
            },
            created_at=created_at,
        )

    def explicitly_referenced(
        self,
        session_id: str,
        candidate_id: str,
        *,
        source_revision_id: str,
        generation_or_step_id: str,
        evidence_ref: str,
        created_at: str | None = None,
    ) -> AgentObservation:
        revision_id = _canonical_revision_id(source_revision_id)
        return self.store.record_observation(
            session_id,
            candidate_id,
            UsageState.EXPLICITLY_REFERENCED,
            source_revision_id=revision_id,
            evidence_class=EvidenceClass.MECHANICAL,
            evidence_refs=(evidence_ref,),
            generation_or_step_id=generation_or_step_id,
            created_at=created_at,
        )

    @staticmethod
    def _payload_text(payload: Mapping[str, Any], key: str, *, required: bool = True) -> str | None:
        value = payload.get(key)
        if value is None:
            if required:
                raise ValueError(f"agent event payload requires {key}")
            return None
        text = str(value).strip()
        if not text and required:
            raise ValueError(f"agent event payload requires {key}")
        return text or None

    @staticmethod
    def _event_refs(payload: Mapping[str, Any]) -> tuple[str, ...]:
        values = payload.get("evidence_refs")
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise ValueError("CONTEXT_EXPOSED evidence_refs must be a sequence")
        refs = _normalized_refs(values)
        if not refs:
            raise ValueError("CONTEXT_EXPOSED evidence_refs must not be empty")
        return refs

    def _find_exact_observation(
        self,
        *,
        session_id: str,
        candidate_id: str,
        state: UsageState,
        occurred_at: str,
        source_revision_id: str | None,
        evidence_refs: tuple[str, ...],
        generation_or_step_id: str | None,
        metadata: Mapping[str, Any],
    ) -> AgentObservation | None:
        at = _normalized_time(occurred_at)
        for observation in self.store.observations(session_id, candidate_id=candidate_id):
            if (
                observation.usage_state is state
                and observation.created_at == at
                and observation.source_revision_id == source_revision_id
                and observation.evidence_class is EvidenceClass.MECHANICAL
                and observation.evidence_refs == evidence_refs
                and observation.generation_or_step_id == generation_or_step_id
                and dict(observation.metadata) == dict(metadata)
            ):
                return observation
        return None

    def _find_exact_capture_replay(
        self,
        event: AgentEvent,
    ) -> WorkInputCandidate | AgentObservation | None:
        payload = event.payload
        if event.event_type is AgentEventType.SOURCE_DISCOVERED:
            metadata = payload.get("metadata")
            if metadata is not None and not isinstance(metadata, Mapping):
                raise ValueError("SOURCE_DISCOVERED metadata must be a mapping")
            clean_metadata = self._validate_discovery_metadata(
                None if metadata is None else dict(metadata)
            )
            locator = self._payload_text(payload, "locator") or ""
            title = self._payload_text(payload, "title", required=False)
            candidate = next(
                (item for item in self.store.list_candidates(event.session_id) if item.locator == locator),
                None,
            )
            if candidate is None:
                return None
            observation = self._find_exact_observation(
                session_id=event.session_id,
                candidate_id=candidate.candidate_id,
                state=UsageState.DISCOVERED,
                occurred_at=event.occurred_at,
                source_revision_id=None,
                evidence_refs=(candidate.candidate_id,),
                generation_or_step_id=None,
                metadata={"locator": locator, "title": title, **clean_metadata},
            )
            return candidate if observation is not None else None

        candidate_id = self._payload_text(payload, "candidate_id") or ""
        source_revision_id = _canonical_revision_id(
            self._payload_text(payload, "source_revision_id") or ""
        )
        if event.event_type is AgentEventType.SOURCE_FETCHED:
            return self._find_exact_observation(
                session_id=event.session_id,
                candidate_id=candidate_id,
                state=UsageState.FETCHED,
                occurred_at=event.occurred_at,
                source_revision_id=source_revision_id,
                evidence_refs=(self._payload_text(payload, "retrieval_receipt_ref") or "",),
                generation_or_step_id=None,
                metadata={},
            )
        if event.event_type is AgentEventType.SOURCE_INSPECTED:
            return self._find_exact_observation(
                session_id=event.session_id,
                candidate_id=candidate_id,
                state=UsageState.INSPECTED,
                occurred_at=event.occurred_at,
                source_revision_id=source_revision_id,
                evidence_refs=(self._payload_text(payload, "inspection_ref") or "",),
                generation_or_step_id=None,
                metadata={},
            )
        if event.event_type is AgentEventType.CONTEXT_EXPOSED:
            return self._find_exact_observation(
                session_id=event.session_id,
                candidate_id=candidate_id,
                state=UsageState.EXPOSED_TO_MODEL,
                occurred_at=event.occurred_at,
                source_revision_id=source_revision_id,
                evidence_refs=self._event_refs(payload),
                generation_or_step_id=self._payload_text(payload, "generation_or_step_id") or "",
                metadata={
                    "retrieval_method": self._payload_text(payload, "retrieval_method", required=False),
                    "context_role": self._payload_text(payload, "context_role", required=False),
                    "cognition_inferred": False,
                },
            )
        if event.event_type is AgentEventType.SOURCE_EXPLICITLY_REFERENCED:
            return self._find_exact_observation(
                session_id=event.session_id,
                candidate_id=candidate_id,
                state=UsageState.EXPLICITLY_REFERENCED,
                occurred_at=event.occurred_at,
                source_revision_id=source_revision_id,
                evidence_refs=(self._payload_text(payload, "evidence_ref") or "",),
                generation_or_step_id=self._payload_text(payload, "generation_or_step_id") or "",
                metadata={},
            )
        return None

    def _closed_event_replay(self, event: AgentEvent, session: WorkSession) -> object:
        if event.event_type in _CAPTURE_EVENT_TYPES:
            existing = self._find_exact_capture_replay(event)
            if existing is not None:
                return existing
            raise ValueError(
                "closed WorkSession accepts only an exact replay of a previously recorded capture event"
            )

        at = _normalized_time(event.occurred_at)
        if event.event_type is AgentEventType.SESSION_COMPLETED:
            if session.status in _COMPLETION_CLOSED_STATUSES and session.ended_at == at:
                return session
            raise ValueError("conflicting SESSION_COMPLETED replay for closed WorkSession")
        if event.event_type is AgentEventType.SESSION_ABORTED:
            reason = self._payload_text(event.payload, "reason", required=False)
            lifecycle = self.store.lifecycle_event(event.session_id, WorkSessionStatus.ABORTED)
            expected_metadata = {} if reason is None else {"reason": reason}
            if (
                session.status is WorkSessionStatus.ABORTED
                and session.ended_at == at
                and lifecycle is not None
                and dict(lifecycle.get("metadata") or {}) == expected_metadata
            ):
                return session
            raise ValueError("conflicting SESSION_ABORTED replay for closed WorkSession")
        raise ValueError(f"unsupported agent event type: {event.event_type.value}")

    def apply_event(self, event: AgentEvent) -> WorkInputCandidate | AgentObservation | object:
        """Apply one validated host-neutral agent protocol event.

        Event payloads remain deliberately small; adapters should resolve exact
        source revisions/evidence before sending higher-fidelity usage events.
        Once the WorkSession is closed, only an exact replay of a previously
        persisted event is accepted; new late capture fails closed.
        """

        session = self.store.get_session(event.session_id)
        if session.status is not WorkSessionStatus.OPEN:
            return self._closed_event_replay(event, session)

        payload = event.payload
        if event.event_type is AgentEventType.SOURCE_DISCOVERED:
            metadata = payload.get("metadata")
            if metadata is not None and not isinstance(metadata, Mapping):
                raise ValueError("SOURCE_DISCOVERED metadata must be a mapping")
            return self.discover(
                event.session_id,
                locator=self._payload_text(payload, "locator") or "",
                title=self._payload_text(payload, "title", required=False),
                metadata=None if metadata is None else dict(metadata),
                observed_at=event.occurred_at,
            )
        if event.event_type is AgentEventType.SOURCE_FETCHED:
            return self.fetched(
                event.session_id,
                self._payload_text(payload, "candidate_id") or "",
                source_revision_id=self._payload_text(payload, "source_revision_id") or "",
                retrieval_receipt_ref=self._payload_text(payload, "retrieval_receipt_ref") or "",
                created_at=event.occurred_at,
            )
        if event.event_type is AgentEventType.SOURCE_INSPECTED:
            return self.inspected(
                event.session_id,
                self._payload_text(payload, "candidate_id") or "",
                source_revision_id=self._payload_text(payload, "source_revision_id") or "",
                inspection_ref=self._payload_text(payload, "inspection_ref") or "",
                created_at=event.occurred_at,
            )
        if event.event_type is AgentEventType.CONTEXT_EXPOSED:
            refs = self._event_refs(payload)
            return self.expose_to_model(
                event.session_id,
                self._payload_text(payload, "candidate_id") or "",
                source_revision_id=self._payload_text(payload, "source_revision_id") or "",
                generation_or_step_id=self._payload_text(payload, "generation_or_step_id") or "",
                evidence_refs=refs,
                retrieval_method=self._payload_text(payload, "retrieval_method", required=False),
                context_role=self._payload_text(payload, "context_role", required=False),
                created_at=event.occurred_at,
            )
        if event.event_type is AgentEventType.SOURCE_EXPLICITLY_REFERENCED:
            return self.explicitly_referenced(
                event.session_id,
                self._payload_text(payload, "candidate_id") or "",
                source_revision_id=self._payload_text(payload, "source_revision_id") or "",
                generation_or_step_id=self._payload_text(payload, "generation_or_step_id") or "",
                evidence_ref=self._payload_text(payload, "evidence_ref") or "",
                created_at=event.occurred_at,
            )
        if event.event_type is AgentEventType.SESSION_COMPLETED:
            return self.store.complete(event.session_id, created_at=event.occurred_at)
        if event.event_type is AgentEventType.SESSION_ABORTED:
            return self.store.abort(
                event.session_id,
                reason=self._payload_text(payload, "reason", required=False),
                created_at=event.occurred_at,
            )
        raise ValueError(f"unsupported agent event type: {event.event_type.value}")
