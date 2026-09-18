from __future__ import annotations

from .revision_relations import TestamurRelationRevisionStore


class TestamurStore(TestamurRelationRevisionStore):
    """Canonical Testamur owner of the migrated historical graph store.

    Legacy SQLite table and wire identifiers are implementation-level migration
    inputs only. New Python code must depend on ``TestamurStore``.
    """


__all__ = ["TestamurStore"]
