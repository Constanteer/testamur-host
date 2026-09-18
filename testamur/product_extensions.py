from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .contracts import ObjectKind


ObjectReader = Callable[[str], Mapping[str, Any] | None]
ImpactReader = Callable[[str], Mapping[str, Any]]
TemporalReader = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]
TemporalEventReader = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]
MonitorTargetProvider = Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class ProductExtensions:
    """Dependency-injection boundary for W2-W5 canonical capabilities.

    W1 owns composition, not the semantic engines.  Extension implementations
    are supplied by canonical ``testamur.*`` modules after worker integration;
    this contract intentionally contains no Witness imports and no fallback that
    guesses semantics when a capability is absent.
    """

    object_readers: Mapping[ObjectKind, ObjectReader]
    impact_reader: ImpactReader | None = None
    temporal_reader: TemporalReader | None = None
    temporal_event_reader: TemporalEventReader | None = None
    monitor_target_providers: Mapping[str, MonitorTargetProvider] = field(default_factory=dict)
    monitor_target_provider_specs: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "ProductExtensions":
        return cls(object_readers={})

    def reader_for(self, kind: ObjectKind) -> ObjectReader | None:
        return self.object_readers.get(kind)

    def monitor_target_provider(self, name: str) -> MonitorTargetProvider | None:
        return self.monitor_target_providers.get(str(name).strip())

    def capabilities(self) -> dict[str, Any]:
        return {
            "schema": "testamur.product.extensions.v1",
            "object_kinds": sorted(kind.value for kind in self.object_readers),
            "impact": self.impact_reader is not None,
            "temporal": self.temporal_reader is not None,
            "temporal_events": self.temporal_event_reader is not None,
            "monitor_target_providers": sorted(self.monitor_target_providers),
            "monitor_target_provider_specs": {
                name: dict(self.monitor_target_provider_specs.get(name, {}))
                for name in sorted(self.monitor_target_providers)
            },
            "semantics": {
                "missing_capability_is_explicit": True,
                "extension_does_not_promote_truth": True,
                "extension_does_not_change_object_identity": True,
            },
        }
