from __future__ import annotations

import sqlite3


FORMAL_BINDING_NAME_INDEX = "idx_graph_binding_name_env_claim"
LEGACY_FORMAL_BINDING_NAME_INDEX = "idx_binding_name_env"
_BINDING_COLUMNS = ["lean_name", "environment_hash", "claim_id", "claim_version_id"]


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def ensure_formal_binding_name_index(conn: sqlite3.Connection) -> bool:
    """Own the canonical exact-name FormalBinding lookup index.

    ``formal_bindings`` is owned by ``FormalIndex``. Exact declaration-name reads
    enter through ``(lean_name, environment_hash)``; graph queries additionally
    constrain current mathematical Claim identity/revision. The stronger canonical
    index therefore prefix-covers the historical
    ``idx_binding_name_env(lean_name, environment_hash)`` index.

    The already-shipped ``idx_graph_*`` name is retained to avoid a needless large
    index rename/rebuild. Once the canonical shape exists, the shorter legacy prefix
    index is dropped. Fingerprint and external-uniqueness indexes are deliberately
    untouched because they serve different leading-key/uniqueness semantics.
    """
    if not _table_exists(conn, "formal_bindings"):
        return False

    columns = [
        str(row[2]) if row[2] is not None else ""
        for row in conn.execute(
            f"PRAGMA index_info('{FORMAL_BINDING_NAME_INDEX}')"
        ).fetchall()
    ]
    if columns[:4] != _BINDING_COLUMNS:
        conn.execute(f"DROP INDEX IF EXISTS {FORMAL_BINDING_NAME_INDEX}")
        conn.execute(
            f"""CREATE INDEX {FORMAL_BINDING_NAME_INDEX}
                ON formal_bindings(
                  lean_name,environment_hash,claim_id,claim_version_id
                )"""
        )

    # Safe only after the stronger source-of-truth-owner index exists.
    conn.execute(f"DROP INDEX IF EXISTS {LEGACY_FORMAL_BINDING_NAME_INDEX}")
    return True


def resolve_formal_binding_name_row(
    conn: sqlite3.Connection, lean_name: str, environment_hash: str
) -> sqlite3.Row | None:
    """Resolve one exact name/environment binding without an ORDER BY temp sort.

    This deliberately preserves the historical ``claim_for_name`` priority exactly:

      1. among matching bindings, prefer the greatest Claim ``version_no``;
      2. among bindings at that version number, prefer the greatest binding ``id``.

    The two priorities are expressed as scalar ``MAX`` aggregates rather than a
    cross-table ``ORDER BY cv.version_no DESC, fb.id DESC``. The canonical name/env
    index supplies the candidate range; ``claim_versions`` is reached by primary
    key. No ontology or import-dedup semantics change as part of this planner fix.
    """
    name = str(lean_name or "")
    environment = str(environment_hash or "")

    version = conn.execute(
        """SELECT MAX(cv.version_no)
           FROM formal_bindings fb
           JOIN claim_versions cv ON cv.id=fb.claim_version_id
           WHERE fb.lean_name=? AND fb.environment_hash=?""",
        (name, environment),
    ).fetchone()
    if version is None or version[0] is None:
        return None

    binding = conn.execute(
        """SELECT MAX(fb.id)
           FROM formal_bindings fb
           JOIN claim_versions cv ON cv.id=fb.claim_version_id
           WHERE fb.lean_name=? AND fb.environment_hash=?
             AND cv.version_no=?""",
        (name, environment, int(version[0])),
    ).fetchone()
    if binding is None or binding[0] is None:
        return None
    return conn.execute(
        "SELECT * FROM formal_bindings WHERE id=?", (int(binding[0]),)
    ).fetchone()


def resolve_latest_formal_binding_for_claim_row(
    conn: sqlite3.Connection, claim_id: int
) -> sqlite3.Row | None:
    """Resolve the newest binding on one Claim's current revision without scans.

    The current Claim revision is obtained from ``idx_versions_claim``. The immutable
    FormalBinding table already has a UNIQUE index beginning with
    ``claim_version_id``; constraining both Claim identity and that revision lets the
    second aggregate select ``MAX(id)`` without adding another owner-local index.
    """
    current = conn.execute(
        """SELECT id FROM claim_versions
           WHERE claim_id=? ORDER BY version_no DESC LIMIT 1""",
        (int(claim_id),),
    ).fetchone()
    if current is None:
        return None
    binding = conn.execute(
        """SELECT MAX(id) FROM formal_bindings
           WHERE claim_id=? AND claim_version_id=?""",
        (int(claim_id), int(current[0])),
    ).fetchone()
    if binding is None or binding[0] is None:
        return None
    return conn.execute(
        "SELECT * FROM formal_bindings WHERE id=?", (int(binding[0]),)
    ).fetchone()


def formal_binding_name_plan(
    conn: sqlite3.Connection, lean_name: str, environment_hash: str
) -> list[str]:
    """Planner probe for the sort-free standalone FormalIndex lookup stages."""
    rows: list[sqlite3.Row] = []
    rows.extend(
        conn.execute(
            """EXPLAIN QUERY PLAN
               SELECT MAX(cv.version_no)
               FROM formal_bindings fb
               JOIN claim_versions cv ON cv.id=fb.claim_version_id
               WHERE fb.lean_name=? AND fb.environment_hash=?""",
            (lean_name, environment_hash),
        ).fetchall()
    )
    rows.extend(
        conn.execute(
            """EXPLAIN QUERY PLAN
               SELECT MAX(fb.id)
               FROM formal_bindings fb
               JOIN claim_versions cv ON cv.id=fb.claim_version_id
               WHERE fb.lean_name=? AND fb.environment_hash=?
                 AND cv.version_no=?""",
            (lean_name, environment_hash, 1),
        ).fetchall()
    )
    return [str(row[3]) for row in rows]


def latest_formal_binding_for_claim_plan(
    conn: sqlite3.Connection, claim_id: int
) -> list[str]:
    """Planner probe for current-revision Claim -> FormalBinding resolution."""
    rows: list[sqlite3.Row] = []
    rows.extend(
        conn.execute(
            """EXPLAIN QUERY PLAN
               SELECT id FROM claim_versions
               WHERE claim_id=? ORDER BY version_no DESC LIMIT 1""",
            (int(claim_id),),
        ).fetchall()
    )
    rows.extend(
        conn.execute(
            """EXPLAIN QUERY PLAN
               SELECT MAX(id) FROM formal_bindings
               WHERE claim_id=? AND claim_version_id=?""",
            (int(claim_id), 1),
        ).fetchall()
    )
    return [str(row[3]) for row in rows]
