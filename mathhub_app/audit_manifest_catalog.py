from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from external_audit_ingest import ingest_manifest, validate_manifest


class AuditManifestCatalog:
    """Discover only repository-owned manifests under audits/.

    This is intentionally not an arbitrary path loader. The product UI may bootstrap
    a checked-in case without granting filesystem read access to clients.
    """

    def __init__(self, root: Path, db_path: Path):
        self.root = Path(root).resolve()
        self.audit_dir = self.root / "audits"
        self.db_path = Path(db_path)

    def list(self) -> dict[str, Any]:
        manifests = []
        if self.audit_dir.is_dir():
            for path in sorted(self.audit_dir.glob("*.json")):
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    if raw.get("schema") != "mathhub-external-audit-manifest-v1":
                        continue
                    validate_manifest(raw)
                except Exception:
                    continue
                manifests.append({
                    "case_key": raw["case_key"],
                    "title": raw.get("audit_case", {}).get("title") or raw.get("claim", {}).get("title"),
                    "claim_title": raw.get("claim", {}).get("title"),
                    "repository": raw.get("formal_source", {}).get("repository"),
                    "revision": raw.get("formal_source", {}).get("revision"),
                    "path": path.relative_to(self.root).as_posix(),
                })
        return {"schema": "mathhub-audit-manifest-catalog-v1", "manifests": manifests}

    def get(self, case_key: str) -> dict[str, Any]:
        wanted = str(case_key or "").strip()
        if not wanted:
            raise ValueError("case_key is required")
        for item in self.list()["manifests"]:
            if item["case_key"] == wanted:
                path = (self.root / item["path"]).resolve()
                if self.audit_dir not in path.parents:
                    raise ValueError("manifest path escaped audits directory")
                raw = json.loads(path.read_text(encoding="utf-8"))
                validate_manifest(raw)
                return raw
        raise KeyError(wanted)

    def bootstrap(self, case_key: str) -> dict[str, Any]:
        manifest = self.get(case_key)
        result = ingest_manifest(self.db_path, manifest)
        return {
            "schema": "mathhub-audit-bootstrap-v1",
            "case_key": case_key,
            **result,
        }
