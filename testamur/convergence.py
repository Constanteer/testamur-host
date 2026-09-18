from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Iterable


_SKIP_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
}


class MigrationAction(StrEnum):
    """Recommended repository action for one remaining legacy import."""

    MIGRATE = "migrate"
    SHIM = "shim"
    DELETE = "delete"
    ALLOWED = "allowed"


class LegacyModuleState(StrEnum):
    """Mechanical state of one Python file in the legacy namespace."""

    SHIM = "shim"
    IMPLEMENTATION = "implementation"
    UNREADABLE = "unreadable"


@dataclass(frozen=True, slots=True)
class LegacyImport:
    path: str
    line: int
    module: str
    import_kind: str
    scope: str

    @property
    def recommended_action(self) -> MigrationAction:
        # Any absolute witness->witness dependency inside the compatibility
        # package is implementation that still needs to disappear. A true shim
        # points one-way to Testamur and therefore does not appear in this scan.
        if self.scope == "legacy_package":
            return MigrationAction.DELETE
        return MigrationAction.MIGRATE

    @property
    def release_blocking(self) -> bool:
        # Reverse edges from canonical Testamur are immediately forbidden. Other
        # consumers remain tracked migration debt until the final legacy-package
        # deletion gate is enabled.
        return self.scope == "canonical_package"

    def to_json(self) -> dict[str, object]:
        return {
            "path": self.path,
            "line": self.line,
            "module": self.module,
            "import_kind": self.import_kind,
            "scope": self.scope,
            "recommended_action": self.recommended_action.value,
            "release_blocking": self.release_blocking,
        }


@dataclass(frozen=True, slots=True)
class LegacyModule:
    path: str
    state: LegacyModuleState
    reasons: tuple[str, ...]

    @property
    def deletion_blocking(self) -> bool:
        return self.state is not LegacyModuleState.SHIM

    def to_json(self) -> dict[str, object]:
        return {
            "path": self.path,
            "state": self.state.value,
            "reasons": list(self.reasons),
            "deletion_blocking": self.deletion_blocking,
        }


def classify_path(path: Path) -> str:
    """Classify a repository-relative Python consumer for migration planning."""

    parts = path.parts
    head = parts[0] if parts else ""
    if head == "witness":
        return "legacy_package"
    if head == "testamur":
        return "canonical_package"
    if head == "tests":
        return "test"
    if head in {"scripts", "tools"}:
        return "tool"
    if head in {"plugins", "integrations"}:
        return "integration"
    return "production_or_other"


def _is_witness_module(module: str) -> bool:
    return module == "witness" or module.startswith("witness.")


def _literal_module_from_call(node: ast.Call) -> tuple[str, str] | None:
    """Recognize simple dynamic imports with literal module names."""

    function = node.func
    kind: str | None = None
    if isinstance(function, ast.Name) and function.id == "__import__":
        kind = "dynamic___import__"
    elif (
        isinstance(function, ast.Attribute)
        and function.attr == "import_module"
        and isinstance(function.value, ast.Name)
        and function.value.id == "importlib"
    ):
        kind = "dynamic_importlib"
    if kind is None or not node.args:
        return None
    first = node.args[0]
    if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
        return None
    return kind, first.value


