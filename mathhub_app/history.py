from __future__ import annotations

from history_legacy import *  # noqa: F401,F403 - compatibility re-export
from history_legacy import HistoryReader as _LegacyHistoryReader
from sqlite_lifecycle import connect_sqlite


class HistoryReader(_LegacyHistoryReader):
    """History reader with deterministic SQLite connection lifecycle."""

    def connect(self):
        return connect_sqlite(self.path, wal=False)


__all__ = [name for name in globals() if not name.startswith("_")]
