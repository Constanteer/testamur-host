from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .agent_protocol import (
    RelianceCommitRequest,
    RelianceSink,
    WatchRegistrationRequest,
    WatchSink,
)
from .work_session import (
    EvidenceClass,
    ReconciliationPolicy,
    RelianceDecision,
    ReconciliationDecisionRecord,
    UsageState,
    WatchCandidate,
    WorkSessionStatus,
    WorkSessionStore,
)


_RECONCILIATION_READABLE_STATUSES = {
    WorkSessionStatus.COMPLETED_UNRECONCILED,
    WorkSessionStatus.RECONCILED,
    WorkSessionStatus.PUSHED,
    WorkSessionStatus.ATTESTED,
}


@dataclass(frozen=True)
class ReconciliationInput:
    candidate_id: str
    locator: str
    title: str | None
    source_revision_id: str | None
    usage_states: tuple[UsageState, ...]
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ReconciliationDeclaration:
    candidate_id: str
    source_revision_id: str | None
    used: RelianceDecision
    evidence_class: EvidenceClass
    relation_type: str | None = None
    used_for: str | None = None
    project_object_ref: str | None = None
    exact_region_refs: tuple[str, ...] = ()
    notes: str | None = None


@dataclass(frozen=True)
class ReconciliationReport:
    session_id: str
    reconciliation_id: str
    status: WorkSessionStatus
    decisions: tuple[ReconciliationDecisionRecord, ...]
    watch_candidates: tuple[WatchCandidate, ...]
    pending_reliance_decision_ids: tuple[str, ...]
    pending_watch_candidate_ids: tuple[str, ...] = ()
    exports_complete: bool = False


def prepare_reconciliation(store: WorkSessionStore, session_id: str) -> tuple[ReconciliationInput, ...]:
    session = store.get_session(session_id)
    if session.status not in _RECONCILIATION_READABLE_STATUSES:
        raise ValueError("prepare_reconciliation requires a completed WorkSession")
    result: list[ReconciliationInput] = []
    for candidate_id, revision in store.observed_input_keys(session_id):
        candidate = store.get_candidate(candidate_id)
        result.append(
            ReconciliationInput(
                candidate_id=candidate_id,
                locator=candidate.locator,
                title=candidate.title,
                source_revision_id=revision,
                usage_states=store.usage_states_for_input(session_id, candidate_id, revision),
                evidence_refs=store.evidence_for_input(session_id, candidate_id, revision),
            )
        )
    return tuple(result)


def _validate_policy(policy: ReconciliationPolicy, declaration: ReconciliationDeclaration) -> None:
    if policy is ReconciliationPolicy.ASK_USER_TO_CONFIRM:
        if declaration.evidence_class is not EvidenceClass.HUMAN_CONFIRMED:
            raise ValueError("ASK_USER_TO_CONFIRM requires HUMAN_CONFIRMED decisions")
    elif policy is ReconciliationPolicy.MECHANICAL_ONLY:
        if declaration.evidence_class is not EvidenceClass.MECHANICAL:
            raise ValueError("MECHANICAL_ONLY requires MECHANICAL decisions")
    elif policy is ReconciliationPolicy.TRUST_AGENT_DECLARATION:
        if declaration.evidence_class not in {EvidenceClass.AGENT_DECLARED, EvidenceClass.HUMAN_CONFIRMED, EvidenceClass.MECHANICAL}:
            raise ValueError("unsupported evidence class")
    else:
        raise ValueError("DISABLED policy intentionally does not reconcile")


def _key(candidate_id: str, source_revision_id: str | None) -> tuple[str, str | None]:
    return candidate_id, source_revision_id


def _reliance_idempotency_key(decision_id: str) -> str:
    """Stable cross-worker key for one immutable WorkSession reliance decision."""
    return f"work-session-decision:{decision_id}"


def _pending_watch_candidate_ids(store: WorkSessionStore, session_id: str) -> tuple[str, ...]:
    return tuple(
        candidate.watch_candidate_id
        for candidate in store.watch_candidates(session_id)
        if store.external_watch_ref(candidate.watch_candidate_id) is None
    )


