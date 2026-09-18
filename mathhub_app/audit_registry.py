from __future__ import annotations

from audit_registry_legacy import *  # noqa: F401,F403 - compatibility re-export
from audit_registry_legacy import AuditRegistry as _LegacyAuditRegistry
from sqlite_lifecycle import connect_sqlite


class AuditRegistry(_LegacyAuditRegistry):
    """Audit registry with deterministic SQLite connection lifecycle."""

    def connect(self):
        return connect_sqlite(self.path, wal=True)


__all__ = [name for name in globals() if not name.startswith("_")]
