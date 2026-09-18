from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

from .policy import PolicyStore

ASSESSMENT_SCHEMA_VERSION = "testamur.assessment.v1"


class AssessmentState(StrEnum):
    SUPPORTED = "SUPPORTED"
    CONFLICTED = "CONFLICTED"
    STALE = "STALE"
    RETRACTED = "RETRACTED"
    UNVERIFIED = "UNVERIFIED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    UNKNOWN = "UNKNOWN"


@runtime_checkable
class PolicyEvidenceView(Protocol):
    """Read-only bridge into canonical Testamur objects and evidence."""

    def object_state(self, object_ref: str) -> Mapping[str, Any]: ...
    def revision_ref(self, object_ref: str) -> str | None: ...
    def assurances_for(self, object_ref: str) -> Iterable[Mapping[str, Any]]: ...
    def outgoing_relations(self, object_ref: str) -> Iterable[Mapping[str, Any]]: ...
    def incoming_relations(self, object_ref: str) -> Iterable[Mapping[str, Any]]: ...


def _scope_satisfies(actual: Any, required: Any) -> bool:
    if isinstance(required, Mapping):
        if not isinstance(actual, Mapping):
            return False
        return all(
            key in actual and _scope_satisfies(actual[key], value)
            for key, value in required.items()
        )
    return actual == required


def _relation_type(edge: Mapping[str, Any]) -> str:
    return str(edge.get("relation_type") or edge.get("kind") or edge.get("type") or "")


def _relation_id(edge: Mapping[str, Any]) -> str:
    return str(edge.get("relation_id") or edge.get("id") or "")


def _relation_target(edge: Mapping[str, Any]) -> str:
    return str(
        edge.get("to_ref")
        or edge.get("target_ref")
        or edge.get("target_object_id")
        or edge.get("target")
        or ""
    )


def _relation_source(edge: Mapping[str, Any]) -> str:
    return str(
        edge.get("from_ref")
        or edge.get("source_ref")
        or edge.get("source_object_id")
        or edge.get("source")
        or ""
    )


def _assurance_id(item: Mapping[str, Any]) -> str:
    return str(
        item.get("assurance_id")
        or item.get("verification_id")
        or item.get("id")
        or ""
    )


def _assurance_kind(item: Mapping[str, Any]) -> str:
    return str(
        item.get("assurance_kind")
        or item.get("kind")
        or item.get("verifier_class")
        or ""
    )


def _assurance_status(item: Mapping[str, Any]) -> str:
    return str(
        item.get("effective_status")
        or item.get("status")
        or item.get("checker_status")
        or ""
    ).lower()


def _is_stale(item: Mapping[str, Any]) -> bool:
    return bool(item.get("stale")) or str(
        item.get("revision_state") or ""
    ).lower() == "stale"


def _assurance_selection_key(item: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(item.get("recorded_at") or item.get("created_at") or ""),
        _assurance_id(item),
    )


def _is_admissible_assurance(item: Mapping[str, Any]) -> bool:
    if "admissible" in item:
        return bool(item.get("admissible")) and not _is_stale(item)
    return _assurance_status(item) in {
        "passed",
        "supported",
        "reviewed",
        "tested",
        "reproduced",
        "mechanically_checked",
        "formally_proven",
        "empirically_supported",
        "statistically_supported",
        "simulation_supported",
    } and not _is_stale(item)


def _classify(
    blockers: list[dict[str, Any]], object_state: Mapping[str, Any]
) -> AssessmentState:
    declared = str(
        object_state.get("state") or object_state.get("status") or ""
    ).lower()
    if declared in {"retracted", "withdrawn"}:
        return AssessmentState.RETRACTED

    types = {str(item.get("type") or "") for item in blockers}
    if types & {
        "dependency_cycle",
        "missing_policy",
        "depth_limit",
        "node_limit",
        "dependency_unknown",
    }:
        return AssessmentState.UNKNOWN
    if types & {"stale_assurance", "dependency_stale"}:
        return AssessmentState.STALE
    if types & {
        "blocking_relation",
        "conflicted_assurance",
        "dependency_conflicted",
    }:
        return AssessmentState.CONFLICTED
    if "missing_assurance" in types:
        return AssessmentState.UNVERIFIED
    if blockers:
        return AssessmentState.INSUFFICIENT_EVIDENCE
    return AssessmentState.SUPPORTED


