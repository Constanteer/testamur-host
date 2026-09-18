from __future__ import annotations

import ipaddress
import math
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_REDIRECTS = 5


@dataclass(frozen=True, slots=True)
class SourceFetchPolicy:
    """Explicit bounds for one local HTTP(S) Source capture.

    This is a local transport policy, not an authorization boundary for a
    multi-user server. Hosted retrieval must add authenticated egress policy and
    network sandboxing.
    """

    max_bytes: int = DEFAULT_MAX_BYTES
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_redirects: int = DEFAULT_MAX_REDIRECTS
    allow_private_network: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.max_bytes, bool) or not isinstance(self.max_bytes, int) or self.max_bytes < 1:
            raise ValueError("fetch max_bytes must be a positive integer")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(float(self.timeout_seconds))
            or float(self.timeout_seconds) <= 0
        ):
            raise ValueError("fetch timeout_seconds must be a finite positive number")
        if (
            isinstance(self.max_redirects, bool)
            or not isinstance(self.max_redirects, int)
            or self.max_redirects < 0
        ):
            raise ValueError("fetch max_redirects must be a non-negative integer")
        if not isinstance(self.allow_private_network, bool):
            raise ValueError("allow_private_network must be a boolean")


@dataclass(frozen=True, slots=True)
class SourceFetchResult:
    requested_locator: str
    final_locator: str
    status: str
    content: bytes | None
    metadata: dict[str, Any]


class SourceFetchPolicyError(ValueError):
    pass


def _port_for(parsed: urllib.parse.SplitResult) -> int:
    try:
        explicit = parsed.port
    except ValueError as exc:
        raise SourceFetchPolicyError("source URL has an invalid port") from exc
    if explicit is not None:
        return int(explicit)
    return 443 if parsed.scheme == "https" else 80


def _validate_locator(locator: str, *, allow_private_network: bool) -> str:
    raw = str(locator).strip()
    if not raw:
        raise SourceFetchPolicyError("source locator must not be empty")
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"http", "https"}:
        raise SourceFetchPolicyError("fetch supports only http:// and https:// locators")
    if parsed.username is not None or parsed.password is not None:
        raise SourceFetchPolicyError("source fetch URL must not contain embedded credentials")
    host = parsed.hostname
    if not host:
        raise SourceFetchPolicyError("source fetch URL must include a hostname")
    port = _port_for(parsed)
    if allow_private_network:
        return raw

    try:
        resolved = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise OSError(f"DNS resolution failed for {host}: {exc}") from exc

    addresses = {item[4][0] for item in resolved if item and item[4]}
    if not addresses:
        raise OSError(f"DNS resolution returned no addresses for {host}")
    blocked: list[str] = []
    for address in sorted(addresses):
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            blocked.append(address)
            continue
        if not ip.is_global:
            blocked.append(address)
    if blocked:
        joined = ", ".join(blocked)
        raise SourceFetchPolicyError(
            f"source fetch resolved to non-public address(es): {joined}; "
            "allow_private_network must be an explicit local policy choice"
        )
    return raw


