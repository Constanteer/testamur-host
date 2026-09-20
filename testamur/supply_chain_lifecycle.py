from __future__ import annotations

from typing import Any


def install_immutable_scan_observations() -> None:
    """Make scan invocations append-only while leaving state records deduplicated.

    Manifest and dependency records describe observed state and remain content-
    deduplicated. A supply-chain-scan revision describes an observation event, so
    every successful invocation must remain in immutable project history even
    when the observed state is byte-for-byte identical to the previous scan.
    """

    from . import supply_chain

    current = supply_chain._ensure_record_revision
    if getattr(current, "_testamur_immutable_scan_observations", False):
        return

    def ensure_record_revision(
        service: Any,
        *,
        record_id: str,
        record_kind: str,
        statement: str,
        basis: list[dict[str, Any]],
        title: str,
    ) -> tuple[dict[str, Any], bool]:
        if record_kind != "supply-chain-scan":
            return current(
                service,
                record_id=record_id,
                record_kind=record_kind,
                statement=statement,
                basis=basis,
                title=title,
            )

        existing = service.records.get_record(record_id)
        if existing is None:
            created = service.records.create_record(
                record_kind=record_kind,
                statement=statement,
                basis=basis,
                title=title,
                created_by="testamur:supply-chain-import",
                record_id=record_id,
            )
            return created["revision"], True

        latest = service.records.latest_revision(record_id)
        if latest is None:
            raise RuntimeError(f"record has no revision: {record_id}")
        revision = service.records.append_revision(
            record_id,
            expected_parent_revision_id=latest["revision_id"],
            statement=statement,
            basis=basis,
            title=title,
            created_by="testamur:supply-chain-import",
        )
        return revision, True

    ensure_record_revision._testamur_immutable_scan_observations = True  # type: ignore[attr-defined]
    supply_chain._ensure_record_revision = ensure_record_revision
