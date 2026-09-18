"""Canonical revision-DAG/CAS store facade.

Historical storage/wire identifiers are handled by the internal compatibility
implementation. The public Python surface exposes only Testamur-owned types.
"""

from .revision_store_legacy import (
    CONTENT_ADDRESS_VERSION,
    SNAPSHOT_VERSION,
    STATE_REVISION_VERSION,
)
from .revision_store_v2 import TestamurRevisionStore as _RevisionStoreImplementation


class TestamurRevisionStore(_RevisionStoreImplementation):
    """Public Testamur revision/CAS store over the legacy-compatible backend."""

    def snapshot_project(
        self,
        project: str,
        *,
        parent_addresses=None,
        actor_ref: str = "",
        message: str = "",
    ):
        """Compatibility spelling for committing an immutable project snapshot."""
        return self.commit_project_revision(
            project,
            parent_addresses=parent_addresses,
            actor_ref=actor_ref,
            message=message,
        )


__all__ = [
    "CONTENT_ADDRESS_VERSION",
    "SNAPSHOT_VERSION",
    "STATE_REVISION_VERSION",
    "TestamurRevisionStore",
]
