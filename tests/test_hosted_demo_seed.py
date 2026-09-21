from __future__ import annotations

from pathlib import Path

from scripts.seed_supply_chain_demo import seed_demo
from testamur.product_service import TestamurProductService


def test_hosted_demo_seed_creates_real_two_observation_project(tmp_path: Path) -> None:
    database = tmp_path / "state" / "testamur.sqlite3"
    repository = tmp_path / "demo-repository"

    result = seed_demo(database=database, repository=repository)

    assert result["baseline_scan_revision_id"] != result["current_scan_revision_id"]
    assert result["counts"]["dependencies_changed"] == 1
    assert result["counts"]["dependencies_removed"] == 1
    assert result["semantics"]["changed_implies_invalid"] is False
    assert result["semantics"]["dependency_change_implies_vulnerable"] is False
    assert result["semantics"]["affectedness_inferred"] is False
    assert result["semantics"]["generic_trust_score_used"] is False
    assert (repository / "requirements.txt").read_text(encoding="utf-8").splitlines()[-1] == "requests==2.32.5"

    service = TestamurProductService.integrated(database)
    project = service.project(str(result["project_id"]))
    assert project["ok"] is True
    assert project["object"]["ref"] == result["project_id"]
    assert project["data"]["project_id"] == result["project_id"]
    assert project["data"]["supply_chain"]["dependency_count"] == 1
