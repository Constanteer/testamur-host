from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from .source_fetch import SourceFetchPolicy
from .source_gateway import TestamurSourceGateway


def _default_db() -> Path:
    explicit = os.environ.get("TESTAMUR_DB")
    if explicit:
        return Path(explicit).expanduser()
    return Path.cwd() / ".testamur" / "evidence.db"


def _add_fetch_policy(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-bytes", type=int, default=10 * 1024 * 1024)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--max-redirects", type=int, default=5)
    parser.add_argument("--allow-private-network", action="store_true")


def _policy(args: argparse.Namespace) -> SourceFetchPolicy:
    return SourceFetchPolicy(
        max_bytes=int(args.max_bytes),
        timeout_seconds=float(args.timeout),
        max_redirects=int(args.max_redirects),
        allow_private_network=bool(args.allow_private_network),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="testamur-gateway",
        description="Exact-revision Source Gateway for agent/tool integrations.",
    )
    parser.add_argument("--db", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="capture exact bytes and mint a SourceRevision")
    fetch.add_argument("locator")
    fetch.add_argument("--session")
    _add_fetch_policy(fetch)

    opened = sub.add_parser("open-revision", help="write retained exact revision bytes to stdout")
    opened.add_argument("revision_id")
    opened.add_argument("--max-bytes", type=int, default=None)

    status = sub.add_parser("source-status", help="show latest captured state for a Source")
    status.add_argument("source_id")

    revalidate = sub.add_parser("revalidate", help="capture the Source's initial locator again")
    revalidate.add_argument("source_id")
    revalidate.add_argument("--session")
    _add_fetch_policy(revalidate)

    refresh = sub.add_parser("watch-refresh", help="revalidate and mechanically evaluate one Watch")
    refresh.add_argument("watch_id")
    refresh.add_argument("--session")
    _add_fetch_policy(refresh)
    return parser


def _json(value: object) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    db = args.db or _default_db()
    gateway = TestamurSourceGateway(db)
    try:
        if args.command == "fetch":
            result = gateway.fetch(
                args.locator,
                session_id=args.session,
                actor={"ref": "testamur:gateway-cli"},
                policy=_policy(args),
            )
            _json(result.metadata())
            return 0
        if args.command == "open-revision":
            value = gateway.open_revision(args.revision_id, max_bytes=args.max_bytes)
            sys.stdout.buffer.write(value)
            return 0
        if args.command == "source-status":
            _json(gateway.source_status(args.source_id))
            return 0
        if args.command == "revalidate":
            result = gateway.revalidate(
                args.source_id,
                session_id=args.session,
                actor={"ref": "testamur:gateway-cli"},
                policy=_policy(args),
            )
            _json(result.metadata())
            return 0
        if args.command == "watch-refresh":
            _json(
                gateway.refresh_watch(
                    args.watch_id,
                    session_id=args.session,
                    actor={"ref": "testamur:gateway-cli"},
                    policy=_policy(args),
                )
            )
            return 0
        raise AssertionError(args.command)
    except Exception as exc:
        sys.stderr.write(f"testamur-gateway: {exc}\n")
        return 1
    finally:
        gateway.close()


if __name__ == "__main__":
    raise SystemExit(main())
