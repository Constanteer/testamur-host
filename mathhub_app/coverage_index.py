from __future__ import annotations

from coverage_index_legacy import *  # noqa: F401,F403 - compatibility re-export
from coverage_index_legacy import CoverageIndex as _LegacyCoverageIndex
from sqlite_lifecycle import connect_sqlite


class CoverageIndex(_LegacyCoverageIndex):
    """Coverage index with deterministic SQLite connection lifecycle."""

    def connect(self):
        return connect_sqlite(self.path, wal=True)


__all__ = [name for name in globals() if not name.startswith("_")]
