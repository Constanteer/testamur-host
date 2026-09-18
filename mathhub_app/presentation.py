from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from presentation_ir import (
    IR_VERSION,
    canonical_from_declaration as _canonical_from_declaration,
    normalize_surface,
    parse_declaration_ir,
    render_latex,
)
from registry import canonical_hash, claim_ref, utc_now, version_ref

RENDERER_VERSION = IR_VERSION
CURATED_KINDS = {"concise", "textbook", "intuitive", "translation"}


def normalize_math_text(text: str) -> str:
    """Compatibility wrapper for the deterministic presentation normalizer."""
    return normalize_surface(text)


def canonical_from_declaration(declaration: str) -> dict[str, Any]:
    """Build the current deterministic presentation from a Lean declaration.

    The returned IR is a presentation structure, not a second formal semantics.
    Lean remains authoritative for the Claim.
    """
    return _canonical_from_declaration(declaration)


def math_text_to_latex(text: str) -> str:
    """Compatibility helper for callers with a proposition-only surface string."""
    return render_latex({"node": "atom", "text": normalize_surface(text)})


def suggested_title(lean_name: str) -> str:
    tail = (lean_name or "Claim").split(".")[-1].strip() or "Claim"
    words = [word for word in tail.replace("'", " prime").split("_") if word]
    return (" ".join(words) or tail).strip().capitalize()


