from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from formal_binding_index import (
    ensure_formal_binding_name_index,
    resolve_formal_binding_name_row,
    resolve_latest_formal_binding_for_claim_row,
)
from registry import build_ref, claim_ref, parse_ref, version_ref, utc_now


TYPE_SHAPE_VERSION = "lean-expr-result-v1"
TYPE_PARTITION_VERSION = "lean-expr-partition-v1"


class FormalIndex:
    """Formal-substrate evidence connecting Lean declarations and Claim revisions.

    FormalBinding resolves declaration names to Claims. ``formal_type_shapes`` and
    ``formal_type_partitions`` are separate immutable Build observations extracted
    directly from ``ConstantInfo.type``. None of these tables creates mathematical
    graph edges.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS formal_bindings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    claim_id INTEGER NOT NULL REFERENCES claims(id),
                    claim_version_id INTEGER NOT NULL REFERENCES claim_versions(id),
                    source_build_id INTEGER REFERENCES builds(id),
                    lean_name TEXT NOT NULL,
                    environment_hash TEXT NOT NULL,
                    type_hash TEXT NOT NULL,
                    formal_fingerprint TEXT NOT NULL,
                    origin TEXT NOT NULL DEFAULT 'local-build',
                    created_at TEXT NOT NULL,
                    UNIQUE(claim_version_id, source_build_id, lean_name, environment_hash, type_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_binding_fp
                  ON formal_bindings(formal_fingerprint, environment_hash);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_binding_external_unique
                  ON formal_bindings(claim_version_id, lean_name, environment_hash, type_hash)
                  WHERE source_build_id IS NULL;
                CREATE TRIGGER IF NOT EXISTS formal_bindings_no_update
                BEFORE UPDATE ON formal_bindings BEGIN
                    SELECT RAISE(ABORT, 'formal bindings are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS formal_bindings_no_delete
                BEFORE DELETE ON formal_bindings BEGIN
                    SELECT RAISE(ABORT, 'formal bindings are immutable');
                END;

                CREATE TABLE IF NOT EXISTS formal_type_shapes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    claim_id INTEGER NOT NULL REFERENCES claims(id),
                    claim_version_id INTEGER NOT NULL REFERENCES claim_versions(id),
                    source_build_id INTEGER NOT NULL REFERENCES builds(id),
                    environment_hash TEXT NOT NULL,
                    type_hash TEXT NOT NULL,
                    shape_version TEXT NOT NULL,
                    result_head TEXT,
                    result_binder_count INTEGER NOT NULL CHECK(result_binder_count >= 0),
                    created_at TEXT NOT NULL,
                    UNIQUE(source_build_id, shape_version)
                );
                CREATE INDEX IF NOT EXISTS idx_type_shape_head
                  ON formal_type_shapes(shape_version, result_head, source_build_id);
                CREATE INDEX IF NOT EXISTS idx_type_shape_version
                  ON formal_type_shapes(claim_version_id, source_build_id);
                CREATE TRIGGER IF NOT EXISTS formal_type_shapes_no_update
                BEFORE UPDATE ON formal_type_shapes BEGIN
                    SELECT RAISE(ABORT, 'formal type-shape observations are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS formal_type_shapes_no_delete
                BEFORE DELETE ON formal_type_shapes BEGIN
                    SELECT RAISE(ABORT, 'formal type-shape observations are immutable');
                END;

                CREATE TABLE IF NOT EXISTS formal_type_partitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    claim_id INTEGER NOT NULL REFERENCES claims(id),
                    claim_version_id INTEGER NOT NULL REFERENCES claim_versions(id),
                    source_build_id INTEGER NOT NULL REFERENCES builds(id),
                    environment_hash TEXT NOT NULL,
                    type_hash TEXT NOT NULL,
                    partition_version TEXT NOT NULL,
                    result_binder_count INTEGER NOT NULL CHECK(result_binder_count >= 0),
                    created_at TEXT NOT NULL,
                    UNIQUE(source_build_id, partition_version)
                );
                CREATE INDEX IF NOT EXISTS idx_type_partition_version
                  ON formal_type_partitions(partition_version, source_build_id);
                CREATE INDEX IF NOT EXISTS idx_type_partition_claim_version
                  ON formal_type_partitions(claim_version_id, source_build_id);
                CREATE TRIGGER IF NOT EXISTS formal_type_partitions_no_update
                BEFORE UPDATE ON formal_type_partitions BEGIN
                    SELECT RAISE(ABORT, 'formal type-partition observations are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS formal_type_partitions_no_delete
                BEFORE DELETE ON formal_type_partitions BEGIN
                    SELECT RAISE(ABORT, 'formal type-partition observations are immutable');
                END;

                CREATE TABLE IF NOT EXISTS formal_type_partition_refs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    partition_id INTEGER NOT NULL REFERENCES formal_type_partitions(id),
                    ref_kind TEXT NOT NULL CHECK(ref_kind IN ('binder-domain-head','result-ref')),
                    constant_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(partition_id, ref_kind, constant_name)
                );
                CREATE INDEX IF NOT EXISTS idx_type_partition_ref
                  ON formal_type_partition_refs(ref_kind, constant_name, partition_id);
                CREATE TRIGGER IF NOT EXISTS formal_type_partition_refs_no_update
                BEFORE UPDATE ON formal_type_partition_refs BEGIN
                    SELECT RAISE(ABORT, 'formal type-partition refs are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS formal_type_partition_refs_no_delete
                BEFORE DELETE ON formal_type_partition_refs BEGIN
                    SELECT RAISE(ABORT, 'formal type-partition refs are immutable');
                END;
                """
            )
            ensure_formal_binding_name_index(conn)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @staticmethod
    def _last_marker_block(output: str, begin_marker: str, end_marker: str) -> str | None:
        """Return the final inspector-owned block, resistant to earlier fake logs."""
        text = str(output or "")
        ends = list(re.finditer(re.escape(end_marker) + r"([^\s]+)", text))
        if not ends:
            return None
        end = ends[-1]
        name = end.group(1)
        prefix = text[: end.start()]
        begin_pattern = re.escape(begin_marker + name) + r"(?=\s|$)"
        begins = list(re.finditer(begin_pattern, prefix))
        if not begins:
            return None
        return prefix[begins[-1].end() :]

    @staticmethod
    def _unique_strings(values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(value) for value in values if str(value)))

    @classmethod
    def _shape_from_output(cls, output: str) -> dict[str, Any] | None:
        """Parse the final complete inspector-owned type-shape block."""
        body = cls._last_marker_block(
            output,
            "MATHHUB_TYPE_SHAPE_BEGIN:",
            "MATHHUB_TYPE_SHAPE_END:",
        )
        if body is None:
            return None
        version = re.search(r"MATHHUB_TYPE_SHAPE_VERSION:([^\s]+)", body)
        binders = re.search(r"MATHHUB_RESULT_BINDERS:([0-9]+)", body)
        if not (version and binders):
            return None
        head = re.search(r"MATHHUB_RESULT_HEAD:([^\s]+)", body)
        return {
            "shape_version": version.group(1),
            "result_head": head.group(1) if head else None,
            "result_binder_count": int(binders.group(1)),
        }

    @classmethod
    def _partition_from_output(cls, output: str) -> dict[str, Any] | None:
        """Parse positional constant evidence from the final result partition block."""
        body = cls._last_marker_block(
            output,
            "MATHHUB_TYPE_PARTITION_BEGIN:",
            "MATHHUB_TYPE_PARTITION_END:",
        )
        if body is None:
            return None
        version = re.search(r"MATHHUB_TYPE_PARTITION_VERSION:([^\s]+)", body)
        binders = re.search(r"MATHHUB_PARTITION_BINDERS:([0-9]+)", body)
        if not (version and binders):
            return None
        binder_heads = cls._unique_strings(
            re.findall(r"MATHHUB_BINDER_DOMAIN_HEAD:([^\s]+)", body)
        )
        result_refs = cls._unique_strings(
            re.findall(r"MATHHUB_RESULT_REF:([^\s]+)", body)
        )
        return {
            "partition_version": version.group(1),
            "result_binder_count": int(binders.group(1)),
            "binder_domain_heads": binder_heads,
            "result_refs": result_refs,
        }

    def record_build(
        self,
        proof: dict[str, Any],
        build: dict[str, Any],
        audit: dict[str, Any],
        environment: dict[str, Any],
    ) -> dict[str, Any] | None:
        cid = parse_ref(str(proof["claim_id"]), "CLM")
        vid = parse_ref(str(proof["claim_version_id"]), "VER")
        bid = parse_ref(str(build["build_id"]), "BLD")
        lean_name = str(proof.get("lean_name") or "")
        environment_hash = str(environment.get("environment_hash") or "")
        type_hash = str(audit.get("type_hash") or "")
        fingerprint = str(audit.get("formal_fingerprint") or "")
        output = str(audit.get("output") or "")

        shape = self._shape_from_output(output)
        if shape and environment_hash and type_hash:
            self.record_type_shape(
                claim_id=cid,
                claim_version_id=vid,
                source_build_id=bid,
                environment_hash=environment_hash,
                type_hash=type_hash,
                shape_version=str(shape["shape_version"]),
                result_head=shape["result_head"],
                result_binder_count=int(shape["result_binder_count"]),
            )

        partition = self._partition_from_output(output)
        if partition and environment_hash and type_hash:
            self.record_type_partition(
                claim_id=cid,
                claim_version_id=vid,
                source_build_id=bid,
                environment_hash=environment_hash,
                type_hash=type_hash,
                partition_version=str(partition["partition_version"]),
                result_binder_count=int(partition["result_binder_count"]),
                binder_domain_heads=list(partition["binder_domain_heads"]),
                result_refs=list(partition["result_refs"]),
            )

        if not (lean_name and environment_hash and type_hash and fingerprint):
            return None
        return self.record_binding(
            claim_id=cid,
            claim_version_id=vid,
            source_build_id=bid,
            lean_name=lean_name,
            environment_hash=environment_hash,
            type_hash=type_hash,
            formal_fingerprint=fingerprint,
            origin="local-build",
        )

    def record_type_shape(
        self,
        *,
        claim_id: int,
        claim_version_id: int,
        source_build_id: int,
        environment_hash: str,
        type_hash: str,
        shape_version: str,
        result_head: str | None,
        result_binder_count: int,
    ) -> dict[str, Any]:
        environment_hash = str(environment_hash or "").strip()
        type_hash = str(type_hash or "").strip()
        shape_version = str(shape_version or "").strip()
        head = str(result_head or "").strip() or None
        binder_count = int(result_binder_count)
        if not (environment_hash and type_hash and shape_version):
            raise ValueError("complete formal type-shape metadata is required")
        if binder_count < 0:
            raise ValueError("result_binder_count must be non-negative")
        expected = (
            int(claim_id), int(claim_version_id), int(source_build_id), environment_hash,
            type_hash, shape_version, head, binder_count,
        )
        with self.connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO formal_type_shapes(
                   claim_id,claim_version_id,source_build_id,environment_hash,type_hash,
                   shape_version,result_head,result_binder_count,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (*expected, utc_now()),
            )
            row = conn.execute(
                """SELECT * FROM formal_type_shapes
                   WHERE source_build_id=? AND shape_version=?""",
                (source_build_id, shape_version),
            ).fetchone()
        if row is None:
            raise RuntimeError("formal type-shape observation was not persisted")
        actual = (
            int(row["claim_id"]), int(row["claim_version_id"]), int(row["source_build_id"]),
            str(row["environment_hash"]), str(row["type_hash"]), str(row["shape_version"]),
            row["result_head"], int(row["result_binder_count"]),
        )
        if actual != expected:
            raise ValueError("conflicting formal type-shape evidence for the same Build/schema")
        return self._serialize_type_shape(row)

    def record_type_partition(
        self,
        *,
        claim_id: int,
        claim_version_id: int,
        source_build_id: int,
        environment_hash: str,
        type_hash: str,
        partition_version: str,
        result_binder_count: int,
        binder_domain_heads: list[str],
        result_refs: list[str],
    ) -> dict[str, Any]:
        environment_hash = str(environment_hash or "").strip()
        type_hash = str(type_hash or "").strip()
        partition_version = str(partition_version or "").strip()
        binder_count = int(result_binder_count)
        binder_heads = sorted(set(self._unique_strings(binder_domain_heads)))
        result_constants = sorted(set(self._unique_strings(result_refs)))
        if not (environment_hash and type_hash and partition_version):
            raise ValueError("complete formal type-partition metadata is required")
        if binder_count < 0:
            raise ValueError("result_binder_count must be non-negative")
        expected_meta = (
            int(claim_id), int(claim_version_id), int(source_build_id), environment_hash,
            type_hash, partition_version, binder_count,
        )
        expected_refs = {
            ("binder-domain-head", name) for name in binder_heads
        } | {
            ("result-ref", name) for name in result_constants
        }
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO formal_type_partitions(
                   claim_id,claim_version_id,source_build_id,environment_hash,type_hash,
                   partition_version,result_binder_count,created_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (*expected_meta, utc_now()),
            )
            row = conn.execute(
                """SELECT * FROM formal_type_partitions
                   WHERE source_build_id=? AND partition_version=?""",
                (source_build_id, partition_version),
            ).fetchone()
            if row is None:
                raise RuntimeError("formal type-partition observation was not persisted")
            actual_meta = (
                int(row["claim_id"]), int(row["claim_version_id"]), int(row["source_build_id"]),
                str(row["environment_hash"]), str(row["type_hash"]),
                str(row["partition_version"]), int(row["result_binder_count"]),
            )
            if actual_meta != expected_meta:
                raise ValueError("conflicting formal type-partition evidence for the same Build/schema")
            partition_id = int(row["id"])
            if cur.rowcount == 0:
                existing = {
                    (str(ref["ref_kind"]), str(ref["constant_name"]))
                    for ref in conn.execute(
                        "SELECT ref_kind,constant_name FROM formal_type_partition_refs WHERE partition_id=?",
                        (partition_id,),
                    ).fetchall()
                }
                if existing != expected_refs:
                    raise ValueError("conflicting formal type-partition refs for the same Build/schema")
            else:
                conn.executemany(
                    """INSERT INTO formal_type_partition_refs(
                       partition_id,ref_kind,constant_name,created_at
                       ) VALUES(?,?,?,?)""",
                    [
                        (partition_id, kind, name, utc_now())
                        for kind, name in sorted(expected_refs)
                    ],
                )
            refs = conn.execute(
                """SELECT ref_kind,constant_name FROM formal_type_partition_refs
                   WHERE partition_id=? ORDER BY ref_kind,constant_name""",
                (partition_id,),
            ).fetchall()
        return self._serialize_type_partition(row, refs)

    def record_binding(
        self,
        *,
        claim_id: int,
        claim_version_id: int,
        source_build_id: int | None,
        lean_name: str,
        environment_hash: str,
        type_hash: str,
        formal_fingerprint: str,
        origin: str,
    ) -> dict[str, Any] | None:
        lean_name = str(lean_name or "").strip()
        environment_hash = str(environment_hash or "").strip()
        type_hash = str(type_hash or "").strip()
        formal_fingerprint = str(formal_fingerprint or "").strip()
        origin = str(origin or "").strip() or "external-binding"
        if not (lean_name and environment_hash and type_hash and formal_fingerprint):
            raise ValueError("complete formal binding metadata is required")

        with self.connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO formal_bindings(
                    claim_id, claim_version_id, source_build_id, lean_name,
                    environment_hash, type_hash, formal_fingerprint, origin, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    claim_id, claim_version_id, source_build_id, lean_name,
                    environment_hash, type_hash, formal_fingerprint, origin, utc_now(),
                ),
            )
            if source_build_id is None:
                row = conn.execute(
                    """SELECT * FROM formal_bindings
                       WHERE claim_version_id=? AND source_build_id IS NULL
                         AND lean_name=? AND environment_hash=? AND type_hash=?
                       ORDER BY id DESC LIMIT 1""",
                    (claim_version_id, lean_name, environment_hash, type_hash),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT * FROM formal_bindings
                       WHERE claim_version_id=? AND source_build_id=?
                         AND lean_name=? AND environment_hash=? AND type_hash=?
                       ORDER BY id DESC LIMIT 1""",
                    (claim_version_id, source_build_id, lean_name, environment_hash, type_hash),
                ).fetchone()
        return None if row is None else self._serialize(row)

    def claim_for_name(self, lean_name: str, environment_hash: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = resolve_formal_binding_name_row(conn, lean_name, environment_hash)
        return None if row is None else self._serialize(row)

    def latest_for_claim(self, claim_internal_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = resolve_latest_formal_binding_for_claim_row(conn, claim_internal_id)
        return None if row is None else self._serialize(row)

    @staticmethod
    def _serialize(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "claim_id": claim_ref(int(row["claim_id"])),
            "claim_version_id": version_ref(int(row["claim_version_id"])),
            "source_build_id": build_ref(int(row["source_build_id"])) if row["source_build_id"] else None,
            "lean_name": row["lean_name"],
            "environment_hash": row["environment_hash"],
            "type_hash": row["type_hash"],
            "formal_fingerprint": row["formal_fingerprint"],
            "origin": row["origin"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _serialize_type_shape(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "claim_id": claim_ref(int(row["claim_id"])),
            "claim_version_id": version_ref(int(row["claim_version_id"])),
            "source_build_id": build_ref(int(row["source_build_id"])),
            "environment_hash": row["environment_hash"],
            "type_hash": row["type_hash"],
            "shape_version": row["shape_version"],
            "result_head": row["result_head"],
            "result_binder_count": int(row["result_binder_count"]),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _serialize_type_partition(
        row: sqlite3.Row, refs: list[sqlite3.Row]
    ) -> dict[str, Any]:
        binder_heads = [
            str(ref["constant_name"]) for ref in refs if ref["ref_kind"] == "binder-domain-head"
        ]
        result_refs = [
            str(ref["constant_name"]) for ref in refs if ref["ref_kind"] == "result-ref"
        ]
        return {
            "claim_id": claim_ref(int(row["claim_id"])),
            "claim_version_id": version_ref(int(row["claim_version_id"])),
            "source_build_id": build_ref(int(row["source_build_id"])),
            "environment_hash": row["environment_hash"],
            "type_hash": row["type_hash"],
            "partition_version": row["partition_version"],
            "result_binder_count": int(row["result_binder_count"]),
            "binder_domain_heads": binder_heads,
            "result_refs": result_refs,
            "created_at": row["created_at"],
        }
