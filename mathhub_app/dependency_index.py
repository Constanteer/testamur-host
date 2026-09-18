from __future__ import annotations

import sqlite3


PROOF_DEPENDENCY_ROUTE_INDEX = "idx_graph_dep_route_target"
LEGACY_ARGUMENT_PROOF_INDEX = "idx_arg_proof"
_ROUTE_COLUMNS = ["proof_id", "source_build_id", "target_claim_id", "target_claim_version_id"]


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _index_columns(conn: sqlite3.Connection, name: str) -> list[str]:
    return [
        str(row[2]) if row[2] is not None else ""
        for row in conn.execute(f"PRAGMA index_info('{name}')").fetchall()
    ]


def ensure_proof_dependency_route_index(conn: sqlite3.Connection) -> bool:
    """Own the canonical forward/same-route ProofDependency index.

    ``proof_claim_dependencies`` is owned by ``ArgumentIndex``. The canonical
    access path begins with ``(proof_id, source_build_id)`` and continues with the
    target Claim identity/revision used by graph queries. The longer index therefore
    subsumes the historical ``idx_arg_proof(proof_id,source_build_id)`` prefix.

    Existing databases keep the already-shipped ``idx_graph_dep_route_target`` name
    to avoid a needless large-index rename/rebuild. Once the canonical index exists,
    the shorter legacy prefix index is dropped. The reverse target-oriented index is
    deliberately not touched because ``used_by`` enters from target Claim identity.
    """
    if not _table_exists(conn, "proof_claim_dependencies"):
        return False

    current = _index_columns(conn, PROOF_DEPENDENCY_ROUTE_INDEX)
    if current[:4] != _ROUTE_COLUMNS:
        conn.execute(f"DROP INDEX IF EXISTS {PROOF_DEPENDENCY_ROUTE_INDEX}")
        conn.execute(
            f"""CREATE INDEX {PROOF_DEPENDENCY_ROUTE_INDEX}
                ON proof_claim_dependencies(
                  proof_id,source_build_id,target_claim_id,target_claim_version_id
                )"""
        )

    # Safe only after the stronger route index is present. Keep idx_arg_target: it
    # serves the inverse target->source access pattern and is not prefix-subsumed.
    conn.execute(f"DROP INDEX IF EXISTS {LEGACY_ARGUMENT_PROOF_INDEX}")
    return True


def proof_dependency_route_plan(
    conn: sqlite3.Connection,
    *,
    proof_id: int = 1,
    source_build_id: int = 1,
    target_claim_id: int = 1,
    target_claim_version_id: int = 1,
) -> list[str]:
    """Deterministic planner probe used by ownership regressions."""
    rows = conn.execute(
        """EXPLAIN QUERY PLAN
           SELECT 1 FROM proof_claim_dependencies d
           WHERE d.proof_id=?
             AND d.source_build_id=?
             AND d.target_claim_id=?
             AND d.target_claim_version_id=?
           LIMIT 1""",
        (proof_id, source_build_id, target_claim_id, target_claim_version_id),
    ).fetchall()
    return [str(row[3]) for row in rows]
