from __future__ import annotations

import argparse
import json
import re
from collections import deque
from pathlib import Path
from typing import Any

FORMAT = "mathhub-lean-import-audit-v1"
IMPORT_LINE_RE = re.compile(r"^\s*(?:(?:public|private)\s+)?import\s+(.+?)\s*$")
MODULE_RE = re.compile(r"^[A-Za-z0-9_.«»'_-]+$")


def module_path(module: str) -> Path:
    value = str(module or "").strip()
    if not value or not MODULE_RE.fullmatch(value):
        raise ValueError(f"unsupported Lean module name: {module!r}")
    return Path(*value.split(".")).with_suffix(".lean")


def parse_imports(source: str) -> list[str]:
    """Read ordinary top-level Lean import commands without interpreting code.

    Lean import commands are line-oriented in the projects audited here. Multiple
    modules after one `import` are accepted. Comments on import lines are stripped
    conservatively; this is a source-import audit, not a Lean parser replacement.
    """
    imports: list[str] = []
    in_block_comment = False
    for raw in str(source or "").splitlines():
        line = raw
        # Minimal block-comment handling so documentation examples do not become
        # import edges. Nested Lean comments are deliberately not implemented here;
        # any ambiguous line is rejected rather than silently trusted.
        if in_block_comment:
            if "-/" in line:
                line = line.split("-/", 1)[1]
                in_block_comment = False
            else:
                continue
        if "/-" in line:
            before, after = line.split("/-", 1)
            line = before
            if "-/" not in after:
                in_block_comment = True
        line = line.split("--", 1)[0].strip()
        if not line:
            continue
        match = IMPORT_LINE_RE.match(line)
        if not match:
            continue
        for token in match.group(1).split():
            token = token.strip()
            if not MODULE_RE.fullmatch(token):
                raise ValueError(f"ambiguous/unsupported import token: {token!r}")
            if token not in imports:
                imports.append(token)
    return imports


def local_module_file(checkout: Path, module: str) -> Path | None:
    candidate = Path(checkout) / module_path(module)
    return candidate if candidate.is_file() else None


def build_local_import_closure(
    checkout: Path,
    root_module: str,
    *,
    forbidden_prefixes: list[str] | tuple[str, ...] = (),
    max_modules: int = 5000,
) -> dict[str, Any]:
    checkout = Path(checkout).resolve()
    if max_modules < 1:
        raise ValueError("max_modules must be >= 1")
    root_file = local_module_file(checkout, root_module)
    if root_file is None:
        raise FileNotFoundError(f"root module not found in checkout: {root_module}")

    forbidden = tuple(str(x).strip() for x in forbidden_prefixes if str(x).strip())
    queue = deque([root_module])
    seen: set[str] = set()
    edges: list[dict[str, Any]] = []
    modules: list[dict[str, Any]] = []
    unresolved_external: set[str] = set()
    forbidden_hits: list[dict[str, str]] = []
    truncated = False

    while queue:
        module = queue.popleft()
        if module in seen:
            continue
        if len(seen) >= max_modules:
            truncated = True
            break
        seen.add(module)
        path = local_module_file(checkout, module)
        if path is None:
            unresolved_external.add(module)
            continue
        source = path.read_text(encoding="utf-8")
        direct = parse_imports(source)
        modules.append({
            "module": module,
            "path": str(path.relative_to(checkout)),
            "imports": direct,
        })
        for dep in direct:
            local = local_module_file(checkout, dep) is not None
            edges.append({"from": module, "to": dep, "local": local})
            if any(dep == prefix or dep.startswith(prefix + ".") for prefix in forbidden):
                forbidden_hits.append({"from": module, "forbidden_module": dep})
            if local:
                queue.append(dep)
            else:
                unresolved_external.add(dep)

    return {
        "format": FORMAT,
        "root_module": root_module,
        "complete_local_closure": not truncated,
        "truncated": truncated,
        "max_modules": max_modules,
        "local_module_count": len(modules),
        "edge_count": len(edges),
        "modules": sorted(modules, key=lambda x: x["module"]),
        "edges": edges,
        "unresolved_external_imports": sorted(unresolved_external),
        "forbidden_prefixes": list(forbidden),
        "forbidden_hits": forbidden_hits,
        "forbidden_reachable": bool(forbidden_hits),
        "interpretation": (
            "forbidden local module reachable from source import closure"
            if forbidden_hits else
            "no forbidden module found in repository-local source import closure"
        ),
        "limitations": [
            "This audits repository-local Lean source imports, not transitive imports inside external Lake dependencies.",
            "A clean source import closure does not replace proof-expression dependency or axiom audits.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit repository-local Lean import closure")
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--root-module", required=True)
    parser.add_argument("--forbid-prefix", action="append", default=[])
    parser.add_argument("--max-modules", type=int, default=5000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = build_local_import_closure(
        args.checkout,
        args.root_module,
        forbidden_prefixes=args.forbid_prefix,
        max_modules=args.max_modules,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 1 if result["forbidden_reachable"] or result["truncated"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
