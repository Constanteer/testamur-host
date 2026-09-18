from __future__ import annotations

import sqlite3
from typing import Sequence

from ._work_session_types import (
    ReconciliationDecisionRecord, ReconciliationPolicy, ReconciliationRun, RelianceDecision,
    EvidenceClass, RelianceExport, UsageState, WatchCandidate, WorkSession, WorkSessionStatus,
    _USAGE_ORDER, _json, _payload, _refs, _required, _sid, _time,
)


class _ReconciliationMixin:
    # ---- reconciliation ------------------------------------------------
    def start_reconciliation(self, session_id: str, *, policy: ReconciliationPolicy | str,
                             actor_ref: str, created_at: str | None = None) -> ReconciliationRun:
        session = self.get_session(session_id)
        if session.status not in {WorkSessionStatus.COMPLETED_UNRECONCILED, WorkSessionStatus.RECONCILED}:
            raise ValueError("reconciliation requires a completed WorkSession")
        p = ReconciliationPolicy(str(getattr(policy, "value", policy)))
        if p is ReconciliationPolicy.DISABLED:
            raise ValueError("DISABLED policy intentionally leaves the session unreconciled")
        if p is not session.reconciliation_policy:
            raise ValueError("reconciliation policy must match the policy pinned on WorkSession")
        actor, at = _required(actor_ref, "actor_ref"), _time(created_at, "created_at")
        rid = _sid("reconciliation", {"session_id": session_id, "policy": p.value})
        self._conn.execute("INSERT OR IGNORE INTO testamur_work_session_reconciliations VALUES(?,?,?,?,?)",
                           (rid, session_id, p.value, actor, at))
        row = self._conn.execute("SELECT * FROM testamur_work_session_reconciliations WHERE session_id=?", (session_id,)).fetchone()
        if row is None or str(row["policy"]) != p.value or str(row["actor_ref"]) != actor:
            raise ValueError("session already has a different immutable reconciliation run")
        self._conn.commit()
        return ReconciliationRun(str(row["reconciliation_id"]), session_id, p, actor, str(row["created_at"]))

    def get_reconciliation(self, session_id: str) -> ReconciliationRun | None:
        row = self._conn.execute("SELECT * FROM testamur_work_session_reconciliations WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            return None
        return ReconciliationRun(str(row["reconciliation_id"]), session_id, ReconciliationPolicy(str(row["policy"])),
                                 str(row["actor_ref"]), str(row["created_at"]))

    @staticmethod
    def _validate_decision_evidence_class(policy: ReconciliationPolicy, evidence_class: EvidenceClass) -> None:
        if policy is ReconciliationPolicy.ASK_USER_TO_CONFIRM:
            if evidence_class is not EvidenceClass.HUMAN_CONFIRMED:
                raise ValueError("ASK_USER_TO_CONFIRM requires HUMAN_CONFIRMED decisions")
        elif policy is ReconciliationPolicy.MECHANICAL_ONLY:
            if evidence_class is not EvidenceClass.MECHANICAL:
                raise ValueError("MECHANICAL_ONLY requires MECHANICAL decisions")
        elif policy is ReconciliationPolicy.TRUST_AGENT_DECLARATION:
            if evidence_class not in {
                EvidenceClass.AGENT_DECLARED, EvidenceClass.HUMAN_CONFIRMED, EvidenceClass.MECHANICAL,
            }:
                raise ValueError("unsupported evidence class")
        else:
            raise ValueError("DISABLED policy intentionally does not reconcile")

    def record_decision(self, reconciliation_id: str, *, candidate_id: str,
                        source_revision_id: str | None, used: RelianceDecision | str,
                        evidence_class: EvidenceClass | str, relation_type: str | None = None,
                        used_for: str | None = None, project_object_ref: str | None = None,
                        evidence_refs: Sequence[str] = (), notes: str | None = None,
                        created_at: str | None = None) -> ReconciliationDecisionRecord:
        run = self._conn.execute("SELECT * FROM testamur_work_session_reconciliations WHERE reconciliation_id=?",
                                 (reconciliation_id,)).fetchone()
        if run is None:
            raise KeyError(reconciliation_id)
        session_id = str(run["session_id"])
        session = self.get_session(session_id)
        if self.get_candidate(candidate_id).session_id != session_id:
            raise ValueError("candidate belongs to a different WorkSession")
        decision = RelianceDecision(str(getattr(used, "value", used)))
        eclass = EvidenceClass(str(getattr(evidence_class, "value", evidence_class)))
        revision = None if source_revision_id is None else _required(source_revision_id, "source_revision_id")
        run_policy = ReconciliationPolicy(str(run["policy"]))
        if run_policy is not session.reconciliation_policy:
            raise ValueError("reconciliation run policy no longer matches WorkSession policy")
        self._validate_decision_evidence_class(run_policy, eclass)
        if (candidate_id, revision) not in set(self.observed_input_keys(session_id)):
            raise ValueError("reconciliation decision must bind an exact observed WorkSession input")
        refs = _refs(evidence_refs)
        if decision is RelianceDecision.YES:
            relation_type = _required(relation_type, "relation_type")
            used_for = _required(used_for, "used_for")
            project_object_ref = _required(project_object_ref, "project_object_ref")
            if revision is None or not refs:
                raise ValueError("used=yes requires exact source revision and evidence refs")
        elif relation_type or used_for or project_object_ref:
            raise ValueError("used=no/uncertain cannot create a durable project relation")
        data = dict(reconciliation_id=reconciliation_id, session_id=session_id, candidate_id=candidate_id,
                    source_revision_id=revision, used=decision.value, relation_type=relation_type,
                    used_for=used_for, project_object_ref=project_object_ref,
                    evidence_class=eclass.value, evidence_refs=refs, notes=notes)
        did = _sid("reconciliation-decision", data)
        data["decision_id"], data["created_at"] = did, _time(created_at, "created_at")
        key = revision or ""
        self._conn.execute("INSERT OR IGNORE INTO testamur_work_session_decisions VALUES(?,?,?,?,?,?,?)",
                           (did, reconciliation_id, session_id, candidate_id, key, decision.value, _json(data)))
        row = self._conn.execute(
            "SELECT payload_json FROM testamur_work_session_decisions WHERE reconciliation_id=? AND candidate_id=? AND source_revision_key=?",
            (reconciliation_id, candidate_id, key),
        ).fetchone()
        if row is None:
            raise RuntimeError("decision insert failed")
        stored = self._decision(row)
        if stored.decision_id != did:
            raise ValueError("reconciliation input already has a different immutable decision")
        self._conn.commit()
        return stored

    @staticmethod
    def _decision(row: sqlite3.Row) -> ReconciliationDecisionRecord:
        d = _payload(row)
        return ReconciliationDecisionRecord(
            d["decision_id"], d["reconciliation_id"], d["session_id"], d["candidate_id"],
            d["source_revision_id"], RelianceDecision(d["used"]), d["relation_type"], d["used_for"],
            d["project_object_ref"], EvidenceClass(d["evidence_class"]), tuple(d["evidence_refs"]),
            d["notes"], d["created_at"],
        )

    def decisions(self, session_id: str) -> list[ReconciliationDecisionRecord]:
        rows = self._conn.execute("SELECT payload_json FROM testamur_work_session_decisions WHERE session_id=? ORDER BY rowid",
                                  (session_id,)).fetchall()
        return [self._decision(r) for r in rows]

    # ---- reliance/watch exports ---------------------------------------
    def reliance_export_for(self, decision_id: str) -> RelianceExport | None:
        row = self._conn.execute("SELECT * FROM testamur_work_session_reliance_exports WHERE decision_id=?", (decision_id,)).fetchone()
        return None if row is None else RelianceExport(decision_id, str(row["reliance_id"]), str(row["created_at"]))

    def record_reliance_export(self, decision_id: str, *, reliance_id: str,
                               created_at: str | None = None) -> RelianceExport:
        row = self._conn.execute("SELECT payload_json FROM testamur_work_session_decisions WHERE decision_id=?", (decision_id,)).fetchone()
        if row is None:
            raise KeyError(decision_id)
        decision = self._decision(row)
        if decision.used is not RelianceDecision.YES or decision.source_revision_id is None:
            raise ValueError("only used=yes decisions can be exported to durable reliance")
        rid, at = _required(reliance_id, "reliance_id"), _time(created_at, "created_at")
        if not rid.startswith("tst:reliance:") or rid == "tst:reliance:":
            raise ValueError("reliance_id must use canonical tst:reliance:<id> format")
        self._conn.execute("INSERT OR IGNORE INTO testamur_work_session_reliance_exports VALUES(?,?,?)", (decision_id, rid, at))
        stored = self.reliance_export_for(decision_id)
        if stored is None or stored.reliance_id != rid:
            raise ValueError("decision already exported to a different immutable reliance id")
        existing = [o for o in self.observations(decision.session_id, candidate_id=decision.candidate_id)
                    if o.source_revision_id == decision.source_revision_id and o.usage_state is UsageState.RELIED_ON_BY_PROJECT_OBJECT]
        if not existing:
            self._insert_observation(
                decision.session_id, decision.candidate_id, UsageState.RELIED_ON_BY_PROJECT_OBJECT,
                source_revision_id=decision.source_revision_id, evidence_class=decision.evidence_class,
                evidence_refs=(decision.decision_id, rid, *decision.evidence_refs), generation_or_step_id=None,
                metadata={"project_object_ref": decision.project_object_ref,
                          "relation_type": decision.relation_type, "used_for": decision.used_for}, created_at=at,
            )
        self._conn.commit()
        return stored

    def ensure_watch_candidate(self, decision_id: str, *, created_at: str | None = None) -> WatchCandidate:
        row = self._conn.execute("SELECT payload_json FROM testamur_work_session_decisions WHERE decision_id=?", (decision_id,)).fetchone()
        if row is None:
            raise KeyError(decision_id)
        decision, export = self._decision(row), self.reliance_export_for(decision_id)
        if export is None or decision.source_revision_id is None:
            raise ValueError("watch candidate requires successful durable reliance export")
        candidate = self.get_candidate(decision.candidate_id)
        wid = _sid("watch-candidate", {"decision_id": decision_id, "reliance_id": export.reliance_id,
                                        "source_revision_id": decision.source_revision_id})
        at = _time(created_at, "created_at")
        self._conn.execute("INSERT OR IGNORE INTO testamur_work_session_watch_candidates VALUES(?,?,?,?,?,?,?)",
                           (wid, decision_id, export.reliance_id, decision.session_id,
                            decision.source_revision_id, candidate.locator, at))
        row = self._conn.execute("SELECT * FROM testamur_work_session_watch_candidates WHERE decision_id=?", (decision_id,)).fetchone()
        if row is None or str(row["watch_candidate_id"]) != wid:
            raise ValueError("decision already mapped to a different immutable watch candidate")
        self._conn.commit()
        return WatchCandidate(*(str(row[k]) for k in (
            "watch_candidate_id", "decision_id", "reliance_id", "session_id", "source_revision_id", "locator", "created_at"
        )))

    def watch_candidates(self, session_id: str) -> list[WatchCandidate]:
        rows = self._conn.execute("SELECT * FROM testamur_work_session_watch_candidates WHERE session_id=? ORDER BY rowid",
                                  (session_id,)).fetchall()
        return [WatchCandidate(*(str(r[k]) for k in (
            "watch_candidate_id", "decision_id", "reliance_id", "session_id", "source_revision_id", "locator", "created_at"
        ))) for r in rows]

    def record_external_watch_export(self, watch_candidate_id: str, *, watch_ref: str,
                                     created_at: str | None = None) -> str:
        if not self._conn.execute("SELECT 1 FROM testamur_work_session_watch_candidates WHERE watch_candidate_id=?",
                                  (watch_candidate_id,)).fetchone():
            raise KeyError(watch_candidate_id)
        ref, at = _required(watch_ref, "watch_ref"), _time(created_at, "created_at")
        if not ref.startswith("tst:watch:") or ref == "tst:watch:":
            raise ValueError("watch_ref must use canonical tst:watch:<id> format")
        self._conn.execute("INSERT OR IGNORE INTO testamur_work_session_external_watch_exports VALUES(?,?,?)",
                           (watch_candidate_id, ref, at))
        stored = self.external_watch_ref(watch_candidate_id)
        if stored != ref:
            raise ValueError("watch candidate already exported to a different immutable watch ref")
        self._conn.commit()
        return ref

    def external_watch_ref(self, watch_candidate_id: str) -> str | None:
        row = self._conn.execute("SELECT watch_ref FROM testamur_work_session_external_watch_exports WHERE watch_candidate_id=?",
                                 (watch_candidate_id,)).fetchone()
        return None if row is None else str(row["watch_ref"])

    def mark_reconciled_if_complete(self, session_id: str) -> WorkSession:
        session = self.get_session(session_id)
        if session.status is WorkSessionStatus.RECONCILED:
            return session
        if session.status is not WorkSessionStatus.COMPLETED_UNRECONCILED:
            raise ValueError("only COMPLETED_UNRECONCILED can become RECONCILED")
        if self.get_reconciliation(session_id) is None:
            raise ValueError("reconciliation requires an explicit reconciliation run")
        decisions = self.decisions(session_id)
        expected_inputs = set(self.observed_input_keys(session_id))
        decided_inputs = {(d.candidate_id, d.source_revision_id) for d in decisions}
        if decided_inputs != expected_inputs:
            missing = sorted(expected_inputs - decided_inputs)
            extra = sorted(decided_inputs - expected_inputs)
            raise ValueError(
                "reconciliation decisions must cover every observed input before completion; "
                f"missing={missing!r} extra={extra!r}"
            )
        relied = [d for d in decisions if d.used is RelianceDecision.YES]
        if any(self.reliance_export_for(d.decision_id) is None for d in relied):
            raise ValueError("used=yes decision is not yet exported to durable reliance")
        watch_decision_ids = {candidate.decision_id for candidate in self.watch_candidates(session_id)}
        missing_watch_candidates = [d.decision_id for d in relied if d.decision_id not in watch_decision_ids]
        if missing_watch_candidates:
            raise ValueError(
                "used=yes decision is missing its durable watch candidate; "
                f"decision_ids={missing_watch_candidates!r}"
            )
        return self.transition(session_id, WorkSessionStatus.RECONCILED)

    # ---- reconciliation projections ----------------------------------
    def observed_input_keys(self, session_id: str) -> list[tuple[str, str | None]]:
        keys: list[tuple[str, str | None]] = []
        for candidate in self.list_candidates(session_id):
            revisions: list[str] = []
            for obs in self.observations(session_id, candidate_id=candidate.candidate_id):
                if obs.source_revision_id and obs.source_revision_id not in revisions:
                    revisions.append(obs.source_revision_id)
            keys.extend((candidate.candidate_id, r) for r in revisions) if revisions else keys.append((candidate.candidate_id, None))
        return keys

    def evidence_for_input(self, session_id: str, candidate_id: str,
                           source_revision_id: str | None) -> tuple[str, ...]:
        refs: list[str] = []
        for obs in self.observations(session_id, candidate_id=candidate_id):
            if obs.usage_state is UsageState.RELIED_ON_BY_PROJECT_OBJECT:
                continue  # reconciliation output must not become its next immutable input
            if source_revision_id is None:
                if obs.source_revision_id is not None:
                    continue
            elif obs.source_revision_id != source_revision_id:
                continue
            refs.extend(ref for ref in obs.evidence_refs if ref not in refs)
        return tuple(refs)

    def usage_states_for_input(self, session_id: str, candidate_id: str,
                               source_revision_id: str | None) -> tuple[UsageState, ...]:
        states: list[UsageState] = []
        for obs in self.observations(session_id, candidate_id=candidate_id):
            if source_revision_id is None:
                if obs.source_revision_id is not None and obs.usage_state is not UsageState.DISCOVERED:
                    continue
            elif obs.source_revision_id not in {None, source_revision_id}:
                continue
            if obs.usage_state not in states:
                states.append(obs.usage_state)
        return tuple(sorted(states, key=_USAGE_ORDER.__getitem__))