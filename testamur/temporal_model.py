from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


class TemporalMode(str, Enum):
    KNOWN_AT = "known_at"
    AVAILABLE_BY = "available_by"
    EFFECTIVE_AT = "effective_at"


class TemporalPrecision(str, Enum):
    INSTANT = "instant"
    DAY = "day"
    MONTH = "month"
    YEAR = "year"
    INTERVAL = "interval"
    BEFORE = "before"
    AFTER = "after"
    CIRCA = "circa"
    UNKNOWN = "unknown"


class TemporalBasis(str, Enum):
    OBSERVED = "observed"
    SOURCE_METADATA = "source_metadata"
    ARCHIVE_OBSERVATION = "archive_observation"
    EXTERNALLY_DECLARED = "externally_declared"
    DERIVED = "derived"
    UNKNOWN = "unknown"


class TemporalEventKind(str, Enum):
    RETROSPECTIVE_DISCOVERY = "retrospective_discovery"
    LATE_OBSERVATION = "late_observation"
    HISTORICAL_AVAILABILITY_ESTABLISHED = "historical_availability_established"
    RETROSPECTIVE_CORRECTION = "retrospective_correction"


FUZZY_PRECISIONS = frozenset(
    {
        TemporalPrecision.DAY,
        TemporalPrecision.MONTH,
        TemporalPrecision.YEAR,
        TemporalPrecision.INTERVAL,
        TemporalPrecision.BEFORE,
        TemporalPrecision.AFTER,
        TemporalPrecision.CIRCA,
    }
)


def parse_instant(value: str | datetime) -> datetime:
    """Parse an exact timezone-aware instant and normalize it to UTC.

    Naive datetimes are rejected: Testamur must not silently guess a timezone.
    """
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).strip()
        if not raw:
            raise ValueError("temporal instant must not be empty")
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"invalid ISO-8601 temporal instant: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("temporal instant must include an explicit timezone")
    return parsed.astimezone(timezone.utc)


def format_instant(value: str | datetime) -> str:
    return parse_instant(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class TemporalAssertion:
    """One provenance-bearing assertion about time.

    Exact instants are queryable only when precision is INSTANT. Fuzzy values
    retain their original representation and deliberately remain unqueryable
    until a caller supplies an explicit interpretation policy.
    """

    instant: str | datetime | None
    precision: TemporalPrecision = TemporalPrecision.INSTANT
    basis: TemporalBasis = TemporalBasis.UNKNOWN
    source_ref: str | None = None
    evidence_class: str | None = None
    original_value: str | None = None

    def __post_init__(self) -> None:
        if self.precision is TemporalPrecision.INSTANT:
            if self.instant is None:
                raise ValueError("instant precision requires an exact temporal instant")
            object.__setattr__(self, "instant", format_instant(self.instant))
        elif self.precision is TemporalPrecision.UNKNOWN:
            if self.instant is not None:
                raise ValueError("unknown temporal precision must not carry an exact instant")
        else:
            if self.precision not in FUZZY_PRECISIONS:
                raise ValueError(f"unsupported temporal precision: {self.precision}")
            if self.instant is not None:
                raise ValueError(
                    "fuzzy temporal precision must not carry an exact instant; "
                    "preserve the original value instead"
                )
            if self.original_value is None or not str(self.original_value).strip():
                raise ValueError("fuzzy temporal precision requires original_value")

        if self.basis not in {TemporalBasis.UNKNOWN, TemporalBasis.OBSERVED} and not self.source_ref:
            raise ValueError("non-observed temporal basis requires source_ref provenance")

    @classmethod
    def fuzzy(
        cls,
        value: str,
        *,
        precision: TemporalPrecision,
        basis: TemporalBasis = TemporalBasis.UNKNOWN,
        source_ref: str | None = None,
        evidence_class: str | None = None,
    ) -> "TemporalAssertion":
        if precision not in FUZZY_PRECISIONS:
            raise ValueError("fuzzy() requires a non-instant fuzzy precision")
        return cls(
            instant=None,
            precision=precision,
            basis=basis,
            source_ref=source_ref,
            evidence_class=evidence_class,
            original_value=str(value),
        )

    @property
    def queryable(self) -> bool:
        return self.precision is TemporalPrecision.INSTANT and self.instant is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "instant": self.instant,
            "precision": self.precision.value,
            "basis": self.basis.value,
            "source_ref": self.source_ref,
            "evidence_class": self.evidence_class,
            "original_value": self.original_value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TemporalAssertion":
        return cls(
            instant=value.get("instant"),
            precision=TemporalPrecision(str(value.get("precision", "instant"))),
            basis=TemporalBasis(str(value.get("basis", "unknown"))),
            source_ref=value.get("source_ref"),
            evidence_class=value.get("evidence_class"),
            original_value=value.get("original_value"),
        )


@dataclass(frozen=True)
class EffectiveInterval:
    valid_from: TemporalAssertion | None = None
    valid_until: TemporalAssertion | None = None

    def __post_init__(self) -> None:
        if self.valid_from is None and self.valid_until is None:
            raise ValueError("effective interval requires at least one explicit bound")
        if self.valid_from and not self.valid_from.queryable:
            raise ValueError("valid_from must be queryable when present")
        if self.valid_until and not self.valid_until.queryable:
            raise ValueError("valid_until must be queryable when present")
        if self.valid_from and self.valid_until:
            if parse_instant(self.valid_until.instant) <= parse_instant(self.valid_from.instant):
                raise ValueError("valid_until must be later than valid_from")

    def contains(self, instant: str | datetime) -> bool:
        target = parse_instant(instant)
        if self.valid_from and target < parse_instant(self.valid_from.instant):
            return False
        if self.valid_until and target >= parse_instant(self.valid_until.instant):
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid_from": None if self.valid_from is None else self.valid_from.to_dict(),
            "valid_until": None if self.valid_until is None else self.valid_until.to_dict(),
            "interval_semantics": "half_open",
        }


