from __future__ import annotations

import hashlib
import re
from typing import Any

import server as core
from presentation import canonical_from_declaration, suggested_title
from registry import parse_ref

# Import is intentionally conservative. Supporting escaped identifiers later is
# preferable to embedding arbitrary non-whitespace text into generated Lean source.
IMPORTABLE_LEAN_NAME = re.compile(
    r"^[A-Za-z_α-ωΑ-Ω][A-Za-z0-9_'α-ωΑ-Ω]*(?:\.[A-Za-z_α-ωΑ-Ω][A-Za-z0-9_'α-ωΑ-Ω]*)*$"
)


def _stable_alias(external_name: str, environment_hash: str) -> str:
    digest = hashlib.sha256(
        f"mathhub-import-v1\0{environment_hash}\0{external_name}".encode()
    ).hexdigest()[:20]
    return f"MathHub.Import.c{digest}"


def _persist_preflight_build(
    proof_id: str,
    audit: dict[str, Any],
    environment: dict[str, Any],
) -> dict[str, Any]:
    """Persist an audit that already ran successfully before Claim creation."""
    proof = core.REGISTRY.get_proof(proof_id)
    build = core.REGISTRY.store_build(proof_id, audit, environment)
    formal_binding = core.FORMAL.record_build(proof, build, audit, environment)
    argument = core.ARGUMENT.record_build(proof, build, audit, environment)
    presentation = core.PRESENTATION_BUILDS.record(proof, build, audit, environment)
    return {
        **build,
        "formal_binding": formal_binding,
        "argument": argument,
        "presentation": presentation,
        "pretty_type": audit.get("pretty_type", ""),
        "formal_fingerprint": audit.get("formal_fingerprint", ""),
    }


def import_existing_declaration(payload: dict[str, Any]) -> dict[str, Any]:
    """Register an existing environment theorem as a normal MathHub Claim.

    The existing declaration is *not* re-declared under its original name. Instead
    MathHub creates a stable local alias theorem proved by `exact <external>`, then
    binds the original Lean name to the same Claim revision in FormalIndex.

    This gives the Claim a real kernel-checked Proof while allowing old and future
    Proof Expr references to the library's original name to resolve to the Claim.
    """
    external_name = str(
        payload.get("declaration_name") or payload.get("lean_name") or payload.get("name") or ""
    ).strip()
    if not external_name or not IMPORTABLE_LEAN_NAME.fullmatch(external_name):
        raise ValueError("declaration_name must be a conservative qualified Lean identifier")

    imports = core.validate_imports(payload.get("imports") or ["Mathlib"])
    environment = core.runtime_status()
    if not environment.get("ready"):
        raise ValueError(str(environment.get("message") or "Lean environment unavailable"))
    environment_hash = str(environment.get("environment_hash") or "")
    if not environment_hash:
        raise ValueError("pinned Lean environment has no environment hash")

    existing = core.FORMAL.claim_for_name(external_name, environment_hash)
    if existing is not None:
        return {
            "already_registered": True,
            "claim_id": existing["claim_id"],
            "claim_version_id": existing["claim_version_id"],
            "external_binding": existing,
        }

    inspected = core.inspect_declaration(
        {"imports": imports, "declaration_name": external_name}
    )
    if not inspected.get("ok"):
        raise ValueError(
            str(inspected.get("error") or inspected.get("engine_error") or "Lean could not inspect declaration")
        )
    pretty_type = str(inspected.get("pretty_type") or "").strip()
    if not pretty_type:
        raise ValueError("Lean inspected the declaration but emitted no printable type")

    alias = _stable_alias(external_name, environment_hash)
    declaration = f"theorem {alias} : {pretty_type}"
    proof_source = f"by\n  exact {external_name}"

    # This is the ontology gate for imported constants. Definitions, constructors,
    # instances, etc. cannot masquerade as Claims merely because they have a name:
    # the generated theorem must compile with the existing declaration as its proof.
    preflight = core.audit(
        {
            "imports": imports,
            "declaration": declaration,
            "proof": proof_source,
            "theorem_name": alias,
            "audit": True,
        }
    )
    if not preflight.get("ok"):
        raise ValueError(
            "the selected Lean declaration cannot establish a theorem Claim: "
            + str(preflight.get("output") or preflight.get("error") or "preflight failed")
        )
    if not preflight.get("audit_ok"):
        raise ValueError("the theorem compiled, but dependency indexing did not complete")
    if preflight.get("contains_sorry"):
        raise ValueError("import bridge unexpectedly depends on sorryAx")

    # Re-check after the expensive preflight so two concurrent import attempts do
    # not normally create duplicate Claims for the same environment declaration.
    existing = core.FORMAL.claim_for_name(external_name, environment_hash)
    if existing is not None:
        return {
            "already_registered": True,
            "claim_id": existing["claim_id"],
            "claim_version_id": existing["claim_version_id"],
            "external_binding": existing,
        }

    canonical = canonical_from_declaration(declaration)
    created = core.register_claim(
        {
            "title": str(payload.get("title") or "").strip() or suggested_title(external_name),
            "statement": str(payload.get("statement") or "").strip() or canonical["text"],
            "latex": str(payload.get("latex") or "").strip() or canonical["latex"],
            "area": str(payload.get("area") or "").strip(),
            "claim_author": str(payload.get("claim_author") or "").strip(),
            "formal_declaration": declaration,
            "lean_name": alias,
            "proof_author": str(payload.get("proof_author") or "").strip(),
            "imports": imports,
            "proof_source": proof_source,
            "build": False,
        }
    )
    proof_id = str(created.get("created_proof_id") or "")
    if not proof_id:
        raise RuntimeError("import bridge created no Proof record")

    persisted_build = _persist_preflight_build(proof_id, preflight, environment)
    proof = core.REGISTRY.get_proof(proof_id)
    claim_id = parse_ref(str(proof["claim_id"]), "CLM")
    version_id = parse_ref(str(proof["claim_version_id"]), "VER")
    build_id = parse_ref(str(persisted_build["build_id"]), "BLD")

    external_binding = core.FORMAL.record_binding(
        claim_id=claim_id,
        claim_version_id=version_id,
        source_build_id=build_id,
        lean_name=external_name,
        environment_hash=environment_hash,
        type_hash=str(inspected.get("type_hash") or preflight.get("type_hash") or ""),
        formal_fingerprint=str(
            inspected.get("formal_fingerprint") or preflight.get("formal_fingerprint") or ""
        ),
        origin="imported-declaration",
    )

    # FormalBinding is a live resolution index over immutable Build observations.
    # Reconcile the environment now so old Proof Builds can gain the new Claim
    # projection without being recompiled or mutating their raw evidence.
    with core.ARGUMENT.connect() as conn:
        core.ARGUMENT._reconcile(conn, environment_hash)

    result = core.REGISTRY.get_claim(str(proof["claim_id"]))
    result["already_registered"] = False
    result["created_proof_id"] = proof_id
    result["created_build"] = persisted_build
    result["external_declaration"] = external_name
    result["external_binding"] = external_binding
    result["import_alias"] = alias
    return result
