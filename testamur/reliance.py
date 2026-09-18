from __future__ import annotations

import json
import sqlite3
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from .assessment import AssessmentState, PolicyEngine
from .policy import canonical_hash, canonical_json
from .reliance_basis import exact_basis_drift, exact_basis_snapshot

RELIANCE_SCHEMA_VERSION = "testamur.reliance.v1"
_RELIANCE_PREFIX = "tst:reliance:"
_RELIANT_REVISION_STATUSES = {
    "not_pinned",
    "observed_current",
    "observed_different",
    "not_assessable",
    "legacy_unknown",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _required(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must not be empty")
    return result


def _optional_revision(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip() or None


class RelianceStore:
    """Immutable exact-basis reliance receipts over canonical Testamur evidence."""

    def __init__(self, database_path: str | Path, engine: PolicyEngine) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = engine
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _create_table(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS testamur_reliance_receipts(
              receipt_id TEXT PRIMARY KEY,
              scope_ref TEXT NOT NULL,
              reliant_ref TEXT NOT NULL,
              reliant_revision_ref TEXT,
              reliant_revision_issuance_status TEXT NOT NULL DEFAULT 'legacy_unknown',
              object_ref TEXT NOT NULL,
              purpose TEXT NOT NULL,
              assessment_json TEXT NOT NULL,
              assessment_hash TEXT NOT NULL,
              pinned_revisions_json TEXT NOT NULL,
              assurance_ids_json TEXT NOT NULL,
              policy_ids_json TEXT NOT NULL,
              relation_ids_json TEXT NOT NULL,
              topology_fingerprint TEXT NOT NULL,
              actor_ref TEXT,
              metadata_json TEXT NOT NULL,
              idempotency_key TEXT,
              created_at TEXT NOT NULL
            )
            """
        )

    @staticmethod
    def _install_aux_schema(conn: sqlite3.Connection) -> None:
        conn.execute("""CREATE INDEX IF NOT EXISTS testamur_reliance_object_idx ON testamur_reliance_receipts(scope_ref,object_ref,purpose,created_at)""")
        conn.execute("""CREATE INDEX IF NOT EXISTS testamur_reliance_reliant_idx ON testamur_reliance_receipts(scope_ref,reliant_ref,created_at)""")
        conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS testamur_reliance_idempotency_idx ON testamur_reliance_receipts(scope_ref,idempotency_key) WHERE idempotency_key IS NOT NULL""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS testamur_reliance_receipts_no_update BEFORE UPDATE ON testamur_reliance_receipts BEGIN SELECT RAISE(ABORT, 'Testamur reliance receipts are immutable'); END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS testamur_reliance_receipts_no_delete BEFORE DELETE ON testamur_reliance_receipts BEGIN SELECT RAISE(ABORT, 'Testamur reliance receipts are immutable'); END""")

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._create_table(conn)
            columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(testamur_reliance_receipts)").fetchall()}
            if "reliant_revision_ref" not in columns:
                conn.execute("ALTER TABLE testamur_reliance_receipts ADD COLUMN reliant_revision_ref TEXT")
            if "reliant_revision_issuance_status" not in columns:
                conn.execute(
                    "ALTER TABLE testamur_reliance_receipts "
                    "ADD COLUMN reliant_revision_issuance_status TEXT NOT NULL "
                    "DEFAULT 'legacy_unknown'"
                )
            if "idempotency_key" not in columns:
                conn.execute("ALTER TABLE testamur_reliance_receipts ADD COLUMN idempotency_key TEXT")
            self._install_aux_schema(conn)

    @staticmethod
    def _serialize(row: sqlite3.Row) -> dict[str, Any]:
        issuance_status = str(row["reliant_revision_issuance_status"] or "legacy_unknown")
        if issuance_status not in _RELIANT_REVISION_STATUSES:
            issuance_status = "legacy_unknown"
        return {
            "schema_version": RELIANCE_SCHEMA_VERSION, "receipt_id": str(row["receipt_id"]),
            "scope_ref": str(row["scope_ref"]), "reliant_ref": str(row["reliant_ref"]),
            "reliant_revision_ref": row["reliant_revision_ref"],
            "reliant_revision_issuance_status": issuance_status,
            "object_ref": str(row["object_ref"]),
            "purpose": str(row["purpose"]), "assessment": json.loads(str(row["assessment_json"])),
            "assessment_hash": str(row["assessment_hash"]), "pinned_revisions": json.loads(str(row["pinned_revisions_json"])),
            "assurance_ids": json.loads(str(row["assurance_ids_json"])), "policy_ids": json.loads(str(row["policy_ids_json"])),
            "relation_ids": json.loads(str(row["relation_ids_json"])), "topology_fingerprint": str(row["topology_fingerprint"]),
            "actor_ref": row["actor_ref"], "metadata": json.loads(str(row["metadata_json"])),
            "idempotency_key": row["idempotency_key"], "created_at": str(row["created_at"]),
            "semantics": {"explicit_reliance": True, "exposure_implies_reliance": False, "stale_implies_false": False,
                          "change_implies_invalidity": False, "pinned_revisions_are_assessment_observed": True,
                          "reliant_revision_issuance_provenance_is_frozen": True,
                          "unavailable_revision_implies_change": False,
                          "exact_issuance_uses_optimistic_revision_validation": True,
                          "exact_issuance_uses_full_basis_double_observation": True,
                          "optimistic_validation_is_not_atomic_snapshot": True},
        }

    @staticmethod
    def _assessment_revisions(assessment: Mapping[str, Any]) -> dict[str, str | None]:
        revisions: dict[str, str | None] = {}
        stack: list[Mapping[str, Any]] = [assessment]
        while stack:
            current = stack.pop()
            object_ref = str(current.get("object_ref") or "").strip()
            if object_ref:
                raw_revision = current.get("object_revision_ref")
                revision = _optional_revision(raw_revision)
                if object_ref in revisions and revisions[object_ref] != revision:
                    raise ValueError("assessment observed inconsistent revision identity for " + object_ref)
                revisions[object_ref] = revision
            for dependency in current.get("dependencies", []):
                if isinstance(dependency, Mapping) and isinstance(dependency.get("state"), Mapping):
                    stack.append(dependency["state"])
        return revisions

    @staticmethod
    def _validate_exact_basis_identities(assessment: Mapping[str, Any]) -> None:
        """Reject exact receipts when selected basis objects lack durable IDs."""
        missing_assurances: set[str] = set()
        missing_relations: set[str] = set()
        missing_policies: set[str] = set()
        stack: list[Mapping[str, Any]] = [assessment]
        while stack:
            current = stack.pop()
            object_ref = str(current.get("object_ref") or "unknown")
            policy = current.get("policy")
            if isinstance(policy, Mapping) and not str(policy.get("policy_id") or "").strip():
                missing_policies.add(object_ref)
            for assurance in current.get("assurances", []):
                if not isinstance(assurance, Mapping):
                    continue
                assurance_id = str(
                    assurance.get("assurance_id")
                    or assurance.get("verification_id")
                    or assurance.get("id")
                    or ""
                ).strip()
                if not assurance_id:
                    kind = str(assurance.get("assurance_kind") or assurance.get("kind") or "unknown")
                    missing_assurances.add(f"{object_ref}:{kind}")
            for dependency in current.get("dependencies", []):
                if not isinstance(dependency, Mapping):
                    continue
                relation = dependency.get("relation")
                if isinstance(relation, Mapping):
                    relation_id = str(relation.get("relation_id") or relation.get("id") or "").strip()
                    if not relation_id:
                        relation_type = str(relation.get("relation_type") or relation.get("kind") or "unknown")
                        target = str(relation.get("to_ref") or relation.get("target_ref") or relation.get("target") or "unknown")
                        missing_relations.add(f"{object_ref}->{target}:{relation_type}")
                child = dependency.get("state")
                if isinstance(child, Mapping):
                    stack.append(child)
            for blocker in current.get("blockers", []):
                if not isinstance(blocker, Mapping):
                    continue
                if str(blocker.get("type") or "") == "blocking_relation" and not str(blocker.get("relation_id") or "").strip():
                    relation_type = str(blocker.get("relation_type") or "unknown")
                    source = str(blocker.get("source_ref") or "unknown")
                    missing_relations.add(f"{source}->{object_ref}:{relation_type}")
        if missing_policies:
            raise ValueError("exact reliance requires durable policy identity for: " + ", ".join(sorted(missing_policies)))
        if missing_assurances:
            raise ValueError("exact reliance requires durable assurance identity for: " + ", ".join(sorted(missing_assurances)))
        if missing_relations:
            raise ValueError("exact reliance requires durable relation identity for: " + ", ".join(sorted(missing_relations)))

    def _validate_exact_revisions_current(self, pinned: Mapping[str, str | None]) -> None:
        """Optimistically reject an exact receipt if its observed revisions already drifted."""
        changed: list[tuple[str, str | None, str | None]] = []
        unavailable: list[str] = []
        for object_ref, observed in sorted(pinned.items()):
            try:
                current = self.engine.evidence.revision_ref(object_ref)
            except (KeyError, ValueError):
                unavailable.append(object_ref)
                continue
            current = _optional_revision(current)
            if current is None:
                unavailable.append(object_ref)
            elif current != observed:
                changed.append((object_ref, observed, current))
        if unavailable:
            raise ValueError("exact reliance basis became unavailable before commit: " + ", ".join(unavailable))
        if changed:
            detail = ", ".join(f"{ref} {was!r}->{now!r}" for ref, was, now in changed)
            raise ValueError("exact reliance basis changed before commit: " + detail)

    def _observe_reliant_revision(
        self, reliant_ref: str, explicit_revision: str | None
    ) -> tuple[str | None, str]:
        try:
            current = _optional_revision(self.engine.evidence.revision_ref(reliant_ref))
        except (KeyError, ValueError):
            current = None
        if explicit_revision is None:
            if current is None:
                return None, "not_pinned"
            return current, "observed_current"
        if current is None:
            return explicit_revision, "not_assessable"
        if current == explicit_revision:
            return explicit_revision, "observed_current"
        return explicit_revision, "observed_different"

    def _validate_exact_reliant_revision_current(
        self,
        *,
        reliant_ref: str,
        pinned_revision: str | None,
        issuance_status: str,
    ) -> None:
        if issuance_status != "observed_current" or pinned_revision is None:
            return
        try:
            current = _optional_revision(self.engine.evidence.revision_ref(reliant_ref))
        except (KeyError, ValueError):
            current = None
        if current is None:
            raise ValueError(
                "exact reliant revision became unavailable before commit: "
                + reliant_ref
            )
        if current != pinned_revision:
            raise ValueError(
                "exact reliant revision changed before commit: "
                f"{reliant_ref} {pinned_revision!r}->{current!r}"
            )

    def issue(self, *, scope_ref: str, reliant_ref: str, object_ref: str, purpose: str,
              reliant_revision_ref: str | None = None, actor_ref: str | None = None,
              metadata: Mapping[str, Any] | None = None, idempotency_key: str | None = None,
              require_admissible: bool = True, require_exact_revisions: bool = True) -> dict[str, Any]:
        scope = _required(scope_ref, "scope_ref"); reliant = _required(reliant_ref, "reliant_ref")
        relied = _required(object_ref, "object_ref"); normalized_purpose = _required(purpose, "purpose")
        normalized_idempotency_key = None if idempotency_key is None else _required(idempotency_key, "idempotency_key")
        explicit_downstream_revision = None if reliant_revision_ref is None else _required(reliant_revision_ref, "reliant_revision_ref")
        if normalized_idempotency_key is not None:
            existing = self.get_by_idempotency_key(scope_ref=scope, idempotency_key=normalized_idempotency_key)
            if existing is not None:
                self._validate_idempotent_binding(existing, reliant_ref=reliant, object_ref=relied, purpose=normalized_purpose, reliant_revision_ref=explicit_downstream_revision)
                self._validate_idempotent_requirements(existing, require_admissible=require_admissible, require_exact_revisions=require_exact_revisions)
                return self.verify(str(existing["receipt_id"]))
        downstream_revision, downstream_issuance_status = self._observe_reliant_revision(
            reliant, explicit_downstream_revision
        )
        assessment = self.engine.evaluate(scope_ref=scope, object_ref=relied, purpose=normalized_purpose)
        if require_admissible and not assessment["admissible"]:
            raise ValueError("cannot issue affirmative reliance for an inadmissible assessment")
        basis = self.engine.basis(assessment); observed_revisions = self._assessment_revisions(assessment)
        pinned = {ref: observed_revisions.get(ref) for ref in basis["object_refs"]}
        if require_exact_revisions:
            self._validate_exact_basis_identities(assessment)
            missing = sorted(ref for ref, revision in pinned.items() if not revision)
            if missing: raise ValueError("exact reliance requires revision identity for: " + ", ".join(missing))
            self._validate_exact_revisions_current(pinned)
            first_snapshot = exact_basis_snapshot(self.engine, assessment)
            second_assessment = self.engine.evaluate(scope_ref=scope, object_ref=relied, purpose=normalized_purpose)
            self._validate_exact_basis_identities(second_assessment)
            second_snapshot = exact_basis_snapshot(self.engine, second_assessment)
            drift = exact_basis_drift(first_snapshot, second_snapshot)
            if drift:
                reason_types = ", ".join(str(item["type"]) for item in drift)
                raise ValueError("exact reliance basis changed before commit: " + reason_types)
            self._validate_exact_revisions_current(first_snapshot["pinned_revisions"])
            self._validate_exact_reliant_revision_current(
                reliant_ref=reliant,
                pinned_revision=downstream_revision,
                issuance_status=downstream_issuance_status,
            )
            pinned = dict(first_snapshot["pinned_revisions"])
            basis = {
                "object_refs": sorted(pinned),
                "assurance_ids": list(first_snapshot["assurance_ids"]),
                "policy_ids": list(first_snapshot["policy_ids"]),
                "relation_ids": list(first_snapshot["relation_ids"]),
            }
        assessment_hash = canonical_hash(assessment); topology_fingerprint = canonical_hash(basis["relation_ids"])
        receipt_id = _RELIANCE_PREFIX + uuid.uuid4().hex; metadata_payload = dict(metadata or {}); canonical_json(metadata_payload)
        try:
            with self.connect() as conn:
                conn.execute("""INSERT INTO testamur_reliance_receipts(receipt_id,scope_ref,reliant_ref,reliant_revision_ref,reliant_revision_issuance_status,object_ref,purpose,assessment_json,assessment_hash,pinned_revisions_json,assurance_ids_json,policy_ids_json,relation_ids_json,topology_fingerprint,actor_ref,metadata_json,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (receipt_id, scope, reliant, downstream_revision, downstream_issuance_status, relied, normalized_purpose, canonical_json(assessment), assessment_hash,
                     canonical_json(pinned), canonical_json(basis["assurance_ids"]), canonical_json(basis["policy_ids"]), canonical_json(basis["relation_ids"]),
                     topology_fingerprint, None if actor_ref is None else str(actor_ref), canonical_json(metadata_payload), normalized_idempotency_key, _utc_now()))
        except sqlite3.IntegrityError:
            if normalized_idempotency_key is None: raise
            existing = self.get_by_idempotency_key(scope_ref=scope, idempotency_key=normalized_idempotency_key)
            if existing is None: raise
            self._validate_idempotent_binding(existing, reliant_ref=reliant, object_ref=relied, purpose=normalized_purpose, reliant_revision_ref=explicit_downstream_revision)
            self._validate_idempotent_requirements(existing, require_admissible=require_admissible, require_exact_revisions=require_exact_revisions)
            return self.verify(str(existing["receipt_id"]))
        return self.verify(receipt_id)

    @staticmethod
    def _validate_idempotent_binding(receipt: Mapping[str, Any], *, reliant_ref: str, object_ref: str, purpose: str, reliant_revision_ref: str | None) -> None:
        if str(receipt.get("reliant_ref") or "") != reliant_ref: raise ValueError("idempotency key is already bound to a different reliant_ref")
        if str(receipt.get("object_ref") or "") != object_ref: raise ValueError("idempotency key is already bound to a different object_ref")
        if str(receipt.get("purpose") or "") != purpose: raise ValueError("idempotency key is already bound to a different purpose")
        if reliant_revision_ref is not None and receipt.get("reliant_revision_ref") != str(reliant_revision_ref): raise ValueError("idempotency key is already bound to a different reliant_revision_ref")

    @staticmethod
    def _validate_idempotent_requirements(receipt: Mapping[str, Any], *, require_admissible: bool, require_exact_revisions: bool) -> None:
        if require_admissible:
            assessment = receipt.get("assessment")
            if not isinstance(assessment, Mapping) or not bool(assessment.get("admissible")):
                raise ValueError("idempotency key is bound to a receipt that was not admissible at issuance")
        if require_exact_revisions:
            assessment = receipt.get("assessment")
            if not isinstance(assessment, Mapping):
                raise ValueError("idempotency key is bound to a receipt without a frozen assessment basis")
            RelianceStore._validate_exact_basis_identities(assessment)
            pinned = receipt.get("pinned_revisions")
            if not isinstance(pinned, Mapping):
                raise ValueError("idempotency key is bound to a receipt without an exact revision basis")
            missing = sorted(str(ref) for ref, revision in pinned.items() if not revision)
            if missing:
                raise ValueError("idempotency key is bound to a receipt that lacks exact revision identity for: " + ", ".join(missing))

    def get_by_idempotency_key(self, *, scope_ref: str, idempotency_key: str) -> dict[str, Any] | None:
        scope = _required(scope_ref, "scope_ref"); key = _required(idempotency_key, "idempotency_key")
        with self.connect() as conn: row = conn.execute("SELECT * FROM testamur_reliance_receipts WHERE scope_ref=? AND idempotency_key=?", (scope, key)).fetchone()
        return None if row is None else self._serialize(row)

    def get(self, receipt_id: str) -> dict[str, Any] | None:
        with self.connect() as conn: row = conn.execute("SELECT * FROM testamur_reliance_receipts WHERE receipt_id=?", (str(receipt_id),)).fetchone()
        return None if row is None else self._serialize(row)

    def _revision_status(self, receipt: Mapping[str, Any], reasons: list[dict[str, Any]]) -> tuple[str, str | None]:
        pinned = receipt["reliant_revision_ref"]
        if pinned is None:
            return "not_pinned", None
        issuance_status = str(
            receipt.get("reliant_revision_issuance_status") or "legacy_unknown"
        )
        try:
            current = _optional_revision(self.engine.evidence.revision_ref(receipt["reliant_ref"]))
        except (KeyError, ValueError):
            current = None
        if current is None:
            if issuance_status == "not_assessable":
                return "not_assessable", None
            reasons.append({
                "type": "reliant_revision_not_assessable",
                "reliant_ref": receipt["reliant_ref"],
                "was": pinned,
                "now": None,
                "issuance_status": issuance_status,
                "reconsideration_required": True,
            })
            return "not_assessable", None
        if current == pinned:
            return "current", current
        reasons.append({"type": "reliant_revision_changed", "reliant_ref": receipt["reliant_ref"], "was": pinned, "now": current})
        return "changed", current

    def _check_pinned_revisions(self, receipt: Mapping[str, Any], reasons: list[dict[str, Any]]) -> list[str]:
        unavailable: list[str] = []
        for object_ref, was_revision in sorted(receipt["pinned_revisions"].items()):
            try: now_revision = self.engine.evidence.revision_ref(object_ref)
            except (KeyError, ValueError): unavailable.append(str(object_ref)); continue
            if not now_revision: unavailable.append(str(object_ref)); continue
            if now_revision != was_revision: reasons.append({"type": "object_revision_changed", "object_ref": object_ref, "was": was_revision, "now": now_revision})
        return unavailable

    def _unavailable_verification(self, receipt: Mapping[str, Any], *, exc: KeyError | ValueError, reasons: list[dict[str, Any]], reliant_revision_status: str, current_reliant_revision: str | None, unavailable_basis_refs: Iterable[str]) -> dict[str, Any]:
        refs = sorted({str(ref) for ref in unavailable_basis_refs if str(ref)}); detail = str(exc.args[0] if isinstance(exc, KeyError) and exc.args else exc).strip() or None
        reasons.append({"type": "current_evidence_not_assessable", "detail": detail, "basis_object_refs": refs, "reconsideration_required": True})
        return {**receipt, "stale": True, "reliant_revision_status": reliant_revision_status, "current_reliant_revision_ref": current_reliant_revision,
                "current_admissible": None, "current_assessment_state": AssessmentState.UNKNOWN.value, "current_assessment_hash": None,
                "staleness_reasons": reasons, "current_assessment": None, "assessment_available": False, "unavailable_basis_object_refs": refs,
                "reconsideration_required": True, "semantics": {**receipt["semantics"], "staleness_requires_reconsideration": True,
                "staleness_implies_false": False, "unavailable_evidence_implies_false": False, "unknown_implies_false": False, "unknown_implies_inadmissible": False}}

    @staticmethod
    def _decision_signature(assessment: Mapping[str, Any]) -> tuple[bool, str, str]:
        return (bool(assessment.get("admissible")), str(assessment.get("assessment_state") or ""), str(assessment.get("status") or ""))

    def verify(self, receipt_id: str) -> dict[str, Any]:
        receipt = self.get(receipt_id)
        if receipt is None: raise KeyError(receipt_id)
        reasons: list[dict[str, Any]] = []
        reliant_revision_status, current_reliant_revision = self._revision_status(receipt, reasons)
        unavailable_refs = self._check_pinned_revisions(receipt, reasons)
        try: current = self.engine.evaluate(scope_ref=receipt["scope_ref"], object_ref=receipt["object_ref"], purpose=receipt["purpose"])
        except (KeyError, ValueError) as exc:
            if not unavailable_refs: unavailable_refs = [str(receipt["object_ref"])]
            return self._unavailable_verification(receipt, exc=exc, reasons=reasons, reliant_revision_status=reliant_revision_status, current_reliant_revision=current_reliant_revision, unavailable_basis_refs=unavailable_refs)
        current_basis = self.engine.basis(current)
        if unavailable_refs: reasons.append({"type": "basis_revision_not_assessable", "basis_object_refs": unavailable_refs, "reconsideration_required": True})
        if current_basis["assurance_ids"] != receipt["assurance_ids"]: reasons.append({"type": "assurance_selection_changed", "was": receipt["assurance_ids"], "now": current_basis["assurance_ids"]})
        if current_basis["policy_ids"] != receipt["policy_ids"]: reasons.append({"type": "policy_changed", "was": receipt["policy_ids"], "now": current_basis["policy_ids"]})
        current_topology = canonical_hash(current_basis["relation_ids"])
        if current_topology != receipt["topology_fingerprint"]: reasons.append({"type": "dependency_topology_changed", "was": receipt["relation_ids"], "now": current_basis["relation_ids"]})
        current_hash = canonical_hash(current)
        if current_hash != receipt["assessment_hash"]:
            reasons.append({"type": "assessment_snapshot_changed", "was": receipt["assessment_hash"], "now": current_hash})
            previous = receipt["assessment"]
            if self._decision_signature(previous) != self._decision_signature(current):
                reasons.append({"type": "admission_decision_changed", "was_admissible": bool(previous.get("admissible")), "now_admissible": bool(current.get("admissible")),
                                "was_assessment_state": previous.get("assessment_state"), "now_assessment_state": current.get("assessment_state"),
                                "was_status": previous.get("status"), "now_status": current.get("status"), "current_root_causes": current.get("root_causes", [])})
        current_state = str(current.get("assessment_state") or AssessmentState.UNKNOWN.value)
        projected_admissible = None if current_state == AssessmentState.UNKNOWN.value else bool(current.get("admissible"))
        return {**receipt, "stale": bool(reasons), "reliant_revision_status": reliant_revision_status, "current_reliant_revision_ref": current_reliant_revision,
                "current_admissible": projected_admissible, "current_assessment_state": current_state, "current_assessment_hash": current_hash,
                "staleness_reasons": reasons, "current_assessment": current, "assessment_available": True, "unavailable_basis_object_refs": unavailable_refs,
                "reconsideration_required": bool(reasons), "semantics": {**receipt["semantics"], "staleness_requires_reconsideration": True,
                "staleness_implies_false": False, "unknown_implies_inadmissible": False}}

    def list(self, *, scope_ref: str, reliant_ref: str | None = None, object_ref: str | None = None) -> list[dict[str, Any]]:
        where = ["scope_ref=?"]; params: list[Any] = [str(scope_ref)]
        if reliant_ref is not None: where.append("reliant_ref=?"); params.append(str(reliant_ref))
        if object_ref is not None: where.append("object_ref=?"); params.append(str(object_ref))
        with self.connect() as conn: rows = conn.execute(f"SELECT receipt_id FROM testamur_reliance_receipts WHERE {' AND '.join(where)} ORDER BY rowid", params).fetchall()
        return [self.verify(str(row["receipt_id"])) for row in rows]

    def blast_radius(self, *, scope_ref: str, changed_object_refs: Iterable[str]) -> dict[str, Any]:
        scope = _required(scope_ref, "scope_ref"); changed = sorted({str(item) for item in changed_object_refs})
        with self.connect() as conn: rows = conn.execute("SELECT * FROM testamur_reliance_receipts WHERE scope_ref=? ORDER BY rowid", (scope,)).fetchall()
        affected: list[dict[str, Any]] = []; unaffected = 0; purposes: Counter[str] = Counter(); reliant_refs: set[str] = set()
        for row in rows:
            receipt = self._serialize(row); hits = sorted(set(changed) & set(receipt["pinned_revisions"]))
            if not hits: unaffected += 1; continue
            current = self.verify(receipt["receipt_id"]); purposes[receipt["purpose"]] += 1; reliant_refs.add(receipt["reliant_ref"])
            affected.append({"receipt_id": receipt["receipt_id"], "reliant_ref": receipt["reliant_ref"], "reliant_revision_ref": receipt["reliant_revision_ref"],
                             "relied_object_ref": receipt["object_ref"], "purpose": receipt["purpose"], "changed_basis_object_refs": hits,
                             "currently_stale": bool(current["stale"]), "assessment_available": bool(current.get("assessment_available", True)),
                             "current_assessment_state": current.get("current_assessment_state", AssessmentState.UNKNOWN.value), "currently_admissible": current.get("current_admissible"),
                             "reconsideration_required": True, "unavailable_basis_object_refs": list(current.get("unavailable_basis_object_refs") or []),
                             "staleness_reason_types": sorted({str(reason.get("type") or "unknown") for reason in current.get("staleness_reasons", []) if isinstance(reason, Mapping)}),
                             "issued_at": receipt["created_at"]})
        return {"schema_version": RELIANCE_SCHEMA_VERSION, "scope_ref": scope, "changed_object_refs": changed, "affected_receipt_count": len(affected),
                "unaffected_receipt_count": unaffected, "affected_reliant_refs": sorted(reliant_refs), "purpose_counts": dict(sorted(purposes.items())),
                "affected_receipts": affected, "semantics": {"actual_reliance_only": True, "change_implies_reconsideration": True, "change_implies_false": False,
                "unavailable_evidence_erases_reliance": False, "unknown_implies_inadmissible": False}}
