from __future__ import annotations

import secrets
from typing import Any, Mapping, Protocol

from .runtime_protocol import PROTOCOL_VERSION


class RuntimeInspectionClient(Protocol):
    def call(self, request: Mapping[str, Any]) -> dict[str, Any]: ...


def _key(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(12)}"


def _must(response: Mapping[str, Any]) -> dict[str, Any]:
    if not response.get("ok"):
        error = response.get("error") or {}
        raise RuntimeError(
            f"{error.get('code', 'runtime_error')}: {error.get('message', '')}"
        )
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("runtime returned no result object")
    return result


def inspect_run(
    client: RuntimeInspectionClient,
    run_id: str,
    *,
    client_id: str = "testamur-cli",
) -> dict[str, Any]:
    """Read one durable runtime run without depending on the Witness namespace.

    The request deliberately preserves the historical protocol token and operation
    shape because those are durable wire semantics, not Python package ownership.
    Inspection is read-only and does not imply verification or reliance.
    """
    return _must(
        client.call(
            {
                "protocol": PROTOCOL_VERSION,
                "client_id": str(client_id),
                "request_id": _key("req-inspect"),
                "idempotency_key": _key("inspect"),
                "operation": "get_run",
                "run_id": str(run_id),
                "payload": {},
            }
        )
    )


__all__ = ["RuntimeInspectionClient", "inspect_run"]
