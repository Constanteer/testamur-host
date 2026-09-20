"""Testamur canonical package.

The ``witness`` namespace is migration compatibility only. New production code
must use ``testamur``; historical storage and wire identifiers may remain stable
without requiring the legacy Python package as the implementation owner.
"""

from .project_review_surface import project_with_advisory_reviews
from .store_lifecycle import install_store_connection_lifecycle
from .supply_chain_lifecycle import install_immutable_scan_observations

__version__ = "0.3.0.dev0"

# Keep the historical graph-store resource semantics when consumers enter through
# the canonical namespace.  This is idempotent and imports no legacy package.
install_store_connection_lifecycle()

# Supply-chain state revisions remain deduplicated, but each scan invocation is
# an immutable observation event even when it observes identical state.
install_immutable_scan_observations()

__all__ = ["__version__", "project_with_advisory_reviews"]
