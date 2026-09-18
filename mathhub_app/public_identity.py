from __future__ import annotations

import re
import secrets
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Crockford Base32: exactly 32 symbols => 5 bits per character.
# I/L/O/U are omitted from canonical output; human input tolerates O→0, I/L→1.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
TOKEN_CHARS = 16  # exactly 80 bits of entropy
GROUP_SIZE = 4
KINDS = {
    "claim": "CLM",
    "version": "VER",
    "proof": "PRF",
    "build": "BLD",
}
PREFIX_TO_KIND = {v: k for k, v in KINDS.items()}
INPUT_TRANSLATION = str.maketrans({"O": "0", "I": "1", "L": "1"})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def token() -> str:
    """Generate an 80-bit token grouped as XXXX-XXXX-XXXX-XXXX."""
    raw = "".join(secrets.choice(ALPHABET) for _ in range(TOKEN_CHARS))
    return "-".join(raw[i : i + GROUP_SIZE] for i in range(0, TOKEN_CHARS, GROUP_SIZE))


def slugify(value: str, fallback: str = "object") -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    if not slug:
        fallback = re.sub(r"[^A-Za-z0-9]+", "-", fallback or "object").strip("-").lower()
        slug = fallback or "object"
    return slug[:80]


def _normalize_group(group: str) -> str:
    normalized = group.upper().translate(INPUT_TRANSLATION)
    if len(normalized) != GROUP_SIZE or any(ch not in ALPHABET for ch in normalized):
        raise ValueError(f"invalid public reference group: {group!r}")
    return normalized


def parse_public_ref(value: str) -> tuple[str, str]:
    """Validate and normalize a public reference.

    New objects use four 4-character groups (80 bits). Two-group references are
    accepted only for compatibility with very early local prototype databases.
    """
    parts = value.strip().upper().split("-")
    if len(parts) not in (3, 5):
        raise ValueError(f"invalid public object reference: {value!r}")
    prefix, raw_groups = parts[0], parts[1:]
    if prefix not in PREFIX_TO_KIND or len(raw_groups) not in (2, 4):
        raise ValueError(f"invalid public object reference: {value!r}")
    try:
        groups = [_normalize_group(group) for group in raw_groups]
    except ValueError as exc:
        raise ValueError(f"invalid public object reference: {value!r}") from exc
    return PREFIX_TO_KIND[prefix], "-".join([prefix, *groups])


class PublicIdentity:
    """Stable user-facing references layered over SQLite integer primary keys.

    SQLite integers remain internal implementation keys. Public references are
    opaque and immutable. They are deliberately *not* content hashes: a Claim
    keeps one public reference while its append-only versions evolve, whereas
    version/proof/build content identity is tracked separately by cryptographic
    hashes in the registry.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS public_objects (
                    kind TEXT NOT NULL,
                    internal_id INTEGER NOT NULL,
                    public_id TEXT NOT NULL UNIQUE,
                    slug TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(kind, internal_id)
                );
                CREATE INDEX IF NOT EXISTS idx_public_objects_slug
                    ON public_objects(kind, slug);

                CREATE TRIGGER IF NOT EXISTS public_objects_no_update
                BEFORE UPDATE ON public_objects BEGIN
                    SELECT RAISE(ABORT, 'public object references are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS public_objects_no_delete
                BEFORE DELETE ON public_objects BEGIN
                    SELECT RAISE(ABORT, 'public object references are immutable');
                END;
            """)

    def ensure(self, kind: str, internal_id: int, *, title: str = "", fallback: str = "") -> dict[str, Any]:
        if kind not in KINDS:
            raise ValueError(f"unsupported public object kind: {kind}")
        if internal_id <= 0:
            raise ValueError("internal_id must be a positive integer")

        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM public_objects WHERE kind = ? AND internal_id = ?",
                (kind, internal_id),
            ).fetchone()
            if row is not None:
                return dict(row)

            prefix = KINDS[kind]
            object_slug = slugify(title, fallback) if kind == "claim" else ""
            for _ in range(32):
                public_id = f"{prefix}-{token()}"
                try:
                    conn.execute(
                        """INSERT INTO public_objects(kind, internal_id, public_id, slug, created_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (kind, internal_id, public_id, object_slug, utc_now()),
                    )
                    return {
                        "kind": kind,
                        "internal_id": internal_id,
                        "public_id": public_id,
                        "slug": object_slug,
                    }
                except sqlite3.IntegrityError:
                    continue
        raise RuntimeError("could not allocate a unique public object reference")

    def get(self, kind: str, internal_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM public_objects WHERE kind = ? AND internal_id = ?",
                (kind, internal_id),
            ).fetchone()
            return dict(row) if row is not None else None

    def resolve(self, public_id: str, expected_kind: str | None = None) -> tuple[str, int]:
        kind, normalized = parse_public_ref(public_id)
        if expected_kind and kind != expected_kind:
            raise ValueError(f"expected a {expected_kind} reference")
        with self.connect() as conn:
            row = conn.execute(
                "SELECT kind, internal_id FROM public_objects WHERE public_id = ?",
                (normalized,),
            ).fetchone()
            if row is None:
                raise KeyError(public_id)
            return str(row["kind"]), int(row["internal_id"])

    def ensure_claim(self, claim_internal_id: int, title: str, lean_name: str) -> dict[str, Any]:
        return self.ensure("claim", claim_internal_id, title=title, fallback=lean_name)

    def ensure_version(self, version_internal_id: int) -> dict[str, Any]:
        return self.ensure("version", version_internal_id)

    def ensure_proof(self, proof_internal_id: int) -> dict[str, Any]:
        return self.ensure("proof", proof_internal_id)

    def ensure_build(self, build_internal_id: int) -> dict[str, Any]:
        return self.ensure("build", build_internal_id)