def reconcile_session(
    store: WorkSessionStore,
    session_id: str,
    declarations: Sequence[ReconciliationDeclaration],
    *,
    actor_ref: str,
    reliance_sink: RelianceSink | None,
    watch_sink: WatchSink | None = None,
) -> ReconciliationReport:
    session = store.get_session(session_id)
    if session.status not in {WorkSessionStatus.COMPLETED_UNRECONCILED, WorkSessionStatus.RECONCILED}:
        raise ValueError("reconciliation requires a completed WorkSession")
    if session.reconciliation_policy is ReconciliationPolicy.DISABLED:
        raise ValueError("this WorkSession explicitly disabled reconciliation")

    inputs = prepare_reconciliation(store, session_id)
    expected = {_key(item.candidate_id, item.source_revision_id): item for item in inputs}
    declared: dict[tuple[str, str | None], ReconciliationDeclaration] = {}
    for declaration in declarations:
        key = _key(declaration.candidate_id, declaration.source_revision_id)
        if key in declared:
            raise ValueError(f"duplicate reconciliation declaration for {key}")
        declared[key] = declaration
    if set(declared) != set(expected):
        missing = sorted(set(expected) - set(declared))
        extra = sorted(set(declared) - set(expected))
        raise ValueError(f"reconciliation must cover every observed input; missing={missing!r} extra={extra!r}")

    prepared: list[tuple[ReconciliationDeclaration, tuple[str, ...]]] = []
    for key, item in expected.items():
        declaration = declared[key]
        _validate_policy(session.reconciliation_policy, declaration)
        evidence_refs = tuple(dict.fromkeys((*declaration.exact_region_refs, *item.evidence_refs)))
        if declaration.used is RelianceDecision.YES:
            if item.source_revision_id is None:
                raise ValueError("discovered-only locator cannot be promoted to durable reliance")
            if not declaration.relation_type or not declaration.relation_type.strip():
                raise ValueError("used=yes requires relation_type")
            if not declaration.used_for or not declaration.used_for.strip():
                raise ValueError("used=yes requires used_for")
            if not declaration.project_object_ref or not declaration.project_object_ref.strip():
                raise ValueError("used=yes requires project_object_ref")
            if not evidence_refs:
                raise ValueError("used=yes requires exact evidence_refs")
        elif declaration.relation_type or declaration.used_for or declaration.project_object_ref:
            raise ValueError("used=no/uncertain cannot create a durable project relation")
        prepared.append((declaration, evidence_refs))

    run = store.start_reconciliation(session_id, policy=session.reconciliation_policy, actor_ref=actor_ref)
    records: list[ReconciliationDecisionRecord] = []
    for declaration, evidence_refs in prepared:
        records.append(store.record_decision(
            run.reconciliation_id,
            candidate_id=declaration.candidate_id,
            source_revision_id=declaration.source_revision_id,
            used=declaration.used,
            relation_type=declaration.relation_type,
            used_for=declaration.used_for,
            project_object_ref=declaration.project_object_ref,
            evidence_class=declaration.evidence_class,
            evidence_refs=evidence_refs,
            notes=declaration.notes,
        ))

    pending: list[str] = []
    for decision in records:
        if decision.used is not RelianceDecision.YES:
            continue
        export = store.reliance_export_for(decision.decision_id)
        if export is None:
            if reliance_sink is None:
                pending.append(decision.decision_id)
                continue
            assert decision.source_revision_id is not None
            result = reliance_sink.commit_reliance(RelianceCommitRequest(
                work_session_id=session_id,
                reconciliation_id=run.reconciliation_id,
                decision_id=decision.decision_id,
                project_ref=session.project_ref,
                project_object_ref=decision.project_object_ref or "",
                relation_type=decision.relation_type or "",
                used_for=decision.used_for or "",
                source_revision_id=decision.source_revision_id,
                evidence_refs=decision.evidence_refs,
                evidence_class=decision.evidence_class.value,
                reconciliation_policy=run.policy.value,
                actor_ref=run.actor_ref,
                idempotency_key=_reliance_idempotency_key(decision.decision_id),
            ))
            export = store.record_reliance_export(decision.decision_id, reliance_id=result.reliance_id)
        watch_candidate = store.ensure_watch_candidate(decision.decision_id)
        if watch_sink is not None and store.external_watch_ref(watch_candidate.watch_candidate_id) is None:
            watch_result = watch_sink.register_watch(WatchRegistrationRequest(
                watch_candidate_id=watch_candidate.watch_candidate_id,
                work_session_id=session_id,
                reliance_id=watch_candidate.reliance_id,
                source_revision_id=watch_candidate.source_revision_id,
                locator=watch_candidate.locator,
            ))
            store.record_external_watch_export(watch_candidate.watch_candidate_id, watch_ref=watch_result.watch_ref)

    if not pending:
        store.mark_reconciled_if_complete(session_id)

    final = store.get_session(session_id)
    watch_candidates = tuple(store.watch_candidates(session_id))
    pending_watch = _pending_watch_candidate_ids(store, session_id)
    return ReconciliationReport(
        session_id=session_id,
        reconciliation_id=run.reconciliation_id,
        status=final.status,
        decisions=tuple(store.decisions(session_id)),
        watch_candidates=watch_candidates,
        pending_reliance_decision_ids=tuple(pending),
        pending_watch_candidate_ids=pending_watch,
        exports_complete=(
            final.status is WorkSessionStatus.RECONCILED
            and not pending
            and not pending_watch
        ),
    )


@dataclass(frozen=True)
class ArchiveLaneEntry:
    candidate_id: str
    source_revision_id: str | None
    locator: str
    title: str | None
    decision: RelianceDecision
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class OperationalLaneProjection:
    watch: tuple[WatchCandidate, ...]
    archive: tuple[ArchiveLaneEntry, ...]
    pending_reliance_decision_ids: tuple[str, ...]
    unreconciled_inputs: tuple[ReconciliationInput, ...]


def project_operational_lanes(store: WorkSessionStore, session_id: str) -> OperationalLaneProjection:
    """Project consequences without upgrading epistemic meaning."""
    inputs = prepare_reconciliation(store, session_id)
    decisions = {_key(d.candidate_id, d.source_revision_id): d for d in store.decisions(session_id)}
    archive: list[ArchiveLaneEntry] = []
    pending: list[str] = []
    unreconciled: list[ReconciliationInput] = []
    for item in inputs:
        decision = decisions.get(_key(item.candidate_id, item.source_revision_id))
        if decision is None:
            unreconciled.append(item)
            continue
        if decision.used is RelianceDecision.YES:
            if store.reliance_export_for(decision.decision_id) is None:
                pending.append(decision.decision_id)
            continue
        archive.append(ArchiveLaneEntry(
            candidate_id=item.candidate_id,
            source_revision_id=item.source_revision_id,
            locator=item.locator,
            title=item.title,
            decision=decision.used,
            evidence_refs=decision.evidence_refs,
        ))
    return OperationalLaneProjection(
        watch=tuple(store.watch_candidates(session_id)),
        archive=tuple(archive),
        pending_reliance_decision_ids=tuple(pending),
        unreconciled_inputs=tuple(unreconciled),
    )
