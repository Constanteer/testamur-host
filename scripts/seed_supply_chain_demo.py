from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from testamur.product_service import TestamurProductService
from testamur.supply_chain import diff_project_supply_chain, scan_bound_project_supply_chain


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples" / "supply-chain-demo"


def seed_demo(*, database: Path, repository: Path, reset_repository: bool = False) -> dict[str, object]:
    """Seed the deterministic launch demo through the real Project scan lifecycle.

    The result contains observations and a mechanical diff only. It deliberately
    does not infer validity, vulnerability, reliance, affectedness, or a trust score.
    """
    baseline = FIXTURE / "baseline"
    current = FIXTURE / "current"
    if not baseline.is_dir() or not current.is_dir():
        raise RuntimeError(f"demo fixture is incomplete: {FIXTURE}")

    if repository.exists():
        if not reset_repository:
            raise RuntimeError(
                f"demo repository already exists: {repository}; pass --reset-repository to replace it"
            )
        shutil.rmtree(repository)
    repository.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(baseline, repository)

    database.parent.mkdir(parents=True, exist_ok=True)
    service = TestamurProductService.integrated(database)
    project = service.projects.create_project(name="Testamur supply-chain demo")
    service.projects.bind_repository(project["project_id"], locator=str(repository))

    baseline_scan = scan_bound_project_supply_chain(service, project["project_id"])
    shutil.rmtree(repository)
    shutil.copytree(current, repository)
    current_scan = scan_bound_project_supply_chain(service, project["project_id"])
    comparison = diff_project_supply_chain(service, project["project_id"])

    semantics = comparison.get("semantics") or {}
    for key in (
        "changed_implies_invalid",
        "dependency_change_implies_vulnerable",
        "affectedness_inferred",
        "generic_trust_score_used",
    ):
        if semantics.get(key) is not False:
            raise RuntimeError(f"demo semantic boundary violated: {key}={semantics.get(key)!r}")

    return {
        "schema": "testamur.hosted.demo-seed.v1",
        "project_id": project["project_id"],
        "repository": str(repository),
        "baseline_scan_revision_id": baseline_scan["scan_revision_id"],
        "current_scan_revision_id": current_scan["scan_revision_id"],
        "counts": comparison["counts"],
        "semantics": semantics,
        "next": "Open the Project, inspect Compare, then review impact before recording any conclusion.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the deterministic Testamur hosted supply-chain demo")
    parser.add_argument("--database", type=Path, required=True, help="Target Testamur SQLite database")
    parser.add_argument("--repository", type=Path, required=True, help="Persistent demo repository path")
    parser.add_argument(
        "--reset-repository",
        action="store_true",
        help="Replace an existing demo repository. The database is never deleted.",
    )
    args = parser.parse_args()
    result = seed_demo(
        database=args.database.expanduser().resolve(),
        repository=args.repository.expanduser().resolve(),
        reset_repository=args.reset_repository,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