class PresentationIndex:
    """Append-only display records attached to Claim revisions.

    Presentation data never participates in Lean verification or dependency
    extraction. Generated canonical records may be regenerated when the
    renderer evolves; curated records remain historical human artifacts.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS claim_presentations(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  claim_id INTEGER NOT NULL REFERENCES claims(id),
                  claim_version_id INTEGER NOT NULL REFERENCES claim_versions(id),
                  kind TEXT NOT NULL,
                  language TEXT NOT NULL DEFAULT 'und',
                  source_kind TEXT NOT NULL CHECK(source_kind IN('generated','curated','imported')),
                  text TEXT NOT NULL DEFAULT '',
                  latex TEXT NOT NULL DEFAULT '',
                  author TEXT NOT NULL DEFAULT '',
                  renderer_version TEXT NOT NULL DEFAULT '',
                  content_hash TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  UNIQUE(claim_version_id,kind,language,source_kind,content_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_presentations_version
                  ON claim_presentations(claim_version_id,id DESC);
                CREATE INDEX IF NOT EXISTS idx_presentations_surface
                  ON claim_presentations(claim_version_id,source_kind,kind,renderer_version,id DESC);
                CREATE TRIGGER IF NOT EXISTS presentations_no_update
                BEFORE UPDATE ON claim_presentations BEGIN
                  SELECT RAISE(ABORT,'presentation records are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS presentations_no_delete
                BEFORE DELETE ON claim_presentations BEGIN
                  SELECT RAISE(ABORT,'presentation records are append-only');
                END;
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(claim_presentations)")}
            if "ir_json" not in columns:
                conn.execute("ALTER TABLE claim_presentations ADD COLUMN ir_json TEXT NOT NULL DEFAULT '{}'")

    def _current_version(self, conn: sqlite3.Connection, claim_id: int) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM claim_versions WHERE claim_id=? ORDER BY version_no DESC LIMIT 1",
            (claim_id,),
        ).fetchone()
        if row is None:
            raise KeyError(claim_ref(claim_id))
        return row

    def _insert(
        self,
        conn: sqlite3.Connection,
        *,
        claim_id: int,
        version_id: int,
        kind: str,
        language: str,
        source_kind: str,
        text: str,
        latex: str,
        author: str = "",
        renderer_version: str = "",
        ir: dict[str, Any] | None = None,
    ) -> None:
        ir = ir or {}
        payload = {
            "kind": kind,
            "language": language,
            "source_kind": source_kind,
            "text": text,
            "latex": latex,
            "author": author,
            "renderer_version": renderer_version,
            "ir": ir,
        }
        conn.execute(
            """INSERT OR IGNORE INTO claim_presentations(
              claim_id,claim_version_id,kind,language,source_kind,text,latex,author,
              renderer_version,content_hash,created_at,ir_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                claim_id,
                version_id,
                kind,
                language,
                source_kind,
                text,
                latex,
                author,
                renderer_version,
                canonical_hash(payload),
                utc_now(),
                json.dumps(ir, ensure_ascii=False, separators=(",", ":")),
            ),
        )

    def _ensure_generated(self, conn: sqlite3.Connection, version: sqlite3.Row) -> None:
        rendered = canonical_from_declaration(str(version["formal_declaration"]))
        self._insert(
            conn,
            claim_id=int(version["claim_id"]),
            version_id=int(version["id"]),
            kind="canonical",
            language="zxx",
            source_kind="generated",
            text=str(rendered["text"]),
            latex=str(rendered["latex"]),
            renderer_version=str(rendered["renderer_version"]),
            ir=rendered.get("ir") or parse_declaration_ir(str(version["formal_declaration"])),
        )
        legacy_text = str(version["statement"] or "").strip()
        legacy_latex = str(version["latex"] or "").strip()
        if legacy_text and (legacy_text != rendered["text"] or legacy_latex):
            self._insert(
                conn,
                claim_id=int(version["claim_id"]),
                version_id=int(version["id"]),
                kind="concise",
                language="und",
                source_kind="curated",
                text=legacy_text,
                latex=legacy_latex,
                author=str(version["claim_author"] or ""),
            )

    @staticmethod
    def _serialize(row: sqlite3.Row) -> dict[str, Any]:
        try:
            ir = json.loads(row["ir_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            ir = {}
        return {
            "kind": row["kind"],
            "language": row["language"],
            "source_kind": row["source_kind"],
            "text": row["text"],
            "latex": row["latex"],
            "author": row["author"],
            "renderer_version": row["renderer_version"],
            "ir": ir,
            "created_at": row["created_at"],
        }

    @staticmethod
    def _select_canonical(items: list[dict[str, Any]]) -> dict[str, Any] | None:
        generated = [
            item
            for item in items
            if item["kind"] == "canonical" and item["source_kind"] == "generated"
        ]
        # Selection is semantic, never insertion-order policy:
        # current Lean-pretty > current source-IR > any older generated surface.
        lean_current = next(
            (
                item
                for item in generated
                if item["renderer_version"] == RENDERER_VERSION
                and (item.get("ir") or {}).get("input_basis") == "lean-pretty-type"
            ),
            None,
        )
        if lean_current is not None:
            return lean_current
        source_current = next(
            (item for item in generated if item["renderer_version"] == RENDERER_VERSION),
            None,
        )
        return source_current or (generated[0] if generated else None)

    def for_claim(self, claim_id: int, *, limit: int = 24) -> dict[str, Any]:
        """Return a bounded reading model without letting one surface evict another.

        Canonical generated surfaces, the preferred concise wording, and the
        recent curated list are queried independently. A Claim with thousands
        of explanations therefore still returns its canonical mathematics.
        """
        limit = max(1, min(int(limit), 100))
        with self.connect() as conn:
            version = self._current_version(conn, claim_id)
            version_id = int(version["id"])
            self._ensure_generated(conn, version)

            current_generated_rows = conn.execute(
                """SELECT * FROM claim_presentations
                   WHERE claim_version_id=? AND source_kind='generated'
                     AND kind='canonical' AND renderer_version=?
                   ORDER BY id DESC LIMIT 24""",
                (version_id, RENDERER_VERSION),
            ).fetchall()
            generated_rows = list(current_generated_rows)
            if not generated_rows:
                fallback = conn.execute(
                    """SELECT * FROM claim_presentations
                       WHERE claim_version_id=? AND source_kind='generated'
                         AND kind='canonical'
                       ORDER BY id DESC LIMIT 1""",
                    (version_id,),
                ).fetchone()
                if fallback is not None:
                    generated_rows.append(fallback)

            curated_rows = conn.execute(
                """SELECT * FROM claim_presentations
                   WHERE claim_version_id=? AND source_kind='curated'
                   ORDER BY id DESC LIMIT ?""",
                (version_id, limit),
            ).fetchall()
            concise_row = conn.execute(
                """SELECT * FROM claim_presentations
                   WHERE claim_version_id=? AND source_kind='curated' AND kind='concise'
                   ORDER BY id DESC LIMIT 1""",
                (version_id,),
            ).fetchone()

        generated = [self._serialize(row) for row in generated_rows]
        canonical = self._select_canonical(generated)
        curated = [self._serialize(row) for row in curated_rows]
        concise = self._serialize(concise_row) if concise_row is not None else None
        preferred = concise or canonical
        return {
            "claim_id": claim_ref(claim_id),
            "claim_version_id": version_ref(version_id),
            "suggested_title": suggested_title(str(version["lean_name"])),
            "canonical": canonical,
            "preferred": preferred,
            "curated": curated,
        }

    def add_curated(self, claim_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        kind = str(payload.get("kind") or "concise").strip().lower()
        if kind not in CURATED_KINDS:
            raise ValueError(f"kind must be one of: {', '.join(sorted(CURATED_KINDS))}")
        language = str(payload.get("language") or "und").strip().lower() or "und"
        if not re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]+)*|und", language):
            raise ValueError("language must be a BCP-47-like tag such as en, zh, or und")
        text = str(payload.get("text") or "").strip()
        latex = str(payload.get("latex") or "").strip()
        if not text and not latex:
            raise ValueError("presentation text or latex is required")
        author = str(payload.get("author") or "").strip()
        with self.connect() as conn:
            version = self._current_version(conn, claim_id)
            self._insert(
                conn,
                claim_id=claim_id,
                version_id=int(version["id"]),
                kind=kind,
                language=language,
                source_kind="curated",
                text=text,
                latex=latex,
                author=author,
            )
        return self.for_claim(claim_id)
