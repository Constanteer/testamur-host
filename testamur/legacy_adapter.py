from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .contracts import ObjectKind, classify_object_ref


_LEGACY_RANDOM_ID_RE = re.compile(r"^[a-z][a-z0-9-]*_[0-9a-f]{16}$")


class ReferenceNamespace(StrEnum):
    TESTAMUR = "testamur"
    HISTORICAL_WIRE = "historical_wire"
    LEGACY_RANDOM_ID = "legacy_random_id"
    OPAQUE = "opaque"


class WriteDisposition(StrEnum):
    CANONICAL_NEW_WRITE = "canonical_new_write"
    HISTORICAL_PROTOCOL_COMPATIBILITY = "historical_protocol_compatibility"
    LEGACY_READ_COMPATIBILITY = "legacy_read_compatibility"
    OPAQUE_EXTERNAL_REFERENCE = "opaque_external_reference"


@dataclass(frozen=True, slots=True)
class ResolvedReference:
    raw: str
    namespace: ReferenceNamespace
    write_disposition: WriteDisposition
    durable_kind: ObjectKind | None = None

    @property
    def canonical_for_new_writes(self) -> bool:
        return self.write_disposition is WriteDisposition.CANONICAL_NEW_WRITE

    def to_json(self) -> dict[str, object]:
        return {
            "ref": self.raw,
            "namespace": self.namespace.value,
            "write_disposition": self.write_disposition.value,
            "durable_kind": None if self.durable_kind is None else self.durable_kind.value,
            "canonical_for_new_writes": self.canonical_for_new_writes,
        }


def resolve_reference(value: str) -> ResolvedReference:
    """Classify canonical and historical references during namespace migration.

    This resolver is deliberately syntactic. Recognizing a namespace or ID
    shape does not assert that an object exists, that provenance is valid, or
    that a historical object has been projected into canonical Testamur storage.

    New durable/runtime object families use ``tst:*``. Historical runtime
    ``wtn:*`` identities are read-only compatibility references; the canonical
    Testamur runtime no longer creates them. Random legacy IDs (``wrr_...``,
    ``wap_...``, etc.) are compatibility references only.
    """

    raw = str(value).strip()
    if not raw:
        raise ValueError("reference must not be empty")

    classified = classify_object_ref(raw)
    if raw.startswith("tst:"):
        return ResolvedReference(
            raw=raw,
            namespace=ReferenceNamespace.TESTAMUR,
            write_disposition=WriteDisposition.CANONICAL_NEW_WRITE,
            durable_kind=classified.kind if classified.durable else None,
        )

    if raw.startswith("wtn:"):
        return ResolvedReference(
            raw=raw,
            namespace=ReferenceNamespace.HISTORICAL_WIRE,
            write_disposition=WriteDisposition.HISTORICAL_PROTOCOL_COMPATIBILITY,
            durable_kind=classified.kind if classified.durable else None,
        )

    if _LEGACY_RANDOM_ID_RE.fullmatch(raw):
        return ResolvedReference(
            raw=raw,
            namespace=ReferenceNamespace.LEGACY_RANDOM_ID,
            write_disposition=WriteDisposition.LEGACY_READ_COMPATIBILITY,
        )

    return ResolvedReference(
        raw=raw,
        namespace=ReferenceNamespace.OPAQUE,
        write_disposition=WriteDisposition.OPAQUE_EXTERNAL_REFERENCE,
        durable_kind=classified.kind if classified.durable else None,
    )


__all__ = [
    "ReferenceNamespace",
    "ResolvedReference",
    "WriteDisposition",
    "resolve_reference",
]