class _CheckedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, policy: SourceFetchPolicy, history: list[dict[str, Any]]) -> None:
        super().__init__()
        self.policy = policy
        self.history = history

    def redirect_request(self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> urllib.request.Request | None:
        if len(self.history) >= int(self.policy.max_redirects):
            raise SourceFetchPolicyError(
                f"source fetch exceeded max_redirects={int(self.policy.max_redirects)}"
            )
        absolute = urllib.parse.urljoin(req.full_url, newurl)
        _validate_locator(absolute, allow_private_network=bool(self.policy.allow_private_network))
        self.history.append({"status_code": int(code), "from": req.full_url, "to": absolute})
        return super().redirect_request(req, fp, code, msg, headers, absolute)


def _base_metadata(*, requested: str, final: str, redirects: list[dict[str, Any]], policy: SourceFetchPolicy) -> dict[str, Any]:
    return {
        "transport": "http",
        "requested_locator": requested,
        "final_locator": final,
        "redirects": list(redirects),
        "redirect_count": len(redirects),
        "policy": {
            "max_bytes": int(policy.max_bytes),
            "timeout_seconds": float(policy.timeout_seconds),
            "max_redirects": int(policy.max_redirects),
            "allow_private_network": bool(policy.allow_private_network),
            "ambient_proxy_used": False,
        },
        "semantics": {
            "retrieval_is_observation_not_truth": True,
            "transport_failure_is_recordable": True,
            "redirect_equivalence_not_inferred": True,
            "server_authorization_boundary": False,
        },
    }


def fetch_source(locator: str, *, policy: SourceFetchPolicy | None = None) -> SourceFetchResult:
    """Fetch one exact HTTP(S) response entity under explicit local bounds."""

    effective = policy or SourceFetchPolicy()
    requested = str(locator).strip()
    redirects: list[dict[str, Any]] = []
    try:
        _validate_locator(requested, allow_private_network=bool(effective.allow_private_network))
    except SourceFetchPolicyError:
        raise
    except OSError as exc:
        metadata = _base_metadata(requested=requested, final=requested, redirects=redirects, policy=effective)
        metadata["error"] = {"kind": "resolution_error", "message": str(exc)}
        return SourceFetchResult(requested, requested, "unavailable", None, metadata)

    handler = _CheckedRedirectHandler(effective, redirects)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), handler)
    request = urllib.request.Request(
        requested,
        method="GET",
        headers={
            "User-Agent": "Testamur/0.3 source-gateway",
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "Connection": "close",
        },
    )

    try:
        with opener.open(request, timeout=float(effective.timeout_seconds)) as response:
            final_locator = str(response.geturl())
            status_code = int(getattr(response, "status", response.getcode()))
            content_length_raw = response.headers.get("Content-Length")
            if content_length_raw is not None:
                try:
                    content_length = int(content_length_raw)
                except ValueError:
                    content_length = None
                if content_length is not None and content_length > int(effective.max_bytes):
                    metadata = _base_metadata(requested=requested, final=final_locator, redirects=redirects, policy=effective)
                    metadata.update({
                        "status_code": status_code,
                        "content_length_header": content_length,
                        "error": {"kind": "response_too_large", "message": f"Content-Length {content_length} exceeds max_bytes={int(effective.max_bytes)}"},
                    })
                    return SourceFetchResult(requested, final_locator, "unavailable", None, metadata)

            content = response.read(int(effective.max_bytes) + 1)
            if len(content) > int(effective.max_bytes):
                metadata = _base_metadata(requested=requested, final=final_locator, redirects=redirects, policy=effective)
                metadata.update({"status_code": status_code, "error": {"kind": "response_too_large", "message": f"response exceeded max_bytes={int(effective.max_bytes)}"}})
                return SourceFetchResult(requested, final_locator, "unavailable", None, metadata)

            metadata = _base_metadata(requested=requested, final=final_locator, redirects=redirects, policy=effective)
            metadata.update({
                "status_code": status_code,
                "content_type": response.headers.get("Content-Type"),
                "etag": response.headers.get("ETag"),
                "last_modified": response.headers.get("Last-Modified"),
                "content_length_header": content_length_raw,
                "byte_size": len(content),
            })
            return SourceFetchResult(requested, final_locator, "captured", content, metadata)
    except SourceFetchPolicyError:
        raise
    except urllib.error.HTTPError as exc:
        final_locator = str(exc.geturl() or requested)
        metadata = _base_metadata(requested=requested, final=final_locator, redirects=redirects, policy=effective)
        metadata.update({
            "status_code": int(exc.code),
            "content_type": None if exc.headers is None else exc.headers.get("Content-Type"),
            "error": {"kind": "http_error", "message": str(exc.reason or exc)},
        })
        return SourceFetchResult(requested, final_locator, "unavailable", None, metadata)
    except (urllib.error.URLError, TimeoutError, socket.timeout, ssl.SSLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        metadata = _base_metadata(requested=requested, final=requested, redirects=redirects, policy=effective)
        metadata["error"] = {"kind": "transport_error", "message": str(reason)}
        return SourceFetchResult(requested, requested, "unavailable", None, metadata)
