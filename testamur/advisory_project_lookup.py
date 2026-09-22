from __future__ import annotations

import json
from typing import Any, Iterable

from .advisory import TestamurAdvisoryStore, _refs


def _latest_advisory_revisions(store: TestamurAdvisoryStore) -> list[dict[str, Any]]:
    """Return every latest advisory revision without an inventory cap."""
    with store.connect() as conn:
        rows = conn.execute(
            """SELECT revision_json FROM testamur_advisory_revisions AS r
               WHERE r.rowid = (
                 SELECT r2.rowid FROM testamur_advisory_revisions AS r2
                 WHERE r2.event_id = r.event_id
                 ORDER BY r2.rowid DESC LIMIT 1
               ) ORDER BY r.rowid DESC"""
        ).fetchall()
    return [json.loads(str(row["revision_json"])) for row in rows]


def latest_advisory_revisions_for_upstream_refs(
    store: TestamurAdvisoryStore,
    upstream_refs: Iterable[str],
) -> list[dict[str, Any]]:
    """Return latest advisory revisions having exact upstream-ref overlap.

    This deliberately has no global advisory inventory limit. Exact overlap is
    candidate membership only; it does not infer affectedness, verification, or
    reliance.
    """
    refs = set(_refs(upstream_refs, field="upstream_refs"))
    if not refs:
        return []
    matches: list[dict[str, Any]] = []
    for revision in _latest_advisory_revisions(store):
        revision_refs = {
            str(value)
            for value in revision.get("upstream_refs") or []
            if isinstance(value, str) and value
        }
        if refs & revision_refs:
            matches.append(revision)
    return matches


def project_advisory_revisions_for_upstream_refs(
    store: TestamurAdvisoryStore,
    upstream_refs: Iterable[str],
) -> tuple[list[dict[str, Any]], int]:
    """Project exact candidates and unresolved identities without a global cap.

    The unresolved count is deliberately independent of candidate membership:
    an advisory with upstream identity but no exact upstream refs is recorded as
    unresolved and is never fuzzy-matched into the candidate set.
    """
    refs = set(_refs(upstream_refs, field="upstream_refs"))
    matches: list[dict[str, Any]] = []
    unresolved_count = 0
    for revision in _latest_advisory_revisions(store):
        revision_refs = {
            str(value)
            for value in revision.get("upstream_refs") or []
            if isinstance(value, str) and value
        }
        if not revision_refs:
            if revision.get("upstream_identity"):
                unresolved_count += 1
            continue
        if refs & revision_refs:
            matches.append(revision)
    return matches, unresolved_count
