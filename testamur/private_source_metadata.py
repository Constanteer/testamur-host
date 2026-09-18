from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class TestamurPrivateSourceMetadataStore:
    """Erasable owner-only convenience metadata for Sources.

    This table is intentionally *not* append-only evidence. Values such as a
    client filename can be sensitive and must be renameable/deletable without
    changing Source/Revision identity. Hosted deployments should protect this
    table with the same tenant isolation and encryption boundary as other
    account-private metadata.
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
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS testamur_private_source_metadata(
                  source_id TEXT PRIMARY KEY,
                  display_name TEXT,
                  media_type TEXT,
                  updated_at TEXT NOT NULL
                );
                """
            )

    def set(
        self,
        source_id: str,
        *,
        display_name: str | None = None,
        media_type: str | None = None,
    ) -> dict[str, Any]:
        resolved_source_id = str(source_id).strip()
        if not resolved_source_id:
            raise ValueError("source_id must not be empty")
        resolved_name = None if display_name is None else str(display_name).strip() or None
        resolved_media = None if media_type is None else str(media_type).strip() or None
        updated_at = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO testamur_private_source_metadata(
                     source_id,display_name,media_type,updated_at
                   ) VALUES(?,?,?,?)
                   ON CONFLICT(source_id) DO UPDATE SET
                     display_name=excluded.display_name,
                     media_type=excluded.media_type,
                     updated_at=excluded.updated_at""",
                (resolved_source_id, resolved_name, resolved_media, updated_at),
            )
        return {
            "source_id": resolved_source_id,
            "display_name": resolved_name,
            "media_type": resolved_media,
            "updated_at": updated_at,
            "erasable": True,
            "part_of_evidence_identity": False,
        }

    def get(self, source_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT source_id,display_name,media_type,updated_at
                   FROM testamur_private_source_metadata WHERE source_id=?""",
                (str(source_id),),
            ).fetchone()
        if row is None:
            return None
        return {
            "source_id": str(row["source_id"]),
            "display_name": row["display_name"],
            "media_type": row["media_type"],
            "updated_at": str(row["updated_at"]),
            "erasable": True,
            "part_of_evidence_identity": False,
        }

    def delete(self, source_id: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM testamur_private_source_metadata WHERE source_id=?",
                (str(source_id),),
            )
        return int(cursor.rowcount or 0) > 0
