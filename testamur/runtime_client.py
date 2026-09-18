from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any, Protocol

from .runtime_protocol import canonical_json


class RuntimeClient(Protocol):
    """Canonical thin-client boundary for a Testamur runtime service.

    The client transports canonical runtime requests; it does not assign truth,
    verification, reliance, or affectedness semantics to returned records.
    """

    execution_filesystem_scope: str

    def call(self, request: Mapping[str, Any]) -> dict[str, Any]: ...

    def execute_command(self, request: Mapping[str, Any]) -> dict[str, Any]: ...

    def status(self) -> dict[str, Any]: ...


class HttpRuntimeClient:
    """Provider-neutral HTTP transport for a canonical Testamur runtime.

    Hosted and self-hosted runtimes can share this transport as long as they
    implement the canonical Testamur protocol. Transport success is not
    verification and a fetched response is not durable reliance.
    """

    execution_filesystem_scope = "remote_or_unknown"

    def __init__(self, endpoint: str) -> None:
        value = str(endpoint).strip().rstrip("/")
        if not value:
            raise ValueError("runtime endpoint must not be empty")
        self.endpoint = value

    def _post(self, path: str, request: Mapping[str, Any]) -> dict[str, Any]:
        wire = urllib.request.Request(
            self.endpoint + path,
            data=canonical_json(dict(request)).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(wire, timeout=30) as response:
                value = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            value = json.loads(exc.read())
        if not isinstance(value, dict):
            raise RuntimeError("runtime returned a non-object response")
        return value

    def call(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._post("/v1/runtime", request)

    def execute_command(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._post("/v1/execute", request)

    def status(self) -> dict[str, Any]:
        with urllib.request.urlopen(self.endpoint + "/v1/status", timeout=10) as response:
            value = json.loads(response.read())
        if not isinstance(value, dict):
            raise RuntimeError("runtime status returned a non-object response")
        return value
