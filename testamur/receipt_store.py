from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from .artifacts import snapshot_path
from .runtime_protocol import canonical_hash, canonical_json


class TestamurReceiptStore:
    """Append-only local index for Testamur execution receipts.

    Runtime Protocol owns durable command/run state. This store preserves the
    caller-side observations that the runtime protocol intentionally does not:
    declared artifact snapshots, Git context, worktree delta and the complete
    wrapper receipt. Recording these observations does not promote them to
    verification or causal truth.
    """

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
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

    def _init_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS testamur_receipts(
          run_id TEXT PRIMARY KEY,
          receipt_json TEXT NOT NULL,
          receipt_digest TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS testamur_artifact_observations(
          observation_id TEXT PRIMARY KEY,
          run_id TEXT NOT NULL REFERENCES testamur_receipts(run_id),
          role TEXT NOT NULL,
          ordinal INTEGER NOT NULL,
          declared_path TEXT NOT NULL,
          resolved_path TEXT NOT NULL,
          status TEXT NOT NULL,
          content_hash TEXT,
          observation_json TEXT NOT NULL,
          UNIQUE(run_id, role, ordinal)
        );

        CREATE INDEX IF NOT EXISTS testamur_artifact_resolved_path_idx
          ON testamur_artifact_observations(resolved_path, role);
        CREATE INDEX IF NOT EXISTS testamur_artifact_declared_path_idx
          ON testamur_artifact_observations(declared_path, role);

        CREATE TRIGGER IF NOT EXISTS testamur_receipts_no_update
        BEFORE UPDATE ON testamur_receipts BEGIN
          SELECT RAISE(ABORT, 'Testamur receipts are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS testamur_receipts_no_delete
        BEFORE DELETE ON testamur_receipts BEGIN
          SELECT RAISE(ABORT, 'Testamur receipts are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS testamur_artifact_observations_no_update
        BEFORE UPDATE ON testamur_artifact_observations BEGIN
          SELECT RAISE(ABORT, 'Testamur artifact observations are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS testamur_artifact_observations_no_delete
        BEFORE DELETE ON testamur_artifact_observations BEGIN
          SELECT RAISE(ABORT, 'Testamur artifact observations are immutable');
        END;
        """
        with self.connect() as conn:
            conn.executescript(schema)

    @staticmethod
    def _content_hash(observation: Mapping[str, Any]) -> str | None:
        snapshot = observation.get("snapshot")
        if not isinstance(snapshot, Mapping):
            return None
        value = snapshot.get("content_hash") or snapshot.get("sha256") or snapshot.get("file_hash")
        return str(value) if value else None

    @staticmethod
    def _declared_observations(receipt: Mapping[str, Any]) -> list[tuple[str, int, Mapping[str, Any]]]:
        observations: list[tuple[str, int, Mapping[str, Any]]] = []
        for role, field in (("input", "input_artifacts"), ("output", "output_artifacts")):
            values = receipt.get(field) or []
            if not isinstance(values, list):
                continue
            for ordinal, item in enumerate(values):
                if isinstance(item, Mapping):
                    observations.append((role, ordinal, item))
        return observations

    @staticmethod
    def _worktree_observations(receipt: Mapping[str, Any]) -> list[tuple[str, int, Mapping[str, Any]]]:
        delta = receipt.get("worktree_delta")
        if not isinstance(delta, Mapping) or delta.get("status") != "captured":
            return []
        cwd = Path(str(receipt.get("cwd") or ".")).expanduser().resolve()
        result: list[tuple[str, int, Mapping[str, Any]]] = []
        ordinal = 0
        for change_kind in ("added", "changed", "removed"):
            values = delta.get(change_kind) or []
            if not isinstance(values, list):
                continue
            for raw in values:
                logical = str(raw)
                resolved = (cwd / logical).resolve()
                item: dict[str, Any] = {
                    "role": "worktree_change",
                    "change_kind": change_kind,
                    "declared_path": logical,
                    "resolved_path": str(resolved),
                    "required": False,
                    "observation_phase": "post_execution_receipt_persistence",
                    "causal_attribution_implied": False,
                    "status": "removed" if change_kind == "removed" else "pending",
                    "snapshot": None,
                }
                if change_kind != "removed":
                    try:
                        item["snapshot"] = snapshot_path(resolved, root=cwd)
                        item["status"] = "captured"
                    except (OSError, ValueError) as exc:
                        item["status"] = "capture_failed"
                        item["error"] = {"code": type(exc).__name__, "message": str(exc)}
                result.append(("worktree_change", ordinal, item))
                ordinal += 1
        return result

    @staticmethod
    def _row_to_observation(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "observation_id": str(row["observation_id"]),
            "run_id": str(row["run_id"]),
            "role": str(row["role"]),
            "ordinal": int(row["ordinal"]),
            "declared_path": str(row["declared_path"]),
            "resolved_path": str(row["resolved_path"]),
            "status": str(row["status"]),
            "content_hash": row["content_hash"],
            "observation": json.loads(str(row["observation_json"])),
        }

    @staticmethod
    def _canonical_path(value: str | Path, *, root: Path) -> str:
        candidate = Path(value).expanduser()
        absolute = candidate if candidate.is_absolute() else Path(root).expanduser() / candidate
        try:
            return str(absolute.resolve(strict=False))
        except OSError:
            return str(absolute.absolute())

    @classmethod
    def _stored_resolved_path(
        cls,
        item: Mapping[str, Any],
        *,
        receipt_root: Path,
    ) -> str:
        raw = str(item.get("resolved_path") or item.get("declared_path") or "")
        if not raw:
            return raw
        return cls._canonical_path(raw, root=receipt_root)

    def record(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        run_id = str(receipt.get("run_id") or "")
        if not run_id:
            raise ValueError("Testamur receipt requires a durable run_id")
        receipt_value = dict(receipt)
        receipt_json = canonical_json(receipt_value)
        digest = canonical_hash(receipt_value)
        observations = [
            *self._declared_observations(receipt_value),
            *self._worktree_observations(receipt_value),
        ]
        receipt_root = Path(str(receipt_value.get("cwd") or ".")).expanduser()

        with self.connect() as conn:
            existing = conn.execute(
                "SELECT receipt_json,receipt_digest FROM testamur_receipts WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if existing is not None:
                existing_json = str(existing["receipt_json"])
                existing_digest = str(existing["receipt_digest"])
                if existing_digest == digest and existing_json == receipt_json:
                    count = int(
                        conn.execute(
                            "SELECT COUNT(*) FROM testamur_artifact_observations WHERE run_id=?",
                            (run_id,),
                        ).fetchone()[0]
                    )
                    return {
                        "run_id": run_id,
                        "receipt_digest": existing_digest,
                        "artifact_observation_count": count,
                        "verification_implied": bool(receipt_value.get("verification_implied", False)),
                        "replayed_receipt_reused": False,
                    }
                if bool(receipt_value.get("replayed")):
                    previous = json.loads(existing_json)
                    count = int(
                        conn.execute(
                            "SELECT COUNT(*) FROM testamur_artifact_observations WHERE run_id=?",
                            (run_id,),
                        ).fetchone()[0]
                    )
                    return {
                        "run_id": run_id,
                        "receipt_digest": existing_digest,
                        "artifact_observation_count": count,
                        "verification_implied": bool(previous.get("verification_implied", False)),
                        "replayed_receipt_reused": True,
                    }
                raise ValueError("run_id is already bound to a different Testamur receipt")

            conn.execute(
                "INSERT INTO testamur_receipts(run_id,receipt_json,receipt_digest) VALUES(?,?,?)",
                (run_id, receipt_json, digest),
            )

            for role, ordinal, item in observations:
                declared = str(item.get("declared_path") or "")
                resolved = self._stored_resolved_path(item, receipt_root=receipt_root)
                observation_json = canonical_json(dict(item))
                observation_id = "tst:obs:" + canonical_hash(
                    {"run_id": run_id, "role": role, "ordinal": ordinal}
                )
                conn.execute(
                    """INSERT INTO testamur_artifact_observations(
                         observation_id,run_id,role,ordinal,declared_path,resolved_path,
                         status,content_hash,observation_json
                       ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        observation_id,
                        run_id,
                        role,
                        ordinal,
                        declared,
                        resolved,
                        str(item.get("status") or "unknown"),
                        self._content_hash(item),
                        observation_json,
                    ),
                )

        return {
            "run_id": run_id,
            "receipt_digest": digest,
            "artifact_observation_count": len(observations),
            "verification_implied": bool(receipt_value.get("verification_implied", False)),
            "replayed_receipt_reused": False,
        }

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT receipt_json FROM testamur_receipts WHERE run_id=?", (str(run_id),)
            ).fetchone()
        return None if row is None else json.loads(str(row["receipt_json"]))

    def get_observation(self, observation_id: str) -> dict[str, Any] | None:
        """Return one immutable artifact observation by its durable identity."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM testamur_artifact_observations WHERE observation_id=?",
                (str(observation_id),),
            ).fetchone()
        return None if row is None else self._row_to_observation(row)

    def stats(self) -> dict[str, int]:
        with self.connect() as conn:
            receipts = int(conn.execute("SELECT COUNT(*) FROM testamur_receipts").fetchone()[0])
            observations = int(
                conn.execute("SELECT COUNT(*) FROM testamur_artifact_observations").fetchone()[0]
            )
            inputs = int(
                conn.execute(
                    "SELECT COUNT(*) FROM testamur_artifact_observations WHERE role='input'"
                ).fetchone()[0]
            )
            outputs = int(
                conn.execute(
                    "SELECT COUNT(*) FROM testamur_artifact_observations WHERE role='output'"
                ).fetchone()[0]
            )
            worktree = int(
                conn.execute(
                    "SELECT COUNT(*) FROM testamur_artifact_observations WHERE role='worktree_change'"
                ).fetchone()[0]
            )
        return {
            "runs": receipts,
            "artifact_observations": observations,
            "input_observations": inputs,
            "output_observations": outputs,
            "worktree_change_observations": worktree,
        }

    def observations_for_path(self, path: str | Path, *, root: Path) -> list[dict[str, Any]]:
        raw = str(path)
        root_path = Path(root).expanduser()
        candidate = Path(path).expanduser()
        absolute = candidate if candidate.is_absolute() else root_path / candidate
        canonical = self._canonical_path(absolute, root=root_path)

        relative_alias = raw
        try:
            relative = Path(canonical).relative_to(Path(root_path).resolve(strict=False))
        except (OSError, ValueError):
            pass
        else:
            relative_alias = str(relative)

        resolved_values = (raw, str(absolute), canonical)
        declared_values = (raw, relative_alias)
        resolved_aliases = set(resolved_values)
        declared_aliases = set(declared_values)

        basename = Path(canonical).name
        suffix = f"%/{basename}" if basename else "%"
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM testamur_artifact_observations
                   WHERE resolved_path IN (?,?,?)
                      OR declared_path IN (?,?)
                      OR resolved_path LIKE ?
                      OR declared_path LIKE ?
                   ORDER BY rowid DESC""",
                (
                    *resolved_values,
                    *declared_values,
                    suffix,
                    suffix,
                ),
            ).fetchall()

        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            observation = self._row_to_observation(row)
            observation_id = str(observation["observation_id"])
            if observation_id in seen:
                continue
            stored_resolved = str(observation.get("resolved_path") or "")
            stored_declared = str(observation.get("declared_path") or "")
            matches = (
                stored_resolved in resolved_aliases
                or stored_declared in declared_aliases
                or (
                    bool(stored_resolved)
                    and self._canonical_path(stored_resolved, root=root_path) == canonical
                )
                or (
                    bool(stored_declared)
                    and self._canonical_path(stored_declared, root=root_path) == canonical
                )
            )
            if matches:
                seen.add(observation_id)
                result.append(observation)
        return result

    def observations_for_run(self, run_id: str) -> list[dict[str, Any]]:
        """Return the immutable artifact observations persisted for one run."""
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM testamur_artifact_observations
                   WHERE run_id=?
                   ORDER BY rowid ASC""",
                (str(run_id),),
            ).fetchall()
        return [self._row_to_observation(row) for row in rows]

    def current_content_hash(self, path: str | Path, *, root: Path) -> str | None:
        candidate = Path(path).expanduser()
        resolved = candidate if candidate.is_absolute() else root / candidate
        if not resolved.exists() and not resolved.is_symlink():
            return None
        try:
            snapshot = snapshot_path(resolved, root=root)
        except (OSError, ValueError):
            return None
        value = snapshot.get("content_hash") or snapshot.get("sha256") or snapshot.get("file_hash")
        return str(value) if value else None

    def explain(self, target: str, *, root: Path) -> dict[str, Any]:
        receipt = self.get(target)
        if receipt is not None:
            return {
                "target": target,
                "kind": "run",
                "state": receipt.get("state"),
                "command": receipt.get("argv") or [],
                "cwd": receipt.get("cwd"),
                "git_context": receipt.get("git_context") or {},
                "inputs": receipt.get("input_artifacts") or [],
                "outputs": receipt.get("output_artifacts") or [],
                "worktree_delta": receipt.get("worktree_delta") or {},
                "verification": (
                    "verified_by_separate_evidence"
                    if receipt.get("verification_implied")
                    else "not_implied_by_execution"
                ),
                "record_digest": receipt.get("record_digest"),
            }

        observations = self.observations_for_path(target, root=root)
        if not observations:
            raise KeyError(target)
        declared_outputs = [item for item in observations if item["role"] == "output"]
        declared_inputs = [item for item in observations if item["role"] == "input"]
        worktree_changes = [item for item in observations if item["role"] == "worktree_change"]
        return {
            "target": target,
            "kind": "artifact",
            "current_content_hash": self.current_content_hash(target, root=root),
            "observed_as_declared_output": declared_outputs,
            "observed_as_input": declared_inputs,
            "observed_worktree_changes": worktree_changes,
            "causal_attribution_implied": False,
            "verification": "not_inferred_from_artifact_observation",
        }

    def trace(
        self,
        target: str,
        *,
        root: Path,
        recursive: bool = False,
        max_depth: int = 8,
    ) -> dict[str, Any]:
        from .evidence_graph import trace as trace_graph

        return trace_graph(
            self,
            target,
            root=root,
            recursive=recursive,
            max_depth=max_depth,
        )

    def impact(
        self,
        target: str,
        *,
        root: Path,
        recursive: bool = False,
        max_depth: int = 8,
    ) -> dict[str, Any]:
        from .evidence_graph import impact as impact_graph

        return impact_graph(
            self,
            target,
            root=root,
            recursive=recursive,
            max_depth=max_depth,
        )
