from __future__ import annotations

from formal_index_legacy import *  # noqa: F401,F403 - compatibility re-export
from formal_index_legacy import FormalIndex as _LegacyFormalIndex
from sqlite_lifecycle import connect_sqlite


class FormalIndex(_LegacyFormalIndex):
    """Formal index with deterministic SQLite connection lifecycle."""

    def connect(self):
        return connect_sqlite(self.path, wal=True)


__all__ = [name for name in globals() if not name.startswith("_")]
