from __future__ import annotations

import argparse
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, unquote, urlsplit

from .environment import DB_FILE_NAME, ENV_DIR_NAME, discover
from .product_actions import create_monitor, create_project, refresh_monitor, refresh_project_monitors, run_due_monitors
from .monitor_provider_manifest import load_monitor_provider_registry
from .product_service import TestamurProductService

WEB_ROOT = Path(__file__).with_name("web")

_STATIC_ASSETS = {"index.html", "app.js", "styles.css"}
_SPA_PREFIXES = {"app", "object", "compare", "impact", "temporal", "projects", "explore", "monitoring", "status"}
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}
_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'self'; form-action 'self'; "
        "img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
_MAX_JSON_BODY = 64 * 1024


def _json(payload: Mapping[str, Any], *, status: int | None = None) -> dict[str, Any]:
    if status is None:
        status = _status_for_payload(payload)
    return {
        "status": int(status),
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "no-store",
            **_SECURITY_HEADERS,
        },
        "body": dict(payload),
    }


def _error(code: str, message: str, *, status: int = 400) -> dict[str, Any]:
    return _json(
        {"ok": False, "schema": "testamur.error.v1", "error": {"code": code, "message": message}},
        status=status,
    )


def _status_for_payload(payload: Mapping[str, Any]) -> int:
    if payload.get("ok") is True:
        return int(HTTPStatus.OK)
    error = payload.get("error")
    code = str(error.get("code") if isinstance(error, Mapping) else "")
    if code == "object_not_found":
        return int(HTTPStatus.NOT_FOUND)
    if code.endswith("capability_unavailable"):
        return int(HTTPStatus.SERVICE_UNAVAILABLE)
    if code in {"incompatible_compare", "compare_not_supported", "history_not_supported"}:
        return int(HTTPStatus.UNPROCESSABLE_ENTITY)
    return int(HTTPStatus.BAD_REQUEST)


def _one(query: Mapping[str, list[str]], name: str, *, required: bool = True) -> str | None:
    values = query.get(name) or []
    if not values:
        if required:
            raise ValueError(f"missing required query parameter: {name}")
        return None
    if len(values) != 1 or not values[0].strip():
        raise ValueError(f"query parameter {name} must occur exactly once and be non-empty")
    return values[0].strip()


def _limit(query: Mapping[str, list[str]], *, default: int = 50, maximum: int = 500) -> int:
    raw = _one(query, "limit", required=False)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("limit must be an integer") from exc
    if value < 1 or value > maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return value


def _temporal_query(query: Mapping[str, list[str]]) -> dict[str, Any]:
    clauses: list[dict[str, str]] = []
    allowed = {"known_at", "available_by", "effective_at"}
    for raw in query.get("clause") or []:
        mode, separator, at = raw.partition("=")
        normalized = mode.strip().lower()
        if not separator or normalized not in allowed or not at.strip():
            raise ValueError(
                "clause must be MODE=TIME with MODE one of KNOWN_AT, AVAILABLE_BY, EFFECTIVE_AT"
            )
        clauses.append({"mode": normalized, "at": at.strip()})
    if not clauses:
        raise ValueError("at least one temporal clause is required")
    return {
        "clauses": clauses,
        "perspective": _one(query, "perspective", required=False),
        "limit": _limit(query, default=100),
    }


def dispatch_api(service: TestamurProductService, target: str) -> dict[str, Any]:
    split = urlsplit(target)
    path = split.path.rstrip("/") or "/"
    query = parse_qs(split.query, keep_blank_values=True)
    try:
        if path == "/v1/status":
            if query:
                raise ValueError("status accepts no query parameters")
            return _json(service.status())
        if path == "/v1/dashboard":
            unknown = set(query) - {"limit"}
            if unknown:
                raise ValueError(f"dashboard does not accept query parameters: {', '.join(sorted(unknown))}")
            return _json(service.dashboard(limit=_limit(query, default=30, maximum=100)))
        if path == "/v1/project":
            unknown = set(query) - {"ref"}
            if unknown:
                raise ValueError(f"project does not accept query parameters: {', '.join(sorted(unknown))}")
            return _json(service.project(_one(query, "ref") or ""))
        if path == "/v1/object":
            return _json(service.get_object(_one(query, "ref") or ""))
        if path == "/v1/history":
            return _json(service.history(_one(query, "ref") or "", limit=_limit(query)))
        if path == "/v1/compare":
            return _json(service.compare(_one(query, "left") or "", _one(query, "right") or ""))
        if path == "/v1/impact":
            return _json(service.impact(_one(query, "ref") or ""))
        if path == "/v1/temporal":
            return _json(service.temporal(_one(query, "ref") or "", _temporal_query(query)))
        if path == "/v1/temporal-events":
            return _json(
                service.temporal_events(
                    _one(query, "ref") or "",
                    {
                        "event_kinds": query.get("event_kind") or None,
                        "recorded_by": _one(query, "recorded_by", required=False),
                        "event_time_by": _one(query, "event_time_by", required=False),
                        "perspective": _one(query, "perspective", required=False),
                        "limit": _limit(query, default=100),
                    },
                )
            )
    except ValueError as exc:
        return _error("invalid_argument", str(exc))
    return _error("route_not_found", "unknown Testamur API route", status=404)


