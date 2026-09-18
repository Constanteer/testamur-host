from __future__ import annotations

import io
import json

from testamur.product_cli import dispatch
from testamur.product_service import TestamurProductService
from testamur.supply_chain import scan_supply_chain


def test_scan_supply_chain_parses_package_lock_and_cargo(tmp_path) -> None:
    (tmp_path / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"dependencies": {"left-pad": "1.3.0"}},
                    "node_modules/left-pad": {
                        "version": "1.3.0",
                        "resolved": "https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz",
                        "integrity": "sha512-example",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "Cargo.lock").write_text(
        """version = 4

[[package]]
name = "serde"
version = "1.0.210"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "abc123"
""",
        encoding="utf-8",
    )

    scan = scan_supply_chain(tmp_path)

    assert scan["schema"] == "testamur.supply-chain.scan.v1"
    assert len(scan["manifests"]) == 2
    deps = {(item["ecosystem"], item["name"]): item for item in scan["dependencies"]}
    assert deps[("npm", "left-pad")]["direct"] is True
    assert deps[("npm", "left-pad")]["component_revision"]["is_exact_revision"] is True
    assert deps[("cargo", "serde")]["component_revision"]["is_exact_revision"] is True


def test_project_import_is_durable_and_idempotent(tmp_path) -> None:
    root = tmp_path / "demo"
    root.mkdir()
    (root / "requirements.txt").write_text(
        "requests==2.32.5\nflask>=3\n",
        encoding="utf-8",
    )
    service = TestamurProductService(tmp_path / "state" / "evidence.db")

    output = io.StringIO()
    code = dispatch(
        ["project", "import", str(root), "--name", "demo"],
        service=service,
        stdout=output,
    )
    assert code == 0
    first = json.loads(output.getvalue())
    assert first["ok"] is True
    assert first["project_created"] is True
    assert first["manifest_count"] == 1
    assert first["dependency_count"] == 1
    assert any("skipped unpinned" in warning for warning in first["warnings"])

    output = io.StringIO()
    code = dispatch(
        ["project", "import", str(root), "--name", "demo"],
        service=service,
        stdout=output,
    )
    assert code == 0
    second = json.loads(output.getvalue())
    assert second["project_created"] is False
    assert second["scan_revision_id"] == first["scan_revision_id"]
    assert second["scan_revision_created"] is False
    assert second["dependency_revisions_created"] == 0

    project = service.project("demo")
    assert project["ok"] is True
    inventory = project["supply_chain"]
    assert inventory["scan_revision_id"] == first["scan_revision_id"]
    assert inventory["dependency_count"] == 1
    assert inventory["manifest_count"] == 1
    assert inventory["inventory_truncated"] is False
    assert inventory["manifests"][0]["path"] == "requirements.txt"
    assert inventory["manifests"][0]["parser"] == "requirements"
    dependency = inventory["dependencies"][0]
    assert dependency["ecosystem"] == "pypi"
    assert dependency["name"] == "requests"
    assert dependency["version"] == "2.32.5"
    assert dependency["direct"] is True
    assert dependency["observed_in"] == ["requirements.txt"]
    assert dependency["identity_strength"] == "declared-version"
    assert dependency["is_exact_revision"] is False


def test_manifest_change_creates_new_scan_revision(tmp_path) -> None:
    root = tmp_path / "demo"
    root.mkdir()
    requirements = root / "requirements.txt"
    requirements.write_text("requests==2.32.5\n", encoding="utf-8")
    service = TestamurProductService(tmp_path / "state" / "evidence.db")

    first = json.loads(
        _dispatch_json(service, ["project", "import", str(root), "--name", "demo"])
    )
    requirements.write_text("requests==2.32.5\nflask==3.1.2\n", encoding="utf-8")
    second = json.loads(
        _dispatch_json(service, ["project", "import", str(root), "--name", "demo"])
    )

    assert second["scan_revision_id"] != first["scan_revision_id"]
    assert second["scan_revision_created"] is True
    assert second["dependency_count"] == 2


def _dispatch_json(service: TestamurProductService, argv: list[str]) -> str:
    output = io.StringIO()
    assert dispatch(argv, service=service, stdout=output) == 0
    return output.getvalue()


def test_project_supply_chain_projects_only_exact_advisory_candidates(tmp_path) -> None:
    root = tmp_path / "demo"
    root.mkdir()
    (root / "requirements.txt").write_text("requests==2.32.5\n", encoding="utf-8")
    service = TestamurProductService(tmp_path / "state" / "evidence.db")

    imported = json.loads(
        _dispatch_json(service, ["project", "import", str(root), "--name", "demo"])
    )
    component_revision_id = imported["dependencies"][0]["component_revision_id"]

    exact = service.advisories.record_adverse_event(
        provider="osv",
        external_id="OSV-EXACT-1",
        event_class="VULNERABILITY_ADVISORY",
        upstream_refs=[component_revision_id],
        source_refs=["tst:source:osv-exact"],
        known_affected={"provider_declared": True},
    )
    service.advisories.record_adverse_event(
        provider="osv",
        external_id="OSV-UNRESOLVED-1",
        event_class="VULNERABILITY_ADVISORY",
        upstream_identity={
            "provider_model": "osv",
            "affected_packages": [
                {"package": {"ecosystem": "PyPI", "name": "requests"}, "versions": ["2.32.5"]}
            ],
        },
        source_refs=["tst:source:osv-unresolved"],
        known_affected={"provider_declared_versions": [{"name": "requests", "version": "2.32.5"}]},
    )

    project = service.project("demo")
    supply = project["supply_chain"]
    assert supply["advisory_candidate_count"] == 1
    assert supply["unresolved_advisory_count"] == 1
    candidate = supply["advisory_candidates"][0]
    assert candidate["event_revision_id"] == exact["event_revision_id"]
    assert candidate["external_id"] == "OSV-EXACT-1"
    assert candidate["matching_component_revision_ids"] == [component_revision_id]
    assert candidate["status"] == "exact_identity_overlap"
    assert candidate["semantics"]["exact_identity_overlap_is_affectedness_verdict"] is False
    assert candidate["semantics"]["applicability_assessment_required"] is True
    assert supply["semantics"]["unresolved_identity_is_not_fuzzy_matched"] is True