def scan_legacy_imports(root: Path | str) -> list[LegacyImport]:
    """Return a deterministic inventory of absolute ``witness`` imports.

    The scanner is intentionally syntax-only. It never imports repository code,
    so it is safe to run while the legacy package is partially migrated.
    Relative imports *inside* ``witness/`` are implementation-internal and are
    not reported here; the report tracks consumers coupled to the legacy public
    namespace.
    """

    root_path = Path(root).resolve()
    results: list[LegacyImport] = []
    for path in sorted(root_path.rglob("*.py")):
        relative = path.relative_to(root_path)
        if any(part in _SKIP_DIRS for part in relative.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
        except (OSError, UnicodeError, SyntaxError):
            # Import inventory is best effort; unreadable legacy modules are
            # surfaced separately by ``scan_legacy_modules``.
            continue
        scope = classify_path(relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_witness_module(alias.name):
                        results.append(
                            LegacyImport(
                                str(relative), node.lineno, alias.name, "import", scope
                            )
                        )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level == 0 and _is_witness_module(module):
                    results.append(
                        LegacyImport(
                            str(relative), node.lineno, module, "from", scope
                        )
                    )
            elif isinstance(node, ast.Call):
                dynamic = _literal_module_from_call(node)
                if dynamic is not None and _is_witness_module(dynamic[1]):
                    results.append(
                        LegacyImport(
                            str(relative), node.lineno, dynamic[1], dynamic[0], scope
                        )
                    )
    return sorted(
        results,
        key=lambda item: (item.path, item.line, item.module, item.import_kind),
    )


def _assignment_is_wiring_only(node: ast.Assign | ast.AnnAssign) -> bool:
    value = node.value
    if value is None:
        return True
    # Assigning literals, imported names, attributes or simple containers is
    # compatible with a re-export shim. Calls/comprehensions/operators indicate
    # executable implementation and block deletion readiness.
    return not any(
        isinstance(child, (ast.Call, ast.Lambda, ast.comprehension, ast.BinOp, ast.BoolOp))
        for child in ast.walk(value)
    )


def _classify_legacy_tree(tree: ast.Module) -> tuple[LegacyModuleState, tuple[str, ...]]:
    reasons: list[str] = []
    for index, node in enumerate(tree.body):
        if (
            index == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Assign):
            if _assignment_is_wiring_only(node):
                continue
            reasons.append(f"line {node.lineno}: executable assignment")
            continue
        if isinstance(node, ast.AnnAssign):
            if _assignment_is_wiring_only(node):
                continue
            reasons.append(f"line {node.lineno}: executable annotated assignment")
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            reasons.append(f"line {node.lineno}: {type(node).__name__}")
            continue
        # ``pass`` is harmless in an empty compatibility module. Everything else
        # is conservatively treated as executable legacy implementation.
        if isinstance(node, ast.Pass):
            continue
        reasons.append(f"line {getattr(node, 'lineno', 0)}: {type(node).__name__}")
    if reasons:
        return LegacyModuleState.IMPLEMENTATION, tuple(reasons)
    return LegacyModuleState.SHIM, ()


def scan_legacy_modules(root: Path | str) -> list[LegacyModule]:
    """Classify files under ``witness/`` as compatibility shims or implementation.

    This is deliberately conservative and syntax-only. A module is considered a
    shim only when it contains imports/re-exports/constants and no executable
    functions/classes/logic. This does not prove semantic equivalence; it merely
    establishes that no implementation body remains in the legacy namespace.
    """

    root_path = Path(root).resolve()
    legacy_root = root_path / "witness"
    if not legacy_root.exists():
        return []
    modules: list[LegacyModule] = []
    for path in sorted(legacy_root.rglob("*.py")):
        relative = path.relative_to(root_path)
        if any(part in _SKIP_DIRS for part in relative.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
        except (OSError, UnicodeError, SyntaxError) as exc:
            modules.append(
                LegacyModule(
                    path=str(relative),
                    state=LegacyModuleState.UNREADABLE,
                    reasons=(type(exc).__name__,),
                )
            )
            continue
        state, reasons = _classify_legacy_tree(tree)
        modules.append(LegacyModule(str(relative), state, reasons))
    return modules


def legacy_import_report(root: Path | str) -> dict[str, object]:
    imports = scan_legacy_imports(root)
    by_scope: dict[str, int] = {}
    by_action: dict[str, int] = {}
    release_blocking = 0
    for item in imports:
        by_scope[item.scope] = by_scope.get(item.scope, 0) + 1
        action = item.recommended_action.value
        by_action[action] = by_action.get(action, 0) + 1
        release_blocking += int(item.release_blocking)
    return {
        "schema": "testamur.legacy-import-report.v2",
        "total": len(imports),
        "release_blocking": release_blocking,
        "by_scope": dict(sorted(by_scope.items())),
        "by_action": dict(sorted(by_action.items())),
        "imports": [item.to_json() for item in imports],
    }


def legacy_namespace_readiness(root: Path | str) -> dict[str, object]:
    """Return machine-readable criteria for deleting the ``witness`` package.

    Deletion is ready only when no repository consumer imports the legacy public
    namespace and every remaining module under ``witness/`` is mechanically a
    compatibility shim. Historical ``wtn:*`` identifiers or ``witness_*`` SQLite
    tables do not block deletion because data/wire compatibility is independent
    from the Python package name.
    """

    imports = scan_legacy_imports(root)
    modules = scan_legacy_modules(root)
    consumers = [item for item in imports if item.scope != "legacy_package"]
    implementation_modules = [item for item in modules if item.deletion_blocking]
    return {
        "schema": "testamur.legacy-namespace-readiness.v1",
        "ready_to_delete": not consumers and not implementation_modules,
        "consumer_import_count": len(consumers),
        "implementation_module_count": len(implementation_modules),
        "shim_module_count": sum(item.state is LegacyModuleState.SHIM for item in modules),
        "consumers": [item.to_json() for item in consumers],
        "blocking_modules": [item.to_json() for item in implementation_modules],
        "deletion_criteria": {
            "repository_consumers_zero": not consumers,
            "legacy_implementation_zero": not implementation_modules,
            "historical_ids_may_remain": True,
            "historical_sqlite_names_may_remain": True,
        },
    }


def forbidden_production_imports(imports: Iterable[LegacyImport]) -> list[LegacyImport]:
    """Return reverse namespace edges forbidden in canonical Testamur code."""

    return [item for item in imports if item.release_blocking]


__all__ = [
    "LegacyImport",
    "LegacyModule",
    "LegacyModuleState",
    "MigrationAction",
    "classify_path",
    "forbidden_production_imports",
    "legacy_import_report",
    "legacy_namespace_readiness",
    "scan_legacy_imports",
    "scan_legacy_modules",
]
