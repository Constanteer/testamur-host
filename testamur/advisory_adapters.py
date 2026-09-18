from __future__ import annotations

from typing import Any, Mapping, Protocol

from .advisory import AdverseEventClass


class AdvisoryAdapter(Protocol):
    """Normalize one provider payload without making an affectedness verdict."""

    provider: str

    def normalize(
        self, payload: Mapping[str, Any], *, source_ref: str
    ) -> dict[str, Any]: ...


def _required(value: Any, *, field: str, provider: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{provider} payload field {field} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{provider} payload requires {field}")
    return text


def _optional_string(value: Any, *, field: str, provider: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{provider} payload field {field} must be a string")
    return value.strip()


def _optional_list(
    payload: Mapping[str, Any],
    field: str,
    *,
    provider: str,
) -> list[Any]:
    """Return a provider list without silently discarding malformed source facts."""

    raw = payload.get(field)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{provider} payload field {field} must be a list")
    return list(raw)


def _optional_string_list(
    payload: Mapping[str, Any],
    field: str,
    *,
    provider: str,
) -> list[str]:
    values = _optional_list(payload, field, provider=provider)
    result: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, str):
            raise ValueError(
                f"{provider} payload field {field}[{index}] must be a string"
            )
        if value:
            result.append(value)
    return result


class OSVAdvisoryAdapter:
    """Normalize an OSV vulnerability document into Testamur adverse-event input.

    OSV package/range data remains provider evidence. The adapter intentionally
    does not decide whether a fork, vendored copy, distro backport, or custom build
    is affected; lineage and applicability evidence handle that separately.
    """

    provider = "osv"

    def normalize(
        self, payload: Mapping[str, Any], *, source_ref: str
    ) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValueError("OSV payload must be a mapping")
        external_id = _required(payload.get("id"), field="id", provider="OSV")
        source = _required(source_ref, field="source_ref", provider="OSV")

        affected_packages: list[dict[str, Any]] = []
        known_versions: list[dict[str, Any]] = []
        affected_entries = _optional_list(payload, "affected", provider="OSV")
        for index, affected in enumerate(affected_entries):
            if not isinstance(affected, Mapping):
                raise ValueError(f"OSV affected[{index}] must be a mapping")
            package = affected.get("package")
            if package is None:
                package = {}
            elif not isinstance(package, Mapping):
                raise ValueError(f"OSV affected[{index}].package must be a mapping")
            ecosystem = _optional_string(
                package.get("ecosystem"),
                field=f"affected[{index}].package.ecosystem",
                provider="OSV",
            )
            name = _optional_string(
                package.get("name"),
                field=f"affected[{index}].package.name",
                provider="OSV",
            )
            purl = _optional_string(
                package.get("purl"),
                field=f"affected[{index}].package.purl",
                provider="OSV",
            )
            identity = {
                "ecosystem": ecosystem,
                "name": name,
                "purl": purl,
            }

            ranges_raw = affected.get("ranges")
            if ranges_raw is None:
                ranges: list[Any] = []
            elif not isinstance(ranges_raw, list):
                raise ValueError(f"OSV affected[{index}].ranges must be a list")
            else:
                ranges = list(ranges_raw)
                for range_index, range_entry in enumerate(ranges):
                    if not isinstance(range_entry, Mapping):
                        raise ValueError(
                            f"OSV affected[{index}].ranges[{range_index}] must be a mapping"
                        )

            versions_raw = affected.get("versions")
            if versions_raw is None:
                versions: list[Any] = []
            elif not isinstance(versions_raw, list):
                raise ValueError(f"OSV affected[{index}].versions must be a list")
            else:
                versions = list(versions_raw)
                for version_index, version in enumerate(versions):
                    if not isinstance(version, str):
                        raise ValueError(
                            f"OSV affected[{index}].versions[{version_index}] must be a string"
                        )

            database_specific = affected.get("database_specific")
            if database_specific is None:
                database_specific = {}
            elif not isinstance(database_specific, Mapping):
                raise ValueError(
                    f"OSV affected[{index}].database_specific must be a mapping"
                )
            ecosystem_specific = affected.get("ecosystem_specific")
            if ecosystem_specific is None:
                ecosystem_specific = {}
            elif not isinstance(ecosystem_specific, Mapping):
                raise ValueError(
                    f"OSV affected[{index}].ecosystem_specific must be a mapping"
                )

            affected_packages.append(
                {
                    "package": identity,
                    "ranges": ranges,
                    "versions": versions,
                    "database_specific": dict(database_specific),
                    "ecosystem_specific": dict(ecosystem_specific),
                }
            )
            for version in versions:
                known_versions.append(
                    {
                        "ecosystem": ecosystem,
                        "name": name,
                        "purl": purl,
                        "version": version,
                    }
                )

        severity_values = _optional_list(payload, "severity", provider="OSV")
        for severity_index, severity in enumerate(severity_values):
            if not isinstance(severity, Mapping):
                raise ValueError(
                    f"OSV severity[{severity_index}] must be a mapping"
                )
        aliases = sorted(_optional_string_list(payload, "aliases", provider="OSV"))
        related = sorted(_optional_string_list(payload, "related", provider="OSV"))
        return {
            "provider": self.provider,
            "external_id": external_id,
            "event_class": AdverseEventClass.VULNERABILITY_ADVISORY.value,
            "upstream_identity": {
                "provider_model": "osv",
                "affected_packages": affected_packages,
            },
            "known_affected": {
                "provider_declared_versions": known_versions,
                "provider_affected_entries": affected_packages,
            },
            "source_refs": [source],
            "issued_at": payload.get("published") or None,
            "severity": {
                "provider_values": severity_values,
            },
            "metadata": {
                "aliases": aliases,
                "related": related,
                "modified": payload.get("modified"),
                "withdrawn": payload.get("withdrawn"),
                "summary": payload.get("summary"),
                "details": payload.get("details"),
                "schema_version": payload.get("schema_version"),
                "semantics": {
                    "provider_version_match_is_local_affectedness_verdict": False,
                    "fork_or_vendor_applicability_requires_lineage_analysis": True,
                    "malformed_provider_collections_are_not_silently_dropped": True,
                    "provider_identity_fields_are_not_string_coerced": True,
                },
            },
        }


class CISAKEVAdvisoryAdapter:
    """Normalize one CISA Known Exploited Vulnerabilities catalog entry.

    Inclusion in KEV is recorded as an upstream adverse-event fact. Vendor/product
    names and the CVE identifier are useful identity evidence, but they are not a
    Testamur verdict about a fork, vendored copy, patched backport, or transformed
    derivative. Exact derivative applicability still flows through lineage plus
    explicit applicability evidence.
    """

    provider = "cisa-kev"

    def normalize(
        self, payload: Mapping[str, Any], *, source_ref: str
    ) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValueError("CISA KEV payload must be a mapping")
        cve_raw = payload.get("cveID")
        if cve_raw is None:
            cve_raw = payload.get("cve_id")
        cve_id = _required(cve_raw, field="cveID", provider="CISA KEV")
        source = _required(source_ref, field="source_ref", provider="CISA KEV")

        vendor_raw = payload.get("vendorProject")
        if vendor_raw is None:
            vendor_raw = payload.get("vendor_project")
        vendor = _optional_string(
            vendor_raw, field="vendorProject", provider="CISA KEV"
        )
        product = _optional_string(
            payload.get("product"), field="product", provider="CISA KEV"
        )
        date_added = payload.get("dateAdded") or payload.get("date_added") or None
        cwes_raw = payload.get("cwes")
        if cwes_raw is None:
            cwes: list[str] = []
        elif not isinstance(cwes_raw, list):
            raise ValueError("CISA KEV payload field cwes must be a list")
        else:
            cwes = []
            for index, item in enumerate(cwes_raw):
                if not isinstance(item, str):
                    raise ValueError(
                        f"CISA KEV payload field cwes[{index}] must be a string"
                    )
                if item:
                    cwes.append(item)
            cwes.sort()

        return {
            "provider": self.provider,
            "external_id": cve_id,
            "event_class": AdverseEventClass.KNOWN_EXPLOITED_VULNERABILITY.value,
            "upstream_identity": {
                "provider_model": "cisa-kev",
                "cve_id": cve_id,
                "vendor_project": vendor,
                "product": product,
            },
            "known_affected": {
                "provider_listing": "known-exploited-vulnerabilities-catalog",
                "cve_id": cve_id,
                "vendor_project": vendor,
                "product": product,
            },
            "condition": {
                "provider_statement": "catalog inclusion records known exploitation; derivative applicability unresolved"
            },
            "source_refs": [source],
            "issued_at": date_added,
            "metadata": {
                "vulnerability_name": payload.get("vulnerabilityName")
                or payload.get("vulnerability_name"),
                "short_description": payload.get("shortDescription")
                or payload.get("short_description"),
                "required_action": payload.get("requiredAction")
                or payload.get("required_action"),
                "due_date": payload.get("dueDate") or payload.get("due_date"),
                "known_ransomware_campaign_use": payload.get(
                    "knownRansomwareCampaignUse"
                )
                or payload.get("known_ransomware_campaign_use"),
                "notes": payload.get("notes"),
                "cwes": cwes,
                "semantics": {
                    "kev_inclusion_is_upstream_adverse_event_fact": True,
                    "vendor_product_match_is_derivative_affectedness_verdict": False,
                    "fork_or_vendor_applicability_requires_lineage_analysis": True,
                    "provider_identity_fields_are_not_string_coerced": True,
                },
            },
        }
