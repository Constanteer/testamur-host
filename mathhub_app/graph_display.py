from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from registry import parse_ref


class GraphDisplay:
    """Presentation-only canonical Lean names for graph/argument read models.

    Imported library theorems are represented formally inside MathHub by a stable
    local alias. Reader-facing mathematical surfaces should nevertheless show the
    external declaration name mathematicians and Lean users recognize. Decoration
    never mutates Claim/Proof ontology, FormalBinding evidence or ArgumentIndex.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _internal_claim_id(value: Any) -> int | None:
        try:
            return parse_ref(str(value or ""), "CLM")
        except ValueError:
            return None

    def preferred_names(self, claim_ids: list[int]) -> dict[int, str]:
        claim_ids = list(dict.fromkeys(int(value) for value in claim_ids if int(value) > 0))
        if not claim_ids:
            return {}
        placeholders = ",".join("?" for _ in claim_ids)
        with self.connect() as conn:
            available = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='formal_bindings'"
            ).fetchone()
            if not available:
                return {}
            rows = conn.execute(
                f"""SELECT cv.claim_id,
                           (
                             SELECT fb.lean_name FROM formal_bindings fb
                             WHERE fb.claim_version_id=cv.id
                               AND fb.origin='imported-declaration'
                             ORDER BY fb.id DESC LIMIT 1
                           ) external_lean_name
                    FROM claim_versions cv
                    WHERE cv.claim_id IN({placeholders})
                      AND cv.version_no=(
                        SELECT MAX(x.version_no) FROM claim_versions x
                        WHERE x.claim_id=cv.claim_id
                      )""",
                tuple(claim_ids),
            ).fetchall()
        return {
            int(row["claim_id"]): str(row["external_lean_name"])
            for row in rows
            if str(row["external_lean_name"] or "").strip()
        }

    @staticmethod
    def _decorate_record(record: dict[str, Any], claim_id: int | None, external: dict[int, str]) -> dict[str, Any]:
        item = dict(record)
        preferred = external.get(claim_id or -1)
        current = str(item.get("lean_name") or "")
        if preferred and preferred != current:
            item["formal_lean_name"] = current
            item["lean_name"] = preferred
            item["lean_name_source"] = "imported-declaration"
        elif claim_id is not None:
            item.setdefault("lean_name_source", "claim")
        return item

    def decorate(self, graph: dict[str, Any]) -> dict[str, Any]:
        nodes = graph.get("nodes") if isinstance(graph, dict) else None
        if not isinstance(nodes, list) or not nodes:
            return graph

        ids = [
            cid
            for node in nodes
            if isinstance(node, dict) and node.get("kind") != "cluster"
            for cid in [self._internal_claim_id(node.get("id"))]
            if cid is not None
        ]
        external = self.preferred_names(ids)
        decorated: list[dict[str, Any]] = []
        for node in nodes:
            if not isinstance(node, dict) or node.get("kind") == "cluster":
                decorated.append(node)
                continue
            decorated.append(
                self._decorate_record(node, self._internal_claim_id(node.get("id")), external)
            )
        return {**graph, "nodes": decorated}

    def decorate_argument(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Decorate all Claim-shaped records inside one ArgumentIndex read model."""
        if not isinstance(payload, dict):
            return payload
        claim = payload.get("claim") if isinstance(payload.get("claim"), dict) else None
        top_dependencies = payload.get("depends_on") if isinstance(payload.get("depends_on"), list) else []
        top_used_by = payload.get("used_by") if isinstance(payload.get("used_by"), list) else []
        proofs = payload.get("proofs") if isinstance(payload.get("proofs"), list) else []

        records: list[dict[str, Any]] = []
        if claim:
            records.append(claim)
        records.extend(item for item in top_dependencies if isinstance(item, dict))
        records.extend(item for item in top_used_by if isinstance(item, dict))
        for proof in proofs:
            if not isinstance(proof, dict):
                continue
            deps = proof.get("depends_on") if isinstance(proof.get("depends_on"), list) else []
            records.extend(item for item in deps if isinstance(item, dict))

        ids = [
            cid
            for record in records
            for cid in [self._internal_claim_id(record.get("claim_id"))]
            if cid is not None
        ]
        external = self.preferred_names(ids)
        if not external:
            return payload

        result = dict(payload)
        if claim:
            result["claim"] = self._decorate_record(
                claim, self._internal_claim_id(claim.get("claim_id")), external
            )
        result["depends_on"] = [
            self._decorate_record(item, self._internal_claim_id(item.get("claim_id")), external)
            if isinstance(item, dict) else item
            for item in top_dependencies
        ]
        result["used_by"] = [
            self._decorate_record(item, self._internal_claim_id(item.get("claim_id")), external)
            if isinstance(item, dict) else item
            for item in top_used_by
        ]

        decorated_proofs: list[Any] = []
        for proof in proofs:
            if not isinstance(proof, dict):
                decorated_proofs.append(proof)
                continue
            item = dict(proof)
            deps = proof.get("depends_on") if isinstance(proof.get("depends_on"), list) else []
            item["depends_on"] = [
                self._decorate_record(dep, self._internal_claim_id(dep.get("claim_id")), external)
                if isinstance(dep, dict) else dep
                for dep in deps
            ]
            decorated_proofs.append(item)
        result["proofs"] = decorated_proofs
        return result
