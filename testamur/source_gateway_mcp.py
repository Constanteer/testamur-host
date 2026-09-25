from __future__ import annotations

import base64
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Mapping

from .product_service import TestamurProductService
from .project_review_surface import project_with_advisory_reviews
from .source_fetch import SourceFetchPolicy
from .source_gateway import TestamurSourceGateway
from .supply_chain import diff_project_supply_chain, scan_bound_project_supply_chain


SERVER_INFO = {"name": "testamur-source-gateway", "version": "0.1.0"}
MODERN_VERSION = "2026-07-28"
LEGACY_VERSION = "2025-11-25"
SUPPORTED_VERSIONS = (MODERN_VERSION, LEGACY_VERSION)


def _fetch_schema(*, require: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            require: {"type": "string"},
            "session_id": {"type": "string"},
            "max_bytes": {"type": "integer", "minimum": 1, "default": 10485760},
            "timeout_seconds": {"type": "number", "exclusiveMinimum": 0, "default": 15},
            "max_redirects": {"type": "integer", "minimum": 0, "default": 5},
            "allow_private_network": {"type": "boolean", "default": False},
            "max_return_bytes": {"type": "integer", "minimum": 1, "default": 1048576},
        },
        "required": [require],
        "additionalProperties": False,
    }


TOOLS = [
    {
        "name": "testamur.fetch",
        "title": "Capture and open an exact source revision",
        "description": (
            "Fetch an HTTP(S) source through Testamur, retain the exact bytes in local CAS, "
            "mint Source/Snapshot/SourceRevision identities, and return bounded content with "
            "provenance metadata. Capture does not imply durable reliance."
        ),
        "inputSchema": _fetch_schema(require="locator"),
    },
    {
        "name": "testamur.open_revision",
        "title": "Open retained exact revision bytes",
        "description": "Read an already captured SourceRevision from Testamur local CAS.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "revision_id": {"type": "string"},
                "max_return_bytes": {"type": "integer", "minimum": 1, "default": 1048576},
            },
            "required": ["revision_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "testamur.source_status",
        "title": "Inspect latest source state",
        "description": "Return latest Snapshot/SourceRevision and CAS availability for a Source.",
        "inputSchema": {
            "type": "object",
            "properties": {"source_id": {"type": "string"}},
            "required": ["source_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "testamur.revalidate",
        "title": "Revalidate a tracked source",
        "description": "Fetch a Source again under bounded policy and create a new immutable Snapshot.",
        "inputSchema": _fetch_schema(require="source_id"),
    },
    {
        "name": "testamur.watch_refresh",
        "title": "Refresh and evaluate a Testamur Watch",
        "description": "Revalidate a watched Source and mechanically evaluate its Watch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "watch_id": {"type": "string"},
                "session_id": {"type": "string"},
                "max_bytes": {"type": "integer", "minimum": 1, "default": 10485760},
                "timeout_seconds": {"type": "number", "exclusiveMinimum": 0, "default": 15},
                "max_redirects": {"type": "integer", "minimum": 0, "default": 5},
                "allow_private_network": {"type": "boolean", "default": False},
            },
            "required": ["watch_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "testamur.project_supply_chain",
        "title": "Inspect a Project supply-chain inventory",
        "description": (
            "Return the latest immutable dependency-manifest inventory and advisory review "
            "projection for an existing Testamur Project. Advisory overlap is not an affectedness verdict."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"project_ref": {"type": "string"}},
            "required": ["project_ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "testamur.project_scan",
        "title": "Rescan a bound Project repository",
        "description": (
            "Scan dependency manifests through an explicit Project repository binding and "
            "append immutable scan evidence when the observed inventory changes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_ref": {"type": "string"},
                "binding": {"type": "string", "default": "primary"},
            },
            "required": ["project_ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "testamur.project_supply_chain_diff",
        "title": "Compare Project supply-chain scans",
        "description": (
            "Mechanically compare two immutable Project supply-chain scans. Changed dependencies "
            "are not automatically invalid or vulnerable."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_ref": {"type": "string"},
                "from_scan_revision_id": {"type": "string"},
                "to_scan_revision_id": {"type": "string"},
            },
            "required": ["project_ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "testamur.project_advisories",
        "title": "Inspect Project advisory review candidates",
        "description": (
            "Return advisory candidates and recorded affectedness review state for an existing Project "
            "without converting provider/version overlap into a local affectedness verdict."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"project_ref": {"type": "string"}},
            "required": ["project_ref"],
            "additionalProperties": False,
        },
    },
]


class McpError(ValueError):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = int(code)
        self.message = str(message)
        self.data = data


def _db_path() -> Path:
    explicit = os.environ.get("TESTAMUR_DB")
    return Path(explicit).expanduser() if explicit else Path.cwd() / ".testamur" / "evidence.db"


def _positive_int(arguments: Mapping[str, Any], key: str, default: int) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise McpError(-32602, f"{key} must be an integer")
    if value < 1:
        raise McpError(-32602, f"{key} must be at least 1")
    return value


def _nonnegative_int(arguments: Mapping[str, Any], key: str, default: int) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise McpError(-32602, f"{key} must be an integer")
    if value < 0:
        raise McpError(-32602, f"{key} must be at least 0")
    return value


def _positive_number(arguments: Mapping[str, Any], key: str, default: float) -> float:
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise McpError(-32602, f"{key} must be a number")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise McpError(-32602, f"{key} must be a finite positive number")
    return value


def _strict_bool(arguments: Mapping[str, Any], key: str, default: bool) -> bool:
    value = arguments.get(key, default)
    if not isinstance(value, bool):
        raise McpError(-32602, f"{key} must be a boolean")
    return value


def _policy(arguments: Mapping[str, Any]) -> SourceFetchPolicy:
    try:
        return SourceFetchPolicy(
            max_bytes=_positive_int(arguments, "max_bytes", 10 * 1024 * 1024),
            timeout_seconds=_positive_number(arguments, "timeout_seconds", 15.0),
            max_redirects=_nonnegative_int(arguments, "max_redirects", 5),
            allow_private_network=_strict_bool(arguments, "allow_private_network", False),
        )
    except McpError:
        raise
    except (TypeError, ValueError) as exc:
        raise McpError(-32602, f"invalid fetch policy: {exc}") from exc


def _session(arguments: Mapping[str, Any]) -> str | None:
    explicit = arguments.get("session_id")
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()
    inherited = os.environ.get("TESTAMUR_WORK_SESSION")
    return inherited.strip() if inherited and inherited.strip() else None


def _content_payload(content: bytes | None, *, max_return_bytes: int) -> dict[str, Any]:
    if content is None:
        return {"content_available": False, "content_returned": False}
    if len(content) > max_return_bytes:
        return {
            "content_available": True,
            "content_returned": False,
            "byte_size": len(content),
            "reason": "captured_content_exceeds_max_return_bytes",
        }
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "content_available": True,
            "content_returned": True,
            "encoding": "base64",
            "byte_size": len(content),
            "data": base64.b64encode(content).decode("ascii"),
        }
    return {
        "content_available": True,
        "content_returned": True,
        "encoding": "utf-8",
        "byte_size": len(content),
        "text": text,
    }


def _text_result(payload: Mapping[str, Any], *, modern: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}],
        "structuredContent": dict(payload),
        "isError": False,
    }
    if modern:
        result["resultType"] = "complete"
    return result


def _required(arguments: Mapping[str, Any], key: str) -> str:
    value = str(arguments.get(key) or "").strip()
    if not value:
        raise McpError(-32602, f"{key} is required")
    return value


def _call_tool(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    if name.startswith("testamur.project_"):
        service = TestamurProductService.integrated(_db_path())
        project_ref = _required(arguments, "project_ref")
        if name == "testamur.project_supply_chain":
            detail = project_with_advisory_reviews(service, project_ref)
            if detail.get("ok") is not True:
                return detail
            return {
                "ok": True,
                "schema": "testamur.mcp.project-supply-chain.v1",
                "project": detail["project"],
                "supply_chain": detail.get("supply_chain"),
            }
        if name == "testamur.project_scan":
            binding = str(arguments.get("binding") or "primary").strip() or "primary"
            return scan_bound_project_supply_chain(service, project_ref, binding_key=binding)
        if name == "testamur.project_supply_chain_diff":
            from_revision = arguments.get("from_scan_revision_id")
            to_revision = arguments.get("to_scan_revision_id")
            return diff_project_supply_chain(
                service,
                project_ref,
                from_scan_revision_id=None
                if from_revision is None
                else str(from_revision).strip() or None,
                to_scan_revision_id=None
                if to_revision is None
                else str(to_revision).strip() or None,
            )
        if name == "testamur.project_advisories":
            detail = project_with_advisory_reviews(service, project_ref)
            if detail.get("ok") is not True:
                return detail
            supply = detail.get("supply_chain")
            if not isinstance(supply, Mapping):
                supply = {}
            return {
                "ok": True,
                "schema": "testamur.mcp.project-advisories.v1",
                "project": detail["project"],
                "advisory_candidates": list(supply.get("advisory_candidates") or []),
                "advisory_candidate_count": int(supply.get("advisory_candidate_count") or 0),
                "advisory_review_required_count": int(
                    supply.get("advisory_review_required_count") or 0
                ),
                "unresolved_advisory_count": int(supply.get("unresolved_advisory_count") or 0),
                "semantics": {
                    "candidate_overlap_is_not_affectedness_verdict": True,
                    "recorded_assessment_is_not_generic_verification": True,
                    "generic_trust_score_used": False,
                },
            }
        raise McpError(-32602, f"unknown tool: {name}")

    actor = {"ref": "testamur:mcp-source-gateway"}
    with TestamurSourceGateway(_db_path()) as gateway:
        if name == "testamur.fetch":
            max_return = _positive_int(arguments, "max_return_bytes", 1024 * 1024)
            capture = gateway.fetch(
                _required(arguments, "locator"),
                session_id=_session(arguments),
                actor=actor,
                policy=_policy(arguments),
            )
            payload = capture.metadata()
            payload["content"] = _content_payload(capture.content, max_return_bytes=max_return)
            return payload

        if name == "testamur.open_revision":
            revision_id = _required(arguments, "revision_id")
            max_return = _positive_int(arguments, "max_return_bytes", 1024 * 1024)
            revision = gateway.sources.get_revision(revision_id)
            if revision is None:
                raise KeyError(revision_id)
            digest = str(revision["content_hash"])
            blob = gateway.blobs.describe(digest)
            if int(blob["byte_size"]) > max_return:
                content = {
                    "content_available": True,
                    "content_returned": False,
                    "byte_size": int(blob["byte_size"]),
                    "reason": "retained_content_exceeds_max_return_bytes",
                }
            else:
                content = _content_payload(
                    gateway.open_revision(revision_id, max_bytes=max_return),
                    max_return_bytes=max_return,
                )
            return {
                "schema": "testamur.source-gateway.open-revision.v1",
                "revision": revision,
                "blob": blob,
                "content": content,
                "semantics": {"exact_retained_bytes": True, "durable_reliance_implied": False},
            }

        if name == "testamur.source_status":
            return gateway.source_status(_required(arguments, "source_id"))

        if name == "testamur.revalidate":
            max_return = _positive_int(arguments, "max_return_bytes", 1024 * 1024)
            capture = gateway.revalidate(
                _required(arguments, "source_id"),
                session_id=_session(arguments),
                actor=actor,
                policy=_policy(arguments),
            )
            payload = capture.metadata()
            payload["content"] = _content_payload(capture.content, max_return_bytes=max_return)
            return payload

        if name == "testamur.watch_refresh":
            return gateway.refresh_watch(
                _required(arguments, "watch_id"),
                session_id=_session(arguments),
                actor=actor,
                policy=_policy(arguments),
            )

        raise McpError(-32602, f"unknown tool: {name}")


def _is_modern(request: Mapping[str, Any]) -> bool:
    params = request.get("params")
    if not isinstance(params, Mapping):
        return False
    meta = params.get("_meta")
    return isinstance(meta, Mapping) and str(meta.get("io.modelcontextprotocol/protocolVersion") or "") == MODERN_VERSION


def _response(request_id: Any, result: Mapping[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": dict(result)}


def _error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    value: dict[str, Any] = {"code": int(code), "message": str(message)}
    if data is not None:
        value["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": value}


def handle_request(request: Mapping[str, Any]) -> dict[str, Any] | None:
    if request.get("jsonrpc") != "2.0":
        return _error(request.get("id"), -32600, "Invalid Request")
    method = request.get("method")
    request_id = request.get("id")
    if not isinstance(method, str):
        return _error(request_id, -32600, "Invalid Request")
    if request_id is None:  # notifications intentionally produce no response
        return None

    modern = _is_modern(request) or method == "server/discover"
    try:
        if method == "server/discover":
            return _response(
                request_id,
                {
                    "resultType": "complete",
                    "supportedVersions": list(SUPPORTED_VERSIONS),
                    "capabilities": {"tools": {}},
                    "instructions": (
                        "Use testamur.fetch for external sources when exact provenance matters. "
                        "Successful capture does not imply material reliance."
                    ),
                    "ttlMs": 60000,
                    "cacheScope": "private",
                    "_meta": {"io.modelcontextprotocol/serverInfo": SERVER_INFO},
                },
            )
        if method == "initialize":
            params = request.get("params") if isinstance(request.get("params"), Mapping) else {}
            requested = str(params.get("protocolVersion") or LEGACY_VERSION)
            if requested not in SUPPORTED_VERSIONS:
                raise McpError(
                    -32602,
                    "unsupported MCP protocol version",
                    {"requested": requested, "supported": list(SUPPORTED_VERSIONS)},
                )
            # Modern clients use server/discover. If they still send initialize,
            # negotiate the legacy lifecycle rather than pretending modern setup
            # happened through an obsolete handshake.
            negotiated = LEGACY_VERSION
            return _response(
                request_id,
                {
                    "protocolVersion": negotiated,
                    "capabilities": {"tools": {}},
                    "serverInfo": SERVER_INFO,
                    "instructions": "Prefer Testamur Source Gateway for exact-revision external source access.",
                },
            )
        if method == "tools/list":
            result: dict[str, Any] = {"tools": TOOLS}
            if modern:
                result.update({"resultType": "complete", "ttlMs": 60000, "cacheScope": "private"})
            return _response(request_id, result)
        if method == "tools/call":
            params = request.get("params")
            if not isinstance(params, Mapping):
                raise McpError(-32602, "tools/call params must be an object")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, Mapping):
                raise McpError(-32602, "tool arguments must be an object")
            payload = _call_tool(str(params.get("name") or "").strip(), arguments)
            return _response(request_id, _text_result(payload, modern=modern))
        return _error(request_id, -32601, "Method not found")
    except McpError as exc:
        return _error(request_id, exc.code, exc.message, exc.data)
    except Exception as exc:
        result: dict[str, Any] = {
            "content": [{"type": "text", "text": f"Testamur tool failed: {exc}"}],
            "isError": True,
        }
        if modern:
            result["resultType"] = "complete"
        return _response(request_id, result)


def main() -> int:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = (
                handle_request(request)
                if isinstance(request, Mapping)
                else _error(None, -32600, "Invalid Request")
            )
        except json.JSONDecodeError as exc:
            response = _error(None, -32700, "Parse error", str(exc))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
