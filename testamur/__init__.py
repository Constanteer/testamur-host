"""Testamur canonical package.

The ``witness`` namespace is migration compatibility only. New production code
must use ``testamur``; historical storage and wire identifiers may remain stable
without requiring the legacy Python package as the implementation owner.
"""

from .store_lifecycle import install_store_connection_lifecycle

__version__ = "0.3.0.dev0"

# Keep the historical graph-store resource semantics when consumers enter through
# the canonical namespace.  This is idempotent and imports no legacy package.
install_store_connection_lifecycle()

__all__ = ["__version__"]
