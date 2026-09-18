from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .record_store import TestamurRecordStore

AssuranceReader = Callable[[str], Iterable[Mapping[str, Any]]]


class RecordPolicyEvidenceView:
    """W1-owned bridge from canonical Record/Relation rows into W3 policy evaluation.

    The adapter does not create a second object graph. Stable Record identity,
    immutable RecordRevision identity, and Relation provenance remain owned by
    ``TestamurRecordStore``. Assurance evidence is injected explicitly because
    checker/affectedness/other assurance families retain their own semantics.

    Recording a Record is not verification, a Relation is not causality, and an
    assurance reader returning evidence does not make that evidence universally
    true. ``PolicyEngine`` remains the sole owner of purpose/scope evaluation.
    """

    def __init__(
        self,
        records: TestamurRecordStore,
        *,
        assurance_reader: AssuranceReader | None = None,
    ) -> None:
        self.records = records
        self.assurance_reader = assurance_reader or (lambda _ref: ())

    def object_state(self, object_ref: str) -> Mapping[str, Any]:
        record = self.records.get_record(object_ref)
        if record is None:
            raise KeyError(object_ref)
        latest = self.records.latest_revision(object_ref)
        if latest is None:
            raise KeyError(object_ref)
        # PolicyEngine expects a semantic kind on the object state. Preserve the
        # Record's declared kind; do not substitute the storage family "record".
        return {
            **record,
            "kind": record["record_kind"],
            "current_revision_ref": latest["revision_id"],
            "statement": latest["statement"],
            "basis": list(latest.get("basis") or []),
            "semantics": {
                "recorded_is_verified": False,
                "recorded_is_true": False,
                "current_revision_is_exact": True,
            },
        }

    def revision_ref(self, object_ref: str) -> str | None:
        latest = self.records.latest_revision(object_ref)
        return None if latest is None else str(latest["revision_id"])

    def assurances_for(self, object_ref: str) -> Iterable[Mapping[str, Any]]:
        # Materialize once so callers cannot observe a generator changing during
        # one policy evaluation traversal.
        return [dict(item) for item in self.assurance_reader(object_ref)]

    def outgoing_relations(self, object_ref: str) -> Iterable[Mapping[str, Any]]:
        return self.records.relations_for(object_ref, direction="outgoing")

    def incoming_relations(self, object_ref: str) -> Iterable[Mapping[str, Any]]:
        return self.records.relations_for(object_ref, direction="incoming")
