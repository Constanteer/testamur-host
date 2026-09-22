from __future__ import annotations

import sqlite3
from typing import Any

from .project_store import TestamurProjectStore, _utc_now


def _ensure_schema(store: TestamurProjectStore) -> None:
    """Add lifecycle state without rewriting immutable binding revisions."""
    with store.connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS testamur_project_repository_binding_state(
                binding_id TEXT PRIMARY KEY
                    REFERENCES testamur_project_repository_bindings(binding_id) ON DELETE CASCADE,
                enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                changed_at TEXT NOT NULL
            )
            """
        )


def repository_binding_with_state(
    store: TestamurProjectStore,
    project_ref: str,
    *,
    binding_key: str = "primary",
) -> dict[str, Any] | None:
    """Return the latest immutable binding revision plus mutable lifecycle state.

    Lifecycle state controls whether the binding is eligible scanner input. It
    does not rewrite binding history and says nothing about observation,
    reliance, verification, or affectedness.
    """
    binding = store.repository_binding(project_ref, binding_key=binding_key)
    if binding is None:
        return None
    _ensure_schema(store)
    with store.connect() as conn:
        row = conn.execute(
            "SELECT enabled, changed_at FROM testamur_project_repository_binding_state WHERE binding_id = ?",
            (binding["binding_id"],),
        ).fetchone()
    result = dict(binding)
    result["enabled"] = True if row is None else bool(row["enabled"])
    result["lifecycle_changed_at"] = None if row is None else str(row["changed_at"])
    return result


def set_repository_binding_enabled(
    store: TestamurProjectStore,
    project_ref: str,
    *,
    binding_key: str = "primary",
    enabled: bool,
) -> dict[str, Any]:
    binding = store.repository_binding(project_ref, binding_key=binding_key)
    if binding is None:
        raise KeyError(binding_key)
    _ensure_schema(store)
    now = _utc_now()
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO testamur_project_repository_binding_state(binding_id, enabled, changed_at)
            VALUES (?, ?, ?)
            ON CONFLICT(binding_id) DO UPDATE SET enabled = excluded.enabled, changed_at = excluded.changed_at
            """,
            (binding["binding_id"], 1 if enabled else 0, now),
        )
    result = dict(binding)
    result["enabled"] = bool(enabled)
    result["lifecycle_changed_at"] = now
    result["semantics"] = {
        "disabled_binding_is_deleted": False,
        "disabled_binding_is_scanner_eligible": False,
        "state_change_implies_repository_content_observed": False,
        "state_change_implies_reliance": False,
        "state_change_implies_verification": False,
    }
    return result


def unbind_repository(
    store: TestamurProjectStore,
    project_ref: str,
    *,
    binding_key: str = "primary",
) -> dict[str, Any]:
    """Disable a binding while preserving its identity and revision history."""
    return set_repository_binding_enabled(
        store,
        project_ref,
        binding_key=binding_key,
        enabled=False,
    )


def enabled_repository_bindings(
    store: TestamurProjectStore,
    project_ref: str,
) -> list[dict[str, Any]]:
    return [
        binding
        for item in store.repository_bindings(project_ref)
        if (binding := repository_binding_with_state(
            store, project_ref, binding_key=str(item["binding_key"])
        )) is not None
        and binding["enabled"]
    ]
