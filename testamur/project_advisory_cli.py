from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence, TextIO

from .environment import DB_FILE_NAME, ENV_DIR_NAME, discover
from .monitor_provider_manifest import load_monitor_provider_registry
from .product_service import TestamurProductService
from .project_review_surface import project_with_advisory_reviews


def _database_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    environment = discover()
    if environment is not None:
        return environment.database_path
    return Path(ENV_DIR_NAME) / DB_FILE_NAME


def dispatch(argv: Sequence[str] | None = None, *, stdout: TextIO | None = None) -> int:
    """Emit the canonical project advisory/revalidation projection as JSON.

    This command owns no assessment logic. It exposes the same read/work projection
    used by product surfaces, so exact identity overlap remains a candidate rather
    than an affectedness verdict and mechanical change remains non-invalidating.
    """

    parser = argparse.ArgumentParser(prog="testamur project advisory-review")
    parser.add_argument("ref")
    parser.add_argument("--database")
    args = parser.parse_args(list(argv) if argv is not None else None)

    registry = load_monitor_provider_registry()
    service = TestamurProductService.integrated(
        _database_path(args.database),
        monitor_target_providers=registry.providers,
        monitor_target_provider_specs=registry.specs,
    )
    payload = project_with_advisory_reviews(service, args.ref)
    output = stdout or sys.stdout
    output.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    output.write("\n")
    return 0 if payload.get("ok") is True else 1
