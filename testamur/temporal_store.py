from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence, TYPE_CHECKING

from .temporal_model import (
    EffectiveInterval,
    TemporalAssertion,
    TemporalBasis,
    TemporalEvent,
    TemporalEventKind,
    TemporalMode,
    TemporalProjection,
    format_instant,
)

if TYPE_CHECKING:
    from .temporal_query import TemporalClause, TemporalMatch


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _assertion_id(payload: dict[str, Any]) -> str:
    return "tst:temporal:" + hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _event_fields(projection: TemporalProjection) -> tuple[int, str | None, str | None]:
    metadata = dict(projection.metadata or {})
    if metadata.get("projection_kind") != "temporal_event":
        return 0, None, None
    event = TemporalEvent.from_projection(projection)
    event_time = (
        event.event_time.instant
        if event.event_time is not None and event.event_time.queryable
        else None
    )
    return 1, event.kind.value, event_time


def _index_values(assertion_id: str, projection: TemporalProjection) -> tuple[Any, ...]:
    publication = projection.published_at
    publication_supported = (
        publication is not None
        and publication.queryable
        and publication.basis is not TemporalBasis.UNKNOWN
        and bool(publication.source_ref)
    )
    effective = projection.effective
    valid_from = (
        None
        if effective is None or effective.valid_from is None
        else effective.valid_from.instant
    )
    valid_until = (
        None
        if effective is None or effective.valid_until is None
        else effective.valid_until.instant
    )
    is_event, event_kind, event_time = _event_fields(projection)
    return (
        assertion_id,
        projection.object_ref,
        projection.recorded_at.instant,
        publication.instant if publication is not None and publication.queryable else None,
        1 if publication_supported else 0,
        valid_from,
        valid_until,
        1 if effective is not None else 0,
        projection.perspective,
        is_event,
        event_kind,
        event_time,
    )


