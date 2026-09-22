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

                CREATE TABLE IF NOT EXISTS testamur_project_repository_bindings(
                    binding_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES testamur_projects(project_id) ON DELETE CASCADE,
                    binding_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, binding_key)
                );

                CREATE TABLE IF NOT EXISTS testamur_project_repository_binding_revisions(
                    binding_revision_id TEXT PRIMARY KEY,
                    binding_id TEXT NOT NULL REFERENCES testamur_project_repository_bindings(binding_id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    parent_revision_id TEXT,
                    kind TEXT NOT NULL CHECK(kind IN ('local-path', 'git')),
                    locator TEXT NOT NULL,
                    git_ref TEXT,
                    record_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    UNIQUE(binding_id, ordinal)
                );

                CREATE INDEX IF NOT EXISTS testamur_project_monitors_project_idx
                    ON testamur_project_monitors(project_id, created_at);
                CREATE INDEX IF NOT EXISTS testamur_project_monitors_source_idx
                    ON testamur_project_monitors(source_id);
                CREATE INDEX IF NOT EXISTS testamur_project_repository_bindings_project_idx
                    ON testamur_project_repository_bindings(project_id, binding_key);
                CREATE INDEX IF NOT EXISTS testamur_project_repository_binding_revisions_binding_idx
                    ON testamur_project_repository_binding_revisions(binding_id, ordinal DESC);
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

    def bind_repository(
        self,
        project_ref: str,
        *,
        locator: str,
        kind: str = "local-path",
        git_ref: str | None = None,
        binding_key: str = "primary",
    ) -> dict[str, Any]:
        """Create or revise a stable Project-to-repository binding.

        A repository binding is scanner input metadata. It is not a Source and
        does not imply that repository contents were fetched, relied upon, or
        verified.
        """

        project = self.get_project(project_ref)
        if project is None:
            raise KeyError(project_ref)
        normalized_kind = str(kind).strip().casefold()
        if normalized_kind not in _REPOSITORY_BINDING_KINDS:
            raise ValueError("repository binding kind must be local-path or git")
        key = str(binding_key).strip() or "primary"
        if len(key) > 80:
            raise ValueError("repository binding key must be at most 80 characters")

        if normalized_kind == "local-path":
            path = Path(locator).expanduser().resolve()
            if not path.is_dir():
                raise ValueError(f"repository binding path is not a directory: {path}")
            normalized_locator = str(path)
            normalized_git_ref = None
        else:
            normalized_locator = str(locator).strip()
            if not normalized_locator:
                raise ValueError("git repository locator must not be empty")
            normalized_git_ref = None if git_ref is None or not str(git_ref).strip() else str(git_ref).strip()

        now = _utc_now()
        project_id = str(project["project_id"])
        with self.connect() as conn:
            binding = conn.execute(
                """
                SELECT binding_id, created_at
                FROM testamur_project_repository_bindings
                WHERE project_id = ? AND binding_key = ?
                LIMIT 1
                """,
                (project_id, key),
            ).fetchone()
            created = binding is None
            if binding is None:
                binding_id = REPOSITORY_BINDING_PREFIX + uuid.uuid4().hex
                conn.execute(
                    """
                    INSERT INTO testamur_project_repository_bindings(
                        binding_id, project_id, binding_key, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (binding_id, project_id, key, now, now),
                )
                ordinal = 1
                parent_revision_id = None
            else:
                binding_id = str(binding["binding_id"])
                latest = conn.execute(
                    """
                    SELECT record_json
                    FROM testamur_project_repository_binding_revisions
                    WHERE binding_id = ?
                    ORDER BY ordinal DESC
                    LIMIT 1
                    """,
                    (binding_id,),
                ).fetchone()
                if latest is None:
                    ordinal = 1
                    parent_revision_id = None
                else:
                    current = json.loads(str(latest["record_json"]))
                    if (
                        current.get("kind") == normalized_kind
                        and current.get("locator") == normalized_locator
                        and current.get("git_ref") == normalized_git_ref
                    ):
                        return {
                            "binding": {
                                "binding_id": binding_id,
                                "project_id": project_id,
                                "binding_key": key,
                                "created_at": str(binding["created_at"]),
                            },
                            "revision": current,
                            "created": False,
                            "changed": False,
                        }
                    ordinal = int(current["ordinal"]) + 1
                    parent_revision_id = str(current["binding_revision_id"])

            revision_id = REPOSITORY_BINDING_REVISION_PREFIX + uuid.uuid4().hex
            record = {
                "binding_revision_id": revision_id,
                "binding_id": binding_id,
                "project_id": project_id,
                "binding_key": key,
                "ordinal": ordinal,
                "parent_revision_id": parent_revision_id,
                "kind": normalized_kind,
                "locator": normalized_locator,
                "git_ref": normalized_git_ref,
                "recorded_at": now,
                "semantics": {
                    "project_repository_binding_is_source": False,
                    "binding_is_scanner_input": True,
                    "binding_implies_repository_content_observed": False,
                    "binding_implies_reliance": False,
                },
            }
            conn.execute(
                """
                INSERT INTO testamur_project_repository_binding_revisions(
                    binding_revision_id, binding_id, ordinal, parent_revision_id,
                    kind, locator, git_ref, record_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    binding_id,
                    ordinal,
                    parent_revision_id,
                    normalized_kind,
                    normalized_locator,
                    normalized_git_ref,
                    json.dumps(record, sort_keys=True, separators=(",", ":")),
                    now,
                ),
            )
            conn.execute(
                "UPDATE testamur_project_repository_bindings SET updated_at = ? WHERE binding_id = ?",
                (now, binding_id),
            )
            conn.execute(
                "UPDATE testamur_projects SET updated_at = ? WHERE project_id = ?",
                (now, project_id),
            )

        return {
            "binding": {
                "binding_id": binding_id,
                "project_id": project_id,
                "binding_key": key,
                "created_at": now if created else str(binding["created_at"]),
            },
            "revision": record,
            "created": created,
            "changed": True,
        }

    def repository_binding(
        self,
        project_ref: str,
        *,
        binding_key: str = "primary",
    ) -> dict[str, Any] | None:
        project = self.get_project(project_ref)
        if project is None:
            raise KeyError(project_ref)
        key = str(binding_key).strip() or "primary"
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT b.binding_id, b.binding_key, b.created_at, r.record_json
                FROM testamur_project_repository_bindings AS b
                JOIN testamur_project_repository_binding_revisions AS r
                  ON r.binding_id = b.binding_id
                WHERE b.project_id = ? AND b.binding_key = ?
                ORDER BY r.ordinal DESC
                LIMIT 1
                """,
                (project["project_id"], key),
            ).fetchone()
        if row is None:
            return None
        return {
            "binding_id": str(row["binding_id"]),
            "project_id": str(project["project_id"]),
            "binding_key": str(row["binding_key"]),
            "created_at": str(row["created_at"]),
            "revision": json.loads(str(row["record_json"])),
        }

    def repository_bindings(self, project_ref: str) -> list[dict[str, Any]]:
        project = self.get_project(project_ref)
        if project is None:
            raise KeyError(project_ref)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT b.binding_id, b.binding_key, b.created_at, r.record_json
                FROM testamur_project_repository_bindings AS b
                JOIN testamur_project_repository_binding_revisions AS r
                  ON r.binding_id = b.binding_id
                WHERE b.project_id = ?
                  AND r.ordinal = (
                    SELECT MAX(r2.ordinal)
                    FROM testamur_project_repository_binding_revisions AS r2
                    WHERE r2.binding_id = b.binding_id
                  )
                ORDER BY b.binding_key, b.binding_id
                """,
                (project["project_id"],),
            ).fetchall()
        return [
            {
                "binding_id": str(row["binding_id"]),
                "project_id": str(project["project_id"]),
                "binding_key": str(row["binding_key"]),
                "created_at": str(row["created_at"]),
                "revision": json.loads(str(row["record_json"])),
            }
            for row in rows
        ]

    def repository_binding_history(
        self,
        project_ref: str,
        *,
        binding_key: str = "primary",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        project = self.get_project(project_ref)
        if project is None:
            raise KeyError(project_ref)
        key = str(binding_key).strip() or "primary"
        bounded = max(1, min(int(limit), 500))
        with self.connect() as conn:
            binding = conn.execute(
                """
                SELECT binding_id
                FROM testamur_project_repository_bindings
                WHERE project_id = ? AND binding_key = ?
                LIMIT 1
                """,
                (project["project_id"], key),
            ).fetchone()
            if binding is None:
                return []
            rows = conn.execute(
                """
                SELECT record_json
                FROM testamur_project_repository_binding_revisions
                WHERE binding_id = ?
                ORDER BY ordinal DESC
                LIMIT ?
                """,
                (binding["binding_id"], bounded),
            ).fetchall()
        return [json.loads(str(row["record_json"])) for row in rows]

    def stats(self) -> dict[str, int]:
        with self.connect() as conn:
            projects = int(conn.execute("SELECT COUNT(*) FROM testamur_projects").fetchone()[0])
            links = int(conn.execute("SELECT COUNT(*) FROM testamur_project_monitors").fetchone()[0])
            repository_bindings = int(conn.execute("SELECT COUNT(*) FROM testamur_project_repository_bindings").fetchone()[0])
        return {
            "projects": projects,
            "project_monitors": links,
            "project_repository_bindings": repository_bindings,
        }
