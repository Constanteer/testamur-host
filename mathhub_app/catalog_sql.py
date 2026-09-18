from __future__ import annotations

from catalog_sql_legacy import *  # noqa: F401,F403 - compatibility re-export
from catalog_sql_legacy import Catalog as _LegacyCatalog
from sqlite_lifecycle import connect_sqlite


class Catalog(_LegacyCatalog):
    """Catalog SQL reader with deterministic SQLite connection lifecycle."""

    def connect(self):
        return connect_sqlite(self.path, wal=False)


__all__ = [name for name in globals() if not name.startswith("_")]