class TemporalStore:
    """Append-only temporal assertions layered over existing immutable stores.

    Assertion JSON is the durable source of truth. A separate rebuildable index
    accelerates explicit temporal clauses without changing assertion identity.
    Temporal events reuse the same immutable assertion stream rather than
    creating a second historical substrate.
    """

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
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_query_index_columns(self, conn: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(testamur_temporal_query_index)").fetchall()
        }
        additions = {
            "is_event": "INTEGER NOT NULL DEFAULT 0",
            "event_kind": "TEXT",
            "event_time": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in columns:
                conn.execute(
                    f"ALTER TABLE testamur_temporal_query_index ADD COLUMN {name} {declaration}"
                )

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS testamur_temporal_assertions(
              assertion_id TEXT PRIMARY KEY,
              object_ref TEXT NOT NULL,
              assertion_json TEXT NOT NULL,
              recorded_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS testamur_temporal_object_recorded_idx
              ON testamur_temporal_assertions(object_ref, recorded_at, assertion_id);
            CREATE INDEX IF NOT EXISTS testamur_temporal_recorded_idx
              ON testamur_temporal_assertions(recorded_at, assertion_id);

            CREATE TABLE IF NOT EXISTS testamur_temporal_query_index(
              assertion_id TEXT PRIMARY KEY
                REFERENCES testamur_temporal_assertions(assertion_id) ON DELETE CASCADE,
              object_ref TEXT NOT NULL,
              recorded_at TEXT NOT NULL,
              published_at TEXT,
              publication_supported INTEGER NOT NULL,
              valid_from TEXT,
              valid_until TEXT,
              has_effective INTEGER NOT NULL,
              perspective TEXT NOT NULL,
              is_event INTEGER NOT NULL DEFAULT 0,
              event_kind TEXT,
              event_time TEXT
            );
            CREATE INDEX IF NOT EXISTS testamur_temporal_qidx_recorded
              ON testamur_temporal_query_index(recorded_at, object_ref, assertion_id);
            CREATE INDEX IF NOT EXISTS testamur_temporal_qidx_published
              ON testamur_temporal_query_index(publication_supported, published_at, object_ref, assertion_id);
            CREATE INDEX IF NOT EXISTS testamur_temporal_qidx_effective
              ON testamur_temporal_query_index(has_effective, valid_from, valid_until, object_ref, assertion_id);
            CREATE INDEX IF NOT EXISTS testamur_temporal_qidx_perspective
              ON testamur_temporal_query_index(perspective, object_ref, assertion_id);

            CREATE TRIGGER IF NOT EXISTS testamur_temporal_no_update
            BEFORE UPDATE ON testamur_temporal_assertions BEGIN
              SELECT RAISE(ABORT, 'Testamur temporal assertions are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS testamur_temporal_no_delete
            BEFORE DELETE ON testamur_temporal_assertions BEGIN
              SELECT RAISE(ABORT, 'Testamur temporal assertions are immutable');
            END;
            """)
            self._ensure_query_index_columns(conn)
            conn.executescript("""
            CREATE INDEX IF NOT EXISTS testamur_temporal_qidx_event
              ON testamur_temporal_query_index(
                is_event, event_kind, object_ref, recorded_at, assertion_id
              );
            CREATE INDEX IF NOT EXISTS testamur_temporal_qidx_event_time
              ON testamur_temporal_query_index(
                is_event, event_time, object_ref, assertion_id
              );
            """)
            rows = conn.execute(
                """SELECT assertion_id, assertion_json
                   FROM testamur_temporal_assertions
                   WHERE assertion_id NOT IN (
                     SELECT assertion_id FROM testamur_temporal_query_index
                   )
                   ORDER BY assertion_id"""
            ).fetchall()
            for row in rows:
                payload = json.loads(str(row["assertion_json"]))["projection"]
                projection = self._decode(payload)
                conn.execute(
                    """INSERT INTO testamur_temporal_query_index(
                         assertion_id,object_ref,recorded_at,published_at,
                         publication_supported,valid_from,valid_until,
                         has_effective,perspective,is_event,event_kind,event_time
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    _index_values(str(row["assertion_id"]), projection),
                )

    def append_projection(self, projection: TemporalProjection) -> dict[str, Any]:
        payload = projection.to_dict()
        assertion_id = _assertion_id(payload)
        envelope = {"assertion_id": assertion_id, "projection": payload}
        encoded = _canonical_json(envelope)
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO testamur_temporal_assertions(assertion_id,object_ref,assertion_json,recorded_at) VALUES(?,?,?,?)",
                (assertion_id, projection.object_ref, encoded, projection.recorded_at.instant),
            )
            row = conn.execute(
                "SELECT assertion_json FROM testamur_temporal_assertions WHERE assertion_id=?",
                (assertion_id,),
            ).fetchone()
            if row is None or str(row["assertion_json"]) != encoded:
                raise ValueError("temporal assertion identity is bound to different content")
            conn.execute(
                """INSERT OR IGNORE INTO testamur_temporal_query_index(
                     assertion_id,object_ref,recorded_at,published_at,
                     publication_supported,valid_from,valid_until,
                     has_effective,perspective,is_event,event_kind,event_time
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                _index_values(assertion_id, projection),
            )
        return envelope

    def append_event(self, event: TemporalEvent) -> dict[str, Any]:
        """Append an immutable temporal event into the shared assertion stream."""
        envelope = self.append_projection(event.to_projection())
        return {
            "event_id": envelope["assertion_id"],
            "assertion_id": envelope["assertion_id"],
            "event": event.to_dict(),
            "projection": envelope["projection"],
        }

    def projections(
        self,
        *,
        recorded_by: str | None = None,
        limit: int = 500,
        include_events: bool = False,
    ) -> list[TemporalProjection]:
        bounded = max(1, min(int(limit), 5000))
        where = [] if include_events else ["idx.is_event=0"]
        params: list[Any] = []
        if recorded_by is not None:
            where.append("idx.recorded_at<=?")
            params.append(format_instant(recorded_by))
        sql = """
            SELECT a.assertion_json
            FROM testamur_temporal_query_index idx
            JOIN testamur_temporal_assertions a USING(assertion_id)
        """
        if where:
            sql += " WHERE " + " AND ".join(f"({clause})" for clause in where)
        sql += " ORDER BY idx.object_ref,idx.recorded_at,idx.assertion_id LIMIT ?"
        params.append(bounded)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._decode(json.loads(str(row["assertion_json"]))["projection"]) for row in rows]

    def _require_single_candidate_perspective(
        self,
        conn: sqlite3.Connection,
        where: Sequence[str],
        params: Sequence[Any],
    ) -> None:
        sql = "SELECT DISTINCT idx.perspective FROM testamur_temporal_query_index idx"
        if where:
            sql += " WHERE " + " AND ".join(f"({clause})" for clause in where)
        sql += " ORDER BY idx.perspective LIMIT 2"
        rows = conn.execute(sql, list(params)).fetchall()
        if len(rows) > 1:
            raise ValueError(
                "temporal query spans multiple perspectives; select one explicit perspective"
            )

    def query(
        self,
        clauses: Sequence["TemporalClause"],
        *,
        limit: int = 500,
        perspective: str | None = None,
        object_ref: str | None = None,
        include_events: bool = False,
    ) -> list["TemporalMatch"]:
        """Execute explicit temporal clauses through the derived SQLite index.

        Event projections are excluded by default so an audit-event stream
        cannot masquerade as another subject-state row in ordinary views.
        ``object_ref`` scopes both candidate selection and perspective ambiguity
        before time predicates are applied. Mixed perspectives inside that
        non-temporal scope fail closed unless explicitly selected.
        """
        from .temporal_query import query_temporal

        if not clauses:
            raise ValueError("at least one explicit temporal clause is required")
        bounded = max(1, min(int(limit), 5000))

        scope_where: list[str] = [] if include_events else ["idx.is_event=0"]
        scope_params: list[Any] = []
        if object_ref is not None:
            selected_ref = str(object_ref).strip()
            if not selected_ref:
                raise ValueError("object_ref must not be empty")
            scope_where.append("idx.object_ref=?")
            scope_params.append(selected_ref)

        where = list(scope_where)
        params = list(scope_params)
        for clause in clauses:
            at = format_instant(clause.at)
            if clause.mode is TemporalMode.KNOWN_AT:
                where.append("idx.recorded_at<=?")
                params.append(at)
            elif clause.mode is TemporalMode.AVAILABLE_BY:
                where.append(
                    "idx.publication_supported=1 AND idx.published_at IS NOT NULL AND idx.published_at<=?"
                )
                params.append(at)
            elif clause.mode is TemporalMode.EFFECTIVE_AT:
                where.append(
                    "idx.has_effective=1 AND (idx.valid_from IS NULL OR idx.valid_from<=?) "
                    "AND (idx.valid_until IS NULL OR idx.valid_until>?)"
                )
                params.extend((at, at))
            else:
                raise ValueError(f"unsupported temporal mode: {clause.mode}")

        selected_perspective = None
        if perspective is not None:
            selected_perspective = str(perspective).strip()
            if not selected_perspective:
                raise ValueError("perspective must not be empty")
            where.append("idx.perspective=?")
            params.append(selected_perspective)

        sql = f"""
            SELECT a.assertion_json
            FROM testamur_temporal_query_index idx
            JOIN testamur_temporal_assertions a USING(assertion_id)
            WHERE {' AND '.join(f'({clause})' for clause in where)}
            ORDER BY idx.object_ref, idx.recorded_at, idx.assertion_id
            LIMIT ?
        """
        with self.connect() as conn:
            if selected_perspective is None:
                self._require_single_candidate_perspective(conn, scope_where, scope_params)
            rows = conn.execute(sql, [*params, bounded]).fetchall()
        projections = [
            self._decode(json.loads(str(row["assertion_json"]))["projection"])
            for row in rows
        ]
        return query_temporal(
            projections,
            clauses,
            limit=bounded,
            perspective=selected_perspective,
        )

    def events_for(
        self,
        subject_ref: str | None = None,
        *,
        event_kinds: Iterable[TemporalEventKind | str] | TemporalEventKind | str | None = None,
        recorded_by: str | None = None,
        event_time_by: str | None = None,
        perspective: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return first-class temporal events from the immutable assertion stream.

        ``subject_ref`` and ``event_kinds`` define the non-temporal scope used
        for perspective ambiguity. Time cuts are applied only after that scope is
        resolved, so a historical cut cannot silently choose a node perspective.
        """
        bounded = max(1, min(int(limit), 5000))
        scope_where = ["idx.is_event=1"]
        scope_params: list[Any] = []

        if subject_ref is not None:
            subject = str(subject_ref).strip()
            if not subject:
                raise ValueError("subject_ref must not be empty")
            scope_where.append("idx.object_ref=?")
            scope_params.append(subject)

        if event_kinds is not None:
            raw_kinds: Iterable[TemporalEventKind | str]
            if isinstance(event_kinds, (TemporalEventKind, str)):
                raw_kinds = (event_kinds,)
            else:
                raw_kinds = event_kinds
            normalized = sorted(
                {
                    (kind if isinstance(kind, TemporalEventKind) else TemporalEventKind(str(kind))).value
                    for kind in raw_kinds
                }
            )
            if not normalized:
                return []
            placeholders = ",".join("?" for _ in normalized)
            scope_where.append(f"idx.event_kind IN ({placeholders})")
            scope_params.extend(normalized)

        where = list(scope_where)
        params = list(scope_params)
        if recorded_by is not None:
            where.append("idx.recorded_at<=?")
            params.append(format_instant(recorded_by))
        if event_time_by is not None:
            where.append("idx.event_time IS NOT NULL AND idx.event_time<=?")
            params.append(format_instant(event_time_by))

        selected_perspective = None
        if perspective is not None:
            selected_perspective = str(perspective).strip()
            if not selected_perspective:
                raise ValueError("perspective must not be empty")
            where.append("idx.perspective=?")
            params.append(selected_perspective)

        sql = f"""
            SELECT idx.assertion_id, a.assertion_json
            FROM testamur_temporal_query_index idx
            JOIN testamur_temporal_assertions a USING(assertion_id)
            WHERE {' AND '.join(f'({clause})' for clause in where)}
            ORDER BY idx.recorded_at, idx.assertion_id
            LIMIT ?
        """
        with self.connect() as conn:
            if selected_perspective is None:
                self._require_single_candidate_perspective(conn, scope_where, scope_params)
            rows = conn.execute(sql, [*params, bounded]).fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            projection = self._decode(
                json.loads(str(row["assertion_json"]))["projection"]
            )
            event = TemporalEvent.from_projection(projection)
            result.append(
                {
                    "event_id": str(row["assertion_id"]),
                    "assertion_id": str(row["assertion_id"]),
                    "event": event.to_dict(),
                }
            )
        return result

    @staticmethod
    def _decode(payload: dict[str, Any]) -> TemporalProjection:
        published = payload.get("published_at")
        subject_recorded = payload.get("subject_recorded_at")
        effective = payload.get("effective")
        interval = None
        if effective is not None:
            interval = EffectiveInterval(
                valid_from=(
                    None
                    if effective.get("valid_from") is None
                    else TemporalAssertion.from_dict(effective["valid_from"])
                ),
                valid_until=(
                    None
                    if effective.get("valid_until") is None
                    else TemporalAssertion.from_dict(effective["valid_until"])
                ),
            )
        return TemporalProjection(
            object_ref=str(payload["object_ref"]),
            recorded_at=TemporalAssertion.from_dict(payload["recorded_at"]),
            subject_recorded_at=(
                None if subject_recorded is None else TemporalAssertion.from_dict(subject_recorded)
            ),
            published_at=(
                None if published is None else TemporalAssertion.from_dict(published)
            ),
            effective=interval,
            perspective=str(payload.get("perspective", "local")),
            metadata=dict(payload.get("metadata") or {}),
        )
