from __future__ import annotations

from pathlib import Path
from typing import Any

from presentation import PresentationIndex, RENDERER_VERSION
from presentation_ir import canonical_from_declaration
from registry import parse_ref


class BuildPresentationRecorder:
    """Persist a deterministic canonical surface produced from a Lean Build.

    The record is attached to the exact Claim revision proved by the Proof.
    Rebuilding an old Proof therefore never replaces the current revision's
    presentation. Presentation remains derived display data, not verification.

    Build IDs intentionally do not participate in presentation identity. A
    repeated Build in the same formal environment should not create another
    copy of the same displayed formula; Build provenance remains in Builds.
    """

    def __init__(self, path: Path) -> None:
        self.index = PresentationIndex(path)

    def record(
        self,
        proof: dict[str, Any],
        build: dict[str, Any],
        audit: dict[str, Any],
        environment: dict[str, Any],
    ) -> dict[str, Any] | None:
        pretty_type = str(audit.get("pretty_type") or "").strip()
        if not pretty_type or not audit.get("audit_ok"):
            return None

        claim_id = parse_ref(str(proof["claim_id"]), "CLM")
        version_id = parse_ref(str(proof["claim_version_id"]), "VER")
        rendered = canonical_from_declaration(
            f"theorem {proof['lean_name']} : {pretty_type}"
        )
        ir = dict(rendered.get("ir") or {})
        ir.update(
            {
                "input_basis": "lean-pretty-type",
                "environment_hash": str(environment.get("environment_hash") or ""),
                "formal_fingerprint": str(audit.get("formal_fingerprint") or ""),
            }
        )

        with self.index.connect() as conn:
            # The Proof is immutable, so its claim_version_id is the authoritative
            # revision target even when a newer Claim revision exists.
            row = conn.execute(
                "SELECT claim_id FROM claim_versions WHERE id=?",
                (version_id,),
            ).fetchone()
            if row is None or int(row["claim_id"]) != claim_id:
                raise ValueError("Proof/Claim revision mismatch while recording presentation")
            self.index._insert(
                conn,
                claim_id=claim_id,
                version_id=version_id,
                kind="canonical",
                language="zxx",
                source_kind="generated",
                text=str(rendered["text"]),
                latex=str(rendered["latex"]),
                renderer_version=RENDERER_VERSION,
                ir=ir,
            )

        return {
            "claim_id": proof["claim_id"],
            "claim_version_id": proof["claim_version_id"],
            "renderer_version": RENDERER_VERSION,
            "input_basis": "lean-pretty-type",
            "text": rendered["text"],
            "latex": rendered["latex"],
            "source_build_id": build.get("build_id"),
        }
