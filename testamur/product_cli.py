from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence, TextIO

from .contracts import error_envelope
from .product_actions import create_monitor, create_project, refresh_monitor, refresh_project_monitors, run_due_monitors
from .environment import DB_FILE_NAME, ENV_DIR_NAME, discover
from .monitor_provider_manifest import load_monitor_provider_registry
from .product_service import TestamurProductService


EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_FOUND = 4
EXIT_CAPABILITY_UNAVAILABLE = 5
EXIT_ERROR = 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="testamur product",
        description="Canonical Testamur Source/Record/Watch product read surface.",
    )
    parser.add_argument(
        "--database",
        default=None,
        help="explicit Testamur SQLite path; defaults to the discovered environment store",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status")

    project = sub.add_parser("project")
    project_sub = project.add_subparsers(dest="project_command", required=True)
    project_create = project_sub.add_parser("create")
    project_create.add_argument("name")
    project_create.add_argument("--description")
    project_create.add_argument("--visibility", choices=("private", "public"), default="private")
    project_sub.add_parser("list")
    project_show = project_sub.add_parser("show")
    project_show.add_argument("ref")
    project_refresh = project_sub.add_parser("refresh")
    project_refresh.add_argument("ref")

    monitor = sub.add_parser("monitor")
    monitor_sub = monitor.add_subparsers(dest="monitor_command", required=True)
    monitor_add = monitor_sub.add_parser("add")
    monitor_add.add_argument("--project", required=True)
    monitor_add.add_argument("--provider", default="source")
    monitor_add.add_argument("--provider-config", help="JSON object passed to a plugin monitor target provider")
    monitor_target = monitor_add.add_mutually_exclusive_group(required=False)
    monitor_target.add_argument("--locator")
    monitor_target.add_argument("--source-id")
    monitor_add.add_argument("--label")
    monitor_add.add_argument("--interval", choices=("manual", "5m", "15m", "1h", "6h", "24h"), default="manual")
    monitor_add.add_argument("--alert-on", action="append", choices=("changed", "unavailable", "recovered"), dest="alert_on")
    monitor_list = monitor_sub.add_parser("list")
    monitor_list.add_argument("--project", required=True)
    monitor_refresh = monitor_sub.add_parser("refresh")
    monitor_refresh.add_argument("watch_id")
    monitor_sub.add_parser("run-due")

    show = sub.add_parser("show")
    show.add_argument("ref")

    history = sub.add_parser("history")
    history.add_argument("ref")
    history.add_argument("--limit", type=int, default=50)

    compare = sub.add_parser("compare")
    compare.add_argument("left_ref")
    compare.add_argument("right_ref")

    impact = sub.add_parser("impact")
    impact.add_argument("ref")

    temporal = sub.add_parser("temporal")
    temporal.add_argument("ref")
    temporal.add_argument(
        "--clause",
        action="append",
        required=True,
        metavar="MODE=TIME",
        help="Explicit temporal clause: KNOWN_AT=..., AVAILABLE_BY=..., or EFFECTIVE_AT=...",
    )
    temporal.add_argument("--perspective")
    temporal.add_argument("--limit", type=int, default=500)

    temporal_events = sub.add_parser(
        "temporal-events",
        help="read first-class retrospective/late temporal audit events",
    )
    temporal_events.add_argument("ref")
    temporal_events.add_argument(
        "--event-kind",
        action="append",
        dest="event_kinds",
        metavar="KIND",
        help="restrict to an event kind; repeat to select multiple kinds",
    )
    temporal_events.add_argument(
        "--recorded-by",
        help="include events durably recorded no later than this explicit timestamp",
    )
    temporal_events.add_argument(
        "--event-time-by",
        help="include exact/queryable represented-world event times no later than this timestamp",
    )
    temporal_events.add_argument("--perspective")
    temporal_events.add_argument("--limit", type=int, default=500)
    return parser


def _database_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    environment = discover()
    if environment is not None:
        return environment.database_path
    return Path(ENV_DIR_NAME) / DB_FILE_NAME


def _monitor_interval_seconds(value: str) -> int | None:
    mapping = {
        "manual": None,
        "5m": 300,
        "15m": 900,
        "1h": 3600,
        "6h": 21600,
        "24h": 86400,
    }
    return mapping[str(value)]


def _parse_temporal_clauses(values: Sequence[str]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    allowed = {"known_at", "available_by", "effective_at"}
    for value in values:
        mode, separator, at = str(value).partition("=")
        normalized = mode.strip().lower()
        if not separator or normalized not in allowed or not at.strip():
            raise ValueError(
                "temporal clauses must be MODE=TIME with MODE one of "
                "KNOWN_AT, AVAILABLE_BY, EFFECTIVE_AT"
            )
        result.append({"mode": normalized, "at": at.strip()})
    return result


def _exit_code(payload: dict[str, Any]) -> int:
    if payload.get("ok") is True:
        return EXIT_OK
    error = payload.get("error")
    code = str(error.get("code") if isinstance(error, dict) else "")
    if code == "object_not_found":
        return EXIT_NOT_FOUND
    if code.endswith("capability_unavailable"):
        return EXIT_CAPABILITY_UNAVAILABLE
    if code in {
        "unsupported_object_kind",
        "history_not_supported",
        "compare_not_supported",
        "incompatible_compare",
        "invalid_temporal_query",
        "invalid_temporal_event_query",
    }:
        return EXIT_USAGE
    return EXIT_ERROR


def dispatch(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    service: TestamurProductService | None = None,
) -> int:
    """Execute one product read command and emit exactly one JSON object.

    The CLI is intentionally a projection over TestamurProductService. It owns no
    durable rows and does not reconstruct missing W2-W5 semantics. This makes it
    safe for the open-source CLI and Hosted to consume the same canonical service
    contracts without a second truth model.
    """

    output = stdout or sys.stdout
    parser = _parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
        if service is None:
            registry = load_monitor_provider_registry()
            product = TestamurProductService.integrated(
                _database_path(args.database),
                monitor_target_providers=registry.providers,
                monitor_target_provider_specs=registry.specs,
            )
        else:
            product = service
        if args.command == "status":
            payload = product.status()
        elif args.command == "project":
            if args.project_command == "create":
                payload = create_project(
                    product,
                    name=args.name,
                    description=args.description,
                    visibility=args.visibility,
                )
            elif args.project_command == "list":
                payload = {
                    "ok": True,
                    "schema": "testamur.product.projects.v2",
                    "projects": product.dashboard(limit=100).get("projects", []),
                }
            elif args.project_command == "show":
                payload = product.project(args.ref)
            elif args.project_command == "refresh":
                payload = refresh_project_monitors(product, project_ref=args.ref)
            else:
                raise ValueError(f"unsupported project command: {args.project_command}")
        elif args.command == "monitor":
            if args.monitor_command == "add":
                provider_config = None
                if args.provider_config:
                    parsed_config = json.loads(args.provider_config)
                    if not isinstance(parsed_config, dict):
                        raise ValueError("--provider-config must be a JSON object")
                    provider_config = parsed_config
                payload = create_monitor(
                    product,
                    project_id=args.project,
                    locator=args.locator,
                    source_id=args.source_id,
                    provider=args.provider,
                    provider_config=provider_config,
                    label=args.label,
                    interval_seconds=_monitor_interval_seconds(args.interval),
                    alert_on=args.alert_on,
                )
            elif args.monitor_command == "list":
                detail = product.project(args.project)
                payload = detail if not detail.get("ok") else {
                    "ok": True,
                    "schema": "testamur.product.project-monitors.v2",
                    "project": detail["project"],
                    "monitors": detail["monitors"],
                }
            elif args.monitor_command == "refresh":
                payload = refresh_monitor(product, watch_id=args.watch_id)
            elif args.monitor_command == "run-due":
                payload = run_due_monitors(product)
            else:
                raise ValueError(f"unsupported monitor command: {args.monitor_command}")
        elif args.command == "show":
            payload = product.get_object(args.ref)
        elif args.command == "history":
            payload = product.history(args.ref, limit=args.limit)
        elif args.command == "compare":
            payload = product.compare(args.left_ref, args.right_ref)
        elif args.command == "impact":
            payload = product.impact(args.ref)
        elif args.command == "temporal":
            payload = product.temporal(
                args.ref,
                {
                    "clauses": _parse_temporal_clauses(args.clause),
                    "perspective": args.perspective,
                    "limit": args.limit,
                },
            )
        elif args.command == "temporal-events":
            payload = product.temporal_events(
                args.ref,
                {
                    "event_kinds": args.event_kinds,
                    "recorded_by": args.recorded_by,
                    "event_time_by": args.event_time_by,
                    "perspective": args.perspective,
                    "limit": args.limit,
                },
            )
        else:  # pragma: no cover - argparse owns command validation.
            raise ValueError(f"unsupported product command: {args.command}")
    except ValueError as exc:
        payload = error_envelope("invalid_product_request", str(exc))
        code = EXIT_USAGE
    else:
        code = _exit_code(payload)

    output.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    output.write("\n")
    return code


def main(argv: Sequence[str] | None = None) -> int:
    return dispatch(argv)


if __name__ == "__main__":
    raise SystemExit(main())
