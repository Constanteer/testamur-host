from __future__ import annotations

from typing import Any, Mapping

from . import source_gateway_mcp as _base
from . import source_gateway_project_mcp as _project
from .repository_binding_lifecycle import (
    repository_binding_with_state,
    set_repository_binding_enabled,
    unbind_repository,
)
from .repository_binding_cli import _scan_enabled


BINDING_TOOL = {
    "name": "testamur.project_repository_binding",
    "title": "Read Project repository binding state",
    "description": "Read an explicit durable Project repository binding and its scanner-eligibility lifecycle state. Binding state is not evidence of repository content, verification, reliance, invalidity, or affectedness.",
    "inputSchema": {"type": "object", "properties": {"project_ref": {"type": "string"}, "binding": {"type": "string", "default": "primary"}}, "required": ["project_ref"], "additionalProperties": False},
}
BINDING_STATE_TOOL = {
    "name": "testamur.set_project_repository_binding_enabled",
    "title": "Enable or disable Project repository scanning",
    "description": "Change scanner eligibility without deleting the binding or rewriting immutable binding revisions. This state transition does not imply observation, verification, reliance, invalidity, or affectedness.",
    "inputSchema": {"type": "object", "properties": {"project_ref": {"type": "string"}, "binding": {"type": "string", "default": "primary"}, "enabled": {"type": "boolean"}}, "required": ["project_ref", "enabled"], "additionalProperties": False},
}
UNBIND_TOOL = {
    "name": "testamur.project_repository_unbind",
    "title": "Unbind Project repository scanner input",
    "description": "Disable a Project repository binding while preserving its stable identity and immutable revision history. Unbind is non-destructive lifecycle state, not an evidence or affectedness verdict.",
    "inputSchema": {"type": "object", "properties": {"project_ref": {"type": "string"}, "binding": {"type": "string", "default": "primary"}}, "required": ["project_ref"], "additionalProperties": False},
}


def _semantics() -> dict[str, bool]:
    return {
        "binding_state_implies_content_observed": False,
        "recorded_is_not_verified": True,
        "recorded_is_not_relied": True,
        "changed_is_not_invalid": True,
        "lineage_is_not_affectedness_verdict": True,
        "generic_trust_score_used": False,
    }


def _binding(arguments: Mapping[str, Any]) -> dict[str, Any]:
    service = _base.TestamurProductService.integrated(_base._db_path())
    project_ref = _base._required(arguments, "project_ref")
    binding_key = str(arguments.get("binding") or "primary")
    binding = repository_binding_with_state(service.projects, project_ref, binding_key=binding_key)
    if binding is None:
        raise ValueError(f"project has no repository binding named {binding_key!r}")
    return {"ok": True, "schema": "testamur.mcp.project-repository-binding.v1", "binding": binding, "semantics": _semantics()}


def _set_binding(arguments: Mapping[str, Any]) -> dict[str, Any]:
    service = _base.TestamurProductService.integrated(_base._db_path())
    result = set_repository_binding_enabled(
        service.projects,
        _base._required(arguments, "project_ref"),
        binding_key=str(arguments.get("binding") or "primary"),
        enabled=bool(arguments["enabled"]),
    )
    return {"ok": True, "schema": "testamur.mcp.project-repository-binding-lifecycle.v1", "binding": result, "semantics": _semantics()}


def _unbind(arguments: Mapping[str, Any]) -> dict[str, Any]:
    service = _base.TestamurProductService.integrated(_base._db_path())
    result = unbind_repository(
        service.projects,
        _base._required(arguments, "project_ref"),
        binding_key=str(arguments.get("binding") or "primary"),
    )
    return {"ok": True, "schema": "testamur.mcp.project-repository-binding-lifecycle.v1", "binding": result, "semantics": _semantics()}


def _install() -> None:
    _project._install()
    for tool in (BINDING_TOOL, BINDING_STATE_TOOL, UNBIND_TOOL):
        if not any(existing.get("name") == tool["name"] for existing in _base.TOOLS):
            _base.TOOLS.append(tool)
    base_call = _base._call_tool
    if getattr(base_call, "_testamur_binding_extension", False):
        return

    def call_tool(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if name == BINDING_TOOL["name"]:
            return _binding(arguments)
        if name == BINDING_STATE_TOOL["name"]:
            return _set_binding(arguments)
        if name == UNBIND_TOOL["name"]:
            return _unbind(arguments)
        if name == _project.PROJECT_SCAN_TOOL["name"]:
            service = _base.TestamurProductService.integrated(_base._db_path())
            result = _scan_enabled(
                service,
                _base._required(arguments, "project_ref"),
                str(arguments.get("binding") or "primary"),
            )
            return {**result, "schema": "testamur.mcp.project-supply-chain-scan.v1", "semantics": _semantics()}
        return base_call(name, arguments)

    setattr(call_tool, "_testamur_binding_extension", True)
    # The wrapper includes the complete project extension. Marking both keeps
    # repeated installs from wrapping the dispatch chain recursively.
    setattr(call_tool, "_testamur_project_extension", True)
    _base._call_tool = call_tool


def main() -> int:
    _install()
    return int(_base.main())


if __name__ == "__main__":
    raise SystemExit(main())
