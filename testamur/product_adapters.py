from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Mapping

from .advisory import TestamurAdvisoryStore
from .affectedness import TestamurAffectednessStore
from .affectedness_reliance import RevisionPinnedRelianceResolver
from .contracts import ObjectKind
from .lineage import TestamurLineageStore
from .policy import PolicyStore
from .product_extensions import (
    ImpactReader,
    ProductExtensions,
    MonitorTargetProvider,
    TemporalEventReader,
    TemporalReader,
)
from .reliance import RelianceStore
from .temporal_query import TemporalClause
from .temporal_store import TemporalStore
from .work_session import WorkSessionStore


def _json_value(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    to_json = getattr(value, "to_json", None)
    if callable(to_json):
        result = to_json()
        if isinstance(result, Mapping):
            return dict(result)
    raise TypeError(f"canonical product reader returned non-object value: {type(value).__name__}")


def _work_session_reader(store: WorkSessionStore):
    def read(ref: str) -> Mapping[str, Any] | None:
        try:
            return _json_value(store.get_session(ref))
        except KeyError:
            return None

    return read


def _advisory_reader(store: TestamurAdvisoryStore):
    def read(ref: str) -> Mapping[str, Any] | None:
        # Event identities and immutable event revisions intentionally share the
        # advisory extension family. Prefer the stable event identity first.
        return store.get_event(ref) or store.get_revision(ref)

    return read


def _bounded_limit(query: Mapping[str, Any]) -> int:
    try:
        return max(1, min(int(query.get("limit", 500)), 5000))
    except (TypeError, ValueError) as exc:
        raise ValueError("temporal query limit must be an integer") from exc


def _selected_perspective(query: Mapping[str, Any]) -> str | None:
    perspective_raw = query.get("perspective")
    return None if perspective_raw is None else str(perspective_raw)


def _temporal_reader(store: TemporalStore) -> TemporalReader:
    def read(ref: str, query: Mapping[str, Any]) -> Mapping[str, Any]:
        raw_clauses = query.get("clauses")
        if not isinstance(raw_clauses, list) or not raw_clauses:
            raise ValueError("temporal query requires a non-empty clauses list")
        clauses: list[TemporalClause] = []
        for raw in raw_clauses:
            if not isinstance(raw, Mapping):
                raise ValueError("temporal clauses must be objects")
            mode = str(raw.get("mode") or "").strip().lower()
            clauses.append(TemporalClause(mode=mode, at=str(raw.get("at") or "")))
        limit = _bounded_limit(query)
        perspective = _selected_perspective(query)
        matches = store.query(
            clauses,
            limit=limit,
            perspective=perspective,
            object_ref=ref,
        )
        selected = [match.to_dict() for match in matches]
        return {
            "ok": True,
            "schema": "testamur.product.temporal.v1",
            "ref": ref,
            "clauses": [{"mode": clause.mode.value, "at": clause.at} for clause in clauses],
            "perspective": perspective,
            "items": selected,
            "semantics": {
                "generic_as_of_used": False,
                "known_at_is_available_by": False,
                "available_by_requires_provenance": True,
                "effective_at_is_transaction_time": False,
                "object_ref_scoped": True,
                "perspective_fail_closed": True,
                "time_cut_selects_perspective": False,
                "temporal_events_in_state_view": False,
            },
        }

    return read


def _temporal_event_reader(store: TemporalStore) -> TemporalEventReader:
    def read(ref: str, query: Mapping[str, Any]) -> Mapping[str, Any]:
        raw_kinds = query.get("event_kinds")
        if raw_kinds is not None and not isinstance(raw_kinds, (str, list, tuple)):
            raise ValueError("event_kinds must be a string or sequence")
        limit = _bounded_limit(query)
        perspective = _selected_perspective(query)
        recorded_by_raw = query.get("recorded_by")
        event_time_by_raw = query.get("event_time_by")
        recorded_by = None if recorded_by_raw is None else str(recorded_by_raw)
        event_time_by = None if event_time_by_raw is None else str(event_time_by_raw)
        items = store.events_for(
            ref,
            event_kinds=raw_kinds,
            recorded_by=recorded_by,
            event_time_by=event_time_by,
            perspective=perspective,
            limit=limit,
        )
        return {
            "ok": True,
            "schema": "testamur.product.temporal-events.v1",
            "ref": ref,
            "event_kinds": raw_kinds,
            "recorded_by": recorded_by,
            "event_time_by": event_time_by,
            "perspective": perspective,
            "items": items,
            "semantics": {
                "first_class_events": True,
                "separate_from_state_view": True,
                "recorded_time_is_event_time": False,
                "subject_ref_scoped": True,
                "event_kind_is_non_temporal_scope": True,
                "perspective_fail_closed": True,
                "time_cut_selects_perspective": False,
            },
        }

    return read


def _reliance_impact_reader(store: RelianceStore, scope_ref: str) -> ImpactReader:
    """Project one changed object into the exact durable W3 reliance surface.

    This adapter deliberately delegates to RelianceStore.blast_radius instead of
    traversing Records/Relations heuristically. A changed object creates a review
    obligation only where an immutable reliance receipt actually pinned it; the
    result never promotes change/staleness into falsity or invalidity.
    """

    scope = str(scope_ref or "").strip()
    if not scope:
        raise ValueError("impact_scope_ref must not be empty")

    def read(ref: str) -> Mapping[str, Any]:
        result = dict(store.blast_radius(scope_ref=scope, changed_object_refs=[ref]))
        return {
            "ok": True,
            "schema": "testamur.product.impact.v1",
            "ref": ref,
            "impact": result,
            "semantics": {
                "actual_durable_reliance_only": True,
                "change_implies_review": True,
                "change_implies_invalid": False,
                "stale_implies_false": False,
            },
        }

    return read


def revision_pinned_impact_reader(store: RelianceStore, scope_ref: str) -> ImpactReader:
    """Build a W5-compatible impact reader for an exact affected revision.

    W3's generic blast radius is keyed by relied object identity. W5 affectedness
    conclusions are keyed by exact artifact/component revision. This adapter uses
    RevisionPinnedRelianceResolver so a revision is matched against receipt pin
    *values*, never incorrectly passed as an object key. The result is review
    obligation only: affectedness is not downstream falsity or invalidity.
    """

    resolver = RevisionPinnedRelianceResolver(store=store, scope_ref=scope_ref)

    def read(ref: str) -> Mapping[str, Any]:
        result = resolver.blast_radius_for_refs([ref])
        return {
            "ok": True,
            "schema": "testamur.product.impact.v1",
            "ref": ref,
            "impact": result,
            "semantics": {
                "actual_durable_reliance_only": True,
                "match_is_exact_pinned_revision": True,
                "lineage_propagation_implies_vulnerability_verdict": False,
                "affectedness_implies_downstream_false": False,
                "review_obligation_only": True,
            },
        }

    return read


def canonical_product_extensions(
    database_path: str | Path,
    *,
    reliance_store: RelianceStore | None = None,
    impact_scope_ref: str | None = None,
    impact_reader: ImpactReader | None = None,
    temporal_reader: TemporalReader | None = None,
    temporal_event_reader: TemporalEventReader | None = None,
    monitor_target_providers: Mapping[str, MonitorTargetProvider] | None = None,
    monitor_target_provider_specs: Mapping[str, Mapping[str, Any]] | None = None,
    work_session_store: WorkSessionStore | None = None,
) -> ProductExtensions:
    """Bind integrated W2-W5 canonical stores to W1's product read surface.

    This is composition only: semantic engines remain owners of their rows,
    identities and verdicts. Reading lineage never manufactures affectedness,
    reading an advisory never manufactures a vulnerability verdict, and reading a
    WorkSession never promotes exposure to reliance.

    ``RelianceStore`` remains injectable because constructing it requires the
    caller's explicit policy/evidence view. When both a RelianceStore and an
    ``impact_scope_ref`` are supplied, W1 exposes W3's exact durable blast radius
    as the default product impact provider. An explicit ``impact_reader`` still
    takes precedence; W5 exact-revision impact should use
    ``revision_pinned_impact_reader`` so revision pins are not confused with W3
    object keys. W2 temporal storage is self-contained and is wired by default
    without changing KNOWN_AT/AVAILABLE_BY/EFFECTIVE_AT semantics.
    """

    path = Path(database_path)
    policies = PolicyStore(path)
    lineage = TestamurLineageStore(path)
    affectedness = TestamurAffectednessStore(path)
    advisories = TestamurAdvisoryStore(path)
    temporal = TemporalStore(path)
    sessions = work_session_store or WorkSessionStore(path)

    readers = {
        ObjectKind.POLICY: policies.get,
        ObjectKind.WORK_SESSION: _work_session_reader(sessions),
        ObjectKind.LINEAGE: lineage.get_lineage,
        ObjectKind.AFFECTEDNESS: affectedness.get_assessment,
        ObjectKind.ADVISORY: _advisory_reader(advisories),
    }
    if reliance_store is not None:
        readers[ObjectKind.RELIANCE] = reliance_store.get

    resolved_impact = impact_reader
    if resolved_impact is None and reliance_store is not None and impact_scope_ref is not None:
        resolved_impact = _reliance_impact_reader(reliance_store, impact_scope_ref)

    return ProductExtensions(
        object_readers=readers,
        impact_reader=resolved_impact,
        temporal_reader=temporal_reader or _temporal_reader(temporal),
        temporal_event_reader=temporal_event_reader or _temporal_event_reader(temporal),
        monitor_target_providers=dict(monitor_target_providers or {}),
        monitor_target_provider_specs={
            str(name): dict(spec)
            for name, spec in dict(monitor_target_provider_specs or {}).items()
        },
    )
