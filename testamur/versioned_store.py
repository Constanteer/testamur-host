"""Canonical entrypoint for the fully versioned historical graph store.

Consumers that need revision DAGs, CAS-backed project snapshots, relation
lifecycle snapshots and assurance snapshot extensions should import from this
module rather than depending on the internal compatibility inheritance chain.
Historical storage identifiers may remain stable; the public Python type is
Testamur-owned.
"""

from .revision_assurance import (
    ASSURANCE_SNAPSHOT_VERSION,
    TestamurVersionedStore,
)
from .revision_store import (
    CONTENT_ADDRESS_VERSION,
    SNAPSHOT_VERSION,
    STATE_REVISION_VERSION,
)

__all__ = [
    "ASSURANCE_SNAPSHOT_VERSION",
    "CONTENT_ADDRESS_VERSION",
    "SNAPSHOT_VERSION",
    "STATE_REVISION_VERSION",
    "TestamurVersionedStore",
]
