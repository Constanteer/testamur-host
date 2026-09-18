from __future__ import annotations

from typing import Any, Mapping


def _required_ref(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    return text


def _optional_ref(value: Any, *, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string when supplied")
    return value.strip()


def validate_affectedness_supersession(
    previous: Mapping[str, Any],
    *,
    event_revision_id: str,
    subject_revision: str,
    event_id: str | None = None,
) -> None:
    """Fail closed unless an assessment stays in one subject+event history.

    The exact same adverse-event revision is sufficient to establish continuity.
    When a newer advisory revision supersedes an older one, both assessments must
    additionally carry the same stable ``event_id``.  Sharing only a subject, or
    merely having two advisory-looking revision references, is insufficient.

    Supersession identity comparisons never coerce arbitrary values to strings.
    Historical event/subject references must already be canonical string refs.
    """

    if not isinstance(previous, Mapping):
        raise ValueError("previous assessment must be a mapping")
    previous_event_revision = _required_ref(
        previous.get("event_revision_id"), field="previous.event_revision_id"
    )
    previous_event_id = _optional_ref(
        previous.get("event_id"), field="previous.event_id"
    )
    previous_subject = _required_ref(
        previous.get("subject_revision"), field="previous.subject_revision"
    )
    event_revision_ref = _required_ref(
        event_revision_id, field="event_revision_id"
    )
    stable_event_ref = _optional_ref(event_id, field="event_id")
    subject_ref = _required_ref(subject_revision, field="subject_revision")

    if previous_subject != subject_ref:
        raise ValueError(
            "superseded assessment must concern the same subject revision"
        )

    if previous_event_revision == event_revision_ref:
        if previous_event_id and stable_event_ref and previous_event_id != stable_event_ref:
            raise ValueError(
                "same event revision cannot carry conflicting stable adverse-event identities"
            )
        return

    if not stable_event_ref or not previous_event_id:
        raise ValueError(
            "cross-revision supersession requires the same stable adverse-event identity"
        )
    if previous_event_id != stable_event_ref:
        raise ValueError(
            "superseded assessment must concern the same stable adverse-event identity"
        )
