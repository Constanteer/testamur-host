from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True, slots=True)
class NamespaceViolation:
    path: str
    line: int
    target: str
    import_kind: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.import_kind} {self.target}"


def _is_forbidden(target: str, *, forbidden_roots: frozenset[str]) -> bool:
    return any(target == root or target.startswith(root + ".") for root in forbidden_roots)


def scan_python_namespace_dependencies(
    package_root: str | Path,
    *,
    forbidden_roots: Iterable[str] = ("witness", "witness_service"),
) -> tuple[NamespaceViolation, ...]:
    """Return deterministic forbidden namespace dependencies for Python input.

    ``package_root`` may be either a Python file or a directory tree. The scan is
    syntax-only: static imports and literal ``__import__``/``import_module``
    targets are detected without importing code. Supporting a single file keeps
    release checks for transition entrypoints (for example ``mathhub.py``)
    precise instead of forcing an expensive repository-wide parse.
    """
    root = Path(package_root)
    forbidden = frozenset(str(value).strip() for value in forbidden_roots if str(value).strip())
    violations: list[NamespaceViolation] = []

    if root.is_file():
        paths = (root,)
        relative_base = root.parent
    elif root.exists():
        paths = tuple(sorted(root.rglob("*.py")))
        relative_base = root
    else:
        return ()

    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(relative_base).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_forbidden(alias.name, forbidden_roots=forbidden):
                        violations.append(NamespaceViolation(relative, node.lineno, alias.name, "import"))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if _is_forbidden(module, forbidden_roots=forbidden):
                    violations.append(NamespaceViolation(relative, node.lineno, module, "from"))
            elif isinstance(node, ast.Call) and node.args:
                function_name = ""
                if isinstance(node.func, ast.Name):
                    function_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    function_name = node.func.attr
                if function_name not in {"__import__", "import_module"}:
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    target = first.value
                    if _is_forbidden(target, forbidden_roots=forbidden):
                        violations.append(NamespaceViolation(relative, node.lineno, target, "dynamic import"))

    return tuple(sorted(violations, key=lambda item: (item.path, item.line, item.import_kind, item.target)))


def assert_python_namespace_clean(
    package_root: str | Path,
    *,
    forbidden_roots: Iterable[str] = ("witness", "witness_service"),
) -> None:
    violations = scan_python_namespace_dependencies(package_root, forbidden_roots=forbidden_roots)
    if violations:
        rendered = "\n".join(item.render() for item in violations)
        raise RuntimeError("release-blocking reverse namespace edges remain:\n" + rendered)
