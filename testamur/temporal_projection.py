from __future__ import annotations

from typing import Any, Mapping

from .temporal_model import (
    EffectiveInterval,
    TemporalAssertion,
    TemporalBasis,
    TemporalPrecision,
    TemporalProjection,
)


def _observed(value: str, *, source_ref: str) -> TemporalAssertion:
    return TemporalAssertion(
        instant=value,
        precision=TemporalPrecision.INSTANT,
        basis=TemporalBasis.OBSERVED,
        source_ref=source_ref,
    )


def _effective_interval(
    *,
    valid_from: str | None,
    valid_until: str | None,
    basis: TemporalBasis,
    source_ref: str | None,
) -> EffectiveInterval | None:
    if valid_from is None and valid_until is None:
        return None
    if basis is TemporalBasis.UNKNOWN:
        raise ValueError("effective validity requires a non-unknown provenance basis")
    if not source_ref:
        raise ValueError("effective validity requires validity_source_ref provenance")
    return EffectiveInterval(
        valid_from=(
            None
            if valid_from is None
            else TemporalAssertion(
                valid_from,
                basis=basis,
                source_ref=source_ref,
            )
        ),
        valid_until=(
            None
            if valid_until is None
            else TemporalAssertion(
                valid_until,
                basis=basis,
                source_ref=source_ref,
            )
        ),
    )


def source_snapshot_projection(
    snapshot: Mapping[str, Any],
    *,
    asserted_at: str | None = None,
    published_at: str | None = None,
    publication_basis: TemporalBasis = TemporalBasis.UNKNOWN,
    publication_source_ref: str | None = None,
    publication_precision: TemporalPrecision = TemporalPrecision.INSTANT,
    publication_original_value: str | None = None,
    perspective: str = "local",
) -> TemporalProjection:
    """Project an existing Source Snapshot without mutating its durable row.

    The snapshot's own ``recorded_at`` is retained as ``subject_recorded_at``.
    ``TemporalProjection.recorded_at`` is the transaction time at which this
    temporal assertion became durable. For a bare snapshot projection those
    times may be identical. Adding publication evidence later requires an
    explicit ``asserted_at`` so the later discovery can never leak backward
    into KNOWN_AT queries.
    """
    snapshot_id = str(snapshot.get("snapshot_id") or "").strip()
    snapshot_recorded_raw = str(snapshot.get("recorded_at") or "").strip()
    if not snapshot_id or not snapshot_recorded_raw:
        raise ValueError("source snapshot requires snapshot_id and recorded_at")

    subject_recorded = _observed(snapshot_recorded_raw, source_ref=snapshot_id)

    has_publication_assertion = published_at is not None or publication_original_value is not None
    publication = None
    if has_publication_assertion:
        if asserted_at is None:
            raise ValueError(
                "asserted_at is required when attaching publication evidence to an existing snapshot"
            )
        if publication_basis is not TemporalBasis.UNKNOWN and not publication_source_ref:
            raise ValueError("publication evidence with a known basis requires publication_source_ref")
        publication = TemporalAssertion(
            instant=published_at,
            precision=publication_precision,
            basis=publication_basis,
            source_ref=publication_source_ref,
            original_value=publication_original_value,
        )

    transaction_raw = asserted_at if asserted_at is not None else snapshot_recorded_raw
    transaction = _observed(transaction_raw, source_ref=snapshot_id)

    return TemporalProjection(
        object_ref=snapshot_id,
        recorded_at=transaction,
        subject_recorded_at=subject_recorded,
        published_at=publication,
        perspective=perspective,
        metadata={
            "source_id": snapshot.get("source_id"),
            "revision_id": snapshot.get("revision_id"),
            "observed_at": snapshot.get("observed_at"),
            "projection_kind": "source_snapshot",
            "temporal_assertion_is_separate_event": transaction.instant != subject_recorded.instant,
        },
    )


