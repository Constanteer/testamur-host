from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence, TextIO

from .environment import DB_FILE_NAME, ENV_DIR_NAME, discover
from .product_service import TestamurProductService
from .repository_binding_lifecycle import (
    repository_binding_with_state,
    set_repository_binding_enabled,
    unbind_repository,
)
from .supply_chain import scan_bound_project_supply_chain


def _database_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    environment = discover()
    if environment is not None:
        return environment.database_path
    return Path(ENV_DIR_NAME) / DB_FILE_NAME


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="testamur-project-repo",
        description="Explicit Project repository-binding lifecycle operations.",
    )
    parser.add_argument("--database")
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show")
    show.add_argument("project_ref")
    show.add_argument("--binding", default="primary")

    disable = sub.add_parser("disable", aliases=["unbind"])
    disable.add_argument("project_ref")
    disable.add_argument("--binding", default="primary")

    enable = sub.add_parser("enable")
    enable.add_argument("project_ref")
    enable.add_argument("--binding", default="primary")

    scan = sub.add_parser("scan")
    scan.add_argument("project_ref")
    scan.add_argument("--binding", default="primary")
    return parser


def _scan_enabled(service: TestamurProductService, project_ref: str, binding_key: str) -> dict:
    binding = repository_binding_with_state(service.projects, project_ref, binding_key=binding_key)
    if binding is None:
        raise ValueError(f"project has no repository binding named {binding_key!r}")
    if not binding["enabled"]:
        raise ValueError(
            f"repository binding {binding_key!r} is disabled; enable it before scanning"
        )
    return scan_bound_project_supply_chain(service, project_ref, binding_key=binding_key)


def dispatch(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    service: TestamurProductService | None = None,
) -> int:
    output = stdout or sys.stdout
    args = _parser().parse_args(list(argv) if argv is not None else None)
    product = service or TestamurProductService.integrated(_database_path(args.database))
    try:
        if args.command == "show":
            binding = repository_binding_with_state(
                product.projects, args.project_ref, binding_key=args.binding
            )
            if binding is None:
                raise ValueError(f"project has no repository binding named {args.binding!r}")
            payload = {
                "ok": True,
                "schema": "testamur.project-repository-binding-lifecycle.v1",
                "binding": binding,
            }
        elif args.command in {"disable", "unbind"}:
            payload = {
                "ok": True,
                "schema": "testamur.project-repository-binding-lifecycle.v1",
                "binding": unbind_repository(
                    product.projects, args.project_ref, binding_key=args.binding
                ),
            }
        elif args.command == "enable":
            payload = {
                "ok": True,
                "schema": "testamur.project-repository-binding-lifecycle.v1",
                "binding": set_repository_binding_enabled(
                    product.projects,
                    args.project_ref,
                    binding_key=args.binding,
                    enabled=True,
                ),
            }
        elif args.command == "scan":
            payload = _scan_enabled(product, args.project_ref, args.binding)
        else:  # pragma: no cover
            raise ValueError(f"unsupported command: {args.command}")
    except (KeyError, ValueError) as exc:
        payload = {
            "ok": False,
            "error": {"code": "invalid_repository_binding_request", "message": str(exc)},
        }
        code = 2
    else:
        code = 0
    output.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return code


def main(argv: Sequence[str] | None = None) -> int:
    return dispatch(argv)


if __name__ == "__main__":
    raise SystemExit(main())
