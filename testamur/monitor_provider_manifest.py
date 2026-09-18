from __future__ import annotations

import json
import os
import re
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from .product_extensions import MonitorTargetProvider


_SCHEMA = "testamur.monitor-providers.v1"
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True, slots=True)
class MonitorProviderRegistry:
    providers: Mapping[str, MonitorTargetProvider]
    specs: Mapping[str, Mapping[str, Any]]
    manifests: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


def _manifest_candidates(
    extra_paths: list[str | Path] | None = None,
    *,
    include_defaults: bool = True,
) -> list[Path]:
    candidates: list[Path] = []

    if include_defaults:
        explicit = os.environ.get("TESTAMUR_MONITOR_PROVIDER_MANIFESTS", "")
        for raw in explicit.split(os.pathsep):
            if raw.strip():
                candidates.append(Path(raw).expanduser())

    for raw in extra_paths or []:
        candidates.append(Path(raw).expanduser())

    if include_defaults:
        home = Path(os.environ.get("TESTAMUR_HOME") or Path.home() / ".testamur").expanduser()
        providers_dir = home / "providers"
        if providers_dir.is_dir():
            candidates.extend(sorted(providers_dir.glob("*.json")))

        # Development/repository marketplace discovery. Installed distributions do
        # not rely on this path; plugins can register into TESTAMUR_HOME/providers.
        repo_root = Path(__file__).resolve().parents[1]
        plugin_root = repo_root / "plugins"
        if plugin_root.is_dir():
            candidates.extend(sorted(plugin_root.glob("*/testamur-monitor-providers.json")))

    flattened: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.is_dir():
            values = sorted(candidate.glob("*.json"))
        else:
            values = [candidate]
        for value in values:
            key = str(value.resolve()) if value.exists() else str(value.absolute())
            if key in seen:
                continue
            seen.add(key)
            flattened.append(value)
    return flattened


def _field_spec(name: str, raw: Any) -> dict[str, Any]:
    if not _FIELD_RE.fullmatch(name):
        raise ValueError(f"invalid provider field name: {name}")
    if not isinstance(raw, Mapping):
        raise ValueError(f"provider field {name} must be an object")
    unknown = set(raw) - {"type", "required", "pattern", "label", "placeholder", "description"}
    if unknown:
        raise ValueError(f"provider field {name} has unknown keys: {', '.join(sorted(unknown))}")
    field_type = str(raw.get("type") or "string")
    if field_type != "string":
        raise ValueError(f"provider field {name} only supports type=string")
    required = raw.get("required", True)
    if not isinstance(required, bool):
        raise ValueError(f"provider field {name} required must be boolean")
    pattern = raw.get("pattern")
    if pattern is not None:
        if not isinstance(pattern, str) or len(pattern) > 512:
            raise ValueError(f"provider field {name} pattern must be a bounded string")
        re.compile(pattern)
    spec = {
        "type": "string",
        "required": required,
        "label": str(raw.get("label") or name.replace("_", " ").title()),
    }
    for key in ("pattern", "placeholder", "description"):
        value = raw.get(key)
        if value is not None:
            if not isinstance(value, str):
                raise ValueError(f"provider field {name} {key} must be a string")
            spec[key] = value
    return spec


