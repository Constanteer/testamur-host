from __future__ import annotations

from argument_index_legacy import *  # noqa: F401,F403 - compatibility re-export
from argument_index_legacy import ArgumentIndex as _LegacyArgumentIndex
from sqlite_lifecycle import connect_sqlite


class ArgumentIndex(_LegacyArgumentIndex):
    """Argument index with deterministic SQLite connection lifecycle."""

    def connect(self):
        return connect_sqlite(self.path, wal=True)


__all__ = [name for name in globals() if not name.startswith("_")]
