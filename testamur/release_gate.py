from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib

from .namespace_gate import NamespaceViolation, scan_python_namespace_dependencies


CANONICAL_CLI_TARGET = "testamur.front_router:main"
LEGACY_RUNTIME_TREES = ("witness", "witness_service")


@dataclass(frozen=True)
class ReleaseViolation:
    code: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail}


def _load_pyproject(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        value = tomllib.load(handle)
    if not isinstance(value, dict):
        raise ValueError("pyproject root must be a table")
    return value


def audit_release_surface(repository_root: str | Path) -> dict[str, Any]:
    """Audit the Testamur-only production/package release boundary.

    This is deliberately a structural release check, not an epistemic check.
    Historical storage/wire strings are allowed; retired Python runtime trees
    and imports are not.
    """

    root = Path(repository_root)
    pyproject_path = root / "pyproject.toml"
    testamur_root = root / "testamur"
    tests_root = root / "tests"
    violations: list[ReleaseViolation] = []

    project = _load_pyproject(pyproject_path)
    scripts = project.get("project", {}).get("scripts", {})
    scripts = scripts if isinstance(scripts, dict) else {}
    script_target = scripts.get("testamur")
    if script_target != CANONICAL_CLI_TARGET:
        violations.append(
            ReleaseViolation(
                "cli_entrypoint_not_canonical",
                f"expected {CANONICAL_CLI_TARGET}, got {script_target!r}",
            )
        )

    legacy_cli_entries = sorted(
        f"{name}={target}"
        for name, target in scripts.items()
        if str(name).startswith("witness") or str(target).startswith("witness")
    )
    for entry in legacy_cli_entries:
        violations.append(ReleaseViolation("legacy_cli_entrypoint", entry))

    package_find = (
        project.get("tool", {})
        .get("setuptools", {})
        .get("packages", {})
        .get("find", {})
    )
    include = package_find.get("include", []) if isinstance(package_find, dict) else []
    if not isinstance(include, list):
        include = []

    package_patterns = [str(pattern) for pattern in include]
    testamur_included = any(
        pattern == "testamur" or pattern.startswith("testamur*")
        for pattern in package_patterns
    )
    if not testamur_included:
        violations.append(
            ReleaseViolation(
                "canonical_runtime_package_missing",
                "testamur package is not included",
            )
        )

    forbidden_package_patterns = sorted(
        pattern
        for pattern in package_patterns
        if pattern.split("*", 1)[0] in {"witness", "witness_service"}
    )
    for pattern in forbidden_package_patterns:
        violations.append(
            ReleaseViolation("legacy_runtime_package_in_distribution", pattern)
        )

    legacy_runtime_trees = [
        name for name in LEGACY_RUNTIME_TREES if (root / name).exists()
    ]
    for name in legacy_runtime_trees:
        violations.append(ReleaseViolation("legacy_runtime_tree_present", name))

    namespace_violations: tuple[NamespaceViolation, ...] = (
        scan_python_namespace_dependencies(testamur_root)
    )
    for item in namespace_violations:
        violations.append(
            ReleaseViolation(
                "legacy_runtime_import",
                f"testamur/{item.path}:{item.line}: {item.target}",
            )
        )

    test_namespace_violations: tuple[NamespaceViolation, ...] = tuple(
        item
        for item in scan_python_namespace_dependencies(tests_root)
        if Path(item.path).name.startswith("test_testamur_")
    )
    for item in test_namespace_violations:
        violations.append(
            ReleaseViolation(
                "legacy_runtime_test_import",
                f"tests/{item.path}:{item.line}: {item.target}",
            )
        )

    ordered = sorted(violations, key=lambda item: (item.code, item.detail))
    return {
        "ok": not ordered,
        "cli": {
            "public_command": "testamur",
            "target": script_target,
            "canonical": script_target == CANONICAL_CLI_TARGET,
            "legacy_entries": legacy_cli_entries,
        },
        "package": {
            "patterns": package_patterns,
            "testamur_included": testamur_included,
            "legacy_runtime_patterns": forbidden_package_patterns,
            "testamur_only": testamur_included and not forbidden_package_patterns,
        },
        "runtime": {
            "legacy_runtime_trees": legacy_runtime_trees,
            "legacy_import_count": len(namespace_violations),
            "legacy_test_import_count": len(test_namespace_violations),
            "testamur_only": (
                not legacy_runtime_trees
                and not namespace_violations
                and not test_namespace_violations
            ),
        },
        "violations": [item.to_dict() for item in ordered],
        "semantics": {
            "structural_release_audit_only": True,
            "recorded_implies_verified": False,
            "fetched_implies_relied": False,
            "changed_implies_invalid": False,
            "stale_implies_false": False,
            "lineage_implies_affectedness_verdict": False,
        },
    }
