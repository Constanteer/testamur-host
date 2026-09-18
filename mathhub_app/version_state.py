from __future__ import annotations

import sqlite3
from typing import Any


def current_version_state(
    conn: sqlite3.Connection,
    claim_id: int,
    version_id: int | None = None,
) -> dict[str, Any]:
    """Return the current-version verification state for one durable Claim.

    Claim identity is durable, but verification is not inherited across Claim
    Versions. A Build can witness only the exact Claim Version its Proof targets.

    Status preference is existential rather than "latest build wins": one verified
    proof is enough to keep the current Version verified even if another proof
    attempt later fails. If no verified/partial Build exists, an unbuilt Proof is
    partial; only fully built-and-failed attempts yield failed.
    """
    if version_id is None:
        row = conn.execute(
            "SELECT id FROM claim_versions WHERE claim_id = ? ORDER BY version_no DESC LIMIT 1",
            (claim_id,),
        ).fetchone()
        if row is None:
            raise KeyError(claim_id)
        version_id = int(row["id"])

    proof = conn.execute(
        "SELECT id FROM proofs WHERE claim_version_id = ? ORDER BY id DESC LIMIT 1",
        (version_id,),
    ).fetchone()
    current_proof_id = int(proof["id"]) if proof else None

    latest_build = conn.execute(
        """SELECT b.id, b.status FROM builds b
           JOIN proofs p ON p.id = b.proof_id
           WHERE p.claim_version_id = ? ORDER BY b.id DESC LIMIT 1""",
        (version_id,),
    ).fetchone()
    latest_build_id = int(latest_build["id"]) if latest_build else None

    for desired in ("verified", "partial"):
        witness = conn.execute(
            """SELECT b.id FROM builds b
               JOIN proofs p ON p.id = b.proof_id
               WHERE p.claim_version_id = ? AND b.status = ?
               ORDER BY b.id DESC LIMIT 1""",
            (version_id, desired),
        ).fetchone()
        if witness:
            return {
                "version_id": version_id,
                "status": desired,
                "verification_build_id": int(witness["id"]),
                "latest_build_id": latest_build_id,
                "current_proof_id": current_proof_id,
            }

    unbuilt = conn.execute(
        """SELECT p.id FROM proofs p
           WHERE p.claim_version_id = ?
             AND NOT EXISTS (SELECT 1 FROM builds b WHERE b.proof_id = p.id)
           ORDER BY p.id DESC LIMIT 1""",
        (version_id,),
    ).fetchone()
    if unbuilt:
        return {
            "version_id": version_id,
            "status": "partial",
            "verification_build_id": None,
            "latest_build_id": latest_build_id,
            "current_proof_id": current_proof_id,
        }

    failed = conn.execute(
        """SELECT b.id FROM builds b
           JOIN proofs p ON p.id = b.proof_id
           WHERE p.claim_version_id = ? AND b.status = 'failed'
           ORDER BY b.id DESC LIMIT 1""",
        (version_id,),
    ).fetchone()
    if failed:
        return {
            "version_id": version_id,
            "status": "failed",
            "verification_build_id": int(failed["id"]),
            "latest_build_id": latest_build_id,
            "current_proof_id": current_proof_id,
        }

    return {
        "version_id": version_id,
        "status": "partial" if current_proof_id is not None else "open",
        "verification_build_id": None,
        "latest_build_id": latest_build_id,
        "current_proof_id": current_proof_id,
    }
