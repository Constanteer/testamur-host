from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from .advisory_review import project_advisory_review
from .affectedness import TestamurAffectednessStore


def project_advisory_reviews(
    database_path: str | Path,
    candidates: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Compose project advisory candidates with current affectedness heads.

    This is a read projection only. Exact component/advisory identity overlap remains
    a candidate observation; immutable W5 assessments remain the authority for
    recorded applicability conclusions. Competing unsuperseded heads are preserved
    by ``project_advisory_review`` rather than collapsed by insertion order.
    """

    store = TestamurAffectednessStore(database_path)
    reviews: list[dict[str, Any]] = []
    heads_by_event: dict[str, list[dict[str, Any]]] = {}

    for raw in candidates:
        candidate = dict(raw)
        event_revision_id = str(candidate.get("event_revision_id") or "").strip()
        if not event_revision_id:
            raise ValueError("candidate.event_revision_id must not be empty")
        heads = heads_by_event.get(event_revision_id)
        if heads is None:
            heads = store.current_heads_for_event(event_revision_id)
            heads_by_event[event_revision_id] = heads
        reviews.append(project_advisory_review(candidate, heads))

    reviews.sort(
        key=lambda item: (
            str(item.get("provider") or ""),
            str(item.get("external_id") or ""),
            str(item.get("event_revision_id") or ""),
        )
    )
    return reviews
