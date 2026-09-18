from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Iterable, Protocol

from .model import ObjectKind, canonical_hash

DEFAULT_IGNORES = {".git", ".mathub", ".witness", ".testamur", "__pycache__", ".DS_Store"}


class ArtifactObjectStore(Protocol):
    """Minimal legacy-compatible sink used by ``record_path_artifact``.

    The snapshot kernel itself is store-independent.  This protocol keeps the
    compatibility helper usable by the migrated Testamur store without making
    this module depend on the historical Witness package.
    """

    def create_object(
        self,
        project: str,
        kind: str | ObjectKind,
        payload: dict[str, Any],
        *,
        actor_ref: str = "",
    ) -> dict[str, Any]: ...


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _absolute_without_following_final_symlink(path: str | Path) -> Path:
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    return Path(os.path.abspath(raw))


def snapshot_path(
    path: str | Path,
    *,
    root: str | Path | None = None,
    ignore_names: Iterable[str] = DEFAULT_IGNORES,
    max_entries: int = 20_000,
) -> dict[str, Any]:
    """Create a deterministic content snapshot without following symlinks."""
    target = _absolute_without_following_final_symlink(path)
    if not target.exists() and not target.is_symlink():
        raise FileNotFoundError(target)
    base = Path(root).expanduser().resolve() if root is not None else target.parent.resolve()
    try:
        logical = target.relative_to(base).as_posix()
    except ValueError:
        logical = target.name
    ignored = set(ignore_names)

    if target.is_symlink():
        link = os.readlink(target)
        return {
            "artifact_type": "symlink",
            "logical_path": logical,
            "content_hash": canonical_hash({"symlink": link}),
            "byte_size": len(link.encode()),
            "symlink_target": link,
        }
    target = target.resolve()
    if target.is_file():
        return {
            "artifact_type": "file",
            "logical_path": logical,
            "content_hash": _file_hash(target),
            "byte_size": target.stat().st_size,
        }
    if not target.is_dir():
        raise ValueError(f"unsupported artifact path type: {target}")

    manifest: list[dict[str, Any]] = []
    total_size = 0
    for child in sorted(target.rglob("*"), key=lambda p: p.as_posix()):
        relative = child.relative_to(target)
        if any(part in ignored for part in relative.parts):
            continue
        if len(manifest) >= max_entries:
            raise ValueError(f"artifact tree exceeds max_entries={max_entries}")
        rel = relative.as_posix()
        if child.is_symlink():
            link = os.readlink(child)
            entry = {
                "path": rel,
                "type": "symlink",
                "target": link,
                "hash": canonical_hash({"symlink": link}),
            }
        elif child.is_file():
            size = child.stat().st_size
            total_size += size
            entry = {"path": rel, "type": "file", "size": size, "hash": _file_hash(child)}
        else:
            continue
        manifest.append(entry)
    return {
        "artifact_type": "tree",
        "logical_path": logical,
        "content_hash": canonical_hash(manifest),
        "byte_size": total_size,
        "entry_count": len(manifest),
        "manifest": manifest,
    }


def record_path_artifact(
    store: ArtifactObjectStore,
    project: str,
    path: str | Path,
    *,
    root: str | Path | None = None,
    actor_ref: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = snapshot_path(path, root=root)
    payload = {**snapshot, **(extra or {})}
    payload.setdefault("source", "local_path")
    return store.create_object(project, ObjectKind.ARTIFACT, payload, actor_ref=actor_ref)
