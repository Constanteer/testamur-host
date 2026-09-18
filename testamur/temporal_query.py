from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from .temporal_model import TemporalBasis, TemporalMode, TemporalProjection, parse_instant


@dataclass(frozen=True)
class TemporalClause:
    mode: TemporalMode | str
    at: str

    def __post_init__(self) -> None:
        normalized_mode = self.mode if isinstance(self.mode, TemporalMode) else TemporalMode(str(self.mode))
        object.__setattr__(self, "mode", normalized_mode)
        object.__setattr__(
            self,
            "at",
            parse_instant(self.at).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        )


@dataclass(frozen=True)
class TemporalMatch:
    projection: TemporalProjection
    recognized_retrospectively: bool = False
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "projection": self.projection.to_dict(),
            "recognized_retrospectively": self.recognized_retrospectively,
            "reasons": list(self.reasons),
        }


def _matches_clause(item: TemporalProjection, clause: TemporalClause) -> tuple[bool, str | None]:
    target = parse_instant(clause.at)
    if clause.mode is TemporalMode.KNOWN_AT:
        return parse_instant(item.recorded_at.instant) <= target, "recorded_by_cut"

    if clause.mode is TemporalMode.AVAILABLE_BY:
        publication = item.published_at
        if publication is None or not publication.queryable:
            return False, "publication_time_unknown"
        if publication.basis is TemporalBasis.UNKNOWN:
            return False, "publication_provenance_unknown"
        if not publication.source_ref:
            return False, "publication_provenance_missing"
        return parse_instant(publication.instant) <= target, "publication_supported_by_cut"

    if clause.mode is TemporalMode.EFFECTIVE_AT:
        if item.effective is None:
            return False, "effective_interval_unknown"
        return item.effective.contains(target), "effective_at_cut"

    raise ValueError(f"unsupported temporal mode: {clause.mode}")


def _select_perspective(
    projections: Iterable[TemporalProjection], perspective: str | None
) -> list[TemporalProjection]:
    items = list(projections)
    if perspective is not None:
        selected = str(perspective).strip()
        if not selected:
            raise ValueError("perspective must not be empty")
        return [item for item in items if item.perspective == selected]

    perspectives = {item.perspective for item in items}
    if len(perspectives) > 1:
        raise ValueError(
            "temporal query spans multiple perspectives; select one explicit perspective"
        )
    return items


def query_temporal(
    projections: Iterable[TemporalProjection],
    clauses: Sequence[TemporalClause],
    *,
    limit: int = 500,
    perspective: str | None = None,
) -> list[TemporalMatch]:
    """Apply explicit AND-composed temporal clauses deterministically.

    There is intentionally no generic ``as_of`` argument. Callers must state
    which temporal meaning they want. ``recorded_at`` is the transaction time
    of the temporal assertion itself, so KNOWN_AT can safely compose with a
    retrospective AVAILABLE_BY assertion without leaking that assertion into
    an earlier knowledge cut.

    Distributed visibility is fail-closed: mixed perspectives require an
    explicit ``perspective`` selection rather than being silently combined.
    """
    if not clauses:
        raise ValueError("at least one explicit temporal clause is required")
    bounded = max(1, min(int(limit), 5000))
    items = _select_perspective(projections, perspective)
    matches: list[TemporalMatch] = []
    for item in items:
        reasons: list[str] = []
        accepted = True
        for clause in clauses:
            ok, reason = _matches_clause(item, clause)
            if reason:
                reasons.append(f"{clause.mode.value}:{reason}")
            if not ok:
                accepted = False
                break
        if not accepted:
            continue
        retrospective = any(
            c.mode in {TemporalMode.AVAILABLE_BY, TemporalMode.EFFECTIVE_AT}
            and parse_instant(item.recorded_at.instant) > parse_instant(c.at)
            for c in clauses
        )
        matches.append(TemporalMatch(item, retrospective, tuple(reasons)))
    matches.sort(key=lambda match: (match.projection.object_ref, match.projection.recorded_at.instant))
    return matches[:bounded]


def known_at(
    projections: Iterable[TemporalProjection],
    at: str | datetime,
    *,
    limit: int = 500,
    perspective: str | None = None,
) -> list[TemporalMatch]:
    return query_temporal(
        projections,
        [TemporalClause(TemporalMode.KNOWN_AT, str(at))],
        limit=limit,
        perspective=perspective,
    )


def available_by(
    projections: Iterable[TemporalProjection],
    at: str | datetime,
    *,
    limit: int = 500,
    perspective: str | None = None,
) -> list[TemporalMatch]:
    return query_temporal(
        projections,
        [TemporalClause(TemporalMode.AVAILABLE_BY, str(at))],
        limit=limit,
        perspective=perspective,
    )


def effective_at(
    projections: Iterable[TemporalProjection],
    at: str | datetime,
    *,
    limit: int = 500,
    perspective: str | None = None,
) -> list[TemporalMatch]:
    return query_temporal(
        projections,
        [TemporalClause(TemporalMode.EFFECTIVE_AT, str(at))],
        limit=limit,
        perspective=perspective,
    )
