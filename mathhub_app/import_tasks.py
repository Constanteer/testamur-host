from __future__ import annotations

import import_tasks_legacy as _legacy
from import_tasks_legacy import *  # noqa: F401,F403 - compatibility re-export
from import_tasks_legacy import ImportTaskStore as _LegacyImportTaskStore
from sqlite_lifecycle import connect_sqlite

# Keep the historical module-level patch points working after the storage
# lifecycle wrapper split. Inherited legacy methods resolve these names in
# import_tasks_legacy.__globals__, so each legacy global delegates back through
# this public module. Tests and out-of-tree integrations can therefore keep
# patching import_tasks.<hook> exactly as before.
_attach_external_argument_evidence = _legacy._attach_external_argument_evidence
_classify_unbound = _legacy._classify_unbound
_inspect_batch = _legacy._inspect_batch
import_existing_declarations = _legacy.import_existing_declarations


def _bridge_attach_external_argument_evidence(*args, **kwargs):
    return _attach_external_argument_evidence(*args, **kwargs)


def _bridge_classify_unbound(*args, **kwargs):
    return _classify_unbound(*args, **kwargs)


def _bridge_inspect_batch(*args, **kwargs):
    return _inspect_batch(*args, **kwargs)


def _bridge_import_existing_declarations(*args, **kwargs):
    return import_existing_declarations(*args, **kwargs)


_legacy._attach_external_argument_evidence = _bridge_attach_external_argument_evidence
_legacy._classify_unbound = _bridge_classify_unbound
_legacy._inspect_batch = _bridge_inspect_batch
_legacy.import_existing_declarations = _bridge_import_existing_declarations


class ImportTaskStore(_LegacyImportTaskStore):
    """Import task store with deterministic SQLite connection lifecycle."""

    def connect(self):
        return connect_sqlite(self.path, wal=True)


__all__ = [name for name in globals() if not name.startswith("_")]
