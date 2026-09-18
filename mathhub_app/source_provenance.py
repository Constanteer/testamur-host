from __future__ import annotations

from source_provenance_legacy import *  # noqa: F401,F403 - compatibility re-export
from source_provenance_legacy import SourceProvenance as _LegacySourceProvenance
from sqlite_lifecycle import connect_sqlite


class SourceProvenance(_LegacySourceProvenance):
    """Source provenance reader with deterministic SQLite connection lifecycle."""

    def connect(self):
        return connect_sqlite(self.path, wal=False)


__all__ = [name for name in globals() if not name.startswith("_")]
