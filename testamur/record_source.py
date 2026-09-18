from __future__ import annotations

from typing import Any, Mapping

from .runtime_protocol import canonical_json

from .record_store import TestamurRecordStore
from .source_store import TestamurSourceStore


def create_record_from_source_revision(
    records: TestamurRecordStore,
    sources: TestamurSourceStore,
    *,
    source_revision_id: str,
    statement: str,
    record_kind: str = "assertion",
    snapshot_id: str | None = None,
    region: Any | None = None,
    title: str | None = None,
    created_by: str | None = None,
    record_id: str | None = None,
) -> dict[str, Any]:
    """Create a persistent Record pinned to an existing exact Source Revision.

    This is the kernel form of the early product interaction:

        source snapshot/revision → select region → create normalized Record

    The normalized Record statement remains a declaration distinct from source
    bytes. If a Snapshot is supplied it must actually observe the pinned Source
    Revision; this prevents a UI/API from pairing an exact revision with an
    unrelated capture event.
    """

    source_revision = sources.get_revision(source_revision_id)
    if source_revision is None:
        raise KeyError(source_revision_id)

    snapshot = None
    if snapshot_id is not None:
        snapshot = sources.get_snapshot(snapshot_id)
        if snapshot is None:
            raise KeyError(snapshot_id)
        if snapshot.get("source_id") != source_revision.get("source_id"):
            raise ValueError("snapshot belongs to a different Source")
        if snapshot.get("revision_id") != source_revision_id:
            raise ValueError("snapshot does not observe the pinned Source Revision")

    basis: dict[str, Any] = {
        "kind": "source_revision",
        "ref": source_revision_id,
        "source_ref": source_revision["source_id"],
        "content_hash": source_revision["content_hash"],
    }
    if snapshot is not None:
        basis["snapshot_ref"] = snapshot_id
        basis["observed_at"] = snapshot.get("observed_at")
    if region is not None:
        # Validate that the region/selection anchor is mechanically serializable.
        # Its domain semantics remain declared; Testamur does not pretend a
        # generic selector is universally meaningful across media types.
        canonical_json(region)
        basis["region"] = region

    created = records.create_record(
        record_kind=record_kind,
        statement=statement,
        basis=[basis],
        title=title,
        created_by=created_by,
        record_id=record_id,
    )
    created["source_binding"] = {
        "source_id": source_revision["source_id"],
        "source_revision_id": source_revision_id,
        "snapshot_id": snapshot_id,
        "region_pinned": region is not None,
        "semantics": {
            "record_statement_is_source_bytes": False,
            "source_revision_exists": True,
            "snapshot_revision_consistency_checked": snapshot_id is not None,
            "region_semantics_inferred": False,
        },
    }
    return created
