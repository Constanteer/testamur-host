from __future__ import annotations

from dataclasses import dataclass

from .agent_protocol import RelianceCommitRequest, RelianceSink, WatchRegistrationRequest, WatchSink
from .reconciliation import (
    ReconciliationReport,
    _pending_watch_candidate_ids,
    _reliance_idempotency_key,
)
from .work_session import (
    EvidenceClass,
    RelianceDecision,
    ReconciliationPolicy,
    WorkSessionStatus,
    WorkSessionStore,
)


_RECONCILIATION_CLOSED_STATUSES = {
    WorkSessionStatus.RECONCILED,
    WorkSessionStatus.PUSHED,
    WorkSessionStatus.ATTESTED,
}


@dataclass(frozen=True, slots=True)
class ReconciliationExportStatus:
    """Side-effect recovery state derived only from durable W4 records.

    ``complete`` describes reconciliation closure: every observed input has an
    explicit durable decision and every ``used=yes`` decision has a durable
    reliance export. ``exports_complete`` is stricter and additionally requires
    every generated WatchCandidate to have a durable external Watch
    acknowledgement. Keeping the two signals separate prevents a scheduler from
    confusing epistemic reconciliation with operational monitoring closure.

    This projection is intentionally operational rather than epistemic. It
    tells an integrator which already-authorized exports still need replay; it
    never creates a decision or upgrades an observation into reliance.
    """

    session_id: str
    status: WorkSessionStatus
    reconciliation_id: str | None
    pending_reliance_decision_ids: tuple[str, ...]
    pending_watch_candidate_ids: tuple[str, ...]
    complete: bool
    exports_complete: bool


def reconciliation_export_status(
    store: WorkSessionStore,
    session_id: str,
) -> ReconciliationExportStatus:
    """Return exact replay obligations for a completed WorkSession.

    A YES decision is pending reliance until its immutable local reliance export
    exists. A watch is pending only after reliance succeeded and a deterministic
    watch candidate exists without an external watch acknowledgement. This
    ordering prevents a product scheduler from treating passive observations as
    watch work.

    ``complete`` means reconciliation itself is complete. ``exports_complete``
    additionally means there is no pending operational Watch export. Later
    lifecycle states such as PUSHED and ATTESTED preserve reconciliation closure;
    publication must not erase or block recovery of a pending Watch export.
    """
    session = store.get_session(session_id)
    run = store.get_reconciliation(session_id)
    decisions = tuple(store.decisions(session_id))

    pending_reliance = tuple(
        decision.decision_id
        for decision in decisions
        if decision.used is RelianceDecision.YES
        and store.reliance_export_for(decision.decision_id) is None
    )
    pending_watch = _pending_watch_candidate_ids(store, session_id)
    expected = set(store.observed_input_keys(session_id))
    decided = {(decision.candidate_id, decision.source_revision_id) for decision in decisions}
    complete = (
        session.status in _RECONCILIATION_CLOSED_STATUSES
        and run is not None
        and decided == expected
        and not pending_reliance
    )
    exports_complete = complete and not pending_watch
    return ReconciliationExportStatus(
        session_id=session_id,
        status=session.status,
        reconciliation_id=None if run is None else run.reconciliation_id,
        pending_reliance_decision_ids=pending_reliance,
        pending_watch_candidate_ids=pending_watch,
        complete=complete,
        exports_complete=exports_complete,
    )


def _validate_persisted_decision_policy(policy: ReconciliationPolicy, evidence_class: EvidenceClass) -> None:
    """Fail closed if low-level persisted decisions violate the pinned policy.

    Normal reconciliation validates this before persistence. Recovery is a
    separate trust boundary, though: callers can use WorkSessionStore directly,
    so replay must not assume every persisted row came through reconcile_session.
    """
    if policy is ReconciliationPolicy.ASK_USER_TO_CONFIRM:
        if evidence_class is not EvidenceClass.HUMAN_CONFIRMED:
            raise ValueError("persisted ASK_USER_TO_CONFIRM decision is not HUMAN_CONFIRMED")
    elif policy is ReconciliationPolicy.MECHANICAL_ONLY:
        if evidence_class is not EvidenceClass.MECHANICAL:
            raise ValueError("persisted MECHANICAL_ONLY decision is not MECHANICAL")
    elif policy is ReconciliationPolicy.TRUST_AGENT_DECLARATION:
        if evidence_class not in {
            EvidenceClass.AGENT_DECLARED,
            EvidenceClass.HUMAN_CONFIRMED,
            EvidenceClass.MECHANICAL,
        }:
            raise ValueError("persisted reconciliation decision has unsupported evidence class")
    else:
        raise ValueError("DISABLED policy cannot export reconciliation decisions")