def record_revision_projection(
    revision: Mapping[str, Any],
    *,
    asserted_at: str | None = None,
    published_at: str | None = None,
    publication_basis: TemporalBasis = TemporalBasis.UNKNOWN,
    publication_source_ref: str | None = None,
    publication_precision: TemporalPrecision = TemporalPrecision.INSTANT,
    publication_original_value: str | None = None,
    valid_from: str | None = None,
    valid_until: str | None = None,
    validity_basis: TemporalBasis = TemporalBasis.UNKNOWN,
    validity_source_ref: str | None = None,
    perspective: str = "local",
) -> TemporalProjection:
    """Project a canonical Testamur RecordRevision into temporal semantics.

    Publication/validity supplied by the projection layer are separate temporal
    assertions. The caller must state when that evidence became durable via
    ``asserted_at`` so historical KNOWN_AT views cannot inherit it silently.
    """
    revision_id = str(revision.get("revision_id") or "").strip()
    subject_recorded_raw = str(revision.get("recorded_at") or "").strip()
    if not revision_id or not subject_recorded_raw:
        raise ValueError("record revision requires revision_id and recorded_at")

    has_publication_assertion = published_at is not None or publication_original_value is not None
    publication = None
    if has_publication_assertion:
        if publication_basis is not TemporalBasis.UNKNOWN and not publication_source_ref:
            raise ValueError("publication evidence with a known basis requires publication_source_ref")
        publication = TemporalAssertion(
            instant=published_at,
            precision=publication_precision,
            basis=publication_basis,
            source_ref=publication_source_ref,
            original_value=publication_original_value,
        )

    effective = _effective_interval(
        valid_from=valid_from,
        valid_until=valid_until,
        basis=validity_basis,
        source_ref=validity_source_ref,
    )
    if (publication is not None or effective is not None) and asserted_at is None:
        raise ValueError(
            "asserted_at is required when attaching publication or validity evidence "
            "to a record revision"
        )

    subject_recorded = _observed(subject_recorded_raw, source_ref=revision_id)
    transaction = _observed(
        asserted_at if asserted_at is not None else subject_recorded_raw,
        source_ref=revision_id,
    )
    return TemporalProjection(
        object_ref=revision_id,
        recorded_at=transaction,
        subject_recorded_at=subject_recorded,
        published_at=publication,
        effective=effective,
        perspective=perspective,
        metadata={
            "record_id": revision.get("record_id"),
            "ordinal": revision.get("ordinal"),
            "projection_kind": "record_revision",
            "temporal_assertion_is_separate_event": transaction.instant != subject_recorded.instant,
        },
    )


def relation_projection(
    relation: Mapping[str, Any],
    *,
    asserted_at: str | None = None,
    valid_from: str | None = None,
    valid_until: str | None = None,
    validity_basis: TemporalBasis = TemporalBasis.UNKNOWN,
    validity_source_ref: str | None = None,
    perspective: str = "local",
) -> TemporalProjection:
    """Project a canonical immutable Testamur Relation without rewriting it."""
    relation_id = str(relation.get("relation_id") or "").strip()
    subject_recorded_raw = str(relation.get("recorded_at") or "").strip()
    if not relation_id or not subject_recorded_raw:
        raise ValueError("relation requires relation_id and recorded_at")

    effective = _effective_interval(
        valid_from=valid_from,
        valid_until=valid_until,
        basis=validity_basis,
        source_ref=validity_source_ref,
    )
    if effective is not None and asserted_at is None:
        raise ValueError("asserted_at is required when attaching validity to a relation")

    subject_recorded = _observed(subject_recorded_raw, source_ref=relation_id)
    transaction = _observed(
        asserted_at if asserted_at is not None else subject_recorded_raw,
        source_ref=relation_id,
    )
    return TemporalProjection(
        object_ref=relation_id,
        recorded_at=transaction,
        subject_recorded_at=subject_recorded,
        effective=effective,
        perspective=perspective,
        metadata={
            "relation_type": relation.get("relation_type"),
            "from_ref": relation.get("from_ref"),
            "to_ref": relation.get("to_ref"),
            "projection_kind": "relation",
            "temporal_assertion_is_separate_event": transaction.instant != subject_recorded.instant,
        },
    )
