"""Canonical persistent runtime ledger for Testamur.

Historical ``witness_runtime_*`` SQLite identifiers are handled internally by
storage compatibility code. The public Python surface exposes only Testamur
runtime types; opening an old database does not require a Witness package.
"""

from __future__ import annotations

from .runtime_store_v2 import TestamurProtocolLedger

__all__ = ["TestamurProtocolLedger"]
