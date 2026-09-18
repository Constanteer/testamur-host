from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from formal_index import TYPE_PARTITION_VERSION, TYPE_SHAPE_VERSION
from registry import build_ref, claim_ref, parse_ref, proof_ref, version_ref


class SourceProvenance:
    """Evidence-only formal source read model.

    This layer does not infer mathematical relations or source-module ownership.
    It joins current FormalBindings to their Build witnesses and explains how a
    Proof's verification/argument witnesses relate to an imported declaration.
    Formal type-shape and type-partition rows are exposed as exact Build evidence
    so structural-search hits can be audited from the Claim/Proof UI without
    changing the ontology or manufacturing ProofDependency edges.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None

    @staticmethod
    def _build_ref_id(value: Any) -> int | None:
        try:
            return parse_ref(str(value or ""), "BLD")
        except ValueError:
            return None

    @staticmethod
    def _compact_build(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        minimal_imports: list[str] = []
        try:
            minimal_imports = list(json.loads(row["minimal_imports_json"] or "[]"))
        except (json.JSONDecodeError, TypeError):
            pass
        return {
            "build_id": build_ref(int(row["id"])),
            "proof_id": proof_ref(int(row["proof_id"])),
            "status": row["status"],
            "kernel_ok": bool(row["kernel_ok"]),
            "audit_ok": None if row["audit_ok"] is None else bool(row["audit_ok"]),
            "lean_version": row["lean_version"],
            "lake_version": row["lake_version"],
            "mathlib_revision": row["mathlib_revision"],
            "environment_hash": row["environment_hash"],
            "minimal_imports": minimal_imports,
            "created_at": row["created_at"],
        }

    def _builds(self, conn: sqlite3.Connection, ids: set[int]) -> dict[int, dict[str, Any]]:
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"""SELECT id,proof_id,status,kernel_ok,audit_ok,lean_version,lake_version,
                       mathlib_revision,environment_hash,minimal_imports_json,created_at
                FROM builds WHERE id IN({placeholders})""",
            tuple(sorted(ids)),
        ).fetchall()
        return {int(row["id"]): self._compact_build(row) for row in rows if row is not None}

    def _type_shapes(self, conn: sqlite3.Connection, ids: set[int]) -> dict[int, dict[str, Any]]:
        if not ids or not self._table_exists(conn, "formal_type_shapes"):
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"""SELECT source_build_id,environment_hash,type_hash,shape_version,
                       result_head,result_binder_count,created_at
                FROM formal_type_shapes
                WHERE source_build_id IN({placeholders}) AND shape_version=?
                ORDER BY id DESC""",
            tuple(sorted(ids)) + (TYPE_SHAPE_VERSION,),
        ).fetchall()
        result: dict[int, dict[str, Any]] = {}
        for row in rows:
            bid = int(row["source_build_id"])
            result.setdefault(
                bid,
                {
                    "shape_version": row["shape_version"],
                    "result_head": row["result_head"],
                    "result_binder_count": int(row["result_binder_count"]),
                    "environment_hash": row["environment_hash"],
                    "type_hash": row["type_hash"],
                    "created_at": row["created_at"],
                },
            )
        return result

    def _type_partitions(self, conn: sqlite3.Connection, ids: set[int]) -> dict[int, dict[str, Any]]:
        if (
            not ids
            or not self._table_exists(conn, "formal_type_partitions")
            or not self._table_exists(conn, "formal_type_partition_refs")
        ):
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"""SELECT tp.id partition_id,tp.source_build_id,tp.environment_hash,tp.type_hash,
                       tp.partition_version,tp.result_binder_count,tp.created_at,
                       tr.ref_kind,tr.constant_name
                FROM formal_type_partitions tp
                LEFT JOIN formal_type_partition_refs tr ON tr.partition_id=tp.id
                WHERE tp.source_build_id IN({placeholders}) AND tp.partition_version=?
                ORDER BY tp.id DESC,tr.ref_kind,tr.constant_name""",
            tuple(sorted(ids)) + (TYPE_PARTITION_VERSION,),
        ).fetchall()
        result: dict[int, dict[str, Any]] = {}
        for row in rows:
            bid = int(row["source_build_id"])
            item = result.setdefault(
                bid,
                {
                    "partition_version": row["partition_version"],
                    "result_binder_count": int(row["result_binder_count"]),
                    "binder_domain_heads": [],
                    "result_refs": [],
                    "environment_hash": row["environment_hash"],
                    "type_hash": row["type_hash"],
                    "created_at": row["created_at"],
                },
            )
            name = row["constant_name"]
            if name is None:
                continue
            kind = str(row["ref_kind"] or "")
            target = item["binder_domain_heads"] if kind == "binder-domain-head" else (
                item["result_refs"] if kind == "result-ref" else None
            )
            if target is not None and str(name) not in target:
                target.append(str(name))
        return result

    def for_claim(self, claim_internal_id: int, routes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        routes = [route for route in (routes or []) if isinstance(route, dict)]
        with self.connect() as conn:
            current = conn.execute(
                "SELECT * FROM claim_versions WHERE claim_id=? ORDER BY version_no DESC LIMIT 1",
                (claim_internal_id,),
            ).fetchone()
            if current is None:
                raise KeyError(claim_ref(claim_internal_id))
            version_id = int(current["id"])
            binding_rows = conn.execute(
                """SELECT * FROM formal_bindings
                   WHERE claim_id=? AND claim_version_id=?
                   ORDER BY CASE origin WHEN 'imported-declaration' THEN 0 WHEN 'local-build' THEN 1 ELSE 2 END,
                            id ASC""",
                (claim_internal_id, version_id),
            ).fetchall()

            build_ids: set[int] = {
                int(row["source_build_id"])
                for row in binding_rows
                if row["source_build_id"] is not None
            }
            for route in routes:
                verification = route.get("verification") if isinstance(route.get("verification"), dict) else {}
                for value in (verification.get("build_id"), route.get("argument_build_id")):
                    bid = self._build_ref_id(value)
                    if bid is not None:
                        build_ids.add(bid)
            builds = self._builds(conn, build_ids)
            shapes = self._type_shapes(conn, build_ids)
            partitions = self._type_partitions(conn, build_ids)
            for bid, shape in shapes.items():
                if bid in builds:
                    builds[bid]["type_shape"] = shape
            for bid, partition in partitions.items():
                if bid in builds:
                    builds[bid]["type_partition"] = partition

        bindings: list[dict[str, Any]] = []
        external_names: list[str] = []
        local_names: list[str] = []
        imported_build_ids: set[int] = set()
        for row in binding_rows:
            bid = int(row["source_build_id"]) if row["source_build_id"] is not None else None
            origin = str(row["origin"] or "")
            lean_name = str(row["lean_name"] or "")
            if origin == "imported-declaration":
                external_names.append(lean_name)
                if bid is not None:
                    imported_build_ids.add(bid)
            elif origin == "local-build":
                local_names.append(lean_name)
            bindings.append(
                {
                    "lean_name": lean_name,
                    "origin": origin,
                    "source_build_id": build_ref(bid) if bid is not None else None,
                    "environment_hash": row["environment_hash"],
                    "type_hash": row["type_hash"],
                    "formal_fingerprint": row["formal_fingerprint"],
                    "created_at": row["created_at"],
                    "source_build": builds.get(bid) if bid is not None else None,
                }
            )

        external_names = list(dict.fromkeys(external_names))
        local_names = list(dict.fromkeys(local_names))
        source_kind = "imported-library" if external_names else ("local" if local_names else "unbound")

        import_pairs: list[dict[str, Any]] = []
        for imported in [item for item in bindings if item["origin"] == "imported-declaration"]:
            source_build_id = imported.get("source_build_id")
            aliases = [
                item["lean_name"]
                for item in bindings
                if item["origin"] == "local-build" and item.get("source_build_id") == source_build_id
            ]
            import_pairs.append(
                {
                    "external_declaration": imported["lean_name"],
                    "verification_aliases": list(dict.fromkeys(aliases)),
                    "source_build_id": source_build_id,
                    "environment_hash": imported["environment_hash"],
                    "type_hash": imported["type_hash"],
                    "formal_fingerprint": imported["formal_fingerprint"],
                    "type_shape": (imported.get("source_build") or {}).get("type_shape"),
                    "type_partition": (imported.get("source_build") or {}).get("type_partition"),
                }
            )

        witness_rows: list[dict[str, Any]] = []
        for route in routes:
            verification = route.get("verification") if isinstance(route.get("verification"), dict) else {}
            verification_id = self._build_ref_id(verification.get("build_id"))
            argument_id = self._build_ref_id(route.get("argument_build_id"))
            argument_is_external = argument_id is not None and argument_id in imported_build_ids
            verification_is_bridge = verification_id is not None and verification_id in imported_build_ids
            witness_rows.append(
                {
                    "proof_id": route.get("proof_id"),
                    "proof_author": route.get("proof_author"),
                    "imports": list(route.get("imports") or []),
                    "verification_build_id": build_ref(verification_id) if verification_id is not None else None,
                    "verification_build": builds.get(verification_id) if verification_id is not None else None,
                    "verification_role": "import-bridge" if verification_is_bridge else (
                        "local-proof" if verification_id is not None else "unavailable"
                    ),
                    "argument_build_id": build_ref(argument_id) if argument_id is not None else None,
                    "argument_build": builds.get(argument_id) if argument_id is not None else None,
                    "argument_evidence_origin": "external-declaration-proof-expr" if argument_is_external else (
                        "local-proof-expr" if argument_id is not None else "unavailable"
                    ),
                    "argument_index_complete": bool(route.get("argument_index_complete")),
                }
            )

        return {
            "kind": source_kind,
            "claim_id": claim_ref(claim_internal_id),
            "claim_version_id": version_ref(version_id),
            "claim_version": int(current["version_no"]),
            "claim_lean_name": current["lean_name"],
            "external_declarations": external_names,
            "local_declarations": local_names,
            "bindings": bindings,
            "import_pairs": import_pairs,
            "proof_witnesses": witness_rows,
            "source_module": None,
            "source_module_status": "not-recorded",
            "source_module_note": "MathHub does not infer Lean source-module ownership from declaration namespaces.",
        }
