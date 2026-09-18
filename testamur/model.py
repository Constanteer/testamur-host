from __future__ import annotations

"""Canonical home for the legacy semantic vocabulary and identity utilities.

This module preserves the historical Witness Core wire vocabulary while Python
ownership moves to ``testamur``. Its ``ObjectKind`` is the *semantic graph kind*
vocabulary (claim/evidence/artifact/etc.), not the durable Testamur object-family
classifier in :mod:`testamur.contracts`. Keeping those concepts distinct avoids
silently equating a semantic role with a persisted object family.

Historical schema/version strings are intentionally retained for compatibility;
namespace migration alone must not rewrite existing hashes or stored records.
"""

import hashlib
import json
import re
import secrets
from enum import StrEnum
from typing import Any


SCHEMA_VERSION = "witness-core-v0.1"


class ObjectKind(StrEnum):
    INTENT = "intent"
    CLAIM = "claim"
    REQUIREMENT = "requirement"
    ASSUMPTION = "assumption"
    EVIDENCE = "evidence"
    COUNTEREVIDENCE = "counterevidence"
    UNCERTAINTY = "uncertainty"
    ARTIFACT = "artifact"
    VERIFIER = "verifier"
    SOURCE = "source"
    METHOD = "method"
    ACTOR = "actor"
    DECISION = "decision"


# Explicit alias for new Testamur code. ``ObjectKind`` remains exported so
# historical consumers can migrate without a semantic or API break.
SemanticObjectKind = ObjectKind


class EdgeKind(StrEnum):
    DEPENDS_ON = "depends_on"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    ASSUMES = "assumes"
    VERIFIES = "verifies"
    DERIVED_FROM = "derived_from"
    IMPLEMENTS = "implements"
    INVALIDATES = "invalidates"
    ADDRESSES = "addresses"


SemanticEdgeKind = EdgeKind


class VerificationStatus(StrEnum):
    FORMALLY_PROVEN = "formally_proven"
    MECHANICALLY_CHECKED = "mechanically_checked"
    REPRODUCED = "reproduced"
    EMPIRICALLY_SUPPORTED = "empirically_supported"
    STATISTICALLY_SUPPORTED = "statistically_supported"
    SIMULATION_SUPPORTED = "simulation_supported"
    TESTED = "tested"
    REVIEWED = "reviewed"
    UNVERIFIED = "unverified"
    CONTRADICTED = "contradicted"


class VerifierClass(StrEnum):
    FORMAL_KERNEL = "formal_kernel"
    MECHANICAL = "mechanical"
    TEST_SUITE = "test_suite"
    EMPIRICAL = "empirical"
    STATISTICAL = "statistical"
    SIMULATION = "simulation"
    HUMAN_REVIEW = "human_review"


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_EXTENSION_KIND_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}(?:\.[a-z][a-z0-9_-]{0,31})+$")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def validate_slug(value: str) -> str:
    value = str(value or "").strip().lower()
    if not _SLUG_RE.fullmatch(value):
        raise ValueError("slug must match [a-z0-9][a-z0-9._-]{0,63}")
    return value


def validate_extension_kind(value: Any, label: str = "semantic kind") -> str:
    value = str(value or "").strip().lower()
    if not _EXTENSION_KIND_RE.fullmatch(value):
        raise ValueError(
            f"{label} must be a namespaced identifier such as engineering.measurement"
        )
    return value


def _normalize(enum: type[StrEnum], value: Any, label: str) -> str:
    try:
        return enum(str(value)).value
    except ValueError as exc:
        raise ValueError(
            f"unknown {label} {value!r}; expected one of: {', '.join(x.value for x in enum)}"
        ) from exc


def _normalize_extensible(enum: type[StrEnum], value: Any, label: str) -> str:
    raw = str(value)
    try:
        return enum(raw).value
    except ValueError:
        return validate_extension_kind(raw, label)


def normalize_object_kind(value: str | ObjectKind) -> str:
    return _normalize_extensible(ObjectKind, value, "Testamur semantic object kind")


def normalize_edge_kind(value: str | EdgeKind) -> str:
    return _normalize_extensible(EdgeKind, value, "Testamur semantic edge kind")


def normalize_verification_status(value: str | VerificationStatus) -> str:
    return _normalize(VerificationStatus, value, "verification status")


def normalize_verifier_class(value: str | VerifierClass) -> str:
    return _normalize(VerifierClass, value, "verifier class")


__all__ = [
    "SCHEMA_VERSION",
    "ObjectKind",
    "SemanticObjectKind",
    "EdgeKind",
    "SemanticEdgeKind",
    "VerificationStatus",
    "VerifierClass",
    "canonical_json",
    "canonical_hash",
    "new_id",
    "validate_slug",
    "validate_extension_kind",
    "normalize_object_kind",
    "normalize_edge_kind",
    "normalize_verification_status",
    "normalize_verifier_class",
]
