from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ENV_DIR_NAME = ".testamur"
ENV_FILE_NAME = "environment.json"
DB_FILE_NAME = "testamur.sqlite3"
ENV_VERSION = "testamur-environment-v1"


@dataclass(frozen=True)
class TestamurEnvironment:
    root: Path
    directory: Path
    config_path: Path
    database_path: Path
    config: dict[str, Any]


def _default_name(root: Path) -> str:
    return root.name or "project"


def _run_git(root: Path, *args: str) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return int(completed.returncode), completed.stdout.strip()


def _exclude_local_environment(root: Path) -> None:
    top_code, top_raw = _run_git(root, "rev-parse", "--show-toplevel")
    path_code, exclude_raw = _run_git(root, "rev-parse", "--git-path", "info/exclude")
    if top_code != 0 or path_code != 0 or not top_raw or not exclude_raw:
        return
    top = Path(top_raw).resolve()
    try:
        relative = root.resolve().relative_to(top)
    except ValueError:
        return
    prefix = "" if str(relative) == "." else relative.as_posix().rstrip("/") + "/"
    pattern = f"/{prefix}{ENV_DIR_NAME}/"
    exclude = Path(exclude_raw)
    if not exclude.is_absolute():
        exclude = root / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    lines = {line.strip() for line in existing.splitlines()}
    if pattern in lines:
        return
    with exclude.open("a", encoding="utf-8") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        handle.write(f"\n# Testamur local evidence environment\n{pattern}\n")


def discover(start: str | Path | None = None) -> TestamurEnvironment | None:
    current = Path(start or os.getcwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        directory = candidate / ENV_DIR_NAME
        config_path = directory / ENV_FILE_NAME
        if not config_path.is_file():
            continue
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"invalid Testamur environment at {config_path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise RuntimeError(f"invalid Testamur environment at {config_path}: expected object")
        database_name = str(raw.get("database") or DB_FILE_NAME)
        database_path = Path(database_name)
        if database_path.is_absolute() or ".." in database_path.parts:
            raise RuntimeError(f"invalid Testamur database path in {config_path}")
        return TestamurEnvironment(
            root=candidate,
            directory=directory,
            config_path=config_path,
            database_path=directory / database_path,
            config=raw,
        )
    return None


def initialize(root: str | Path | None = None, *, name: str | None = None) -> TestamurEnvironment:
    project_root = Path(root or os.getcwd()).expanduser().resolve()
    if project_root.is_file():
        raise ValueError("Testamur environment root must be a directory")
    existing = discover(project_root)
    if existing is not None:
        _exclude_local_environment(existing.root)
        return existing
    directory = project_root / ENV_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    config_path = directory / ENV_FILE_NAME
    payload = {
        "version": ENV_VERSION,
        "name": str(name or _default_name(project_root)),
        "database": DB_FILE_NAME,
    }
    config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _exclude_local_environment(project_root)
    return TestamurEnvironment(
        root=project_root,
        directory=directory,
        config_path=config_path,
        database_path=directory / DB_FILE_NAME,
        config=payload,
    )


def git_context(root: Path) -> dict[str, Any]:
    code, top = _run_git(root, "rev-parse", "--show-toplevel")
    if code != 0 or not top:
        return {"status": "not_repository", "head": None, "branch": None, "dirty": None}
    _, head = _run_git(root, "rev-parse", "HEAD")
    branch_code, branch = _run_git(root, "symbolic-ref", "--short", "-q", "HEAD")
    status_code, porcelain = _run_git(root, "status", "--porcelain=v1", "--untracked-files=normal")
    return {
        "status": "captured",
        "root": top,
        "head": head or None,
        "branch": branch if branch_code == 0 and branch else None,
        "dirty": None if status_code != 0 else bool(porcelain),
    }
