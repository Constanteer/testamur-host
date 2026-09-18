from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Protocol, Sequence

from .advisory_resolution import advisory_identity_resolution_basis
from .affectedness_conflicts import conflicting_terminal_signals
from .affectedness_scope import applicability_scope_conflict
from .affectedness_supersession import validate_affectedness_supersession
from .component_identity import canonical_extension_id, canonical_json
from .lineage import LineageEvidenceClass, TestamurLineageStore


class AffectednessState(StrEnum):
    POTENTIALLY_AFFECTED = "POTENTIALLY_AFFECTED"
    CONFIRMED_AFFECTED = "CONFIRMED_AFFECTED"
    MITIGATED = "MITIGATED"
    DISPROVEN = "DISPROVEN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class ApplicabilitySignal(StrEnum):
    MATERIAL_PRESENT = "MATERIAL_PRESENT"
    MATERIAL_ABSENT = "MATERIAL_ABSENT"
    MITIGATION_ESTABLISHED = "MITIGATION_ESTABLISHED"
    MITIGATION_DECLARED = "MITIGATION_DECLARED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    INCONCLUSIVE = "INCONCLUSIVE"
    SCANNER_NO_MATCH = "SCANNER_NO_MATCH"


FINAL_RESOLUTION_STATES = frozenset(
    {
        AffectednessState.CONFIRMED_AFFECTED.value,
        AffectednessState.MITIGATED.value,
        AffectednessState.DISPROVEN.value,
        AffectednessState.NOT_APPLICABLE.value,
    }
)

ACTIONABLE_STATES = frozenset(
    {
        AffectednessState.POTENTIALLY_AFFECTED.value,
        AffectednessState.CONFIRMED_AFFECTED.value,
        AffectednessState.UNKNOWN.value,
    }
)


