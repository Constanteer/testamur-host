from __future__ import annotations

import pytest

from testamur.product_service import TestamurProductService
from testamur.repository_binding_lifecycle import unbind_repository
from testamur.supply_chain import scan_bound_project_supply_chain


def test_canonical_bound_scan_rejects_disabled_binding_before_observation(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("requests==2.32.0\n", encoding="utf-8")

    service = TestamurProductService.integrated(tmp_path / "testamur.sqlite3")
    project = service.projects.create_project(name="Disabled core scan")
    binding = service.projects.bind_repository(project["project_id"], locator=str(repo))
    before_revision = binding["revision"]["binding_revision_id"]

    disabled = unbind_repository(service.projects, project["project_id"])
    assert disabled["enabled"] is False
    assert disabled["revision"]["binding_revision_id"] == before_revision

    with pytest.raises(ValueError, match="disabled"):
        scan_bound_project_supply_chain(service, project["project_id"])

    # Rejection is scanner eligibility only: it must happen before a scan record
    # or other observation evidence is created.
    assert service.records.list_records(record_kind="supply-chain-scan", limit=10) == []

    still_bound = service.projects.repository_binding(project["project_id"])
    assert still_bound is not None
    assert still_bound["binding_id"] == binding["binding_id"]
    assert still_bound["revision"]["binding_revision_id"] == before_revision
