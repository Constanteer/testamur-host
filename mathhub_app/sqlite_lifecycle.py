from __future__ import annotations

import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Any, Type


class ClosingConnection(sqlite3.Connection):
    """SQLite connection whose context manager also closes the descriptor.

    ``sqlite3.Connection.__exit__`` only commits or rolls back. MathHub uses
    short-lived ``with store.connect()`` operations, so leaving close to GC can
    exhaust file descriptors in long-running suites and processes.
    """

    def __exit__(
        self,
        exc_type: Type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


def connect_sqlite(
    path: str | Path,
    *,
    timeout: float = 15,
    wal: bool = False,
) -> ClosingConnection:
    conn = sqlite3.connect(path, timeout=timeout, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    return conn


__all__ = ["ClosingConnection", "connect_sqlite"]
