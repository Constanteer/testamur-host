from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


PROJECT_PREFIX = "prj_"
_VISIBILITIES = {"private", "public"}
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.strip().casefold()).strip("-")
    if not slug:
        raise ValueError("project name must contain at least one letter or number")
    return slug[:80].rstrip("-")


class TestamurProjectStore:
    """Product-level Project containers over canonical Sources and Watches.

    A Project is intentionally not a Source. It groups zero or more canonical
    Watches, and each Watch continues to own its Source identity/configuration.
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
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS testamur_projects(
                    project_id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    description TEXT,
                    visibility TEXT NOT NULL CHECK(visibility IN ('private', 'public')),
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS testamur_project_monitors(
                    project_id TEXT NOT NULL REFERENCES testamur_projects(project_id) ON DELETE CASCADE,
                    watch_id TEXT NOT NULL UNIQUE,
                    source_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, watch_id)
                );

                CREATE INDEX IF NOT EXISTS testamur_project_monitors_project_idx
                    ON testamur_project_monitors(project_id, created_at);
                CREATE INDEX IF NOT EXISTS testamur_project_monitors_source_idx
                    ON testamur_project_monitors(source_id);
                """
            )

    def create_project(
        self,
        *,
        name: str,
        description: str | None = None,
        visibility: str = "private",
    ) -> dict[str, Any]:
        normalized_name = str(name).strip()
        if not normalized_name:
            raise ValueError("project name must not be empty")
        if len(normalized_name) > 100:
            raise ValueError("project name must be at most 100 characters")
        slug = _slugify(normalized_name)
        normalized_description = None if description is None or not str(description).strip() else str(description).strip()
        if normalized_description is not None and len(normalized_description) > 500:
            raise ValueError("project description must be at most 500 characters")
        normalized_visibility = str(visibility).strip().casefold() or "private"
        if normalized_visibility not in _VISIBILITIES:
            raise ValueError("project visibility must be private or public")
        now = _utc_now()
        project_id = PROJECT_PREFIX + uuid.uuid4().hex
        record = {
            "project_id": project_id,
            "slug": slug,
            "name": normalized_name,
            "description": normalized_description,
            "visibility": normalized_visibility,
            "created_at": now,
            "updated_at": now,
            "semantics": {
                "project_is_container": True,
                "project_is_not_source": True,
                "monitors_are_project_children": True,
            },
        }
        try:
            with self.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO testamur_projects(
                        project_id, slug, name, description, visibility, record_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        slug,
                        normalized_name,
                        normalized_description,
                        normalized_visibility,
                        json.dumps(record, sort_keys=True, separators=(",", ":")),
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if "slug" in str(exc).casefold():
                raise ValueError("a project with this name/slug already exists in this workspace") from exc
            raise
        return record

    def get_project(self, project_ref: str) -> dict[str, Any] | None:
        ref = str(project_ref).strip()
        if not ref:
            return None
        with self.connect() as conn:
            row = conn.execute(
                "SELECT record_json FROM testamur_projects WHERE project_id = ? OR slug = ? LIMIT 1",
                (ref, ref.casefold()),
            ).fetchone()
        return None if row is None else json.loads(str(row["record_json"]))

    def list_projects(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT record_json FROM testamur_projects ORDER BY updated_at DESC, created_at DESC"
            ).fetchall()
        return [json.loads(str(row["record_json"])) for row in rows]

    def link_monitor(self, *, project_id: str, watch_id: str, source_id: str) -> None:
        project = self.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO testamur_project_monitors(project_id, watch_id, source_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (project["project_id"], str(watch_id), str(source_id), now),
            )
            conn.execute(
                "UPDATE testamur_projects SET updated_at = ? WHERE project_id = ?",
                (now, project["project_id"]),
            )

    def monitors_for_project(self, project_ref: str) -> list[dict[str, Any]]:
        project = self.get_project(project_ref)
        if project is None:
            raise KeyError(project_ref)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT project_id, watch_id, source_id, created_at
                FROM testamur_project_monitors
                WHERE project_id = ?
                ORDER BY created_at DESC, watch_id DESC
                """,
                (project["project_id"],),
            ).fetchall()
        return [
            {
                "project_id": str(row["project_id"]),
                "watch_id": str(row["watch_id"]),
                "source_id": str(row["source_id"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def project_for_watch(self, watch_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT p.record_json
                FROM testamur_project_monitors pm
                JOIN testamur_projects p ON p.project_id = pm.project_id
                WHERE pm.watch_id = ?
                LIMIT 1
                """,
                (str(watch_id),),
            ).fetchone()
        return None if row is None else json.loads(str(row["record_json"]))

    def project_id_for_watch(self, watch_id: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT project_id FROM testamur_project_monitors WHERE watch_id = ? LIMIT 1",
                (str(watch_id),),
            ).fetchone()
        return None if row is None else str(row["project_id"])

    def stats(self) -> dict[str, int]:
        with self.connect() as conn:
            projects = int(conn.execute("SELECT COUNT(*) FROM testamur_projects").fetchone()[0])
            links = int(conn.execute("SELECT COUNT(*) FROM testamur_project_monitors").fetchone()[0])
        return {"projects": projects, "project_monitors": links}
