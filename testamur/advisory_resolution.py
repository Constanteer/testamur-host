from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from .component_identity import canonical_json


class AdvisoryIdentityResolver(Protocol):
    """W1 integration boundary for resolving advisory identities to exact revisions.

    Implementations may consult package indexes, SBOMs, manifests, source history,
    or provider-specific metadata. They must return evidence-backed exact revision
    references; fuzzy name/version matching is not a sufficient resolution.
    """

    def resolve_advisory_identity(
        self, upstream_identity: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...


def _required_ref(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} entries must be strings")
    text = value.strip()
    if not text:
        raise ValueError(f"{field} entries must not be empty")
    return text


def _refs(value: Any, *, field: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be a sequence of exact revision refs")
    return sorted({_required_ref(item, field=field) for item in value})


def _resolution_evidence(value: Any) -> list[dict[str, Any]]:
    if (
        isinstance(value, (str, bytes, Mapping))
        or not isinstance(value, Sequence)
        or not value
    ):
        raise ValueError("advisory identity resolution requires explicit evidence")

    by_ref: dict[str, tuple[str, dict[str, Any]]] = {}
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError(
                "advisory identity resolution evidence entries must be mappings"
            )
        entry = dict(item)
        ref = _required_ref(entry.get("ref"), field="resolution.evidence.ref")
        entry["ref"] = ref
        encoded = canonical_json(entry)
        previous = by_ref.get(ref)
        if previous is not None:
            if previous[0] != encoded:
                raise ValueError(
                    "advisory identity resolution evidence ref cannot identify conflicting payloads"
                )
            continue
        by_ref[ref] = (encoded, entry)
    return [by_ref[ref][1] for ref in sorted(by_ref)]


def resolve_advisory_upstream_refs(
    event_revision: Mapping[str, Any],
    *,
    resolver: AdvisoryIdentityResolver | None = None,
) -> dict[str, Any]:
    """Return an event projection with exact upstream refs when evidence permits.

    Existing exact ``upstream_refs`` are authoritative input and need no resolver.
    An unresolved ``upstream_identity`` remains unresolved unless a W1/provider
    resolver supplies exact revision refs *and* explicit resolution evidence.
    The helper never interprets package names or versions as affectedness facts.
    Malformed explicit identity/ref fields fail closed instead of being treated as
    if the provider omitted them.
    """

    if not isinstance(event_revision, Mapping):
        raise ValueError("event_revision must be a mapping")
    projected = dict(event_revision)

    raw_identity = projected.get("upstream_identity")
    if raw_identity is not None and not isinstance(raw_identity, Mapping):
        raise ValueError("upstream_identity must be a mapping when supplied")

    raw_existing = projected.get("upstream_refs")
    existing = (
        []
        if raw_existing is None
        else _refs(raw_existing, field="upstream_refs")
    )
    if existing:
        projected["upstream_refs"] = existing
        projected["identity_resolution"] = {
            "status": "already_exact",
            "name_or_version_match_implies_affectedness": False,
        }
        return projected

    identity = raw_identity
    if identity is None or not identity:
        projected["upstream_refs"] = []
        projected["identity_resolution"] = {
            "status": "unresolved",
            "reason": "no_upstream_identity",
            "name_or_version_match_implies_affectedness": False,
        }
        return projected
    if resolver is None:
        projected["upstream_refs"] = []
        projected["identity_resolution"] = {
            "status": "unresolved",
            "reason": "resolver_required",
            "name_or_version_match_implies_affectedness": False,
        }
        return projected

    raw = resolver.resolve_advisory_identity(dict(identity))
    if not isinstance(raw, Mapping):
        raise ValueError("advisory identity resolver must return a mapping")
    if raw.get("exact_revision") is not True:
        raise ValueError("advisory identity resolution must assert exact_revision=true")
    refs = _refs(raw.get("upstream_refs"), field="resolution.upstream_refs")
    if not refs:
        raise ValueError("advisory identity resolution requires exact upstream refs")
    normalized_evidence = _resolution_evidence(raw.get("evidence"))

    projected["upstream_refs"] = refs
    projected["identity_resolution"] = {
        "status": "resolved_exact",
        "upstream_identity": dict(identity),
        "evidence": normalized_evidence,
        "name_or_version_match_implies_affectedness": False,
        "resolution_implies_affectedness_verdict": False,
    }
    return projected


def advisory_identity_resolution_basis(
    event_revision: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return immutable assessment-basis entries for exact identity resolution.

    A resolver-backed advisory projection is only explainable if the evidence that
    mapped provider/package identity to exact revision refs survives into the
    affectedness assessment. This helper validates and normalizes that basis.
    It returns no entries for advisories that already carried exact upstream refs
    or for unresolved identities, and never treats resolution as an applicability
    or vulnerability verdict.
    """

    if not isinstance(event_revision, Mapping):
        raise ValueError("event_revision must be a mapping")
    raw_resolution = event_revision.get("identity_resolution")
    if raw_resolution is None:
        return []
    if not isinstance(raw_resolution, Mapping):
        raise ValueError("identity_resolution must be a mapping")

    status = str(raw_resolution.get("status") or "").strip()
    if status in {"", "already_exact", "unresolved"}:
        return []
    if status != "resolved_exact":
        raise ValueError(f"unsupported identity_resolution status: {status}")

    identity = raw_resolution.get("upstream_identity")
    if not isinstance(identity, Mapping) or not identity:
        raise ValueError("resolved identity projection requires upstream_identity")
    refs = _refs(event_revision.get("upstream_refs"), field="upstream_refs")
    if not refs:
        raise ValueError("resolved identity projection requires exact upstream refs")
    evidence = _resolution_evidence(raw_resolution.get("evidence"))

    return [
        {
            "kind": "advisory_identity_resolution",
            "ref": item["ref"],
            "evidence": item,
            "upstream_identity": dict(identity),
            "resolved_upstream_refs": refs,
            "semantics": {
                "identity_resolution_is_affectedness_verdict": False,
                "name_or_version_match_alone_is_resolution": False,
            },
        }
        for item in evidence
    ]
