from __future__ import annotations

from typing import Any

from .graph_query import TestamurQuery
from .relations import RelationIndex
from .runtime_service import EmbeddedRuntimeClient
from .inspector import TestamurInspector


def local_query(
    client: EmbeddedRuntimeClient,
    object_id: str,
    operation: str,
) -> dict[str, Any]:
    """Query historical semantic-graph objects through canonical Testamur modules.

    This is a compatibility read surface for durable graph objects already stored
    in the shared SQLite database. It does not turn graph relations into durable
    reliance and does not infer truth, invalidity, or verification from recording.
    """
    store = client.store
    with store.connect() as conn:
        # Legacy table identity is consumed only as storage compatibility. New
        # Python/runtime surfaces use Testamur names exclusively.
        row = conn.execute(
            "SELECT project_id FROM witness_objects WHERE id=?", (str(object_id),)
        ).fetchone()
    if row is None:
        raise KeyError(object_id)
    project_id = str(row["project_id"])
    relations = RelationIndex(store)
    inspector = TestamurInspector(store, relations)
    query = TestamurQuery(store, relations, inspector)
    if operation == "why":
        return query.why_believe(project_id, object_id)
    if operation == "trace":
        return query.what_produced(project_id, object_id)
    if operation == "impact":
        return query.invalidation_impact(project_id, object_id)
    raise ValueError(operation)


_local_query = local_query

__all__ = ["local_query"]
