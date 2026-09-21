from __future__ import annotations

import json
from typing import Any, Iterable

from .advisory import TestamurAdvisoryStore, _refs


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
    with store.connect() as conn:
        rows = conn.execute(
            """SELECT revision_json FROM testamur_advisory_revisions AS r
               WHERE r.rowid = (
                 SELECT r2.rowid FROM testamur_advisory_revisions AS r2
                 WHERE r2.event_id = r.event_id
                 ORDER BY r2.rowid DESC LIMIT 1
               ) ORDER BY r.rowid DESC"""
        ).fetchall()
    matches: list[dict[str, Any]] = []
    for row in rows:
        revision = json.loads(str(row["revision_json"]))
        revision_refs = {
            str(value)
            for value in revision.get("upstream_refs") or []
            if isinstance(value, str) and value
        }
        if refs & revision_refs:
            matches.append(revision)
    return matches
