from testamur.project_store import TestamurProjectStore
from testamur.repository_binding_lifecycle import (
    enabled_repository_bindings,
    repository_binding_with_state,
    set_repository_binding_enabled,
    unbind_repository,
)


def test_unbind_preserves_binding_and_revision_history(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    store = TestamurProjectStore(tmp_path / "projects.sqlite3")
    project = store.create_project(name="Lifecycle")
    created = store.bind_repository(project["project_id"], locator=str(repo), binding_key="primary")

    before = repository_binding_with_state(store, project["project_id"])
    assert before is not None
    assert before["enabled"] is True

    disabled = unbind_repository(store, project["project_id"])
    assert disabled["enabled"] is False
    assert disabled["binding_id"] == created["binding"]["binding_id"]
    assert disabled["revision"]["binding_revision_id"] == created["revision"]["binding_revision_id"]
    assert enabled_repository_bindings(store, project["project_id"]) == []

    # Unbind is lifecycle state, not destructive deletion or a new evidence revision.
    still_bound = store.repository_binding(project["project_id"])
    assert still_bound is not None
    assert still_bound["binding_id"] == created["binding"]["binding_id"]
    assert still_bound["revision"]["binding_revision_id"] == created["revision"]["binding_revision_id"]


def test_binding_can_be_reenabled_without_rewriting_identity(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    store = TestamurProjectStore(tmp_path / "projects.sqlite3")
    project = store.create_project(name="Reenable")
    created = store.bind_repository(project["project_id"], locator=str(repo))

    unbind_repository(store, project["project_id"])
    enabled = set_repository_binding_enabled(store, project["project_id"], enabled=True)

    assert enabled["enabled"] is True
    assert enabled["binding_id"] == created["binding"]["binding_id"]
    active = enabled_repository_bindings(store, project["project_id"])
    assert len(active) == 1
    assert active[0]["binding_id"] == created["binding"]["binding_id"]
    assert active[0]["revision"]["binding_revision_id"] == created["revision"]["binding_revision_id"]
