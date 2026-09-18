from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


class ObjectKind(StrEnum):
    """Stable public kinds for the inspectable Testamur surface.

    The first group is implemented by the current local kernels. The extension
    kinds below are the canonical identity families used while policy, agent,
    temporal and lineage functionality converges from the legacy Witness
    substrate. Recognizing an ID is syntactic only; it does not claim a row
    exists or that the referenced statement is true.
    """

    RUN = "run"
    OBSERVATION = "observation"
    VERIFICATION = "verification"
    SOURCE = "source"
    REVISION = "revision"
    SNAPSHOT = "snapshot"
    RECORD = "record"
    RECORD_REVISION = "record_revision"
    RELATION = "relation"
    WATCH = "watch"
    WATCH_REVISION = "watch_revision"
    WATCH_EVALUATION = "watch_evaluation"
    ALERT = "alert"

    WORK_SESSION = "work_session"
    RELIANCE = "reliance"
    POLICY = "policy"
    ASSESSMENT = "assessment"
    LINEAGE = "lineage"
    AFFECTEDNESS = "affectedness"
    ADVISORY = "advisory"
    ATTESTATION = "attestation"

    TARGET = "target"


EXTENSION_OBJECT_KINDS = frozenset(
    {
        ObjectKind.WORK_SESSION,
        ObjectKind.RELIANCE,
        ObjectKind.POLICY,
        ObjectKind.ASSESSMENT,
        ObjectKind.LINEAGE,
        ObjectKind.AFFECTEDNESS,
        ObjectKind.ADVISORY,
        ObjectKind.ATTESTATION,
    }
)


_DURABLE_PREFIXES: tuple[tuple[str, ObjectKind], ...] = (
    ("tst:run:", ObjectKind.RUN),
    # Historical runtime references remain syntactically readable. New runtime
    # writes use tst:run:*; wtn:run:* is compatibility input only.
    ("wtn:run:", ObjectKind.RUN),
    ("tst:obs:", ObjectKind.OBSERVATION),
    ("tst:verification:", ObjectKind.VERIFICATION),
    ("tst:source:", ObjectKind.SOURCE),
    ("tst:revision:", ObjectKind.REVISION),
    ("tst:snapshot:", ObjectKind.SNAPSHOT),
    ("tst:record-revision:", ObjectKind.RECORD_REVISION),
    ("tst:record:", ObjectKind.RECORD),
    ("tst:relation:", ObjectKind.RELATION),
    ("tst:watch-revision:", ObjectKind.WATCH_REVISION),
    ("tst:watch-eval:", ObjectKind.WATCH_EVALUATION),
    ("tst:watch:", ObjectKind.WATCH),
    ("tst:alert:", ObjectKind.ALERT),
    ("tst:work-session:", ObjectKind.WORK_SESSION),
    ("tst:reliance:", ObjectKind.RELIANCE),
    ("tst:policy:", ObjectKind.POLICY),
    ("tst:assessment:", ObjectKind.ASSESSMENT),
    ("tst:lineage:", ObjectKind.LINEAGE),
    ("tst:affectedness:", ObjectKind.AFFECTEDNESS),
    ("tst:advisory:", ObjectKind.ADVISORY),
    ("tst:attestation:", ObjectKind.ATTESTATION),
)


@dataclass(frozen=True, slots=True)
class ObjectRef:
    """A syntactic Testamur object/target reference.

    Classification is intentionally syntactic. It does not claim the referenced
    object exists. Non-durable values remain generic ``target`` references so a
    caller may resolve them as artifact paths or domain/logical references.
    """

    kind: ObjectKind
    value: str
    durable: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "ref": self.value,
            "durable": self.durable,
        }


def classify_object_ref(value: str) -> ObjectRef:
    raw = str(value)
    if not raw:
        raise ValueError("object reference must not be empty")
    for prefix, kind in _DURABLE_PREFIXES:
        if raw.startswith(prefix):
            if len(raw) == len(prefix):
                raise ValueError(f"incomplete durable object reference: {raw}")
            return ObjectRef(kind=kind, value=raw, durable=True)
    return ObjectRef(kind=ObjectKind.TARGET, value=raw, durable=False)


def durable_object_kind(value: str) -> ObjectKind | None:
    ref = classify_object_ref(value)
    return ref.kind if ref.durable else None


def error_envelope(
    code: str,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the shared machine-readable Testamur error shape.

    Human CLI renderers may choose different prose, but machine surfaces should
    converge on this envelope instead of inventing command-specific structures.
    """

    raw_code = str(code).strip()
    raw_message = str(message)
    if not raw_code:
        raise ValueError("error code must not be empty")
    error: dict[str, Any] = {
        "code": raw_code,
        "message": raw_message,
    }
    if details is not None:
        error["details"] = dict(details)
    return {
        "ok": False,
        "schema": "testamur.error.v1",
        "error": error,
    }


def object_envelope(
    ref: ObjectRef,
    payload: Mapping[str, Any],
    *,
    schema: str = "testamur.object.v1",
) -> dict[str, Any]:
    """Wrap a read result without changing the underlying evidence payload."""

    raw_schema = str(schema).strip()
    if not raw_schema:
        raise ValueError("schema must not be empty")
    return {
        "ok": True,
        "schema": raw_schema,
        "object": ref.to_json(),
        "data": dict(payload),
    }
