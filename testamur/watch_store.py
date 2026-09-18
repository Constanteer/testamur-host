from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from .runtime_protocol import canonical_hash, canonical_json
from .source_store import TestamurSourceStore


WATCH_PREFIX = "tst:watch:"
WATCH_REVISION_PREFIX = "tst:watch-revision:"
WATCH_EVALUATION_PREFIX = "tst:watch-eval:"
ALERT_PREFIX = "tst:alert:"

WATCH_EVENTS = frozenset({"changed", "unavailable", "recovered"})
WATCH_INTERVAL_SECONDS = frozenset({300, 900, 3600, 21600, 86400})
_UNSET = object()
_UNAVAILABLE_STATUSES = frozenset({"unavailable", "failed", "capture_failed", "error", "timeout"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _required(value: Any, *, field: str) -> str:
    raw = str(value).strip()
    if not raw:
        raise ValueError(f"{field} must not be empty")
    return raw


def _normalize_events(values: Iterable[str] | None) -> list[str]:
    selected = set(WATCH_EVENTS if values is None else values)
    unknown = selected - WATCH_EVENTS
    if unknown:
        raise ValueError(f"unsupported watch events: {', '.join(sorted(unknown))}")
    return sorted(selected)


def _normalize_interval(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("monitor interval must be an integer number of seconds")
    seconds = value
    if seconds not in WATCH_INTERVAL_SECONDS:
        allowed = ", ".join(str(item) for item in sorted(WATCH_INTERVAL_SECONDS))
        raise ValueError(f"monitor interval must be one of: {allowed} seconds, or manual")
    return seconds


class TestamurWatchStore:
    """Append-only operational Watch / evaluation / Alert store."""

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback(); raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS testamur_watches(watch_id TEXT PRIMARY KEY,source_id TEXT NOT NULL,record_json TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS testamur_watches_source_idx ON testamur_watches(source_id, created_at);
        CREATE TABLE IF NOT EXISTS testamur_watch_revisions(revision_id TEXT PRIMARY KEY,watch_id TEXT NOT NULL REFERENCES testamur_watches(watch_id),ordinal INTEGER NOT NULL,parent_revision_id TEXT REFERENCES testamur_watch_revisions(revision_id),record_json TEXT NOT NULL,recorded_at TEXT NOT NULL,UNIQUE(watch_id, ordinal));
        CREATE INDEX IF NOT EXISTS testamur_watch_revisions_watch_idx ON testamur_watch_revisions(watch_id, ordinal DESC);
        CREATE TABLE IF NOT EXISTS testamur_watch_evaluations(evaluation_id TEXT PRIMARY KEY,watch_id TEXT NOT NULL REFERENCES testamur_watches(watch_id),watch_revision_id TEXT NOT NULL REFERENCES testamur_watch_revisions(revision_id),snapshot_id TEXT NOT NULL,operational_state TEXT NOT NULL,record_json TEXT NOT NULL,recorded_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS testamur_watch_evaluations_watch_idx ON testamur_watch_evaluations(watch_id, recorded_at DESC, evaluation_id DESC);
        CREATE INDEX IF NOT EXISTS testamur_watch_evaluations_snapshot_idx ON testamur_watch_evaluations(snapshot_id, watch_id);
        CREATE TABLE IF NOT EXISTS testamur_alerts(alert_id TEXT PRIMARY KEY,watch_id TEXT NOT NULL REFERENCES testamur_watches(watch_id),evaluation_id TEXT NOT NULL REFERENCES testamur_watch_evaluations(evaluation_id),event_type TEXT NOT NULL,record_json TEXT NOT NULL,recorded_at TEXT NOT NULL,UNIQUE(watch_id, evaluation_id, event_type));
        CREATE INDEX IF NOT EXISTS testamur_alerts_watch_idx ON testamur_alerts(watch_id, recorded_at DESC, alert_id DESC);
        CREATE TRIGGER IF NOT EXISTS testamur_watches_no_update BEFORE UPDATE ON testamur_watches BEGIN SELECT RAISE(ABORT, 'Testamur watches are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS testamur_watches_no_delete BEFORE DELETE ON testamur_watches BEGIN SELECT RAISE(ABORT, 'Testamur watches are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS testamur_watch_revisions_no_update BEFORE UPDATE ON testamur_watch_revisions BEGIN SELECT RAISE(ABORT, 'Testamur watch revisions are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS testamur_watch_revisions_no_delete BEFORE DELETE ON testamur_watch_revisions BEGIN SELECT RAISE(ABORT, 'Testamur watch revisions are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS testamur_watch_evaluations_no_update BEFORE UPDATE ON testamur_watch_evaluations BEGIN SELECT RAISE(ABORT, 'Testamur watch evaluations are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS testamur_watch_evaluations_no_delete BEFORE DELETE ON testamur_watch_evaluations BEGIN SELECT RAISE(ABORT, 'Testamur watch evaluations are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS testamur_alerts_no_update BEFORE UPDATE ON testamur_alerts BEGIN SELECT RAISE(ABORT, 'Testamur alerts are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS testamur_alerts_no_delete BEFORE DELETE ON testamur_alerts BEGIN SELECT RAISE(ABORT, 'Testamur alerts are immutable'); END;
        """
        with self.connect() as conn: conn.executescript(schema)

    def _watch_revision_payload(self, *, watch_id: str, ordinal: int, parent_revision_id: str | None, alert_on: list[str], label: str | None, interval_seconds: int | None, recorded_at: str) -> dict[str, Any]:
        core={"watch_id":watch_id,"ordinal":int(ordinal),"parent_revision_id":parent_revision_id,"alert_on":list(alert_on),"label":label,"interval_seconds":interval_seconds,"recorded_at":recorded_at}
        return {"revision_id":WATCH_REVISION_PREFIX+canonical_hash(core),**core,"semantics":{"immutable_configuration_revision":True,"configuration_changes_append_revision":True,"automatic_refresh_opt_in":interval_seconds is not None,"truth_semantics_modified_by_watch":False}}

    def create_watch(self, sources: TestamurSourceStore, *, source_id: str, alert_on: Iterable[str] | None=None, label: str | None=None, interval_seconds: int | None=None, watch_id: str | None=None) -> dict[str, Any]:
        if sources.get_source(source_id) is None: raise KeyError(source_id)
        wid=WATCH_PREFIX+uuid.uuid4().hex if watch_id is None else _required(watch_id,field="watch_id")
        if not wid.startswith(WATCH_PREFIX) or wid==WATCH_PREFIX: raise ValueError(f"invalid Testamur watch id: {wid}")
        selected=_normalize_events(alert_on); interval=_normalize_interval(interval_seconds); created_at=_utc_now()
        watch={"watch_id":wid,"source_id":source_id,"created_at":created_at,"semantics":{"operational_object":True,"source_identity_modified":False,"truth_status_implied":False}}
        revision=self._watch_revision_payload(watch_id=wid,ordinal=1,parent_revision_id=None,alert_on=selected,label=None if label is None else str(label),interval_seconds=interval,recorded_at=created_at)
        with self.connect() as conn:
            if conn.execute("SELECT 1 FROM testamur_watches WHERE watch_id=?",(wid,)).fetchone() is not None: raise ValueError("watch id already exists")
            conn.execute("INSERT INTO testamur_watches(watch_id,source_id,record_json,created_at) VALUES(?,?,?,?)",(wid,source_id,canonical_json(watch),created_at))
            conn.execute("INSERT INTO testamur_watch_revisions(revision_id,watch_id,ordinal,parent_revision_id,record_json,recorded_at) VALUES(?,?,?,?,?,?)",(revision["revision_id"],wid,1,None,canonical_json(revision),created_at))
        return {"watch":watch,"revision":revision}

    def get_watch(self, watch_id: str) -> dict[str, Any] | None:
        with self.connect() as conn: row=conn.execute("SELECT record_json FROM testamur_watches WHERE watch_id=?",(str(watch_id),)).fetchone()
        return None if row is None else json.loads(str(row["record_json"]))

    def get_watch_revision(self, revision_id: str) -> dict[str, Any] | None:
        with self.connect() as conn: row=conn.execute("SELECT record_json FROM testamur_watch_revisions WHERE revision_id=?",(str(revision_id),)).fetchone()
        return None if row is None else json.loads(str(row["record_json"]))

    def latest_watch_revision(self, watch_id: str) -> dict[str, Any] | None:
        if self.get_watch(watch_id) is None: raise KeyError(watch_id)
        with self.connect() as conn: row=conn.execute("SELECT record_json FROM testamur_watch_revisions WHERE watch_id=? ORDER BY ordinal DESC LIMIT 1",(watch_id,)).fetchone()
        return None if row is None else json.loads(str(row["record_json"]))

    def append_watch_revision(self, watch_id: str, *, expected_parent_revision_id: str, alert_on: Iterable[str] | None=None, label: str | None=None, interval_seconds: int | None | object=_UNSET) -> dict[str, Any]:
        current=self.latest_watch_revision(watch_id)
        if current is None: raise RuntimeError("watch has no configuration revision")
        expected=_required(expected_parent_revision_id,field="expected_parent_revision_id")
        if current["revision_id"]!=expected: raise ValueError("watch configuration head changed")
        selected=list(current["alert_on"]) if alert_on is None else _normalize_events(alert_on); next_label=current.get("label") if label is None else str(label); next_interval=current.get("interval_seconds") if interval_seconds is _UNSET else _normalize_interval(interval_seconds); recorded_at=_utc_now()
        revision=self._watch_revision_payload(watch_id=watch_id,ordinal=int(current["ordinal"])+1,parent_revision_id=expected,alert_on=selected,label=next_label,interval_seconds=next_interval,recorded_at=recorded_at)
        with self.connect() as conn:
            live=conn.execute("SELECT revision_id FROM testamur_watch_revisions WHERE watch_id=? ORDER BY ordinal DESC LIMIT 1",(watch_id,)).fetchone()
            if live is None or str(live["revision_id"])!=expected: raise ValueError("watch configuration head changed during append")
            conn.execute("INSERT INTO testamur_watch_revisions(revision_id,watch_id,ordinal,parent_revision_id,record_json,recorded_at) VALUES(?,?,?,?,?,?)",(revision["revision_id"],watch_id,revision["ordinal"],expected,canonical_json(revision),recorded_at))
        return revision

    @staticmethod
    def _snapshot_state(snapshot: Mapping[str, Any]) -> str:
        if snapshot.get("revision_id") is not None and snapshot.get("content_hash") is not None: return "available"
        status=str(snapshot.get("status") or "").lower()
        if status in _UNAVAILABLE_STATUSES: return "unavailable"
        return "not_assessable"

    @classmethod
    def _transition(cls, previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> tuple[str,str|None]:
        cs=cls._snapshot_state(current)
        if previous is None:
            if cs=="unavailable": return "unavailable","unavailable"
            if cs=="not_assessable": return "not_assessable",None
            return "initial",None
        ps=cls._snapshot_state(previous)
        if cs=="unavailable": return "unavailable", "unavailable" if ps!="unavailable" else None
        if cs=="not_assessable": return "not_assessable",None
        if ps in {"unavailable","not_assessable"}: return "recovered","recovered"
        if current.get("revision_id")!=previous.get("revision_id"): return "changed","changed"
        return "unchanged",None

    def latest_evaluation(self, watch_id: str) -> dict[str, Any] | None:
        if self.get_watch(watch_id) is None: raise KeyError(watch_id)
        with self.connect() as conn: row=conn.execute("SELECT record_json FROM testamur_watch_evaluations WHERE watch_id=? ORDER BY recorded_at DESC, evaluation_id DESC LIMIT 1",(watch_id,)).fetchone()
        return None if row is None else json.loads(str(row["record_json"]))

    def evaluate_snapshot(self, sources: TestamurSourceStore, *, watch_id: str, snapshot_id: str) -> dict[str, Any]:
        watch=self.get_watch(watch_id)
        if watch is None: raise KeyError(watch_id)
        snapshot=sources.get_snapshot(snapshot_id)
        if snapshot is None: raise KeyError(snapshot_id)
        if snapshot.get("source_id")!=watch.get("source_id"): raise ValueError("snapshot belongs to a different Source than this Watch")
        wr=self.latest_watch_revision(watch_id)
        if wr is None: raise RuntimeError("watch has no configuration revision")
        with self.connect() as conn: duplicate=conn.execute("SELECT record_json FROM testamur_watch_evaluations WHERE watch_id=? AND snapshot_id=? ORDER BY recorded_at DESC LIMIT 1",(watch_id,snapshot_id)).fetchone()
        if duplicate is not None:
            existing=json.loads(str(duplicate["record_json"])); return {"evaluation":existing,"alert":self.alert_for_evaluation(existing["evaluation_id"]),"reused":True}
        pe=self.latest_evaluation(watch_id); ps=None if pe is None else sources.get_snapshot(str(pe["snapshot_id"])); state,event=self._transition(ps,snapshot); recorded_at=_utc_now()
        core={"watch_id":watch_id,"watch_revision_id":wr["revision_id"],"source_id":watch["source_id"],"snapshot_id":snapshot_id,"previous_evaluation_id":None if pe is None else pe["evaluation_id"],"previous_snapshot_id":None if pe is None else pe["snapshot_id"],"operational_state":state,"event_type":event,"recorded_at":recorded_at}
        evaluation={"evaluation_id":WATCH_EVALUATION_PREFIX+canonical_hash(core),**core,"semantics":{"mechanical_snapshot_transition":True,"truth_change_implied":False,"downstream_invalidation_implied":False,"source_change_is_revision_identity_change":state=="changed"}}
        with self.connect() as conn: conn.execute("INSERT INTO testamur_watch_evaluations(evaluation_id,watch_id,watch_revision_id,snapshot_id,operational_state,record_json,recorded_at) VALUES(?,?,?,?,?,?,?)",(evaluation["evaluation_id"],watch_id,wr["revision_id"],snapshot_id,state,canonical_json(evaluation),recorded_at))
        alert=None
        if event is not None and event in set(wr["alert_on"]): alert=self._record_alert(watch_id=watch_id,evaluation=evaluation,event_type=event)
        return {"evaluation":evaluation,"alert":alert,"reused":False}

    def _record_alert(self, *, watch_id: str, evaluation: Mapping[str,Any], event_type: str) -> dict[str,Any]:
        if event_type not in WATCH_EVENTS: raise ValueError(f"unsupported alert event: {event_type}")
        recorded_at=_utc_now(); core={"watch_id":watch_id,"evaluation_id":evaluation["evaluation_id"],"source_id":evaluation["source_id"],"snapshot_id":evaluation["snapshot_id"],"event_type":event_type,"recorded_at":recorded_at}
        alert={"alert_id":ALERT_PREFIX+canonical_hash(core),**core,"semantics":{"operational_alert":True,"truth_status_implied":False,"acknowledgement_state_stored_here":False}}
        with self.connect() as conn: conn.execute("INSERT INTO testamur_alerts(alert_id,watch_id,evaluation_id,event_type,record_json,recorded_at) VALUES(?,?,?,?,?,?)",(alert["alert_id"],watch_id,evaluation["evaluation_id"],event_type,canonical_json(alert),recorded_at))
        return alert

    def get_evaluation(self, evaluation_id: str) -> dict[str,Any] | None:
        with self.connect() as conn: row=conn.execute("SELECT record_json FROM testamur_watch_evaluations WHERE evaluation_id=?",(str(evaluation_id),)).fetchone()
        return None if row is None else json.loads(str(row["record_json"]))

    def get_alert(self, alert_id: str) -> dict[str,Any] | None:
        with self.connect() as conn: row=conn.execute("SELECT record_json FROM testamur_alerts WHERE alert_id=?",(str(alert_id),)).fetchone()
        return None if row is None else json.loads(str(row["record_json"]))

    def alert_for_evaluation(self, evaluation_id: str) -> dict[str,Any] | None:
        with self.connect() as conn: row=conn.execute("SELECT record_json FROM testamur_alerts WHERE evaluation_id=? ORDER BY recorded_at DESC LIMIT 1",(str(evaluation_id),)).fetchone()
        return None if row is None else json.loads(str(row["record_json"]))

    def alerts(self, watch_id: str, *, limit: int=100) -> list[dict[str,Any]]:
        if self.get_watch(watch_id) is None: raise KeyError(watch_id)
        bounded=max(1,min(int(limit),500))
        with self.connect() as conn: rows=conn.execute("SELECT record_json FROM testamur_alerts WHERE watch_id=? ORDER BY recorded_at DESC, alert_id DESC LIMIT ?",(watch_id,bounded)).fetchall()
        return [json.loads(str(row["record_json"])) for row in rows]

    def stats(self) -> dict[str,int]:
        with self.connect() as conn:
            watches=int(conn.execute("SELECT COUNT(*) FROM testamur_watches").fetchone()[0]); revisions=int(conn.execute("SELECT COUNT(*) FROM testamur_watch_revisions").fetchone()[0]); evaluations=int(conn.execute("SELECT COUNT(*) FROM testamur_watch_evaluations").fetchone()[0]); alerts=int(conn.execute("SELECT COUNT(*) FROM testamur_alerts").fetchone()[0])
        return {"watches":watches,"watch_revisions":revisions,"evaluations":evaluations,"alerts":alerts}
