from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .component_identity import (
    ComponentIdentity,
    ComponentRevisionIdentity,
    canonical_hash,
    canonical_json,
)

if TYPE_CHECKING:
    from .product_service import TestamurProductService


_IGNORED_PARTS = {
    ".git",
    ".hg",
    ".svn",
    ".testamur",
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    "target",
    "dist",
    "build",
}

_REQUIREMENT_PIN = re.compile(
    r"^\s*([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?\s*==\s*([^\s;]+)"
)


@dataclass(frozen=True, slots=True)
class DependencyObservation:
    ecosystem: str
    name: str
    version: str
    manifest: str
    digest: str = ""
    locator: str = ""
    direct: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def component_revision(self) -> ComponentRevisionIdentity:
        component = ComponentIdentity.create(
            kind="software-package",
            namespace=self.ecosystem,
            name=self.name,
            external_ids={"ecosystem": self.ecosystem},
        )
        return ComponentRevisionIdentity.create(
            component=component,
            version=self.version or None,
            digest=self.digest or None,
            locator=self.locator or None,
        )

    def as_dict(self) -> dict[str, Any]:
        revision = self.component_revision().as_dict()
        return {
            "ecosystem": self.ecosystem,
            "name": self.name,
            "version": self.version,
            "digest": self.digest,
            "locator": self.locator,
            "manifest": self.manifest,
            "direct": self.direct,
            "metadata": dict(self.metadata),
            "component_revision": revision,
            "semantics": {
                "manifest_observation_is_dependency_declaration": True,
                "declared_version_is_exact_content_revision": revision["is_exact_revision"],
                "name_or_version_match_implies_affectedness": False,
            },
        }


@dataclass(frozen=True, slots=True)
class ManifestObservation:
    path: str
    parser: str
    sha256: str
    dependencies: tuple[DependencyObservation, ...]
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "parser": self.parser,
            "sha256": self.sha256,
            "dependency_count": len(self.dependencies),
            "warnings": list(self.warnings),
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _ignored(root: Path, path: Path) -> bool:
    relative = path.relative_to(root)
    return any(part in _IGNORED_PARTS for part in relative.parts)


def discover_dependency_manifests(root: str | Path, *, limit: int = 200) -> list[Path]:
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise ValueError(f"project import path is not a directory: {base}")

    found: set[Path] = set()
    fixed = (
        "uv.lock",
        "poetry.lock",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "Cargo.lock",
        "go.sum",
    )
    for name in fixed:
        for path in base.rglob(name):
            if path.is_file() and not _ignored(base, path):
                found.add(path)
                if len(found) >= limit:
                    return sorted(found)

    for path in base.rglob("requirements*.txt"):
        if path.is_file() and not _ignored(base, path):
            found.add(path)
            if len(found) >= limit:
                break
    return sorted(found)


def _parse_requirements(root: Path, path: Path) -> ManifestObservation:
    dependencies: list[DependencyObservation] = []
    warnings: list[str] = []
    rel = _relative(root, path)
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        match = _REQUIREMENT_PIN.match(line)
        if match is None:
            warnings.append(f"{rel}:{number}: skipped unpinned/unsupported requirement")
            continue
        dependencies.append(
            DependencyObservation(
                ecosystem="pypi",
                name=match.group(1),
                version=match.group(2),
                manifest=rel,
                direct=True,
                metadata={"line": number},
            )
        )
    return ManifestObservation(
        path=rel,
        parser="requirements",
        sha256=_sha256(path),
        dependencies=tuple(dependencies),
        warnings=tuple(warnings),
    )