def resume_reconciliation_exports(
    store: WorkSessionStore,
    session_id: str,
    *,
    reliance_sink: RelianceSink | None,
    watch_sink: WatchSink | None = None,
) -> ReconciliationReport:
    """Resume side-effect export from already-persisted reconciliation decisions.

    This recovery path deliberately accepts no new declarations. It can only
    replay explicit immutable decisions that were durably recorded by a prior
    reconciliation attempt, so a crash between decision persistence, reliance
    export, and watch export cannot force an agent to restate epistemic intent.

    Persisted rows are still revalidated against the WorkSession's pinned
    reconciliation policy before any external side effect. Persistence alone is
    not authority to upgrade an observation into durable reliance.

    ``reliance_sink`` may be absent when every YES decision already has its
    durable reliance export. This deliberately separates the two crash
    boundaries: recovering a pending canonical Watch acknowledgement must not
    require the reliance service to be online again. If any YES decision still
    needs its first durable reliance commit, recovery fails closed instead.

    Recovery remains valid after RECONCILED -> PUSHED -> ATTESTED because those
    later workflow states do not revoke the immutable reconciliation decisions
    or their operational export obligations.
    """
    session = store.get_session(session_id)
    if session.status not in {
        WorkSessionStatus.COMPLETED_UNRECONCILED,
        *_RECONCILIATION_CLOSED_STATUSES,
    }:
        raise ValueError("reconciliation export recovery requires a completed WorkSession")
    run = store.get_reconciliation(session_id)
    if run is None:
        raise ValueError("reconciliation export recovery requires a persisted reconciliation run")
    if run.policy is not session.reconciliation_policy:
        raise ValueError("persisted reconciliation policy differs from WorkSession policy")

    decisions = tuple(store.decisions(session_id))
    expected = set(store.observed_input_keys(session_id))
    decided = {(decision.candidate_id, decision.source_revision_id) for decision in decisions}
    if decided != expected:
        missing = sorted(expected - decided)
        extra = sorted(decided - expected)
        raise ValueError(
            "cannot export an incomplete reconciliation; "
            f"missing={missing!r} extra={extra!r}"
        )

    # Validate the complete durable decision set before making the first
    # external side effect. A malformed low-level row must not allow earlier
    # valid rows to be partially exported before recovery fails.
    for decision in decisions:
        _validate_persisted_decision_policy(run.policy, decision.evidence_class)
        if decision.used is RelianceDecision.YES and decision.source_revision_id is None:
            raise RuntimeError("persisted used=yes decision lost exact source revision")

    for decision in decisions:
        if decision.used is not RelianceDecision.YES:
            continue
        assert decision.source_revision_id is not None

        export = store.reliance_export_for(decision.decision_id)
        if export is None:
            if reliance_sink is None:
                raise ValueError(
                    "reconciliation recovery still requires durable reliance export; "
                    "reliance_sink is unavailable"
                )
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
            export = store.record_reliance_export(
                decision.decision_id,
                reliance_id=result.reliance_id,
            )

        watch_candidate = store.ensure_watch_candidate(decision.decision_id)
        if watch_sink is not None and store.external_watch_ref(watch_candidate.watch_candidate_id) is None:
            watch_result = watch_sink.register_watch(WatchRegistrationRequest(
                watch_candidate_id=watch_candidate.watch_candidate_id,
                work_session_id=session_id,
                reliance_id=export.reliance_id,
                source_revision_id=watch_candidate.source_revision_id,
                locator=watch_candidate.locator,
            ))
            store.record_external_watch_export(
                watch_candidate.watch_candidate_id,
                watch_ref=watch_result.watch_ref,
            )

    if session.status is WorkSessionStatus.COMPLETED_UNRECONCILED:
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
        pending_reliance_decision_ids=(),
        pending_watch_candidate_ids=pending_watch,
        exports_complete=(
            final.status in _RECONCILIATION_CLOSED_STATUSES
            and not pending_watch
        ),
    )
