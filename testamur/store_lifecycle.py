from __future__ import annotations

"""Canonical SQLite connection lifecycle for the migrated graph store."""

import sqlite3
from typing import Any

_INSTALLED = False


class ClosingConnection(sqlite3.Connection):
    """SQLite connection whose context manager commits/rolls back and closes."""

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool | None:
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def install_store_connection_lifecycle() -> None:
    """Install the canonical close-on-exit connection behavior exactly once."""

    global _INSTALLED
    if _INSTALLED:
        return

    from . import store as _store

    def _managed_store_connect(self: _store.TestamurStore) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15, factory=ClosingConnection)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            return conn
        except BaseException:
            conn.close()
            raise

    _store.TestamurStore.connect = _managed_store_connect
    _INSTALLED = True


__all__ = ["ClosingConnection", "install_store_connection_lifecycle"]