class RelianceImpactResolver(Protocol):
    """W3 integration boundary; W5 does not depend on a concrete reliance engine."""

    def blast_radius_for_refs(
        self, refs: Sequence[str], *, policy_ref: str | None = None
    ) -> Any: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _required(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    return text


def _normalize_state(value: str | AffectednessState) -> str:
    try:
        return AffectednessState(str(value)).value
    except ValueError as exc:
        allowed = ", ".join(item.value for item in AffectednessState)
        raise ValueError(
            f"unsupported affectedness state; expected one of: {allowed}"
        ) from exc


def _normalize_mapping(
    value: Mapping[str, Any] | None, *, field: str
) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    result = dict(value)
    canonical_json(result)
    return result


def _normalize_basis(
    values: Iterable[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in values or ():
        if not isinstance(raw, Mapping):
            raise ValueError("basis entries must be mappings")
        item = dict(raw)
        item["kind"] = _required(item.get("kind"), field="basis.kind")
        item["ref"] = _required(item.get("ref"), field="basis.ref")
        canonical_json(item)
        result.append(item)
    if not result:
        raise ValueError("affectedness assessments require explicit basis")
    return result


def _closed_assessment_basis(
    values: Iterable[Mapping[str, Any]] | None,
    *,
    event_revision_id: str,
    lineage_path_edge_ids: Iterable[str] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Close an engine-produced assessment over its immutable advisory/path basis.

    Applicability callers may add analyzer/evidence basis entries, but they must not
    accidentally drop the advisory revision or lineage edges that justified the
    assessed subject. Existing richer entries for the same ``(kind, ref)`` are
    preserved. Conflicting duplicate identities fail closed rather than allowing an
    immutable ref to identify two different payloads.
    """

    event_ref = _required(event_revision_id, field="event_revision_id")
    path_ids = sorted(
        {
            _required(item, field="lineage_path_edge_ids")
            for item in lineage_path_edge_ids or ()
        }
    )
    normalized = _normalize_basis(values)
    by_identity: dict[tuple[str, str], str] = {}
    closed: list[dict[str, Any]] = []
    for item in normalized:
        key = (str(item["kind"]), str(item["ref"]))
        encoded = canonical_json(item)
        previous = by_identity.get(key)
        if previous is not None:
            if previous != encoded:
                raise ValueError(
                    "affectedness basis contains conflicting payloads for "
                    f"{key[0]}:{key[1]}"
                )
            continue
        by_identity[key] = encoded
        closed.append(item)

    required = [
        {"kind": "advisory_revision", "ref": event_ref},
        *(
            {"kind": "lineage_edge", "ref": edge_id}
            for edge_id in path_ids
        ),
    ]
    for item in required:
        key = (item["kind"], item["ref"])
        if key in by_identity:
            continue
        by_identity[key] = canonical_json(item)
        closed.append(item)
    return closed, path_ids


def _normalize_applicability_evidence(
    values: Iterable[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in values or ():
        if not isinstance(raw, Mapping):
            raise ValueError("applicability evidence entries must be mappings")
        item = dict(raw)
        item["ref"] = _required(item.get("ref"), field="evidence.ref")
        try:
            item["signal"] = ApplicabilitySignal(str(item.get("signal"))).value
        except ValueError as exc:
            raise ValueError(
                "evidence.signal is not a supported applicability signal"
            ) from exc
        try:
            item["evidence_class"] = LineageEvidenceClass(
                str(item.get("evidence_class"))
            ).value
        except ValueError as exc:
            raise ValueError(
                "evidence.evidence_class must be OBSERVED, DERIVED, or DECLARED"
            ) from exc
        if item["evidence_class"] == LineageEvidenceClass.DERIVED.value:
            item["analyzer"] = _required(
                item.get("analyzer"), field="evidence.analyzer"
            )
            item["analyzer_version"] = _required(
                item.get("analyzer_version"), field="evidence.analyzer_version"
            )
        if "scope" in item:
            item["scope"] = _normalize_mapping(
                item.get("scope"), field="evidence.scope"
            )
        canonical_json(item)
        result.append(item)
    return result


def resolve_state_from_evidence(
    evidence: Iterable[Mapping[str, Any]],
    *,
    assessment_scope: Mapping[str, Any] | None = None,
) -> tuple[AffectednessState, dict[str, Any]]:
    """Resolve explicit applicability signals without manufacturing certainty.

    Automatic resolution is deliberately conservative. DECLARED evidence remains
    inspectable evidence but does not by itself produce a resolved factual state;
    authority/policy acceptance belongs to W3. Scanner non-detection, generic
    inconclusive analysis, scope mismatches and terminal conflicts remain UNKNOWN.
    """

    normalized = _normalize_applicability_evidence(evidence)
    all_signals = {item["signal"] for item in normalized}
    if not all_signals:
        return AffectednessState.UNKNOWN, {"reason": "no_applicability_evidence"}

    scope_conflict = applicability_scope_conflict(
        normalized,
        assessment_scope=assessment_scope,
    )
    if scope_conflict is not None:
        return AffectednessState.UNKNOWN, {
            **scope_conflict,
            "signals": sorted(all_signals),
        }

    conclusive = [
        item
        for item in normalized
        if item["evidence_class"] != LineageEvidenceClass.DECLARED.value
        and item["signal"] != ApplicabilitySignal.MITIGATION_DECLARED.value
    ]
    signals = {item["signal"] for item in conclusive}
    if not signals:
        return AffectednessState.UNKNOWN, {
            "reason": "declared_evidence_requires_corroboration",
            "signals": sorted(all_signals),
        }

    present = ApplicabilitySignal.MATERIAL_PRESENT.value in signals
    absent = ApplicabilitySignal.MATERIAL_ABSENT.value in signals
    mitigated = ApplicabilitySignal.MITIGATION_ESTABLISHED.value in signals
    out_of_scope = ApplicabilitySignal.OUT_OF_SCOPE.value in signals
    weak_unknown = bool(
        signals
        & {
            ApplicabilitySignal.INCONCLUSIVE.value,
            ApplicabilitySignal.SCANNER_NO_MATCH.value,
        }
    )

    conflicts = conflicting_terminal_signals(signals)
    if conflicts:
        return AffectednessState.UNKNOWN, {
            "reason": "conflicting_applicability_evidence",
            "signals": sorted(all_signals),
            "conflicting_terminal_signals": list(conflicts),
        }
    if out_of_scope:
        return AffectednessState.NOT_APPLICABLE, {
            "reason": "event_scope_excludes_subject",
            "signals": sorted(all_signals),
        }
    if mitigated:
        return AffectednessState.MITIGATED, {
            "reason": "explicit_mitigation_evidence",
            "signals": sorted(all_signals),
        }
    if absent:
        return AffectednessState.DISPROVEN, {
            "reason": "event_relevant_material_mechanically_or_evidentially_absent",
            "signals": sorted(all_signals),
        }
    if present:
        return AffectednessState.CONFIRMED_AFFECTED, {
            "reason": "event_relevant_material_or_condition_present",
            "signals": sorted(all_signals),
        }
    if weak_unknown:
        return AffectednessState.UNKNOWN, {
            "reason": "analysis_did_not_establish_presence_or_absence",
            "signals": sorted(all_signals),
        }
    return AffectednessState.UNKNOWN, {
        "reason": "unresolved",
        "signals": sorted(all_signals),
    }


class TestamurAffectednessStore:
    """Immutable historical affectedness assessments.

    New evidence creates a new assessment that may supersede an earlier one.
    Previous assessments remain queryable and are never rewritten in place.
    """

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS testamur_affectedness_assessments(
          assessment_id TEXT PRIMARY KEY,
          event_revision_id TEXT NOT NULL,
          subject_revision TEXT NOT NULL,
          state TEXT NOT NULL,
          supersedes_assessment_id TEXT,
          assessment_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS testamur_affectedness_event_subject_idx
          ON testamur_affectedness_assessments(
            event_revision_id,subject_revision,created_at DESC,assessment_id DESC
          );
        CREATE INDEX IF NOT EXISTS testamur_affectedness_state_idx
          ON testamur_affectedness_assessments(
            state,event_revision_id,subject_revision
          );

        CREATE TRIGGER IF NOT EXISTS testamur_affectedness_no_update
        BEFORE UPDATE ON testamur_affectedness_assessments BEGIN
          SELECT RAISE(ABORT, 'Testamur affectedness assessments are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS testamur_affectedness_no_delete
        BEFORE DELETE ON testamur_affectedness_assessments BEGIN
          SELECT RAISE(ABORT, 'Testamur affectedness assessments are immutable');
        END;
        """
        with self.connect() as conn:
            conn.executescript(schema)

    def record_assessment(
        self,
        *,
        event_revision_id: str,
        subject_revision: str,
        state: str | AffectednessState,
        basis: Iterable[Mapping[str, Any]],
        event_id: str | None = None,
        evidence: Iterable[Mapping[str, Any]] | None = None,
        scope: Mapping[str, Any] | None = None,
        lineage_path_edge_ids: Iterable[str] | None = None,
        analyzer: Mapping[str, Any] | None = None,
        policy_ref: str | None = None,
        supersedes_assessment_id: str | None = None,
        explanation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        event_ref = _required(event_revision_id, field="event_revision_id")
        stable_event_ref = None if event_id is None else _required(event_id, field="event_id")
        subject_ref = _required(subject_revision, field="subject_revision")
        state_value = _normalize_state(state)
        normalized_basis = _normalize_basis(basis)
        normalized_evidence = _normalize_applicability_evidence(evidence)
        normalized_scope = _normalize_mapping(scope, field="scope")
        normalized_analyzer = _normalize_mapping(analyzer, field="analyzer")
        normalized_explanation = _normalize_mapping(
            explanation, field="explanation"
        )
        path_ids = sorted(
            {
                _required(item, field="lineage_path_edge_ids")
                for item in lineage_path_edge_ids or ()
            }
        )

        if state_value in FINAL_RESOLUTION_STATES and not normalized_evidence:
            raise ValueError(
                f"{state_value} requires explicit applicability evidence"
            )
        scope_conflict = applicability_scope_conflict(
            normalized_evidence,
            assessment_scope=normalized_scope or None,
        )
        if state_value in FINAL_RESOLUTION_STATES and scope_conflict is not None:
            raise ValueError(
                "final affectedness state cannot combine incompatible applicability evidence scopes"
            )
        if state_value in FINAL_RESOLUTION_STATES:
            mechanically_resolved, _ = resolve_state_from_evidence(
                normalized_evidence,
                assessment_scope=normalized_scope or None,
            )
            if mechanically_resolved.value != state_value:
                raise ValueError(
                    "final affectedness state must match mechanical resolution of supplied evidence"
                )
        if (
            state_value == AffectednessState.POTENTIALLY_AFFECTED.value
            and not path_ids
        ):
            raise ValueError("POTENTIALLY_AFFECTED requires a lineage path")

        supersedes = None
        if supersedes_assessment_id is not None:
            supersedes = _required(
                supersedes_assessment_id, field="supersedes_assessment_id"
            )
            previous = self.get_assessment(supersedes)
            if previous is None:
                raise KeyError(supersedes)
            previous_event_revision = str(previous.get("event_revision_id") or "").strip()
            previous_event_id = str(previous.get("event_id") or "").strip()
            if stable_event_ref is None and previous_event_revision == event_ref and previous_event_id:
                stable_event_ref = previous_event_id
            validate_affectedness_supersession(
                previous,
                event_revision_id=event_ref,
                subject_revision=subject_ref,
                event_id=stable_event_ref,
            )

        identity = {
            "event_revision_id": event_ref,
            "subject_revision": subject_ref,
            "state": state_value,
            "basis": normalized_basis,
            "evidence": normalized_evidence,
            "scope": normalized_scope,
            "lineage_path_edge_ids": path_ids,
            "analyzer": normalized_analyzer,
            "policy_ref": None if policy_ref is None else str(policy_ref),
            "supersedes_assessment_id": supersedes,
            "explanation": normalized_explanation,
        }
        if stable_event_ref is not None:
            identity["event_id"] = stable_event_ref
        assessment_id = canonical_extension_id("affectedness", identity)
        created_at = _utc_now()
        payload = {
            "assessment_id": assessment_id,
            **identity,
            "created_at": created_at,
            "semantics": {
                "lineage_propagation_is_verdict": False,
                "potentially_affected_is_confirmed_affected": False,
                "changed_derivative_is_safe": False,
                "scanner_non_detection_is_disproof": False,
                "declared_evidence_is_mechanical_proof": False,
                "assessment_is_event_and_scope_specific": True,
                "cross_revision_supersession_requires_same_event_id": True,
                "same_revision_supersession_inherits_known_event_id": True,
                "terminal_state_requires_scope_compatible_evidence": True,
                "terminal_state_matches_mechanical_evidence_resolution": True,
                "canonical_extension_identity": True,
            },
        }

        with self.connect() as conn:
            existing = conn.execute(
                """SELECT assessment_json
                   FROM testamur_affectedness_assessments
                   WHERE assessment_id=?""",
                (assessment_id,),
            ).fetchone()
            if existing is not None:
                return json.loads(str(existing["assessment_json"]))
            conn.execute(
                """INSERT INTO testamur_affectedness_assessments(
                     assessment_id,event_revision_id,subject_revision,state,
                     supersedes_assessment_id,assessment_json,created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    assessment_id,
                    event_ref,
                    subject_ref,
                    state_value,
                    supersedes,
                    canonical_json(payload),
                    created_at,
                ),
            )
        return payload

    def get_assessment(self, assessment_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT assessment_json
                   FROM testamur_affectedness_assessments
                   WHERE assessment_id=?""",
                (str(assessment_id),),
            ).fetchone()
        return None if row is None else json.loads(str(row["assessment_json"]))

    def history(
        self,
        event_revision_id: str,
        subject_revision: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 1000))
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT assessment_json
                   FROM testamur_affectedness_assessments
                   WHERE event_revision_id=? AND subject_revision=?
                   ORDER BY rowid DESC LIMIT ?""",
                (str(event_revision_id), str(subject_revision), bounded),
            ).fetchall()
        return [json.loads(str(row["assessment_json"])) for row in rows]

    def latest(
        self, event_revision_id: str, subject_revision: str
    ) -> dict[str, Any] | None:
        values = self.history(event_revision_id, subject_revision, limit=1)
        return None if not values else values[0]

    def latest_for_event(self, event_revision_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT assessment_json
                   FROM testamur_affectedness_assessments
                   WHERE event_revision_id=?
                   ORDER BY subject_revision,rowid DESC""",
                (str(event_revision_id),),
            ).fetchall()
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            payload = json.loads(str(row["assessment_json"]))
            latest.setdefault(str(payload["subject_revision"]), payload)
        return [latest[key] for key in sorted(latest)]

    def current_heads_for_event(
        self, event_revision_id: str
    ) -> list[dict[str, Any]]:
        """Return all unsuperseded assessment heads for each subject in an event.

        Independent analyses may legitimately create competing immutable heads.
        Operational propagation must inspect every current head rather than letting
        SQLite insertion order choose one conclusion as the effective truth.
        """

        with self.connect() as conn:
            rows = conn.execute(
                """SELECT assessment_json
                   FROM testamur_affectedness_assessments
                   WHERE event_revision_id=?
                   ORDER BY subject_revision,rowid""",
                (str(event_revision_id),),
            ).fetchall()
        by_subject: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            payload = json.loads(str(row["assessment_json"]))
            by_subject.setdefault(str(payload["subject_revision"]), []).append(payload)

        heads: list[dict[str, Any]] = []
        for subject in sorted(by_subject):
            values = by_subject[subject]
            superseded = {
                str(item["supersedes_assessment_id"])
                for item in values
                if item.get("supersedes_assessment_id")
            }
            heads.extend(
                item
                for item in values
                if str(item["assessment_id"]) not in superseded
            )
        return heads

    def explain_affectedness(self, assessment_id: str) -> dict[str, Any]:
        assessment = self.get_assessment(assessment_id)
        if assessment is None:
            raise KeyError(assessment_id)
        return {
            "assessment": assessment,
            "basis": assessment["basis"],
            "evidence": assessment["evidence"],
            "lineage_path_edge_ids": assessment["lineage_path_edge_ids"],
            "explanation": assessment["explanation"],
            "semantics": {
                "label_without_basis_is_not_an_explanation": True,
                "assessment_does_not_rewrite_prior_history": True,
            },
        }


class TestamurAffectednessEngine:
    """Candidate propagation + explicit applicability resolution."""

    def __init__(
        self,
        *,
        lineage: TestamurLineageStore,
        assessments: TestamurAffectednessStore,
    ) -> None:
        self.lineage = lineage
        self.assessments = assessments

    def find_potentially_affected(
        self,
        event_revision: Mapping[str, Any],
        *,
        max_depth: int = 16,
        max_paths: int = 1000,
    ) -> list[dict[str, Any]]:
        event_revision_id = _required(
            event_revision.get("event_revision_id"),
            field="event_revision.event_revision_id",
        )
        raw_event_id = event_revision.get("event_id")
        event_id = None if raw_event_id is None else _required(raw_event_id, field="event_revision.event_id")
        upstream_refs = [
            _required(item, field="event_revision.upstream_refs")
            for item in event_revision.get("upstream_refs") or []
        ]
        if not upstream_refs:
            return []

        candidates: dict[str, dict[str, Any]] = {}
        for upstream_ref in sorted(set(upstream_refs)):
            paths = self.lineage.lineage_paths(
                upstream_ref,
                max_depth=max_depth,
                max_paths=max_paths,
            )
            for path in paths:
                subject = str(path["downstream_ref"])
                entry = candidates.setdefault(
                    subject,
                    {
                        "event_id": event_id,
                        "event_revision_id": event_revision_id,
                        "subject_revision": subject,
                        "state": AffectednessState.POTENTIALLY_AFFECTED.value,
                        "upstream_refs": set(),
                        "paths": [],
                        "semantics": {
                            "candidate_attention_only": True,
                            "confirmed_affected_implied": False,
                        },
                    },
                )
                entry["upstream_refs"].add(upstream_ref)
                entry["paths"].append(path)

        result: list[dict[str, Any]] = []
        for subject in sorted(candidates):
            item = candidates[subject]
            item["upstream_refs"] = sorted(item["upstream_refs"])
            item["paths"].sort(
                key=lambda path: (
                    int(path["length"]),
                    tuple(path["edge_ids"]),
                )
            )
            result.append(item)
        return result

    @staticmethod
    def _is_generated_candidate_head(item: Mapping[str, Any]) -> bool:
        return (
            str(item.get("state") or "")
            == AffectednessState.POTENTIALLY_AFFECTED.value
            and not (item.get("evidence") or [])
            and isinstance(item.get("explanation"), Mapping)
            and str(item["explanation"].get("reason") or "")
            == "plausible_lineage_path_from_event_subject"
        )

    def _candidate_refresh_predecessor(
        self,
        *,
        event_revision_id: str,
        subject_revision: str,
        event_id: str | None,
        basis: list[dict[str, Any]],
        path_ids: list[str],
        explanation: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Return an idempotent candidate or one safe predecessor to supersede.

        Candidate regeneration may discover additional lineage paths. Sequential
        refreshes should therefore form one explicit candidate chain instead of
        leaving stale POTENTIALLY_AFFECTED heads behind forever. Independent
        analysis heads are never auto-superseded. If concurrent/generated heads
        already conflict, fail conservative by creating a new head rather than
        guessing which history branch owns the refresh.
        """

        generated = [
            item
            for item in self.assessments.current_heads_for_event(event_revision_id)
            if str(item.get("subject_revision") or "") == subject_revision
            and self._is_generated_candidate_head(item)
        ]
        event_identity = "" if event_id is None else str(event_id)
        for item in generated:
            if (
                list(item.get("basis") or []) == basis
                and list(item.get("lineage_path_edge_ids") or []) == path_ids
                and dict(item.get("explanation") or {}) == explanation
                and str(item.get("event_id") or "") == event_identity
            ):
                return item, None
        if len(generated) == 1:
            return None, str(generated[0]["assessment_id"])
        return None, None

    def record_potential_candidates(
        self,
        event_revision: Mapping[str, Any],
        *,
        max_depth: int = 16,
        max_paths: int = 1000,
    ) -> list[dict[str, Any]]:
        resolution_basis = advisory_identity_resolution_basis(event_revision)
        recorded: list[dict[str, Any]] = []
        for candidate in self.find_potentially_affected(
            event_revision,
            max_depth=max_depth,
            max_paths=max_paths,
        ):
            path_ids = sorted(
                {
                    edge_id
                    for path in candidate["paths"]
                    for edge_id in path["edge_ids"]
                }
            )
            basis = [
                {
                    "kind": "advisory_revision",
                    "ref": candidate["event_revision_id"],
                },
                *resolution_basis,
                *(
                    {"kind": "lineage_edge", "ref": edge_id}
                    for edge_id in path_ids
                ),
            ]
            explanation = {
                "reason": "plausible_lineage_path_from_event_subject",
                "upstream_refs": candidate["upstream_refs"],
                "candidate_path_count": len(candidate["paths"]),
            }
            if resolution_basis:
                explanation["identity_resolution_status"] = "resolved_exact"
            existing, supersedes = self._candidate_refresh_predecessor(
                event_revision_id=candidate["event_revision_id"],
                subject_revision=candidate["subject_revision"],
                event_id=candidate.get("event_id"),
                basis=basis,
                path_ids=path_ids,
                explanation=explanation,
            )
            if existing is not None:
                recorded.append(existing)
                continue
            recorded.append(
                self.assessments.record_assessment(
                    event_id=candidate.get("event_id"),
                    event_revision_id=candidate["event_revision_id"],
                    subject_revision=candidate["subject_revision"],
                    state=AffectednessState.POTENTIALLY_AFFECTED,
                    basis=basis,
                    lineage_path_edge_ids=path_ids,
                    supersedes_assessment_id=supersedes,
                    explanation=explanation,
                )
            )
        return recorded

    def assess_from_evidence(
        self,
        *,
        event_revision_id: str,
        subject_revision: str,
        evidence: Iterable[Mapping[str, Any]],
        basis: Iterable[Mapping[str, Any]],
        event_id: str | None = None,
        scope: Mapping[str, Any] | None = None,
        lineage_path_edge_ids: Iterable[str] | None = None,
        analyzer: Mapping[str, Any] | None = None,
        policy_ref: str | None = None,
        supersedes_assessment_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_evidence = _normalize_applicability_evidence(evidence)
        normalized_scope = _normalize_mapping(scope, field="scope")
        normalized_basis, path_ids = _closed_assessment_basis(
            basis,
            event_revision_id=event_revision_id,
            lineage_path_edge_ids=lineage_path_edge_ids,
        )
        state, explanation = resolve_state_from_evidence(
            normalized_evidence,
            assessment_scope=normalized_scope or None,
        )
        explanation = {
            **explanation,
            "basis_closed_over_advisory_and_lineage": True,
        }
        return self.assessments.record_assessment(
            event_id=event_id,
            event_revision_id=event_revision_id,
            subject_revision=subject_revision,
            state=state,
            basis=normalized_basis,
            evidence=normalized_evidence,
            scope=normalized_scope,
            lineage_path_edge_ids=path_ids,
            analyzer=analyzer,
            policy_ref=policy_ref,
            supersedes_assessment_id=supersedes_assessment_id,
            explanation=explanation,
        )

    def actionable_subjects(
        self,
        event_revision_id: str,
        *,
        states: Iterable[str | AffectednessState] | None = None,
    ) -> list[str]:
        selected = (
            set(ACTIONABLE_STATES)
            if states is None
            else {_normalize_state(item) for item in states}
        )
        heads = self.assessments.current_heads_for_event(event_revision_id)
        actionable = {
            str(item["subject_revision"])
            for item in heads
            if item["state"] in selected
        }
        return sorted(actionable)

    def affected_reliance_blast_radius(
        self,
        event_revision_id: str,
        *,
        resolver: RelianceImpactResolver,
        policy_ref: str | None = None,
        states: Iterable[str | AffectednessState] | None = None,
    ) -> Any:
        # Reliance impact is an affectedness verdict surface, not the broader
        # attention queue. Potential/unknown lineage candidates remain actionable
        # for review but must not manufacture downstream affected impact.
        effective_states = (
            {AffectednessState.CONFIRMED_AFFECTED.value}
            if states is None
            else states
        )
        subjects = self.actionable_subjects(
            event_revision_id, states=effective_states
        )
        return resolver.blast_radius_for_refs(
            subjects, policy_ref=policy_ref
        )
