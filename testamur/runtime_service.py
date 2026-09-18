from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .command_execution import COMMAND_EXECUTION_VERSION, CommandExecutionService
from .historical_store import TestamurStore
from .runtime_protocol import PROTOCOL_VERSION, RuntimeProtocol
from .runtime_store import TestamurProtocolLedger


class EmbeddedRuntimeClient:
    """Canonical in-process Testamur runtime client.

    Historical SQLite table names may be consumed by the storage compatibility
    layer, but production Python/runtime identity and new protocol envelopes are
    Testamur-owned. Runtime execution records evidence; it does not imply
    verification or reliance.
    """

    execution_filesystem_scope = "local_same_host"

    def __init__(self, database_path: str | Path, *, network_enabled: bool = False) -> None:
        self.database_path = Path(database_path)
        self.network_enabled = bool(network_enabled)
        self.store = TestamurStore(self.database_path)
        self.ledger = TestamurProtocolLedger(self.store)
        self.protocol = RuntimeProtocol(self.ledger)
        self.command_executor = CommandExecutionService(self.protocol)

    def call(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self.protocol.safe_call(dict(request))

    def execute_command(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self.command_executor.execute(request)

    def status(self) -> dict[str, Any]:
        return {
            "runtime_version": "testamur-embedded-v0.1",
            "running": True,
            "network_enabled": self.network_enabled,
            "transport": "embedded",
            "host": None,
            "port": None,
            "database_path": str(self.database_path),
            "protocol": PROTOCOL_VERSION,
            "legacy_storage_compatibility": True,
            "command_execution": COMMAND_EXECUTION_VERSION,
            "mode": "embedded_local",
        }


__all__ = ["EmbeddedRuntimeClient"]
