from __future__ import annotations

import re
import sqlite3
from typing import Any


ACTIVE_ARGUMENT_BUILD_INDEX = "idx_graph_builds_active_argument"
_PREFERRED_STATUSES = ("verified", "partial", "failed")
_SQL_ALIAS_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _sql_alias(value: str, label: str) -> str:
    value = str(value or "")
    if not _SQL_ALIAS_RE.fullmatch(value):
        raise ValueError(f"invalid internal SQL {label}: {value!r}")
    return value


def active_argument_build_id_sql(proof_alias: str = "p", *, build_alias: str = "ab") -> str:
    """Return the canonical scalar SQL expression for the active argument Build.

    The mathematical/evidentiary rule is unchanged from the original selector:
    only ``audit_ok=1`` Builds are eligible; status priority is
    verified > partial > failed > any legacy/unknown status; ties use newest id.

    It is written as short-circuit equality probes rather than one CASE-ordered
    subquery so SQLite can use the partial ``(proof_id,status,id DESC)`` index
    without a temporary sort in correlated graph reads.
    """
    proof_alias = _sql_alias(proof_alias, "proof alias")
    build_alias = _sql_alias(build_alias, "Build alias")
    pieces = []
    for status in _PREFERRED_STATUSES:
        pieces.append(
            f"""(SELECT {build_alias}.id FROM builds {build_alias}
                 WHERE {build_alias}.proof_id={proof_alias}.id
                   AND {build_alias}.audit_ok=1
                   AND {build_alias}.status='{status}'
                 ORDER BY {build_alias}.id DESC LIMIT 1)"""
        )
    pieces.append(
        f"""(SELECT MAX({build_alias}.id) FROM builds {build_alias}
             WHERE {build_alias}.proof_id={proof_alias}.id
               AND {build_alias}.audit_ok=1
               AND {build_alias}.status NOT IN ('verified','partial','failed'))"""
    )
    return "COALESCE(\n  " + ",\n  ".join(pieces) + "\n)"


def active_argument_build_row(conn: sqlite3.Connection, proof_id: int) -> sqlite3.Row | None:
    """Return the canonical active complete argument Build for one Proof."""
    proof_id = int(proof_id)
    row = conn.execute(
        """SELECT * FROM builds WHERE id=COALESCE(
             (SELECT id FROM builds WHERE proof_id=? AND audit_ok=1 AND status='verified'
              ORDER BY id DESC LIMIT 1),
             (SELECT id FROM builds WHERE proof_id=? AND audit_ok=1 AND status='partial'
              ORDER BY id DESC LIMIT 1),
             (SELECT id FROM builds WHERE proof_id=? AND audit_ok=1 AND status='failed'
              ORDER BY id DESC LIMIT 1),
             (SELECT MAX(id) FROM builds WHERE proof_id=? AND audit_ok=1
              AND status NOT IN ('verified','partial','failed'))
           )""",
        (proof_id, proof_id, proof_id, proof_id),
    ).fetchone()
    return row


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def ensure_active_argument_build_index(conn: sqlite3.Connection) -> bool:
    """Install or one-time migrate the operational active-Build index.

    Returns ``True`` when the current index shape is available after the call.
    The historical ``idx_graph_*`` name is retained to avoid another large-index
    rename/rebuild; ownership is now shared rather than graph-query-specific.
    """
    if not _table_exists(conn, "builds"):
        return False

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
        (ACTIVE_ARGUMENT_BUILD_INDEX,),
    ).fetchone()
    columns = [
        str(info[2]) if info[2] is not None else ""
        for info in conn.execute(
            f"PRAGMA index_info('{ACTIVE_ARGUMENT_BUILD_INDEX}')"
        ).fetchall()
    ]
    definition = "" if row is None or row[0] is None else "".join(str(row[0]).lower().split())
    current = (
        columns[:3] == ["proof_id", "status", "id"]
        and "whereaudit_ok=1" in definition
    )
    if not current:
        conn.execute(f"DROP INDEX IF EXISTS {ACTIVE_ARGUMENT_BUILD_INDEX}")
        conn.execute(
            f"""CREATE INDEX {ACTIVE_ARGUMENT_BUILD_INDEX}
                ON builds(proof_id,status,id DESC)
                WHERE audit_ok=1"""
        )
    return True


def active_argument_build_plan(conn: sqlite3.Connection, proof_id: int) -> list[str]:
    """Small diagnostic helper used by deterministic planner regressions."""
    rows = conn.execute(
        """EXPLAIN QUERY PLAN
           SELECT * FROM builds WHERE id=COALESCE(
             (SELECT id FROM builds WHERE proof_id=? AND audit_ok=1 AND status='verified'
              ORDER BY id DESC LIMIT 1),
             (SELECT id FROM builds WHERE proof_id=? AND audit_ok=1 AND status='partial'
              ORDER BY id DESC LIMIT 1),
             (SELECT id FROM builds WHERE proof_id=? AND audit_ok=1 AND status='failed'
              ORDER BY id DESC LIMIT 1),
             (SELECT MAX(id) FROM builds WHERE proof_id=? AND audit_ok=1
              AND status NOT IN ('verified','partial','failed'))
           )""",
        (proof_id, proof_id, proof_id, proof_id),
    ).fetchall()
    return [str(row[3]) for row in rows]
