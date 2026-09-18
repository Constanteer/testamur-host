from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


_REQUIRED_LEGACY_RECEIPT_FIELDS = {
    "id", "project_id", "object_id", "purpose", "decision", "decision_hash",
    "pinned_revisions", "assurance_run_ids", "policy_ids", "edge_ids",
    "topology_fingerprint", "created_at",
}


def _required(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    return text


def project_legacy_reliance_receipt(receipt: Mapping[str, Any], *, source_namespace: str = "witness") -> Any:
    """Project one legacy immutable receipt through W1's canonical bridge."""
    missing = sorted(_REQUIRED_LEGACY_RECEIPT_FIELDS - set(receipt))
    if missing:
        raise ValueError("reliance receipt missing fields: " + ", ".join(missing))
    source_ref = str(receipt["id"]).strip()
    if not source_ref:
        raise ValueError("reliance receipt id must not be empty")
    try:
        from .contracts import ObjectKind
        from .object_projection import project_legacy_extension
    except (ImportError, ModuleNotFoundError) as exc:
        raise RuntimeError("legacy reliance projection requires W1 canonical object_projection APIs") from exc
    reliance_kind = getattr(ObjectKind, "RELIANCE", None)
    if reliance_kind is None:
        raise RuntimeError("legacy reliance projection requires W1 canonical ObjectKind.RELIANCE")
    return project_legacy_extension(
        reliance_kind,
        identity={"source_namespace": str(source_namespace), "source_ref": source_ref},
        payload=dict(receipt), source_ref=source_ref, source_namespace=source_namespace,
        source_schema=str(receipt.get("schema_version") or "witness-reliance-v0.1"),
        provenance={"projection": "legacy_reliance_receipt", "identity_preserved": True, "decision_revalidated": False},
    )


class CallbackEvidenceView:
    """Small canonical adapter surface for W1/W4/W5 integration."""
    def __init__(self, *, object_state: Callable[[str], Mapping[str, Any]], revision_ref: Callable[[str], str | None], assurances_for: Callable[[str], Iterable[Mapping[str, Any]]], outgoing_relations: Callable[[str], Iterable[Mapping[str, Any]]], incoming_relations: Callable[[str], Iterable[Mapping[str, Any]]]) -> None:
        self._object_state = object_state
        self._revision_ref = revision_ref
        self._assurances_for = assurances_for
        self._outgoing_relations = outgoing_relations
        self._incoming_relations = incoming_relations

    def object_state(self, object_ref: str) -> Mapping[str, Any]: return self._object_state(object_ref)
    def revision_ref(self, object_ref: str) -> str | None: return self._revision_ref(object_ref)
    def assurances_for(self, object_ref: str) -> Iterable[Mapping[str, Any]]: return self._assurances_for(object_ref)
    def outgoing_relations(self, object_ref: str) -> Iterable[Mapping[str, Any]]: return self._outgoing_relations(object_ref)
    def incoming_relations(self, object_ref: str) -> Iterable[Mapping[str, Any]]: return self._incoming_relations(object_ref)


class RecordStoreEvidenceView:
    """Compatibility shim to W1's canonical Record→Policy evidence adapter.

    W1 owns Record/Relation projection semantics. Keeping a second implementation
    here would let the same Record substrate produce different policy evidence
    depending on import path. This historical W3 name therefore delegates to
    ``testamur.policy_evidence.RecordPolicyEvidenceView`` once the W1 convergence
    files are present. ``CallbackEvidenceView`` remains available when W3 is run
    independently against another evidence provider.
    """

    def __init__(
        self,
        record_store: Any,
        *,
        assurance_reader: Callable[[str], Iterable[Mapping[str, Any]]] | None = None,
    ) -> None:
        try:
            from .policy_evidence import RecordPolicyEvidenceView
        except (ImportError, ModuleNotFoundError) as exc:
            raise RuntimeError(
                "RecordStoreEvidenceView requires W1 canonical "
                "testamur.policy_evidence.RecordPolicyEvidenceView"
            ) from exc
        self._delegate = RecordPolicyEvidenceView(
            record_store,
            assurance_reader=assurance_reader,
        )

    def object_state(self, object_ref: str) -> Mapping[str, Any]:
        return self._delegate.object_state(object_ref)

    def revision_ref(self, object_ref: str) -> str | None:
        return self._delegate.revision_ref(object_ref)

    def assurances_for(self, object_ref: str) -> Iterable[Mapping[str, Any]]:
        return self._delegate.assurances_for(object_ref)

    def outgoing_relations(self, object_ref: str) -> Iterable[Mapping[str, Any]]:
        return self._delegate.outgoing_relations(object_ref)

    def incoming_relations(self, object_ref: str) -> Iterable[Mapping[str, Any]]:
        return self._delegate.incoming_relations(object_ref)


@dataclass(frozen=True, slots=True)
class RelianceCommitResultView:
    """Structural result compatible with W4 ``RelianceCommitResult``."""
    reliance_id: str


class AffectednessRelianceHook:
    """W5 resolver over exact durable reliance, without importing W5.

    W5 supplies changed canonical refs and may optionally narrow impact to one
    frozen policy identity. ``policy_ref`` is never a project/scope identity.
    Historical receipt basis is authoritative for blast radius, so unavailable
    current evidence cannot erase reliance. The result creates reconsideration
    obligations only; it never manufactures affectedness or truth verdicts.
    """

    def __init__(self, reliance_store: Any, *, scope_ref: str | None = None) -> None:
        if not callable(getattr(reliance_store, "blast_radius", None)):
            raise TypeError("affectedness reliance hook requires canonical blast_radius")
        self.reliance_store = reliance_store
        self.scope_ref = None if scope_ref is None else _required(scope_ref, "scope_ref")

    def _scope(self) -> str:
        if self.scope_ref is None:
            raise ValueError("W5 blast_radius_for_refs requires a configured scope_ref")
        return self.scope_ref

    def _propagation_result(
        self, *, scope_ref: str, changed_object_refs: Iterable[str]
    ) -> dict[str, Any]:
        scope = _required(scope_ref, "scope_ref")
        changed = sorted({_required(item, "changed_object_ref") for item in changed_object_refs})
        result = self.reliance_store.blast_radius(
            scope_ref=scope,
            changed_object_refs=changed,
        )
        if str(result.get("scope_ref") or "") != scope:
            raise RuntimeError("reliance blast radius returned a different scope")
        returned = sorted(map(str, result.get("changed_object_refs") or []))
        if returned != changed:
            raise RuntimeError("reliance blast radius changed the requested object basis")
        return {
            **result,
            "propagation": {
                "source": "w5-affectedness-or-lineage-change",
                "changed_object_refs": changed,
                "effect": "reconsideration_required",
                "affectedness_verdict_inferred": False,
                "truth_verdict_inferred": False,
            },
        }

    def _policy_filtered(self, result: Mapping[str, Any], policy_ref: str) -> dict[str, Any]:
        policy = _required(policy_ref, "policy_ref")
        kept: list[dict[str, Any]] = []
        for item in result.get("affected_receipts", []):
            if not isinstance(item, Mapping):
                continue
            receipt_id = _required(item.get("receipt_id"), "affected_receipt.receipt_id")
            receipt = self.reliance_store.get(receipt_id)
            if receipt is None:
                raise RuntimeError("blast radius referenced a missing immutable reliance receipt")
            if policy not in {str(value) for value in receipt.get("policy_ids", [])}:
                continue
            kept.append(dict(item))
        purposes: Counter[str] = Counter(str(item.get("purpose") or "") for item in kept)
        reliant_refs = sorted({str(item.get("reliant_ref") or "") for item in kept if str(item.get("reliant_ref") or "")})
        original_affected = int(result.get("affected_receipt_count") or 0)
        original_unaffected = int(result.get("unaffected_receipt_count") or 0)
        return {
            **dict(result),
            "policy_ref": policy,
            "policy_filter_applied": True,
            "affected_receipt_count": len(kept),
            "unaffected_receipt_count": original_unaffected + original_affected - len(kept),
            "affected_reliant_refs": reliant_refs,
            "purpose_counts": dict(sorted((key, value) for key, value in purposes.items() if key)),
            "affected_receipts": kept,
            "semantics": {
                **dict(result.get("semantics") or {}),
                "policy_ref_is_not_scope_ref": True,
                "policy_filter_applied": True,
                "policy_filter_uses_frozen_receipt_basis": True,
            },
        }

    def propagate(
        self, *, scope_ref: str, changed_object_refs: Iterable[str]
    ) -> dict[str, Any]:
        """Explicit-scope W3 API retained for callers outside W5."""
        return self._propagation_result(
            scope_ref=scope_ref,
            changed_object_refs=changed_object_refs,
        )

    def blast_radius_for_refs(
        self, refs: Sequence[str], *, policy_ref: str | None = None
    ) -> dict[str, Any]:
        """Implement W5 ``RelianceImpactResolver`` structurally."""
        result = self._propagation_result(
            scope_ref=self._scope(),
            changed_object_refs=refs,
        )
        if policy_ref is None:
            return result
        return self._policy_filtered(result, policy_ref)


class WorkSessionRelianceSink:
    """W4 ``RelianceSink`` backed by immutable canonical reliance receipts.

    The WorkSession decision pins an observed source revision. Its decision ID is
    also used as the store-level idempotency key, so concurrent/restarted retries
    converge in SQLite rather than relying on a list-and-scan race. Exposure is
    never promoted to reliance without an explicit W4 commit request.
    """
    def __init__(self, reliance_store: Any, *, default_scope_ref: str | None = None, source_object_ref_for_revision: Callable[[str], str] | None = None) -> None:
        self.reliance_store = reliance_store
        self.default_scope_ref = None if default_scope_ref is None else _required(default_scope_ref, "default_scope_ref")
        self.source_object_ref_for_revision = source_object_ref_for_revision

    def _scope_for(self, request: Any) -> str:
        request_scope = getattr(request, "project_ref", None)
        if request_scope is not None and str(request_scope).strip(): return str(request_scope).strip()
        if self.default_scope_ref is not None: return self.default_scope_ref
        raise ValueError("WorkSession reliance commit requires project_ref or default_scope_ref")

    def _source_ref(self, source_revision_id: str) -> str:
        if self.source_object_ref_for_revision is None: return source_revision_id
        return _required(self.source_object_ref_for_revision(source_revision_id), "source_object_ref_for_revision result")

    @staticmethod
    def _idempotency_key(decision_id: str) -> str:
        return "work-session-decision:" + decision_id

    @classmethod
    def _validate_request_idempotency_key(cls, request: Any, decision_id: str) -> str:
        canonical = cls._idempotency_key(decision_id)
        supplied = getattr(request, "idempotency_key", None)
        if supplied is not None and _required(supplied, "idempotency_key") != canonical:
            raise ValueError("WorkSession idempotency_key does not match decision_id")
        return canonical

    @staticmethod
    def _request_metadata(request: Any, *, decision_id: str, source_revision_id: str) -> tuple[str, dict[str, Any]]:
        actor_ref = _required(getattr(request, "actor_ref", None), "actor_ref")
        metadata = {"work_session": {
            "work_session_id": _required(getattr(request, "work_session_id", None), "work_session_id"),
            "reconciliation_id": _required(getattr(request, "reconciliation_id", None), "reconciliation_id"),
            "decision_id": decision_id,
            "relation_type": _required(getattr(request, "relation_type", None), "relation_type"),
            "source_revision_id": source_revision_id,
            "evidence_refs": [str(item) for item in getattr(request, "evidence_refs", ())],
            "evidence_class": _required(getattr(request, "evidence_class", None), "evidence_class"),
            "reconciliation_policy": _required(getattr(request, "reconciliation_policy", None), "reconciliation_policy"),
        }}
        return actor_ref, metadata

    def _existing_for_decision(self, *, scope_ref: str, decision_id: str) -> dict[str, Any] | None:
        getter = getattr(self.reliance_store, "get_by_idempotency_key", None)
        if getter is None:
            return None
        return getter(scope_ref=scope_ref, idempotency_key=self._idempotency_key(decision_id))

    @staticmethod
    def _validate_existing(
        existing: Mapping[str, Any], *, reliant_ref: str, source_ref: str,
        source_revision_id: str, purpose: str, actor_ref: str,
        metadata: Mapping[str, Any],
    ) -> None:
        stored_metadata = existing.get("metadata")
        stored_work_session = (
            stored_metadata.get("work_session")
            if isinstance(stored_metadata, Mapping)
            else None
        )
        expected_work_session = metadata.get("work_session")
        if (
            str(existing.get("reliant_ref") or "") != reliant_ref
            or str(existing.get("object_ref") or "") != source_ref
            or str(existing.get("purpose") or "") != purpose
            or existing.get("pinned_revisions", {}).get(source_ref) != source_revision_id
            or str(existing.get("actor_ref") or "") != actor_ref
            or not isinstance(stored_work_session, Mapping)
            or dict(stored_work_session) != dict(expected_work_session or {})
        ):
            raise ValueError("WorkSession decision_id is already bound to a different reliance receipt")

    def commit_reliance(self, request: Any) -> RelianceCommitResultView:
        scope_ref = self._scope_for(request)
        decision_id = _required(getattr(request, "decision_id", None), "decision_id")
        idempotency_key = self._validate_request_idempotency_key(request, decision_id)
        reliant_ref = _required(getattr(request, "project_object_ref", None), "project_object_ref")
        source_revision_id = _required(getattr(request, "source_revision_id", None), "source_revision_id")
        purpose = _required(getattr(request, "used_for", None), "used_for")
        source_ref = self._source_ref(source_revision_id)
        actor_ref, metadata = self._request_metadata(
            request,
            decision_id=decision_id,
            source_revision_id=source_revision_id,
        )
        existing = self._existing_for_decision(scope_ref=scope_ref, decision_id=decision_id)
        if existing is not None:
            self._validate_existing(
                existing,
                reliant_ref=reliant_ref,
                source_ref=source_ref,
                source_revision_id=source_revision_id,
                purpose=purpose,
                actor_ref=actor_ref,
                metadata=metadata,
            )
            return RelianceCommitResultView(str(existing["receipt_id"]))

        current_revision = self.reliance_store.engine.evidence.revision_ref(source_ref)
        if current_revision != source_revision_id:
            raise ValueError(f"source revision changed before reliance commit: expected {source_revision_id!r}, current {current_revision!r}")

        try:
            receipt = self.reliance_store.issue(
                scope_ref=scope_ref, reliant_ref=reliant_ref, object_ref=source_ref,
                purpose=purpose, actor_ref=actor_ref,
                metadata=metadata, idempotency_key=idempotency_key,
            )
        except (ValueError, sqlite3.IntegrityError):
            # A concurrent retry may win the unique idempotency insert. Re-read
            # and accept only the exact immutable decision and its provenance.
            existing = self._existing_for_decision(scope_ref=scope_ref, decision_id=decision_id)
            if existing is None:
                raise
            self._validate_existing(
                existing,
                reliant_ref=reliant_ref,
                source_ref=source_ref,
                source_revision_id=source_revision_id,
                purpose=purpose,
                actor_ref=actor_ref,
                metadata=metadata,
            )
            receipt = existing
        if receipt["pinned_revisions"].get(source_ref) != source_revision_id:
            raise RuntimeError("reliance receipt lost WorkSession source revision pin")
        return RelianceCommitResultView(str(receipt["receipt_id"]))


__all__ = ["AffectednessRelianceHook", "CallbackEvidenceView", "RecordStoreEvidenceView", "RelianceCommitResultView", "WorkSessionRelianceSink", "project_legacy_reliance_receipt"]
