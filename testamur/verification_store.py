from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .runtime_protocol import canonical_hash, canonical_json
from .receipt_store import TestamurReceiptStore


class TestamurVerificationStore:
    """Revision-pinned checker bindings for the local Testamur environment.

    A passing checker means only that the named checker run exited successfully
    against the recorded target revision. It is evidence, not a universal truth
    predicate. Path targets become stale when their current bytes differ from the
    bytes observed when the checker ran.
    """

    def __init__(self, receipts: TestamurReceiptStore) -> None:
        self.receipts = receipts
        self._init_schema()

    def _init_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS testamur_verifications(
          verification_id TEXT PRIMARY KEY,
          target_kind TEXT NOT NULL,
          target_ref TEXT NOT NULL,
          target_resolved_path TEXT,
          target_content_hash TEXT,
          verifier TEXT NOT NULL,
          checker_run_id TEXT NOT NULL REFERENCES testamur_receipts(run_id),
          checker_status TEXT NOT NULL,
          record_json TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS testamur_verifications_target_ref_idx
          ON testamur_verifications(target_ref);
        CREATE INDEX IF NOT EXISTS testamur_verifications_target_path_idx
          ON testamur_verifications(target_resolved_path);

        CREATE TRIGGER IF NOT EXISTS testamur_verifications_no_update
        BEFORE UPDATE ON testamur_verifications BEGIN
          SELECT RAISE(ABORT, 'Testamur verifications are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS testamur_verifications_no_delete
        BEFORE DELETE ON testamur_verifications BEGIN
          SELECT RAISE(ABORT, 'Testamur verifications are immutable');
        END;
        """
        with self.receipts.connect() as conn:
            conn.executescript(schema)

    def prepare_target_revision(self, target: str, *, root: Path) -> dict[str, Any]:
        """Capture the target identity/revision before a checker is launched."""
        if self.receipts.get(target) is not None:
            return {
                "target_kind": "run",
                "target_ref": target,
                "target_resolved_path": None,
                "target_content_hash": None,
                "target_revision_status": "immutable_run_receipt",
            }

        raw = Path(target).expanduser()
        candidate = raw if raw.is_absolute() else root / raw
        if candidate.exists() or candidate.is_symlink():
            resolved = candidate.resolve()
            content_hash = self.receipts.current_content_hash(resolved, root=root)
            if content_hash is None:
                raise ValueError(f"could not capture target revision: {target}")
            return {
                "target_kind": "artifact_path",
                "target_ref": target,
                "target_resolved_path": str(resolved),
                "target_content_hash": content_hash,
                "target_revision_status": "captured_before_checker",
            }

        return {
            "target_kind": "logical_ref",
            "target_ref": target,
            "target_resolved_path": None,
            "target_content_hash": None,
            "target_revision_status": "not_byte_pinned",
        }

    def record(
        self,
        target: str,
        *,
        root: Path,
        verifier: str,
        checker_receipt: Mapping[str, Any],
        target_revision: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        checker_run_id = str(checker_receipt.get("run_id") or "")
        if not checker_run_id or self.receipts.get(checker_run_id) is None:
            raise ValueError("verification requires a persisted checker run receipt")
        target_data = dict(
            target_revision
            if target_revision is not None
            else self.prepare_target_revision(target, root=root)
        )
        if str(target_data.get("target_ref") or "") != str(target):
            raise ValueError("prepared target revision does not match verification target")
        required = {
            "target_kind",
            "target_ref",
            "target_resolved_path",
            "target_content_hash",
            "target_revision_status",
        }
        if not required.issubset(target_data):
            raise ValueError("prepared target revision is incomplete")

        exit_code = checker_receipt.get("exit_code")
        checker_status = "passed" if exit_code == 0 else "failed"
        record = {
            **target_data,
            "verifier": str(verifier),
            "checker_run_id": checker_run_id,
            "checker_status": checker_status,
            "checker_exit_code": exit_code,
            "semantics": {
                "checker_pass_implies_universal_truth": False,
                "checker_failure_implies_target_false": False,
                "path_revision_pinned": target_data["target_kind"] == "artifact_path",
                "target_revision_captured_before_checker": True,
            },
        }
        verification_id = "tst:verification:" + canonical_hash(record)
        payload = {"verification_id": verification_id, **record}
        payload_json = canonical_json(payload)
        with self.receipts.connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO testamur_verifications(
                     verification_id,target_kind,target_ref,target_resolved_path,
                     target_content_hash,verifier,checker_run_id,checker_status,record_json
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    verification_id,
                    target_data["target_kind"],
                    target_data["target_ref"],
                    target_data["target_resolved_path"],
                    target_data["target_content_hash"],
                    str(verifier),
                    checker_run_id,
                    checker_status,
                    payload_json,
                ),
            )
            row = conn.execute(
                "SELECT record_json FROM testamur_verifications WHERE verification_id=?",
                (verification_id,),
            ).fetchone()
            if row is None or str(row["record_json"]) != payload_json:
                raise ValueError("verification identity is already bound to different content")
        return payload

    def get(self, verification_id: str) -> dict[str, Any] | None:
        """Return one immutable verification record by its durable identity."""
        with self.receipts.connect() as conn:
            row = conn.execute(
                "SELECT record_json FROM testamur_verifications WHERE verification_id=?",
                (str(verification_id),),
            ).fetchone()
        return None if row is None else json.loads(str(row["record_json"]))

    def _rows_for_target(self, target: str, *, root: Path) -> list[Any]:
        candidate = Path(target).expanduser()
        resolved = str((candidate if candidate.is_absolute() else root / candidate).resolve())
        with self.receipts.connect() as conn:
            return conn.execute(
                """SELECT record_json FROM testamur_verifications
                   WHERE target_ref=? OR target_resolved_path=?
                   ORDER BY rowid DESC""",
                (target, resolved),
            ).fetchall()

    def for_target(self, target: str, *, root: Path) -> dict[str, Any]:
        records = [
            json.loads(str(row["record_json"]))
            for row in self._rows_for_target(target, root=root)
        ]
        current_hash = self.receipts.current_content_hash(target, root=root)
        decorated: list[dict[str, Any]] = []
        for record in records:
            kind = record.get("target_kind")
            if kind == "artifact_path":
                pinned = record.get("target_content_hash")
                if current_hash is None:
                    revision_state = "not_assessable"
                elif str(current_hash) == str(pinned):
                    revision_state = "current"
                else:
                    revision_state = "stale"
            elif kind == "run":
                revision_state = "current"
            else:
                revision_state = "not_byte_pinned"
            decorated.append({**record, "revision_state": revision_state})

        current_records = [
            item for item in decorated if item.get("revision_state") == "current"
        ]
        current_passes = [
            item for item in current_records if item.get("checker_status") == "passed"
        ]
        current_failures = [
            item for item in current_records if item.get("checker_status") == "failed"
        ]
        stale = [item for item in decorated if item.get("revision_state") == "stale"]
        not_assessable = [
            item
            for item in decorated
            if item.get("revision_state") in {"not_assessable", "not_byte_pinned"}
        ]

        latest_current = current_records[0] if current_records else None
        if latest_current is not None:
            verification_state = (
                "current_checker_pass"
                if latest_current.get("checker_status") == "passed"
                else "current_checker_failure"
            )
        elif stale:
            verification_state = "stale"
        elif not_assessable:
            verification_state = "not_assessable"
        else:
            verification_state = "not_verified"

        return {
            "target": target,
            "current_content_hash": current_hash,
            "records": decorated,
            "current_passes": len(current_passes),
            "current_failures": len(current_failures),
            "stale_records": len(stale),
            "not_assessable_records": len(not_assessable),
            "latest_current_record": latest_current,
            "verification_state": verification_state,
            "semantics": {
                "checker_pass_implies_universal_truth": False,
                "checker_failure_implies_target_false": False,
                "current_state_uses_latest_current_checker": True,
                "universal_trust_score": None,
            },
        }

    def stats(self, *, root: Path) -> dict[str, Any]:
        """Summarize current verification state per target, not per history row."""
        with self.receipts.connect() as conn:
            rows = conn.execute(
                """SELECT target_ref,target_resolved_path,record_json
                   FROM testamur_verifications
                   ORDER BY rowid DESC"""
            ).fetchall()

        representatives: dict[str, str] = {}
        for row in rows:
            resolved = row["target_resolved_path"]
            target_ref = str(row["target_ref"])
            key = str(resolved) if resolved else f"ref:{target_ref}"
            representatives.setdefault(key, str(resolved) if resolved else target_ref)

        target_states = {
            "current_checker_pass": 0,
            "current_checker_failure": 0,
            "stale": 0,
            "not_assessable": 0,
            "not_verified": 0,
        }
        historical = {
            "current_pass_records": 0,
            "current_failure_records": 0,
            "stale_records": 0,
            "not_assessable_records": 0,
        }

        for target in representatives.values():
            summary = self.for_target(target, root=root)
            state = str(summary.get("verification_state") or "not_verified")
            target_states[state] = target_states.get(state, 0) + 1
            historical["current_pass_records"] += int(summary.get("current_passes") or 0)
            historical["current_failure_records"] += int(summary.get("current_failures") or 0)
            historical["stale_records"] += int(summary.get("stale_records") or 0)
            historical["not_assessable_records"] += int(
                summary.get("not_assessable_records") or 0
            )

        return {
            "records": len(rows),
            "targets": len(representatives),
            "current_passes": target_states["current_checker_pass"],
            "current_failures": target_states["current_checker_failure"],
            "stale_records": target_states["stale"],
            "not_assessable": target_states["not_assessable"],
            "not_verified": target_states["not_verified"],
            "target_states": target_states,
            "historical_record_states": historical,
            "state_unit": "targets",
            "universal_trust_score": None,
        }