@dataclass(frozen=True)
class TemporalProjection:
    object_ref: str
    recorded_at: TemporalAssertion
    published_at: TemporalAssertion | None = None
    effective: EffectiveInterval | None = None
    perspective: str = "local"
    metadata: Mapping[str, Any] | None = None
    subject_recorded_at: TemporalAssertion | None = None

    def __post_init__(self) -> None:
        object_ref = str(self.object_ref).strip()
        if not object_ref:
            raise ValueError("object_ref must not be empty")
        object.__setattr__(self, "object_ref", object_ref)
        perspective = str(self.perspective).strip()
        if not perspective:
            raise ValueError("perspective must not be empty")
        object.__setattr__(self, "perspective", perspective)
        if not self.recorded_at.queryable:
            raise ValueError("recorded_at must be an exact queryable transaction time")
        if self.recorded_at.basis is not TemporalBasis.OBSERVED:
            raise ValueError("recorded_at must use OBSERVED basis")
        if self.subject_recorded_at is not None and not self.subject_recorded_at.queryable:
            raise ValueError("subject_recorded_at must be exact when present")

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_ref": self.object_ref,
            "recorded_at": self.recorded_at.to_dict(),
            "subject_recorded_at": (
                None if self.subject_recorded_at is None else self.subject_recorded_at.to_dict()
            ),
            "published_at": None if self.published_at is None else self.published_at.to_dict(),
            "effective": None if self.effective is None else self.effective.to_dict(),
            "perspective": self.perspective,
            "metadata": dict(self.metadata or {}),
        }


_EVENT_RESERVED_METADATA = frozenset(
    {
        "projection_kind",
        "temporal_event_kind",
        "event_subject_ref",
        "event_time",
        "related_refs",
    }
)