def _toml_package_digest(package: dict[str, Any]) -> str:
    checksum = package.get("checksum")
    if isinstance(checksum, str) and checksum.strip():
        return checksum.strip()

    for key in ("sdist",):
        entry = package.get(key)
        if isinstance(entry, dict):
            value = entry.get("hash")
            if isinstance(value, str) and value.strip():
                return value.strip()

    files = package.get("files")
    if isinstance(files, list):
        for entry in files:
            if isinstance(entry, dict):
                value = entry.get("hash")
                if isinstance(value, str) and value.strip():
                    return value.strip()

    wheels = package.get("wheels")
    if isinstance(wheels, list):
        for entry in wheels:
            if isinstance(entry, dict):
                value = entry.get("hash")
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return ""


def _source_locator(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("url", "git", "registry", "path", "source"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
    return ""


def _parse_toml_lock(root: Path, path: Path) -> ManifestObservation:
    with path.open("rb") as handle:
        payload = tomllib.load(handle)

    rel = _relative(root, path)
    if path.name == "Cargo.lock":
        ecosystem = "cargo"
        parser = "cargo-lock"
    elif path.name == "uv.lock":
        ecosystem = "pypi"
        parser = "uv-lock"
    else:
        ecosystem = "pypi"
        parser = "poetry-lock"

    raw_packages = payload.get("package", [])
    dependencies: list[DependencyObservation] = []
    warnings: list[str] = []
    if not isinstance(raw_packages, list):
        warnings.append(f"{rel}: package collection is not a list")
        raw_packages = []

    for index, package in enumerate(raw_packages):
        if not isinstance(package, dict):
            warnings.append(f"{rel}: package[{index}] is not an object")
            continue
        name = package.get("name")
        version = package.get("version")
        if not isinstance(name, str) or not name.strip():
            warnings.append(f"{rel}: package[{index}] missing name")
            continue
        if not isinstance(version, str) or not version.strip():
            warnings.append(f"{rel}: {name} missing version")
            continue
        source = package.get("source")
        dependencies.append(
            DependencyObservation(
                ecosystem=ecosystem,
                name=name.strip(),
                version=version.strip(),
                digest=_toml_package_digest(package),
                locator=_source_locator(source),
                manifest=rel,
                direct=None,
                metadata={"lock_index": index},
            )
        )

    return ManifestObservation(
        path=rel,
        parser=parser,
        sha256=_sha256(path),
        dependencies=tuple(dependencies),
        warnings=tuple(warnings),
    )


def _package_lock_name(package_path: str, package: dict[str, Any]) -> str:
    explicit = package.get("name")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    marker = "node_modules/"
    if marker not in package_path:
        return ""
    return package_path.rsplit(marker, 1)[1].strip("/")


def _parse_package_lock(root: Path, path: Path) -> ManifestObservation:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rel = _relative(root, path)
    packages = payload.get("packages")
    warnings: list[str] = []
    dependencies: list[DependencyObservation] = []

    if not isinstance(packages, dict):
        warnings.append(f"{rel}: packages map missing; lockfile version is unsupported")
        packages = {}

    root_package = packages.get("")
    direct_names: set[str] = set()
    if isinstance(root_package, dict):
        for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            value = root_package.get(key)
            if isinstance(value, dict):
                direct_names.update(str(name) for name in value)

    for package_path, package in packages.items():
        if package_path == "" or not isinstance(package, dict):
            continue
        name = _package_lock_name(str(package_path), package)
        version = package.get("version")
        if not name or not isinstance(version, str) or not version.strip():
            continue
        integrity = package.get("integrity")
        resolved = package.get("resolved")
        dependencies.append(
            DependencyObservation(
                ecosystem="npm",
                name=name,
                version=version.strip(),
                digest=integrity.strip() if isinstance(integrity, str) else "",
                locator=resolved.strip() if isinstance(resolved, str) else "",
                manifest=rel,
                direct=name in direct_names,
                metadata={"lock_path": str(package_path)},
            )
        )

    return ManifestObservation(
        path=rel,
        parser="package-lock",
        sha256=_sha256(path),
        dependencies=tuple(dependencies),
        warnings=tuple(warnings),
    )


def _parse_go_sum(root: Path, path: Path) -> ManifestObservation:
    rel = _relative(root, path)
    selected: dict[tuple[str, str], DependencyObservation] = {}
    warnings: list[str] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        fields = raw.split()
        if len(fields) != 3:
            if raw.strip():
                warnings.append(f"{rel}:{number}: malformed go.sum entry")
            continue
        module, version, digest = fields
        go_mod_only = version.endswith("/go.mod")
        normalized_version = version.removesuffix("/go.mod")
        key = (module, normalized_version)
        observation = DependencyObservation(
            ecosystem="go",
            name=module,
            version=normalized_version,
            digest=digest,
            manifest=rel,
            direct=None,
            metadata={"line": number, "go_mod_only": go_mod_only},
        )
        existing = selected.get(key)
        if existing is None or (
            bool(existing.metadata.get("go_mod_only")) and not go_mod_only
        ):
            selected[key] = observation

    return ManifestObservation(
        path=rel,
        parser="go-sum",
        sha256=_sha256(path),
        dependencies=tuple(selected[key] for key in sorted(selected)),
        warnings=tuple(warnings),
    )


def parse_dependency_manifest(root: str | Path, path: str | Path) -> ManifestObservation:
    base = Path(root).expanduser().resolve()
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise ValueError(f"dependency manifest does not exist: {target}")
    if base not in target.parents and target != base:
        raise ValueError("dependency manifest must be inside project root")

    if target.name.startswith("requirements") and target.suffix == ".txt":
        return _parse_requirements(base, target)
    if target.name in {"uv.lock", "poetry.lock", "Cargo.lock"}:
        return _parse_toml_lock(base, target)
    if target.name in {"package-lock.json", "npm-shrinkwrap.json"}:
        return _parse_package_lock(base, target)
    if target.name == "go.sum":
        return _parse_go_sum(base, target)
    raise ValueError(f"unsupported dependency manifest: {target.name}")


def scan_supply_chain(root: str | Path) -> dict[str, Any]:
    base = Path(root).expanduser().resolve()
    manifests = [
        parse_dependency_manifest(base, path)
        for path in discover_dependency_manifests(base)
    ]

    by_revision: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for manifest in manifests:
        warnings.extend(manifest.warnings)
        for dependency in manifest.dependencies:
            revision = dependency.component_revision()
            ref = revision.revision_id
            item = by_revision.get(ref)
            if item is None:
                item = {
                    "component_revision_id": ref,
                    "component_revision": revision.as_dict(),
                    "ecosystem": dependency.ecosystem,
                    "name": dependency.name,
                    "version": dependency.version,
                    "digest": dependency.digest,
                    "locator": dependency.locator,
                    "direct": dependency.direct,
                    "observed_in": [dependency.manifest],
                }
                by_revision[ref] = item
            else:
                item["observed_in"] = sorted(
                    set(item["observed_in"]) | {dependency.manifest}
                )
                if dependency.direct is True:
                    item["direct"] = True

    return {
        "schema": "testamur.supply-chain.scan.v1",
        "root": base.name,
        "manifests": [manifest.as_dict() for manifest in manifests],
        "dependencies": [by_revision[key] for key in sorted(by_revision)],
        "warnings": sorted(set(warnings)),
        "semantics": {
            "manifest_declaration_is_not_runtime_use": True,
            "declared_version_is_not_automatically_exact_content": True,
            "name_or_version_match_implies_affectedness": False,
            "advisory_match_requires_resolution_and_affectedness": True,
        },
    }


def _persistent_record_id(kind: str, key: str) -> str:
    return "tst:record:" + canonical_hash(
        {"testamur_supply_chain_record": kind, "key": key}
    )


def _ensure_record_revision(
    service: "TestamurProductService",
    *,
    record_id: str,
    record_kind: str,
    statement: str,
    basis: list[dict[str, Any]],
    title: str,
) -> tuple[dict[str, Any], bool]:
    existing = service.records.get_record(record_id)
    if existing is None:
        created = service.records.create_record(
            record_kind=record_kind,
            statement=statement,
            basis=basis,
            title=title,
            created_by="testamur:supply-chain-import",
            record_id=record_id,
        )
        return created["revision"], True

    latest = service.records.latest_revision(record_id)
    if latest is None:
        raise RuntimeError(f"record has no revision: {record_id}")
    if latest.get("statement") == statement and latest.get("basis") == basis:
        return latest, False

    revision = service.records.append_revision(
        record_id,
        expected_parent_revision_id=latest["revision_id"],
        statement=statement,
        basis=basis,
        title=title,
        created_by="testamur:supply-chain-import",
    )
    return revision, True


def _ensure_relation(
    service: "TestamurProductService",
    relation_type: str,
    *,
    from_ref: str,
    to_ref: str,
    basis: list[dict[str, Any]],
) -> dict[str, Any]:
    for relation in service.records.relations_for(
        from_ref,
        direction="outgoing",
        relation_types=[relation_type],
        limit=500,
    ):
        if relation.get("to_ref") == to_ref:
            return relation
    return service.records.create_relation(
        relation_type,
        from_ref=from_ref,
        to_ref=to_ref,
        basis=basis,
        created_by="testamur:supply-chain-import",
    )


def _find_or_create_project(
    service: "TestamurProductService",
    *,
    name: str,
    visibility: str,
) -> tuple[dict[str, Any], bool]:
    direct = service.projects.get_project(name)
    if direct is not None:
        return direct, False
    normalized = name.strip().casefold()
    for project in service.projects.list_projects():
        if str(project.get("name") or "").strip().casefold() == normalized:
            return project, False
    return (
        service.projects.create_project(
            name=name,
            description="Imported software supply-chain inventory",
            visibility=visibility,
        ),
        True,
    )


def import_project_supply_chain(
    service: "TestamurProductService",
    root: str | Path,
    *,
    project_name: str | None = None,
    visibility: str = "private",
) -> dict[str, Any]:
    base = Path(root).expanduser().resolve()
    scan = scan_supply_chain(base)
    name = (project_name or base.name).strip()
    if not name:
        raise ValueError("project import requires a project name")

    project, project_created = _find_or_create_project(
        service, name=name, visibility=visibility
    )
    project_id = str(project["project_id"])

    manifest_revision_by_path: dict[str, dict[str, Any]] = {}
    manifest_changed = 0
    manifest_dependencies: dict[str, list[str]] = {}
    for dependency in scan["dependencies"]:
        for manifest_path in dependency["observed_in"]:
            manifest_dependencies.setdefault(manifest_path, []).append(
                dependency["component_revision_id"]
            )

    for manifest in scan["manifests"]:
        path = str(manifest["path"])
        statement = canonical_json(
            {
                "schema": "testamur.supply-chain.manifest.v1",
                "project_id": project_id,
                "path": path,
                "parser": manifest["parser"],
                "sha256": manifest["sha256"],
                "dependency_revision_ids": sorted(
                    manifest_dependencies.get(path, [])
                ),
                "semantics": {
                    "manifest_is_declared_dependency_evidence": True,
                    "manifest_digest_is_exact_local_bytes": True,
                },
            }
        )
        basis = [
            {
                "kind": "file_digest",
                "ref": f"sha256:{manifest['sha256']}",
                "path": path,
            }
        ]
        record_id = _persistent_record_id(
            "dependency-manifest", f"{project_id}:{path}"
        )
        revision, changed = _ensure_record_revision(
            service,
            record_id=record_id,
            record_kind="dependency-manifest",
            statement=statement,
            basis=basis,
            title=f"Dependency manifest: {path}",
        )
        manifest_revision_by_path[path] = revision
        manifest_changed += int(changed)

    dependency_results: list[dict[str, Any]] = []
    dependency_changed = 0
    for dependency in scan["dependencies"]:
        manifest_basis = [
            {
                "kind": "dependency_manifest_revision",
                "ref": manifest_revision_by_path[path]["revision_id"],
                "path": path,
            }
            for path in dependency["observed_in"]
            if path in manifest_revision_by_path
        ]
        statement = canonical_json(
            {
                "schema": "testamur.supply-chain.dependency.v1",
                "project_id": project_id,
                "component_revision": dependency["component_revision"],
                "direct": dependency["direct"],
                "observed_in": dependency["observed_in"],
                "semantics": {
                    "manifest_declaration_is_runtime_use": False,
                    "version_match_implies_content_match": False,
                    "identity_match_implies_affectedness": False,
                },
            }
        )
        record_id = _persistent_record_id(
            "project-dependency",
            f"{project_id}:{dependency['component_revision_id']}",
        )
        revision, changed = _ensure_record_revision(
            service,
            record_id=record_id,
            record_kind="project-dependency",
            statement=statement,
            basis=manifest_basis,
            title=f"{dependency['ecosystem']}:{dependency['name']}@{dependency['version']}",
        )
        dependency_changed += int(changed)
        dependency_results.append(
            {
                **dependency,
                "record_id": record_id,
                "record_revision_id": revision["revision_id"],
            }
        )

    scan_basis = [
        {
            "kind": "dependency_manifest_revision",
            "ref": revision["revision_id"],
            "path": path,
        }
        for path, revision in sorted(manifest_revision_by_path.items())
    ]
    scan_statement = canonical_json(
        {
            "schema": "testamur.supply-chain.project-scan.v1",
            "project_id": project_id,
            "root": scan["root"],
            "manifest_revision_ids": [
                item["ref"] for item in scan_basis
            ],
            "dependency_record_revision_ids": sorted(
                item["record_revision_id"] for item in dependency_results
            ),
            "warnings": scan["warnings"],
            "semantics": scan["semantics"],
        }
    )
    scan_record_id = _persistent_record_id("project-scan", project_id)
    scan_revision, scan_changed = _ensure_record_revision(
        service,
        record_id=scan_record_id,
        record_kind="supply-chain-scan",
        statement=scan_statement,
        basis=scan_basis,
        title=f"Supply-chain scan: {project['name']}",
    )

    project_relation = _ensure_relation(
        service,
        "cites",
        from_ref=project_id,
        to_ref=scan_revision["revision_id"],
        basis=[
            {
                "kind": "supply_chain_scan",
                "ref": scan_revision["revision_id"],
            }
        ],
    )
    dependency_relations: list[str] = []
    for dependency in dependency_results:
        relation = _ensure_relation(
            service,
            "depends-on",
            from_ref=scan_revision["revision_id"],
            to_ref=dependency["record_revision_id"],
            basis=[
                {
                    "kind": "manifest_declaration",
                    "ref": manifest_revision_by_path[path]["revision_id"],
                    "path": path,
                }
                for path in dependency["observed_in"]
                if path in manifest_revision_by_path
            ],
        )
        dependency_relations.append(str(relation["relation_id"]))

    return {
        "ok": True,
        "schema": "testamur.supply-chain.import.v1",
        "project": project,
        "project_created": project_created,
        "scan_record_id": scan_record_id,
        "scan_revision_id": scan_revision["revision_id"],
        "scan_revision_created": bool(scan_changed),
        "project_scan_relation_id": project_relation["relation_id"],
        "manifest_count": len(scan["manifests"]),
        "manifest_revisions_created": manifest_changed,
        "dependency_count": len(dependency_results),
        "dependency_revisions_created": dependency_changed,
        "dependency_relation_ids": dependency_relations,
        "manifests": scan["manifests"],
        "dependencies": dependency_results,
        "warnings": scan["warnings"],
        "semantics": scan["semantics"],
    }
