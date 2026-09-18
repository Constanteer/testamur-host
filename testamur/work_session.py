from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Mapping, Sequence

from ._work_session_reconciliation import _ReconciliationMixin
from ._work_session_types import (
    WORK_SESSION_VERSION,
    AgentObservation, EvidenceClass, ReconciliationDecisionRecord, ReconciliationPolicy,
    ReconciliationRun, RelianceDecision, RelianceExport, UsageState, WatchCandidate,
    WorkInputCandidate, WorkSession, WorkSessionStatus,
    _ALLOWED_TRANSITIONS, _json, _payload, _refs, _required, _sid, _time,
)

class WorkSessionStore(_ReconciliationMixin):
    """Append-only operational provenance; capture never implies durable reliance."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.database_path, timeout=15)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        if self.database_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "WorkSessionStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _init_schema(self) -> None:
        self._conn.executescript("""
        CREATE TABLE IF NOT EXISTS testamur_work_sessions(
          session_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS testamur_work_session_lifecycle(
          event_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES testamur_work_sessions(session_id),
          from_status TEXT, to_status TEXT NOT NULL, evidence_ref TEXT, created_at TEXT NOT NULL,
          payload_json TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS testamur_work_session_lifecycle_idx
          ON testamur_work_session_lifecycle(session_id, created_at);
        CREATE TABLE IF NOT EXISTS testamur_work_input_candidates(
          candidate_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES testamur_work_sessions(session_id),
          locator TEXT NOT NULL, payload_json TEXT NOT NULL, UNIQUE(session_id, locator));
        CREATE TABLE IF NOT EXISTS testamur_work_session_observations(
          observation_id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL REFERENCES testamur_work_sessions(session_id),
          candidate_id TEXT NOT NULL REFERENCES testamur_work_input_candidates(candidate_id),
          usage_state TEXT NOT NULL, source_revision_id TEXT, created_at TEXT NOT NULL,
          payload_json TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS testamur_work_session_observations_idx
          ON testamur_work_session_observations(session_id, candidate_id, created_at);
        CREATE TABLE IF NOT EXISTS testamur_work_session_reconciliations(
          reconciliation_id TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE REFERENCES testamur_work_sessions(session_id),
          policy TEXT NOT NULL, actor_ref TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS testamur_work_session_decisions(
          decision_id TEXT PRIMARY KEY,
          reconciliation_id TEXT NOT NULL REFERENCES testamur_work_session_reconciliations(reconciliation_id),
          session_id TEXT NOT NULL REFERENCES testamur_work_sessions(session_id),
          candidate_id TEXT NOT NULL REFERENCES testamur_work_input_candidates(candidate_id),
          source_revision_key TEXT NOT NULL, used TEXT NOT NULL, payload_json TEXT NOT NULL,
          UNIQUE(reconciliation_id, candidate_id, source_revision_key));
        CREATE TABLE IF NOT EXISTS testamur_work_session_reliance_exports(
          decision_id TEXT PRIMARY KEY REFERENCES testamur_work_session_decisions(decision_id),
          reliance_id TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS testamur_work_session_watch_candidates(
          watch_candidate_id TEXT PRIMARY KEY,
          decision_id TEXT NOT NULL UNIQUE REFERENCES testamur_work_session_decisions(decision_id),
          reliance_id TEXT NOT NULL, session_id TEXT NOT NULL, source_revision_id TEXT NOT NULL,
          locator TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS testamur_work_session_external_watch_exports(
          watch_candidate_id TEXT PRIMARY KEY REFERENCES testamur_work_session_watch_candidates(watch_candidate_id),
          watch_ref TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
        """)
        tables = (
            "testamur_work_sessions", "testamur_work_session_lifecycle",
            "testamur_work_input_candidates", "testamur_work_session_observations",
            "testamur_work_session_reconciliations", "testamur_work_session_decisions",
            "testamur_work_session_reliance_exports", "testamur_work_session_watch_candidates",
            "testamur_work_session_external_watch_exports",
        )
        for table in tables:
            name = table.replace("testamur_", "")
            self._conn.executescript(f"""
            CREATE TRIGGER IF NOT EXISTS {name}_no_update BEFORE UPDATE ON {table}
            BEGIN SELECT RAISE(ABORT,'Testamur WorkSession records are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS {name}_no_delete BEFORE DELETE ON {table}
            BEGIN SELECT RAISE(ABORT,'Testamur WorkSession records are immutable'); END;
            """)
        self._conn.commit()

    # ---- lifecycle -----------------------------------------------------
    def start_session(
        self, *, initiating_actor_ref: str, host_environment: str,
        project_ref: str | None = None, workspace_ref: str | None = None,
        agent_ref: str | None = None, agent_version: str | None = None,
        capture_policy_ref: str | None = None,
        reconciliation_policy: ReconciliationPolicy | str = ReconciliationPolicy.TRUST_AGENT_DECLARATION,
        visibility: str = "LOCAL", started_at: str | None = None,
        session_id: str | None = None,
    ) -> WorkSession:
        actor = _required(initiating_actor_ref, "initiating_actor_ref")
        host = _required(host_environment, "host_environment")
        policy = ReconciliationPolicy(str(getattr(reconciliation_policy, "value", reconciliation_policy)))
        at = _time(started_at, "started_at")
        data = dict(
            version=WORK_SESSION_VERSION, project_ref=project_ref, workspace_ref=workspace_ref,
            initiating_actor_ref=actor, agent_ref=agent_ref, agent_version=agent_version,
            host_environment=host, capture_policy_ref=capture_policy_ref,
            reconciliation_policy=policy.value, visibility=str(visibility), started_at=at,
        )
        sid = session_id or _sid("work-session", data)
        if not sid.startswith("tst:work-session:") or sid == "tst:work-session:":
            raise ValueError("session_id must use tst:work-session:<id> format")
        data["session_id"] = sid
        encoded = _json(data)
        self._conn.execute("INSERT OR IGNORE INTO testamur_work_sessions VALUES(?,?)", (sid, encoded))
        row = self._conn.execute("SELECT payload_json FROM testamur_work_sessions WHERE session_id=?", (sid,)).fetchone()
        if row is None or str(row["payload_json"]) != encoded:
            raise ValueError("session_id already exists with different immutable payload")
        if self._conn.execute("SELECT 1 FROM testamur_work_session_lifecycle WHERE session_id=?", (sid,)).fetchone() is None:
            self._append_lifecycle(sid, None, WorkSessionStatus.OPEN, created_at=at)
        self._conn.commit()
        return self.get_session(sid)

    def _append_lifecycle(
        self, session_id: str, old: WorkSessionStatus | None, new: WorkSessionStatus, *,
        evidence_ref: str | None = None, metadata: Mapping[str, Any] | None = None,
        created_at: str | None = None,
    ) -> None:
        at = _time(created_at, "created_at")
        data = dict(session_id=session_id, from_status=None if old is None else old.value,
                    to_status=new.value, evidence_ref=evidence_ref, metadata=dict(metadata or {}), created_at=at)
        eid = _sid("work-session-event", data)
        self._conn.execute(
            "INSERT OR IGNORE INTO testamur_work_session_lifecycle VALUES(?,?,?,?,?,?,?)",
            (eid, session_id, data["from_status"], new.value, evidence_ref, at, _json(data)),
        )

    def _current_status(self, session_id: str) -> WorkSessionStatus:
        row = self._conn.execute(
            "SELECT to_status FROM testamur_work_session_lifecycle WHERE session_id=? ORDER BY rowid DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            raise KeyError(session_id)
        return WorkSessionStatus(str(row["to_status"]))

    def lifecycle_event(self, session_id: str, to_status: WorkSessionStatus | str) -> dict[str, Any] | None:
        target = WorkSessionStatus(str(getattr(to_status, "value", to_status)))
        row = self._conn.execute(
            "SELECT payload_json FROM testamur_work_session_lifecycle WHERE session_id=? AND to_status=? ORDER BY rowid DESC LIMIT 1",
            (session_id, target.value),
        ).fetchone()
        return None if row is None else _payload(row)

    def get_session(self, session_id: str) -> WorkSession:
        row = self._conn.execute("SELECT * FROM testamur_work_sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(session_id)
        data = _payload(row)
        ended = self._conn.execute(
            "SELECT created_at FROM testamur_work_session_lifecycle WHERE session_id=? AND to_status IN (?,?) ORDER BY rowid LIMIT 1",
            (session_id, WorkSessionStatus.COMPLETED_UNRECONCILED.value, WorkSessionStatus.ABORTED.value),
        ).fetchone()
        return WorkSession(
            session_id=session_id, project_ref=data["project_ref"], workspace_ref=data["workspace_ref"],
            initiating_actor_ref=data["initiating_actor_ref"], agent_ref=data["agent_ref"],
            agent_version=data["agent_version"], host_environment=data["host_environment"],
            capture_policy_ref=data["capture_policy_ref"],
            reconciliation_policy=ReconciliationPolicy(data["reconciliation_policy"]),
            visibility=data["visibility"], started_at=data["started_at"],
            status=self._current_status(session_id), ended_at=None if ended is None else str(ended["created_at"]),
        )

    def transition(self, session_id: str, to_status: WorkSessionStatus | str, *,
                   evidence_ref: str | None = None, metadata: Mapping[str, Any] | None = None,
                   created_at: str | None = None) -> WorkSession:
        current = self._current_status(session_id)
        target = WorkSessionStatus(str(getattr(to_status, "value", to_status)))
        normalized_metadata = dict(metadata or {})
        normalized_evidence = None if evidence_ref is None else str(evidence_ref)
        if target == current:
            existing = self.lifecycle_event(session_id, target)
            if existing is None:
                raise RuntimeError("current WorkSession status has no durable lifecycle event")
            if existing.get("evidence_ref") != normalized_evidence or dict(existing.get("metadata") or {}) != normalized_metadata:
                raise ValueError(f"conflicting replay for WorkSession status {target.value}")
            if created_at is not None and existing.get("created_at") != _time(created_at, "created_at"):
                raise ValueError(f"conflicting replay time for WorkSession status {target.value}")
            return self.get_session(session_id)
        if target not in _ALLOWED_TRANSITIONS[current]:
            raise ValueError(f"invalid WorkSession transition {current.value} -> {target.value}")
        self._append_lifecycle(session_id, current, target, evidence_ref=normalized_evidence,
                               metadata=normalized_metadata, created_at=created_at)
        self._conn.commit()
        return self.get_session(session_id)

    def complete(self, session_id: str, *, created_at: str | None = None) -> WorkSession:
        return self.transition(session_id, WorkSessionStatus.COMPLETED_UNRECONCILED, created_at=created_at)

    def abort(self, session_id: str, *, reason: str | None = None, created_at: str | None = None) -> WorkSession:
        return self.transition(session_id, WorkSessionStatus.ABORTED,
                               metadata={} if reason is None else {"reason": reason}, created_at=created_at)

    def mark_pushed(self, session_id: str, *, push_ref: str, created_at: str | None = None) -> WorkSession:
        return self.transition(session_id, WorkSessionStatus.PUSHED,
                               evidence_ref=_required(push_ref, "push_ref"), created_at=created_at)

    def mark_attested(self, session_id: str, *, attestation_ref: str, created_at: str | None = None) -> WorkSession:
        return self.transition(session_id, WorkSessionStatus.ATTESTED,
                               evidence_ref=_required(attestation_ref, "attestation_ref"), created_at=created_at)

    # ---- capture -------------------------------------------------------
    def create_candidate(self, session_id: str, *, locator: str, title: str | None = None,
                         metadata: Mapping[str, Any] | None = None,
                         observed_at: str | None = None) -> WorkInputCandidate:
        if self._current_status(session_id) is not WorkSessionStatus.OPEN:
            raise ValueError("capture is only allowed while WorkSession is OPEN")
        loc, at, meta = _required(locator, "locator"), _time(observed_at, "observed_at"), dict(metadata or {})
        cid = _sid("work-input", {"session_id": session_id, "locator": loc})
        data = dict(candidate_id=cid, session_id=session_id, locator=loc, title=title, observed_at=at, metadata=meta)
        encoded = _json(data)
        self._conn.execute("INSERT OR IGNORE INTO testamur_work_input_candidates VALUES(?,?,?,?)",
                           (cid, session_id, loc, encoded))
        row = self._conn.execute(
            "SELECT session_id,locator FROM testamur_work_input_candidates WHERE candidate_id=?",
            (cid,),
        ).fetchone()
        if row is None or str(row["session_id"]) != session_id or str(row["locator"]) != loc:
            raise ValueError("candidate identity already exists with different session/locator")
        self._insert_observation(
            session_id, cid, UsageState.DISCOVERED, source_revision_id=None,
            evidence_class=EvidenceClass.MECHANICAL, evidence_refs=(cid,),
            generation_or_step_id=None,
            metadata={"locator": loc, "title": title, **meta}, created_at=at,
        )
        self._conn.commit()
        return self.get_candidate(cid)

    def get_candidate(self, candidate_id: str) -> WorkInputCandidate:
        row = self._conn.execute("SELECT payload_json FROM testamur_work_input_candidates WHERE candidate_id=?",
                                 (candidate_id,)).fetchone()
        if row is None:
            raise KeyError(candidate_id)
        d = _payload(row)
        return WorkInputCandidate(d["candidate_id"], d["session_id"], d["locator"], d["title"], d["observed_at"], d["metadata"])

    def list_candidates(self, session_id: str) -> list[WorkInputCandidate]:
        rows = self._conn.execute("SELECT candidate_id FROM testamur_work_input_candidates WHERE session_id=? ORDER BY rowid",
                                  (session_id,)).fetchall()
        return [self.get_candidate(str(r["candidate_id"])) for r in rows]

    def _insert_observation(self, session_id: str, candidate_id: str, state: UsageState, *,
                            source_revision_id: str | None, evidence_class: EvidenceClass,
                            evidence_refs: Sequence[str], generation_or_step_id: str | None,
                            metadata: Mapping[str, Any] | None, created_at: str | None) -> AgentObservation:
        at, refs = _time(created_at, "created_at"), _refs(evidence_refs)
        data = dict(session_id=session_id, candidate_id=candidate_id, usage_state=state.value,
                    source_revision_id=source_revision_id, evidence_class=evidence_class.value,
                    evidence_refs=refs, generation_or_step_id=generation_or_step_id,
                    metadata=dict(metadata or {}), created_at=at)
        oid = _sid("agent-observation", data)
        data["observation_id"] = oid
        self._conn.execute(
            "INSERT OR IGNORE INTO testamur_work_session_observations VALUES(?,?,?,?,?,?,?)",
            (oid, session_id, candidate_id, state.value, source_revision_id, at, _json(data)),
        )
        return self._observation(self._conn.execute(
            "SELECT payload_json FROM testamur_work_session_observations WHERE observation_id=?", (oid,)
        ).fetchone())

    @staticmethod
    def _observation(row: sqlite3.Row) -> AgentObservation:
        d = _payload(row)
        return AgentObservation(
            d["observation_id"], d["session_id"], d["candidate_id"], UsageState(d["usage_state"]),
            d["source_revision_id"], EvidenceClass(d["evidence_class"]), tuple(d["evidence_refs"]),
            d["generation_or_step_id"], d["created_at"], d["metadata"],
        )

    def observations(self, session_id: str, *, candidate_id: str | None = None) -> list[AgentObservation]:
        sql = "SELECT payload_json FROM testamur_work_session_observations WHERE session_id=?"
        args: tuple[Any, ...] = (session_id,)
        if candidate_id is not None:
            sql += " AND candidate_id=?"
            args += (candidate_id,)
        rows = self._conn.execute(sql + " ORDER BY rowid", args).fetchall()
        return [self._observation(r) for r in rows]

    def record_observation(self, session_id: str, candidate_id: str, usage_state: UsageState | str, *,
                           source_revision_id: str | None = None,
                           evidence_class: EvidenceClass | str = EvidenceClass.MECHANICAL,
                           evidence_refs: Sequence[str] = (), generation_or_step_id: str | None = None,
                           metadata: Mapping[str, Any] | None = None,
                           created_at: str | None = None) -> AgentObservation:
        if self._current_status(session_id) is not WorkSessionStatus.OPEN:
            raise ValueError("capture is only allowed while WorkSession is OPEN")
        if self.get_candidate(candidate_id).session_id != session_id:
            raise ValueError("candidate belongs to a different WorkSession")
        state = UsageState(str(getattr(usage_state, "value", usage_state)))
        if state in {UsageState.DISCOVERED, UsageState.RELIED_ON_BY_PROJECT_OBJECT}:
            raise ValueError(f"{state.value} is not accepted through record_observation")
        revision, refs = _required(source_revision_id, "source_revision_id"), _refs(evidence_refs)
        if not revision.startswith("tst:revision:") or revision == "tst:revision:":
            raise ValueError("source_revision_id must use canonical tst:revision:<id> format")
        if not refs:
            raise ValueError(f"{state.value} requires exact evidence_refs")
        prior = [o for o in self.observations(session_id, candidate_id=candidate_id) if o.source_revision_id == revision]
        if state is UsageState.EXPOSED_TO_MODEL and not any(o.usage_state in {UsageState.FETCHED, UsageState.INSPECTED} for o in prior):
            raise ValueError("EXPOSED_TO_MODEL requires a prior FETCHED/INSPECTED observation of the exact revision")
        if state is UsageState.EXPLICITLY_REFERENCED and not any(
            o.usage_state in {UsageState.FETCHED, UsageState.INSPECTED, UsageState.EXPOSED_TO_MODEL} for o in prior
        ):
            raise ValueError("EXPLICITLY_REFERENCED requires prior observed use of the exact revision")
        result = self._insert_observation(
            session_id, candidate_id, state, source_revision_id=revision,
            evidence_class=EvidenceClass(str(getattr(evidence_class, "value", evidence_class))),
            evidence_refs=refs, generation_or_step_id=generation_or_step_id,
            metadata=metadata, created_at=created_at,
        )
        self._conn.commit()
        return result