@dataclass(frozen=True)
class TemporalEvent:
    """A first-class recorded event in the temporal assertion stream.

    Event identity is the immutable temporal assertion ID assigned by
    ``TemporalStore``. ``recorded_at`` answers when this node learned/recorded
    the event; ``event_time`` is optional represented-world occurrence time and
    may be exact, fuzzy, or unknown without being substituted for recorded time.
    """

    kind: TemporalEventKind | str
    subject_ref: str
    recorded_at: TemporalAssertion
    event_time: TemporalAssertion | None = None
    related_refs: tuple[str, ...] = ()
    perspective: str = "local"
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        kind = self.kind if isinstance(self.kind, TemporalEventKind) else TemporalEventKind(str(self.kind))
        object.__setattr__(self, "kind", kind)

        subject_ref = str(self.subject_ref).strip()
        if not subject_ref:
            raise ValueError("temporal event subject_ref must not be empty")
        object.__setattr__(self, "subject_ref", subject_ref)

        if not isinstance(self.recorded_at, TemporalAssertion):
            raise ValueError("temporal event recorded_at must be a TemporalAssertion")
        if not self.recorded_at.queryable:
            raise ValueError("temporal event recorded_at must be an exact queryable transaction time")
        if self.recorded_at.basis is not TemporalBasis.OBSERVED:
            raise ValueError("temporal event recorded_at must use OBSERVED basis")
        if self.event_time is not None and not isinstance(self.event_time, TemporalAssertion):
            raise ValueError("temporal event event_time must be a TemporalAssertion when present")

        perspective = str(self.perspective).strip()
        if not perspective:
            raise ValueError("temporal event perspective must not be empty")
        object.__setattr__(self, "perspective", perspective)

        if isinstance(self.related_refs, (str, bytes)):
            raise ValueError("temporal event related_refs must be a sequence of refs, not a string")
        normalized_refs: list[str] = []
        seen: set[str] = set()
        for raw in self.related_refs:
            ref = str(raw).strip()
            if not ref:
                raise ValueError("temporal event related_refs must not contain empty refs")
            if ref in seen:
                raise ValueError("temporal event related_refs must not contain duplicates")
            seen.add(ref)
            normalized_refs.append(ref)
        object.__setattr__(self, "related_refs", tuple(normalized_refs))

        if self.metadata is not None and not isinstance(self.metadata, Mapping):
            raise ValueError("temporal event metadata must be a mapping when present")
        metadata = dict(self.metadata or {})
        reserved = _EVENT_RESERVED_METADATA.intersection(metadata)
        if reserved:
            raise ValueError(
                "temporal event metadata must not override reserved fields: "
                + ", ".join(sorted(reserved))
            )
        object.__setattr__(self, "metadata", metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "subject_ref": self.subject_ref,
            "recorded_at": self.recorded_at.to_dict(),
            "event_time": None if self.event_time is None else self.event_time.to_dict(),
            "related_refs": list(self.related_refs),
            "perspective": self.perspective,
            "metadata": dict(self.metadata or {}),
        }

    def to_projection(self) -> TemporalProjection:
        metadata = {
            "projection_kind": "temporal_event",
            "temporal_event_kind": self.kind.value,
            "event_subject_ref": self.subject_ref,
            "event_time": None if self.event_time is None else self.event_time.to_dict(),
            "related_refs": list(self.related_refs),
            **dict(self.metadata or {}),
        }
        return TemporalProjection(
            object_ref=self.subject_ref,
            recorded_at=self.recorded_at,
            perspective=self.perspective,
            metadata=metadata,
        )

    @classmethod
    def from_projection(cls, projection: TemporalProjection) -> "TemporalEvent":
        metadata = dict(projection.metadata or {})
        if metadata.get("projection_kind") != "temporal_event":
            raise ValueError("projection is not a temporal event")
        subject_ref = str(metadata.get("event_subject_ref") or "").strip()
        if subject_ref != projection.object_ref:
            raise ValueError("temporal event subject_ref must match projection object_ref")
        event_time_payload = metadata.get("event_time")
        event_time = (
            None
            if event_time_payload is None
            else TemporalAssertion.from_dict(event_time_payload)
        )
        event_metadata = {
            key: value for key, value in metadata.items() if key not in _EVENT_RESERVED_METADATA
        }
        return cls(
            kind=TemporalEventKind(str(metadata.get("temporal_event_kind"))),
            subject_ref=subject_ref,
            recorded_at=projection.recorded_at,
            event_time=event_time,
            related_refs=tuple(metadata.get("related_refs") or ()),
            perspective=projection.perspective,
            metadata=event_metadata,
        )