def dispatch_api_write(
    service: TestamurProductService,
    target: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    split = urlsplit(target)
    path = split.path.rstrip("/") or "/"
    if split.query:
        return _error("invalid_argument", "write routes do not accept query parameters")
    try:
        if path == "/v1/projects/refresh":
            unknown = set(payload) - {"project_ref"}
            if unknown:
                raise ValueError(f"project refresh does not accept fields: {', '.join(sorted(unknown))}")
            project_ref = payload.get("project_ref")
            if not isinstance(project_ref, str):
                raise ValueError("project refresh project_ref must be a string")
            return _json(refresh_project_monitors(service, project_ref=project_ref))
        if path == "/v1/projects":
            unknown = set(payload) - {"name", "description", "visibility"}
            if unknown:
                raise ValueError(f"project creation does not accept fields: {', '.join(sorted(unknown))}")
            name = payload.get("name")
            description = payload.get("description")
            visibility = payload.get("visibility", "private")
            if not isinstance(name, str):
                raise ValueError("project name must be a string")
            if description is not None and not isinstance(description, str):
                raise ValueError("project description must be a string when provided")
            if not isinstance(visibility, str):
                raise ValueError("project visibility must be a string")
            return _json(
                create_project(
                    service,
                    name=name,
                    description=description,
                    visibility=visibility,
                )
            )
        if path == "/v1/monitors/run-due":
            if payload:
                raise ValueError("run-due accepts no fields")
            return _json(run_due_monitors(service))
        if path == "/v1/monitors/refresh":
            unknown = set(payload) - {"watch_id"}
            if unknown:
                raise ValueError(f"monitor refresh does not accept fields: {', '.join(sorted(unknown))}")
            watch_id = payload.get("watch_id")
            if not isinstance(watch_id, str):
                raise ValueError("monitor refresh watch_id must be a string")
            return _json(refresh_monitor(service, watch_id=watch_id))
        if path == "/v1/monitors":
            unknown = set(payload) - {
                "project_id", "source_id", "locator", "provider", "provider_config", "label", "alert_on", "interval_seconds"
            }
            if unknown:
                raise ValueError(f"monitor creation does not accept fields: {', '.join(sorted(unknown))}")
            project_id = payload.get("project_id")
            source_id = payload.get("source_id")
            locator = payload.get("locator")
            provider = payload.get("provider", "source")
            provider_config = payload.get("provider_config")
            label = payload.get("label")
            alert_on = payload.get("alert_on")
            interval_seconds = payload.get("interval_seconds")
            if interval_seconds is not None and (isinstance(interval_seconds, bool) or not isinstance(interval_seconds, int)):
                raise ValueError("monitor interval_seconds must be an integer when provided")
            if project_id is not None and not isinstance(project_id, str):
                raise ValueError("monitor project_id must be a string when provided")
            if source_id is not None and not isinstance(source_id, str):
                raise ValueError("monitor source_id must be a string when provided")
            if locator is not None and not isinstance(locator, str):
                raise ValueError("monitor locator must be a string when provided")
            if not isinstance(provider, str):
                raise ValueError("monitor provider must be a string")
            if provider_config is not None and not isinstance(provider_config, Mapping):
                raise ValueError("monitor provider_config must be an object when provided")
            if label is not None and not isinstance(label, str):
                raise ValueError("monitor label must be a string when provided")
            if alert_on is not None:
                if isinstance(alert_on, (str, bytes)) or not isinstance(alert_on, Sequence):
                    raise ValueError("monitor alert_on must be an array of event names")
                if not all(isinstance(value, str) for value in alert_on):
                    raise ValueError("monitor alert_on entries must be strings")
            return _json(
                create_monitor(
                    service,
                    project_id=project_id,
                    source_id=source_id,
                    locator=locator,
                    provider=provider,
                    provider_config=None if provider_config is None else dict(provider_config),
                    label=label,
                    interval_seconds=interval_seconds,
                    alert_on=alert_on,
                )
            )
    except ValueError as exc:
        return _error("invalid_argument", str(exc))
    return _error("route_not_found", "unknown Testamur write route", status=404)


def _static(path: Path, *, cache: str) -> dict[str, Any]:
    try:
        body = path.read_bytes()
    except OSError:
        return {
            "status": int(HTTPStatus.NOT_FOUND),
            "headers": {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store", **_SECURITY_HEADERS},
            "body": b"Not found\n",
        }
    return {
        "status": int(HTTPStatus.OK),
        "headers": {
            "Content-Type": _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream"),
            "Cache-Control": cache,
            **_SECURITY_HEADERS,
        },
        "body": body,
    }


def dispatch_web_get(service: TestamurProductService, target: str) -> dict[str, Any]:
    split = urlsplit(target)
    path = unquote(split.path or "/")
    if path == "/v1" or path.startswith("/v1/"):
        return dispatch_api(service, target)
    if "\x00" in path:
        return _error("invalid_path", "invalid request path")
    if path in {"/", "/index.html"}:
        return _static(WEB_ROOT / "index.html", cache="no-cache")
    name = path.lstrip("/")
    if name in _STATIC_ASSETS:
        return _static(WEB_ROOT / name, cache="public, max-age=300")
    first = name.split("/", 1)[0]
    if first in _SPA_PREFIXES:
        return _static(WEB_ROOT / "index.html", cache="no-cache")
    return {
        "status": int(HTTPStatus.NOT_FOUND),
        "headers": {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store", **_SECURITY_HEADERS},
        "body": b"Not found\n",
    }


def _database_path(*, database: str | None, start: str | None) -> Path:
    if database:
        return Path(database).expanduser().resolve()
    environment = discover(start)
    if environment is not None:
        return environment.database_path
    root = Path(start or os.getcwd()).expanduser().resolve()
    return root / ENV_DIR_NAME / DB_FILE_NAME


def _host_allowed(value: str | None, bind_host: str) -> bool:
    if not value:
        return False
    host = value.strip().lower()
    if host.startswith("["):
        hostname = host.split("]", 1)[0] + "]"
    else:
        hostname = host.split(":", 1)[0]
    allowed = {bind_host.lower(), "localhost", "127.0.0.1", "[::1]"}
    return hostname in allowed


class TestamurWebHandler(BaseHTTPRequestHandler):
    server_version = "TestamurWeb/1"

    def _send(self, response: Mapping[str, Any]) -> None:
        body = response["body"]
        if isinstance(body, Mapping):
            encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        else:
            encoded = bytes(body)
        self.send_response(int(response["status"]))
        for key, value in dict(response["headers"]).items():
            self.send_header(str(key), str(value))
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        self._send(dispatch_web_get(self.server.product_service, self.path))  # type: ignore[attr-defined]

    def do_POST(self) -> None:
        bind_host = str(self.server.server_address[0])
        if not _host_allowed(self.headers.get("Host"), bind_host):
            self._send(_error("host_not_allowed", "request host is not allowed", status=403))
            return
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._send(_error("invalid_content_type", "write requests require application/json", status=415))
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self._send(_error("invalid_content_length", "invalid Content-Length"))
            return
        if length < 1 or length > _MAX_JSON_BODY:
            self._send(_error("invalid_body_size", f"JSON body must be between 1 and {_MAX_JSON_BODY} bytes", status=413))
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send(_error("invalid_json", "request body must contain valid UTF-8 JSON"))
            return
        if not isinstance(payload, Mapping):
            self._send(_error("invalid_json", "request JSON body must be an object"))
            return
        self._send(dispatch_api_write(self.server.product_service, self.path, payload))  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        if os.environ.get("TESTAMUR_WEB_LOG", "").lower() in {"1", "true", "yes"}:
            super().log_message(format, *args)


def build_server(
    service: TestamurProductService,
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
) -> ThreadingHTTPServer:
    class _Server(ThreadingHTTPServer):
        product_service = service

    server = _Server((host, int(port)), TestamurWebHandler)
    server.product_service = service  # type: ignore[attr-defined]
    return server


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the local Testamur product UI")
    parser.add_argument("--database")
    parser.add_argument("--start")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(list(argv) if argv is not None else None)

    database = _database_path(database=args.database, start=args.start)
    registry = load_monitor_provider_registry()
    service = TestamurProductService.integrated(
        database,
        monitor_target_providers=registry.providers,
        monitor_target_provider_specs=registry.specs,
    )
    server = build_server(service, host=args.host, port=args.port)
    print(f"Testamur web: http://{args.host}:{args.port}/")
    if registry.providers:
        print("Monitor providers: " + ", ".join(sorted(registry.providers)))
    for error in registry.errors:
        print(f"Monitor provider warning: {error}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
