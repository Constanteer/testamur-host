from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


class RelianceReceiptView(Protocol):
    """Narrow structural view of W3's canonical RelianceStore public API."""

    def list(
        self,
        *,
        scope_ref: str,
        reliant_ref: str | None = None,
        object_ref: str | None = None,
    ) -> list[dict[str, Any]]: ...


def _required(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    return text


def _optional_string(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    return _required(value, field=field)


def _string_sequence(value: Any, *, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be a sequence of strings")
    return [_required(item, field=f"{field}[]") for item in value]


def _pinned_revisions(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("reliance receipt pinned_revisions must be a mapping")
    result: dict[str, str] = {}
    for raw_object_ref, raw_revision_ref in value.items():
        object_ref = _required(raw_object_ref, field="pinned_revisions.object_ref")
        revision_ref = _required(
            raw_revision_ref, field=f"pinned_revisions[{object_ref}]"
        )
        result[object_ref] = revision_ref
    if not result:
        raise ValueError("reliance receipt pinned_revisions must not be empty")
    return result


@dataclass(slots=True)
class RevisionPinnedRelianceResolver:
    """Resolve W5 affected revision refs against W3 exact reliance receipts.

    W3 receipts pin ``object_ref -> revision_ref``. W5 assessments identify an
    affected exact revision. Therefore matching must inspect the *values* of the
    receipt's ``pinned_revisions`` map; passing revision refs to W3's object-keyed
    ``blast_radius(changed_object_refs=...)`` would be semantically wrong.

    This adapter uses only W3's public ``list`` output and has no runtime import of
    the W3 module, allowing worker branches to remain independent until W1 merges
    them. A policy filter, when supplied, matches the receipt's pinned policy IDs.

    Receipt shape violations fail closed. Silently skipping a malformed receipt
    could erase real durable reliance and manufacture a false-negative blast radius.
    Exact matching also rejects implicit string coercion: revision/object/policy
    references must already conform to W3's canonical string-valued receipt schema.
    """

    store: RelianceReceiptView
    scope_ref: str

    def __post_init__(self) -> None:
        self.scope_ref = _required(self.scope_ref, field="scope_ref")

    def blast_radius_for_refs(
        self,
        refs: Sequence[str],
        *,
        policy_ref: str | None = None,
    ) -> dict[str, Any]:
        affected_revisions = sorted(set(_string_sequence(refs, field="affected_revision_refs")))
        affected_set = set(affected_revisions)
        policy = (
            None
            if policy_ref is None
            else _required(policy_ref, field="policy_ref")
        )

        affected_receipts: list[dict[str, Any]] = []
        affected_reliants: set[str] = set()
        for receipt in self.store.list(scope_ref=self.scope_ref):
            if not isinstance(receipt, Mapping):
                raise ValueError("reliance store returned a non-mapping receipt")

            receipt_id = _required(receipt.get("receipt_id"), field="receipt_id")
            reliant_ref = _required(receipt.get("reliant_ref"), field="reliant_ref")
            reliant_revision_ref = _optional_string(
                receipt.get("reliant_revision_ref"), field="reliant_revision_ref"
            )
            relied_object_ref = _required(receipt.get("object_ref"), field="object_ref")
            purpose = _required(receipt.get("purpose"), field="purpose")
            policy_ids = _string_sequence(
                receipt.get("policy_ids"), field="policy_ids"
            )
            pinned = _pinned_revisions(receipt.get("pinned_revisions"))

            if policy is not None and policy not in policy_ids:
                continue

            hits = [
                {
                    "object_ref": object_ref,
                    "revision_ref": revision_ref,
                }
                for object_ref, revision_ref in sorted(pinned.items())
                if revision_ref in affected_set
            ]
            if not hits:
                continue

            affected_reliants.add(reliant_ref)
            affected_receipts.append(
                {
                    "receipt_id": receipt_id,
                    "reliant_ref": reliant_ref,
                    "reliant_revision_ref": reliant_revision_ref,
                    "relied_object_ref": relied_object_ref,
                    "purpose": purpose,
                    "policy_ids": policy_ids,
                    "affected_pins": hits,
                    "currently_stale": bool(receipt.get("stale", False)),
                    "currently_admissible": receipt.get("current_admissible"),
                    "issued_at": receipt.get("created_at"),
                }
            )

        affected_receipts.sort(
            key=lambda item: (
                item["reliant_ref"],
                item["purpose"],
                item["receipt_id"],
            )
        )
        return {
            "scope_ref": self.scope_ref,
            "policy_ref": policy,
            "affected_revision_refs": affected_revisions,
            "affected_receipt_count": len(affected_receipts),
            "affected_reliant_refs": sorted(affected_reliants),
            "affected_receipts": affected_receipts,
            "semantics": {
                "actual_durable_reliance_only": True,
                "match_is_exact_pinned_revision": True,
                "exact_match_rejects_string_coercion": True,
                "scalar_revision_collection_is_rejected": True,
                "malformed_receipt_is_unaffected": False,
                "stale_or_inadmissible_erases_historical_reliance": False,
                "affectedness_implies_downstream_false": False,
                "review_obligation_only": True,
            },
        }