def _provider_from_spec(raw: Any) -> tuple[str, MonitorTargetProvider, dict[str, Any]]:
    if not isinstance(raw, Mapping):
        raise ValueError("provider entry must be an object")
    unknown = set(raw) - {"name", "label", "description", "locator_template", "fields"}
    if unknown:
        raise ValueError(f"provider entry has unknown keys: {', '.join(sorted(unknown))}")

    name = str(raw.get("name") or "").strip()
    if not _NAME_RE.fullmatch(name):
        raise ValueError(f"invalid provider name: {name!r}")

    label = str(raw.get("label") or name.replace("_", " ").title()).strip()
    description = str(raw.get("description") or "").strip()
    template = str(raw.get("locator_template") or "").strip()
    if not template:
        raise ValueError(f"provider {name} requires locator_template")
    template_url = urlsplit(template)
    if template_url.scheme != "https" or not template_url.hostname:
        raise ValueError(f"provider {name} locator_template must use an absolute HTTPS URL")
    if "{" in template_url.hostname or "}" in template_url.hostname:
        raise ValueError(f"provider {name} locator_template host must be literal")

    raw_fields = raw.get("fields")
    if not isinstance(raw_fields, Mapping) or not raw_fields:
        raise ValueError(f"provider {name} requires a non-empty fields object")
    fields = {str(key): _field_spec(str(key), value) for key, value in raw_fields.items()}

    placeholders: list[str] = []
    try:
        for _literal, field_name, format_spec, conversion in string.Formatter().parse(template):
            if field_name is None:
                continue
            if format_spec or conversion:
                raise ValueError("format specifiers and conversions are not allowed")
            if field_name not in fields:
                raise ValueError(f"locator_template references undeclared field {field_name}")
            placeholders.append(field_name)
    except ValueError:
        raise
    if not placeholders:
        raise ValueError(f"provider {name} locator_template must reference at least one field")

    def resolve(config: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(config, Mapping):
            raise ValueError("provider config must be an object")
        unknown_config = set(config) - set(fields)
        if unknown_config:
            raise ValueError(f"unknown provider config fields: {', '.join(sorted(unknown_config))}")
        values: dict[str, str] = {}
        for field_name, spec in fields.items():
            raw_value = config.get(field_name)
            if raw_value is None:
                if spec["required"]:
                    raise ValueError(f"{field_name} is required")
                values[field_name] = ""
                continue
            if not isinstance(raw_value, str):
                raise ValueError(f"{field_name} must be a string")
            value = raw_value.strip()
            if spec["required"] and not value:
                raise ValueError(f"{field_name} must not be empty")
            pattern = spec.get("pattern")
            if pattern and not re.fullmatch(str(pattern), value):
                raise ValueError(f"{field_name} does not match the provider pattern")
            values[field_name] = value

        locator = template.format(**values)
        parsed = urlsplit(locator)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("declarative monitor providers must resolve to an absolute HTTPS locator")
        return {"locator": locator}

    public_spec: dict[str, Any] = {
        "name": name,
        "label": label,
        "description": description,
        "fields": fields,
    }
    return name, resolve, public_spec


def load_monitor_provider_registry(
    extra_paths: list[str | Path] | None = None,
    *,
    include_defaults: bool = True,
) -> MonitorProviderRegistry:
    providers: dict[str, MonitorTargetProvider] = {}
    specs: dict[str, Mapping[str, Any]] = {}
    manifests: list[str] = []
    errors: list[str] = []

    for path in _manifest_candidates(extra_paths, include_defaults=include_defaults):
        if not path.is_file():
            errors.append(f"{path}: manifest does not exist")
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, Mapping):
                raise ValueError("manifest must be a JSON object")
            if value.get("schema") != _SCHEMA:
                raise ValueError(f"manifest schema must be {_SCHEMA}")
            entries = value.get("providers")
            if not isinstance(entries, list):
                raise ValueError("manifest providers must be an array")
            local: list[tuple[str, MonitorTargetProvider, dict[str, Any]]] = [
                _provider_from_spec(entry) for entry in entries
            ]
            for name, provider, spec in local:
                if name in providers:
                    if dict(specs[name]) == spec:
                        continue
                    raise ValueError(f"conflicting duplicate provider name: {name}")
                providers[name] = provider
                specs[name] = spec
            manifests.append(str(path.resolve()))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"{path}: {exc}")

    return MonitorProviderRegistry(
        providers=providers,
        specs=specs,
        manifests=tuple(manifests),
        errors=tuple(errors),
    )
