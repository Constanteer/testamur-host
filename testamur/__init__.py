"""Testamur canonical package.

The ``witness`` namespace is migration compatibility only. New production code
must use ``testamur``; historical storage and wire identifiers may remain stable
without requiring the legacy Python package as the implementation owner.
"""

from . import project_store as _project_store
from .project_review_surface import project_with_advisory_reviews
from .store_lifecycle import install_store_connection_lifecycle
from .supply_chain_diff_lifecycle import install_dependency_version_transitions
from .supply_chain_lifecycle import install_immutable_scan_observations

__version__ = "1.1.0"

# Repository-binding identifiers are package-owned stable storage identifiers.
# Keep these assignments here until project_store's next schema edit can inline
# them without rewriting unrelated storage code.
_project_store.REPOSITORY_BINDING_PREFIX = "rpb_"
_project_store.REPOSITORY_BINDING_REVISION_PREFIX = "rpbr_"
_project_store._REPOSITORY_BINDING_KINDS = {"local-path", "git"}

# Keep the historical graph-store resource semantics when consumers enter through
# the canonical namespace. This is idempotent and imports no legacy package.
install_store_connection_lifecycle()

# Supply-chain state revisions remain deduplicated, but each scan invocation is
# an immutable observation event even when it observes identical state.
install_immutable_scan_observations()

# Mechanical dependency diffs expose conservative upgrade/downgrade labels when
# dotted-numeric versions are unambiguous. Unknown ecosystem ordering is never
# guessed, and version direction remains distinct from validity/affectedness.
install_dependency_version_transitions()

__all__ = ["__version__", "project_with_advisory_reviews"]
