from __future__ import annotations

from typing import Any, Iterable, Mapping

from .component_identity import ComponentRevisionIdentity


def _revision_ref(value: Mapping[str, Any]) -> tuple[str, bool]:
    """Validate a component-revision identity and return its canonical ref/exactness."""

    revision = ComponentRevisionIdentity.from_dict(value)
    return revision.revision_id, revision.is_exact_revision


def project_advisory_candidates(
    event_revisions: Iterable[Mapping[str, Any]],
    component_revisions: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Project exact advisory/component identity overlap into review candidates.

    This is deliberately a discovery projection, not affectedness inference. Only
    exact component revisions (digest/revision pinned) can overlap an advisory's
    exact ``upstream_refs``. Declared ecosystem versions remain useful supply-chain
    observations but are not silently promoted to exact identity or applicability.
    """

    exact_components: dict[str, dict[str, Any]] = {}
    for raw in component_revisions:
        if not isinstance(raw, Mapping):
            raise ValueError("component_revisions entries must be mappings")
        item = dict(raw)
        ref, exact = _revision_ref(item)
        if exact:
            exact_components[ref] = item

    candidates: list[dict[str, Any]] = []
    for raw in event_revisions:
        if not isinstance(raw, Mapping):
            raise ValueError("event_revisions entries must be mappings")
        event = dict(raw)
        event_revision_id = str(event.get("event_revision_id") or "").strip()
        if not event_revision_id:
            raise ValueError("event_revision.event_revision_id must not be empty")

        raw_refs = event.get("upstream_refs")
        if raw_refs is None:
            refs: list[str] = []
        elif isinstance(raw_refs, (str, bytes)):
            raise ValueError("event_revision.upstream_refs must be a sequence")
        else:
            refs = sorted({str(ref).strip() for ref in raw_refs if str(ref).strip()})

        matching = sorted(set(refs).intersection(exact_components))
        if not matching:
            continue
        candidates.append({
            "schema": "testamur.product.advisory-candidate.v1",
            "event_id": event.get("event_id"),
            "event_revision_id": event_revision_id,
            "provider": event.get("provider"),
            "external_id": event.get("external_id"),
            "status": "exact_identity_overlap",
            "matching_component_revision_ids": matching,
            "semantics": {
                "identity_overlap_is_affectedness_verdict": False,
                "candidate_is_verification": False,
                "candidate_establishes_reliance": False,
                "changed_implies_invalid": False,
                "stale_implies_false": False,
                "generic_trust_score_used": False,
            },
        })

    candidates.sort(key=lambda item: (
        str(item.get("provider") or ""),
        str(item.get("external_id") or ""),
        str(item["event_revision_id"]),
    ))
    return candidates
