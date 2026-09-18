from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping


COMPONENT_PREFIX = "tst:component:"
COMPONENT_REVISION_PREFIX = "tst:component-revision:"

_EXTENSION_PREFIXES = {
    "lineage": "tst:lineage:",
    "affectedness": "tst:affectedness:",
    "advisory": "tst:advisory:",
}


def canonical_json(value: Any) -> str:
    """Serialize a JSON-compatible value deterministically without Witness imports."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonical_extension_id(kind: str, identity: Mapping[str, Any]) -> str:
    """Mirror W1's canonical extension-object ID algorithm without branch coupling.

    W1 owns the shared ``ObjectKind``/projection implementation. W5 cannot import
    files that are not yet on main, but its durable IDs must nevertheless converge
    byte-for-byte. This helper intentionally implements only W5-owned extension
    kinds and uses the same ``{kind, identity}`` hash envelope as W1.
    """

    resolved = str(kind).strip()
    prefix = _EXTENSION_PREFIXES.get(resolved)
    if prefix is None:
        raise ValueError(f"unsupported W5 extension kind: {kind!r}")
    if not isinstance(identity, Mapping) or not identity:
        raise ValueError("extension identity must be a non-empty mapping")
    normalized = json.loads(canonical_json(dict(identity)))
    return prefix + canonical_hash({"kind": resolved, "identity": normalized})


def _required(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    return text


def _optional_string(value: Any, *, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string when supplied")
    return value.strip()


def _normalized_pairs(
    value: Mapping[str, Any] | None, *, field: str
) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    pairs: list[tuple[str, str]] = []
    for raw_key, raw_value in value.items():
        key = _required(raw_key, field=f"{field}.key")
        item = _required(raw_value, field=f"{field}[{key}]")
        pairs.append((key, item))
    return tuple(sorted(pairs))


def _optional_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    """Read an optional identity mapping without silently discarding malformed input."""

    raw = value.get(key)
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError(f"{key} must be a mapping")
    return raw


@dataclass(frozen=True, slots=True)
class ComponentIdentity:
    """Stable identity for one logical component, independent of a mutable revision.

    Equality is intentionally strict. Similar names, package aliases, or matching
    versions do not establish component equivalence. Callers that know an external
    ecosystem identifier should preserve it in ``external_ids`` or ``qualifiers``.
    Identity fields are type-stable strings; arbitrary values are never coerced into
    durable identity because that can collapse distinct provider data onto one ID.
    """

    kind: str
    name: str
    namespace: str = ""
    qualifiers: tuple[tuple[str, str], ...] = ()
    external_ids: tuple[tuple[str, str], ...] = ()

    @classmethod
    def create(
        cls,
        *,
        kind: str,
        name: str,
        namespace: str | None = None,
        qualifiers: Mapping[str, Any] | None = None,
        external_ids: Mapping[str, Any] | None = None,
    ) -> "ComponentIdentity":
        return cls(
            kind=_required(kind, field="kind"),
            name=_required(name, field="name"),
            namespace=_optional_string(namespace, field="namespace"),
            qualifiers=_normalized_pairs(qualifiers, field="qualifiers"),
            external_ids=_normalized_pairs(external_ids, field="external_ids"),
        )

    @property
    def component_id(self) -> str:
        return COMPONENT_PREFIX + canonical_hash(self.identity_payload())

    def identity_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "namespace": self.namespace,
            "name": self.name,
            "qualifiers": dict(self.qualifiers),
            "external_ids": dict(self.external_ids),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            **self.identity_payload(),
            "semantics": {
                "stable_component_identity": True,
                "identity_fields_are_type_stable_strings": True,
                "name_or_version_match_implies_equivalence": False,
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ComponentIdentity":
        if not isinstance(value, Mapping):
            raise ValueError("component identity must be a mapping")
        result = cls.create(
            kind=value.get("kind"),
            name=value.get("name"),
            namespace=value.get("namespace"),
            qualifiers=_optional_mapping(value, "qualifiers"),
            external_ids=_optional_mapping(value, "external_ids"),
        )
        supplied = value.get("component_id")
        if supplied is not None:
            supplied_ref = _required(supplied, field="component_id")
            if supplied_ref != result.component_id:
                raise ValueError("component_id does not match canonical component identity")
        return result


@dataclass(frozen=True, slots=True)
class ComponentRevisionIdentity:
    """Revision-pinned identity for a component/artifact instance.

    At least one revision discriminator is required. A digest is strongest because
    it is content-addressed; ecosystem versions and mutable locators remain useful
    provenance but are never upgraded to byte/semantic equivalence.
    """

    component: ComponentIdentity
    revision: str = ""
    version: str = ""
    digest: str = ""
    locator: str = ""

    @classmethod
    def create(
        cls,
        *,
        component: ComponentIdentity,
        revision: str | None = None,
        version: str | None = None,
        digest: str | None = None,
        locator: str | None = None,
    ) -> "ComponentRevisionIdentity":
        if not isinstance(component, ComponentIdentity):
            raise ValueError("component must be ComponentIdentity")
        values = {
            "revision": _optional_string(revision, field="revision"),
            "version": _optional_string(version, field="version"),
            "digest": _optional_string(digest, field="digest"),
            "locator": _optional_string(locator, field="locator"),
        }
        if not any(values.values()):
            raise ValueError(
                "component revision requires revision, version, digest, or locator"
            )
        return cls(component=component, **values)

    @property
    def revision_id(self) -> str:
        return COMPONENT_REVISION_PREFIX + canonical_hash(self.identity_payload())

    @property
    def identity_strength(self) -> str:
        if self.digest:
            return "content-addressed"
        if self.revision:
            return "revision-pinned"
        if self.version:
            return "declared-version"
        return "locator-only"

    @property
    def is_exact_revision(self) -> bool:
        """Whether this identity pins immutable material rather than a mutable label.

        Ecosystem versions and locators remain useful discovery/provenance keys but
        are deliberately not treated as exact revision evidence. W1/provider
        resolvers can use this predicate before admitting an identity into lineage
        or advisory propagation.
        """

        return bool(self.digest or self.revision)

    def identity_payload(self) -> dict[str, Any]:
        return {
            "component_id": self.component.component_id,
            "revision": self.revision,
            "version": self.version,
            "digest": self.digest,
            "locator": self.locator,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "component_revision_id": self.revision_id,
            "component": self.component.as_dict(),
            **self.identity_payload(),
            "identity_strength": self.identity_strength,
            "is_exact_revision": self.is_exact_revision,
            "semantics": {
                "revision_pinned_when_revision_or_digest_present": self.is_exact_revision,
                "identity_fields_are_type_stable_strings": True,
                "version_match_implies_content_match": False,
                "locator_match_implies_revision_match": False,
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ComponentRevisionIdentity":
        if not isinstance(value, Mapping):
            raise ValueError("component revision identity must be a mapping")
        component_value = value.get("component")
        if not isinstance(component_value, Mapping):
            raise ValueError("component revision identity requires component mapping")
        result = cls.create(
            component=ComponentIdentity.from_dict(component_value),
            revision=value.get("revision"),
            version=value.get("version"),
            digest=value.get("digest"),
            locator=value.get("locator"),
        )
        supplied = value.get("component_revision_id")
        if supplied is not None:
            supplied_ref = _required(supplied, field="component_revision_id")
            if supplied_ref != result.revision_id:
                raise ValueError(
                    "component_revision_id does not match canonical revision identity"
                )
        return result


def same_component(left: ComponentIdentity, right: ComponentIdentity) -> bool:
    """Return strict stable-identity equality; never fuzzy name/version matching."""

    return left.component_id == right.component_id
