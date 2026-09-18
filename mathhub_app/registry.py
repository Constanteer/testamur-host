from __future__ import annotations

from typing import Any

from registry_legacy import *  # noqa: F401,F403 - compatibility re-export
from registry_legacy import Registry as _LegacyRegistry
from sqlite_lifecycle import connect_sqlite


class Registry(_LegacyRegistry):
    """Registry with deterministic SQLite connection lifecycle."""

    def connect(self):
        return connect_sqlite(self.path, wal=True)

    def create_claim(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = super().create_claim(payload)
        current = result.get("current")
        if isinstance(current, dict) and current.get("version_id"):
            result["claim_version_id"] = current["version_id"]
        return result


__all__ = [name for name in globals() if not name.startswith("_")]
