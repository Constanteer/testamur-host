from __future__ import annotations

import sys
from collections.abc import Sequence

from .entrypoint import main as core_main
from .product_cli import dispatch as product_dispatch
from .project_advisory_cli import dispatch as project_advisory_dispatch
from .source_cli import dispatch as source_dispatch


def _split_global_json(argv: Sequence[str]) -> tuple[bool, list[str]]:
    """Remove Testamur's global --json flag before product sub-routing."""
    values = list(argv)
    try:
        separator = values.index("--")
    except ValueError:
        head, tail = values, []
    else:
        head, tail = values[:separator], values[separator:]
    machine = "--json" in head
    if machine:
        head = [value for value in head if value != "--json"]
    return machine, [*head, *tail]


def _product_argv(argv: Sequence[str]) -> list[str] | None:
    _machine, normalized = _split_global_json(argv)
    if normalized and normalized[0] == "product":
        return normalized[1:]
    return None


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    machine, normalized = _split_global_json(raw)
    if normalized and normalized[0] == "source":
        return int(source_dispatch(normalized[1:], machine=machine))
    if normalized[:2] == ["project", "advisory-review"]:
        return int(project_advisory_dispatch(normalized[2:]))
    if normalized and normalized[0] in {"project", "monitor"}:
        return int(product_dispatch(normalized))

    product_argv = _product_argv(raw)
    if product_argv is not None:
        return int(product_dispatch(product_argv))

    code = int(core_main(raw))
    if not raw or raw in (["--help"], ["-h"]):
        print(
            "\nProjects and monitoring:\n"
            "  testamur project create <name> [--description ...] [--visibility private|public]\n"
            "  testamur project list\n"
            "  testamur project show <name|id>\n"
            "  testamur project refresh <name|id>\n"
            "  testamur project import [path] [--name <name>] [--visibility private|public]\n"
            "  testamur project bind-repo <name|id> <locator> [--kind local-path|git]\n"
            "  testamur project scan <name|id>\n"
            "  testamur project supply-chain <name|id>\n"
            "  testamur project supply-chain-history <name|id>\n"
            "  testamur project supply-chain-diff <name|id>\n"
            "  testamur project advisory-review <name|id>\n"
            "  testamur monitor add --project <name|id> --locator <url> [--interval manual|5m|15m|1h|6h|24h]\n"
            "  testamur monitor add --project <name|id> --provider <name> --provider-config <json>\n"
            "  testamur monitor list --project <name|id>\n"
            "  testamur monitor refresh <watch-id>\n"
            "  testamur monitor run-due\n"
            "\nProduct/kernel reads:\n"
            "  testamur product status\n"
            "  testamur product temporal <ref> --clause KNOWN_AT=<timestamp>\n"
            "  testamur product temporal-events <ref> [--event-kind <kind>]\n"
            "\nSource writes:\n"
            "  testamur source upload <file>\n"
            "  testamur source retention-status <source-id>\n"
            "  testamur source retention <source-id> retain|purge_on_request\n"
            "  testamur source purge <source-id>\n"
            "Source uploads begin private; filename/media-type convenience metadata stays erasable.\n"
            "Raw-byte purge preserves Source/Snapshot/Revision evidence identity.\n"
            "Product reads are machine-first JSON and preserve explicit temporal semantics."
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