@dataclass(slots=True)
class _Budget:
    max_depth: int
    max_nodes: int
    visited_nodes: int = 0


class PolicyEngine:
    """Purpose-aware admission/assessment engine migrated from Witness semantics."""

    def __init__(
        self,
        policies: PolicyStore,
        evidence: PolicyEvidenceView,
        *,
        max_depth: int = 32,
        max_nodes: int = 2048,
    ) -> None:
        if max_depth < 1 or max_nodes < 1:
            raise ValueError("max_depth and max_nodes must be positive")
        self.policies = policies
        self.evidence = evidence
        self.max_depth = int(max_depth)
        self.max_nodes = int(max_nodes)

    @staticmethod
    def _recovery(
        policy: Mapping[str, Any], assurance_kind: str, object_ref: str
    ) -> str:
        definition = (
            policy.get("definition")
            if isinstance(policy.get("definition"), Mapping)
            else {}
        )
        hints = (
            definition.get("recovery_hints")
            if isinstance(definition.get("recovery_hints"), Mapping)
            else {}
        )
        return str(
            hints.get(assurance_kind)
            or f"re-establish {assurance_kind} for {object_ref}"
        )

    def _revision_ref_or_none(self, object_ref: str) -> str | None:
        """Read optional revision identity without turning UNKNOWN into a crash."""
        try:
            return self.evidence.revision_ref(str(object_ref))
        except (KeyError, ValueError):
            return None

    def evaluate(
        self, *, scope_ref: str, object_ref: str, purpose: str
    ) -> dict[str, Any]:
        budget = _Budget(self.max_depth, self.max_nodes)

        def terminal_unknown(
            current_ref: str,
            blocker: dict[str, Any],
            recovery: str,
        ) -> dict[str, Any]:
            return self._result(
                scope_ref,
                current_ref,
                purpose,
                {},
                None,
                [],
                [],
                [blocker],
                [recovery],
            )

        def visit(
            current_ref: str, stack: tuple[str, ...], depth: int
        ) -> dict[str, Any]:
            if current_ref in stack:
                return terminal_unknown(
                    current_ref,
                    {"type": "dependency_cycle", "path": [*stack, current_ref]},
                    f"break dependency cycle involving {current_ref}",
                )
            if depth > budget.max_depth:
                return terminal_unknown(
                    current_ref,
                    {
                        "type": "depth_limit",
                        "object_ref": current_ref,
                        "max_depth": budget.max_depth,
                    },
                    "review dependency depth or narrow the policy graph",
                )
            if budget.visited_nodes >= budget.max_nodes:
                return terminal_unknown(
                    current_ref,
                    {
                        "type": "node_limit",
                        "object_ref": current_ref,
                        "max_nodes": budget.max_nodes,
                    },
                    "review dependency breadth or narrow the policy graph",
                )
            budget.visited_nodes += 1

            obj = dict(self.evidence.object_state(current_ref))
            kind = str(
                obj.get("kind")
                or obj.get("record_kind")
                or obj.get("object_kind")
                or ""
            )
            if not kind:
                raise ValueError(f"object {current_ref} has no kind")

            policy = self.policies.match(
                scope_ref=scope_ref,
                purpose=purpose,
                target_kind=kind,
            )
            if policy is None:
                return self._result(
                    scope_ref,
                    current_ref,
                    purpose,
                    obj,
                    None,
                    [],
                    [],
                    [
                        {
                            "type": "missing_policy",
                            "object_ref": current_ref,
                            "kind": kind,
                            "purpose": purpose,
                        }
                    ],
                    [f"register policy for {kind} under {purpose}"],
                )

            definition = policy["definition"]
            assurances = [
                dict(item) for item in self.evidence.assurances_for(current_ref)
            ]
            latest_by_kind: dict[str, dict[str, Any]] = {}
            for item in assurances:
                kind_name = _assurance_kind(item)
                if not kind_name:
                    continue
                current = latest_by_kind.get(kind_name)
                if current is None or _assurance_selection_key(
                    item
                ) > _assurance_selection_key(current):
                    latest_by_kind[kind_name] = item

            considered: list[dict[str, Any]] = []
            blockers: list[dict[str, Any]] = []
            restoration: list[str] = []
            for assurance_kind in definition["required_assurances"]:
                run = latest_by_kind.get(assurance_kind)
                if run is None:
                    blockers.append(
                        {
                            "type": "missing_assurance",
                            "object_ref": current_ref,
                            "assurance_kind": assurance_kind,
                        }
                    )
                    restoration.append(
                        self._recovery(policy, assurance_kind, current_ref)
                    )
                    continue

                considered.append(run)
                status = _assurance_status(run)
                if _is_stale(run):
                    blockers.append(
                        {
                            "type": "stale_assurance",
                            "object_ref": current_ref,
                            "assurance_kind": assurance_kind,
                            "assurance_id": _assurance_id(run),
                            "status": status,
                        }
                    )
                    restoration.append(
                        self._recovery(policy, assurance_kind, current_ref)
                    )
                elif status in {"contradicted", "conflicted", "failed"}:
                    blockers.append(
                        {
                            "type": "conflicted_assurance",
                            "object_ref": current_ref,
                            "assurance_kind": assurance_kind,
                            "assurance_id": _assurance_id(run),
                            "status": status,
                        }
                    )
                    restoration.append(
                        self._recovery(policy, assurance_kind, current_ref)
                    )
                elif not _is_admissible_assurance(run):
                    blockers.append(
                        {
                            "type": "inadmissible_assurance",
                            "object_ref": current_ref,
                            "assurance_kind": assurance_kind,
                            "assurance_id": _assurance_id(run),
                            "status": status,
                        }
                    )
                    restoration.append(
                        self._recovery(policy, assurance_kind, current_ref)
                    )
                else:
                    required_scope = definition["assurance_scope_requirements"].get(
                        assurance_kind
                    )
                    if required_scope is not None and not _scope_satisfies(
                        run.get("scope", {}), required_scope
                    ):
                        blockers.append(
                            {
                                "type": "assurance_scope_mismatch",
                                "object_ref": current_ref,
                                "assurance_kind": assurance_kind,
                                "assurance_id": _assurance_id(run),
                                "required_scope": required_scope,
                                "actual_scope": run.get("scope", {}),
                            }
                        )
                        restoration.append(
                            self._recovery(policy, assurance_kind, current_ref)
                        )

            incoming = [
                dict(edge) for edge in self.evidence.incoming_relations(current_ref)
            ]
            for edge in sorted(
                incoming,
                key=lambda item: (_relation_type(item), _relation_id(item)),
            ):
                if _relation_type(edge) in definition["blocking_relation_types"]:
                    blockers.append(
                        {
                            "type": "blocking_relation",
                            "object_ref": current_ref,
                            "relation_id": _relation_id(edge),
                            "relation_type": _relation_type(edge),
                            "source_ref": _relation_source(edge),
                        }
                    )
                    restoration.append(
                        f"resolve or retract blocking relation {_relation_id(edge)}"
                    )

            dependencies: list[dict[str, Any]] = []
            if definition["require_dependencies_admissible"]:
                outgoing = [
                    dict(edge)
                    for edge in self.evidence.outgoing_relations(current_ref)
                ]
                selected = [
                    edge
                    for edge in outgoing
                    if _relation_type(edge) in definition["dependency_relation_types"]
                ]
                for edge in sorted(
                    selected,
                    key=lambda item: (
                        _relation_type(item),
                        _relation_id(item),
                        _relation_target(item),
                    ),
                ):
                    target = _relation_target(edge)
                    if not target:
                        continue
                    child = visit(target, (*stack, current_ref), depth + 1)
                    dependencies.append({"relation": edge, "state": child})
                    if child["admissible"]:
                        continue
                    if (
                        child["status"] == "unruled"
                        and definition["allow_unruled_dependencies"]
                    ):
                        continue

                    child_state = child["assessment_state"]
                    blocker_type = {
                        AssessmentState.UNKNOWN.value: "dependency_unknown",
                        AssessmentState.STALE.value: "dependency_stale",
                        AssessmentState.CONFLICTED.value: "dependency_conflicted",
                    }.get(child_state, "dependency_not_admissible")
                    blockers.append(
                        {
                            "type": blocker_type,
                            "object_ref": current_ref,
                            "dependency_ref": target,
                            "relation_id": _relation_id(edge),
                            "relation_type": _relation_type(edge),
                        }
                    )
                    restoration.extend(child.get("restoration_work", []))

            return self._result(
                scope_ref,
                current_ref,
                purpose,
                obj,
                policy,
                considered,
                dependencies,
                blockers,
                restoration,
            )

        result = visit(str(object_ref), (), 0)
        result["root_causes"] = self._root_causes(result)
        result["visited_nodes"] = budget.visited_nodes
        return result

    def _result(
        self,
        scope_ref: str,
        object_ref: str,
        purpose: str,
        obj: Mapping[str, Any],
        policy: Mapping[str, Any] | None,
        assurances: list[dict[str, Any]],
        dependencies: list[dict[str, Any]],
        blockers: list[dict[str, Any]],
        restoration: list[str],
    ) -> dict[str, Any]:
        state = _classify(blockers, obj)
        admissible = state == AssessmentState.SUPPORTED
        status = (
            "admissible"
            if admissible
            else (
                "unruled"
                if any(item.get("type") == "missing_policy" for item in blockers)
                else "blocked"
            )
        )
        return {
            "schema_version": ASSESSMENT_SCHEMA_VERSION,
            "scope_ref": str(scope_ref),
            "object_ref": str(object_ref),
            "object_revision_ref": self._revision_ref_or_none(str(object_ref)),
            "kind": str(
                obj.get("kind")
                or obj.get("record_kind")
                or obj.get("object_kind")
                or "unknown"
            ),
            "purpose": str(purpose),
            "admissible": admissible,
            "status": status,
            "assessment_state": state.value,
            "policy": None if policy is None else dict(policy),
            "assurances": assurances,
            "dependencies": dependencies,
            "blockers": blockers,
            "restoration_work": list(
                dict.fromkeys(step for step in restoration if step)
            ),
            "semantics": {
                "evidence_implies_truth": False,
                "stale_implies_false": False,
                "changed_dependency_implies_false": False,
                "policy_is_purpose_and_scope_dependent": True,
                "revision_identity_required_for_assessment": False,
                "revision_identity_required_for_exact_reliance": True,
                "universal_trust_score": None,
            },
        }

    def _root_causes(self, state: Mapping[str, Any]) -> list[dict[str, Any]]:
        causes: list[dict[str, Any]] = []
        dependency_types = {
            "dependency_not_admissible",
            "dependency_unknown",
            "dependency_stale",
            "dependency_conflicted",
        }
        dependency_refs = {
            item.get("dependency_ref")
            for item in state.get("blockers", [])
            if str(item.get("type", "")) in dependency_types
        }
        for blocker in state.get("blockers", []):
            if str(blocker.get("type", "")) not in dependency_types:
                causes.append(dict(blocker))
        for dep in state.get("dependencies", []):
            child = dep.get("state")
            if isinstance(child, Mapping) and child.get("object_ref") in dependency_refs:
                causes.extend(self._root_causes(child))

        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in causes:
            key = json.dumps(
                item,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique

    @staticmethod
    def basis(state: Mapping[str, Any]) -> dict[str, list[str]]:
        object_refs: set[str] = set()
        assurance_ids: set[str] = set()
        policy_ids: set[str] = set()
        relation_ids: set[str] = set()
        stack = [state]
        seen: set[str] = set()
        while stack:
            current = stack.pop()
            current_ref = str(current.get("object_ref") or "")
            if current_ref and current_ref not in seen:
                seen.add(current_ref)
                object_refs.add(current_ref)
            policy = current.get("policy")
            if isinstance(policy, Mapping) and policy.get("policy_id"):
                policy_ids.add(str(policy["policy_id"]))
            for assurance in current.get("assurances", []):
                if isinstance(assurance, Mapping):
                    assurance_id = _assurance_id(assurance)
                    if assurance_id:
                        assurance_ids.add(assurance_id)
            for dependency in current.get("dependencies", []):
                if not isinstance(dependency, Mapping):
                    continue
                relation = dependency.get("relation")
                if isinstance(relation, Mapping):
                    relation_id = _relation_id(relation)
                    if relation_id:
                        relation_ids.add(relation_id)
                child = dependency.get("state")
                if isinstance(child, Mapping):
                    stack.append(child)
            for blocker in current.get("blockers", []):
                if isinstance(blocker, Mapping) and blocker.get("relation_id"):
                    relation_ids.add(str(blocker["relation_id"]))
        return {
            "object_refs": sorted(object_refs),
            "assurance_ids": sorted(assurance_ids),
            "policy_ids": sorted(policy_ids),
            "relation_ids": sorted(relation_ids),
        }
