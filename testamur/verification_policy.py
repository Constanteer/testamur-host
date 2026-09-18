from __future__ import annotations

"""Typed verifier authorization retained from the historical graph kernel.

This is deliberately separate from :mod:`testamur.policy`, whose PolicyStore
represents purpose/scope admission policy.  A verifier class constrains which
verification status a particular verifier may issue; it is not a universal truth
judgment and it is not a reliance/admission decision.
"""

from typing import Any

from .model import (
    ObjectKind,
    VerificationStatus,
    VerifierClass,
    normalize_verification_status,
    normalize_verifier_class,
)


COMMON = {
    VerificationStatus.UNVERIFIED.value,
    VerificationStatus.CONTRADICTED.value,
}

CLASS_ALLOWED = {
    VerifierClass.FORMAL_KERNEL.value: {
        VerificationStatus.FORMALLY_PROVEN.value,
        VerificationStatus.MECHANICALLY_CHECKED.value,
    },
    VerifierClass.MECHANICAL.value: {
        VerificationStatus.MECHANICALLY_CHECKED.value,
        VerificationStatus.TESTED.value,
        VerificationStatus.REPRODUCED.value,
    },
    VerifierClass.TEST_SUITE.value: {VerificationStatus.TESTED.value},
    VerifierClass.EMPIRICAL.value: {
        VerificationStatus.EMPIRICALLY_SUPPORTED.value,
        VerificationStatus.REPRODUCED.value,
    },
    VerifierClass.STATISTICAL.value: {
        VerificationStatus.STATISTICALLY_SUPPORTED.value,
        VerificationStatus.REPRODUCED.value,
    },
    VerifierClass.SIMULATION.value: {
        VerificationStatus.SIMULATION_SUPPORTED.value,
        VerificationStatus.REPRODUCED.value,
    },
    VerifierClass.HUMAN_REVIEW.value: {VerificationStatus.REVIEWED.value},
}


def verifier_contract(verifier: dict[str, Any]) -> dict[str, Any]:
    if verifier.get("kind") != ObjectKind.VERIFIER.value:
        raise ValueError("verification requires a Verifier object")
    payload = verifier.get("payload") if isinstance(verifier.get("payload"), dict) else {}
    raw_class = payload.get("verifier_class")
    if not raw_class:
        raise ValueError("Verifier payload must declare verifier_class")
    verifier_class = normalize_verifier_class(raw_class)
    base = set(CLASS_ALLOWED[verifier_class]) | COMMON
    declared = payload.get("allowed_statuses")
    if declared is None:
        allowed = base
    else:
        if not isinstance(declared, list) or not declared:
            raise ValueError("Verifier allowed_statuses must be a non-empty list")
        allowed = {normalize_verification_status(item) for item in declared}
        illegal = allowed - base
        if illegal:
            raise ValueError(
                f"Verifier allowed_statuses cannot elevate {verifier_class}: "
                + ", ".join(sorted(illegal))
            )
    return {
        # Historical token participates in compatibility semantics; namespace
        # migration alone must not rewrite it.
        "contract_version": "witness-verifier-contract-v0.1",
        "verifier_class": verifier_class,
        "allowed_statuses": sorted(allowed),
    }


def authorize_verification(verifier: dict[str, Any], status: str) -> dict[str, Any]:
    normalized = normalize_verification_status(status)
    contract = verifier_contract(verifier)
    if normalized not in contract["allowed_statuses"]:
        raise ValueError(
            f"Verifier class {contract['verifier_class']} cannot issue status {normalized}"
        )
    return {**contract, "status": normalized}


__all__ = ["COMMON", "CLASS_ALLOWED", "verifier_contract", "authorize_verification"]
