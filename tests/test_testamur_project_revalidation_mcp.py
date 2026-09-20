from __future__ import annotations

import unittest
from unittest.mock import patch

from testamur import source_gateway_mcp as base
from testamur import source_gateway_project_mcp as project_mcp


class ProjectRevalidationMcpTests(unittest.TestCase):
    def test_tool_contract_preserves_semantic_boundaries(self) -> None:
        tool = project_mcp.PROJECT_REVALIDATION_TOOL
        self.assertEqual(tool["name"], "testamur.project_advisory_revalidation")
        description = tool["description"].lower()
        self.assertIn("not an invalidity", description)
        self.assertIn("not generic verification", description)

    def test_assessment_tool_accepts_evidence_not_caller_verdict(self) -> None:
        tool = project_mcp.ADVISORY_ASSESSMENT_TOOL
        self.assertEqual(tool["name"], "testamur.record_advisory_assessment")
        properties = tool["inputSchema"]["properties"]
        self.assertIn("evidence", properties)
        self.assertIn("basis", properties)
        self.assertNotIn("verdict", properties)
        self.assertNotIn("state", properties)
        self.assertNotIn("trust_score", properties)
        self.assertFalse(tool["inputSchema"]["additionalProperties"])

    def test_project_assessment_tool_requires_project_candidate_identity(self) -> None:
        tool = project_mcp.PROJECT_ADVISORY_ASSESSMENT_TOOL
        self.assertEqual(tool["name"], "testamur.record_project_advisory_assessment")
        properties = tool["inputSchema"]["properties"]
        self.assertIn("project_ref", properties)
        self.assertNotIn("verdict", properties)
        self.assertNotIn("state", properties)
        self.assertNotIn("trust_score", properties)
        self.assertFalse(tool["inputSchema"]["additionalProperties"])

    def test_projection_delegates_to_canonical_project_surface(self) -> None:
        detail = {
            "ok": True,
            "project": {"project_id": "prj_1"},
            "supply_chain": {
                "advisory_revalidation": {
                    "work_items": [{"reason": "affectedness-review-required"}],
                    "work_item_count": 1,
                }
            },
        }
        with patch.object(base.TestamurProductService, "integrated", return_value=object()), patch.object(
            base, "project_with_advisory_reviews", return_value=detail
        ) as canonical:
            payload = project_mcp._project_revalidation({"project_ref": "prj_1"})
        canonical.assert_called_once()
        self.assertEqual(payload["advisory_revalidation"]["work_item_count"], 1)
        semantics = payload["semantics"]
        self.assertTrue(semantics["changed_is_not_invalid"])
        self.assertTrue(semantics["changed_is_not_affectedness_verdict"])
        self.assertTrue(semantics["stale_is_not_false"])
        self.assertFalse(semantics["generic_trust_score_used"])

    def test_assessment_delegates_to_canonical_write_route(self) -> None:
        service = object()
        recorded = {"assessment_id": "aas_1", "state": "unknown"}
        arguments = {
            "event_revision_id": "rev_adv_1",
            "subject_revision": "sha256:abc",
            "evidence": [{"kind": "mechanical"}],
            "basis": [{"kind": "scanner"}],
        }
        with patch.object(base.TestamurProductService, "integrated", return_value=service), patch.object(
            project_mcp, "record_advisory_assessment", return_value=recorded
        ) as canonical:
            payload = project_mcp._record_advisory_assessment(arguments)
        canonical.assert_called_once_with(service, arguments)
        self.assertEqual(payload["assessment"], recorded)
        semantics = payload["semantics"]
        self.assertTrue(semantics["recorded_is_not_verified"])
        self.assertTrue(semantics["recorded_is_not_relied"])
        self.assertTrue(semantics["lineage_is_not_affectedness_verdict"])
        self.assertFalse(semantics["caller_supplies_verdict"])
        self.assertFalse(semantics["generic_trust_score_used"])

    def test_project_assessment_validates_current_candidate_then_delegates(self) -> None:
        service = object()
        detail = {
            "ok": True,
            "project": {"project_id": "prj_1"},
            "supply_chain": {
                "advisory_revalidation": {
                    "reviews": [{
                        "event_revision_id": "rev_adv_1",
                        "subjects": [{"subject_revision": "sha256:abc", "requires_review": True}],
                    }]
                }
            },
        }
        arguments = {
            "project_ref": "prj_1",
            "event_revision_id": "rev_adv_1",
            "subject_revision": "sha256:abc",
            "evidence": [{"kind": "mechanical"}],
            "basis": [{"kind": "scanner"}],
        }
        recorded = {"assessment_id": "aas_1", "state": "UNKNOWN"}
        with patch.object(base.TestamurProductService, "integrated", return_value=service), patch.object(
            base, "project_with_advisory_reviews", return_value=detail
        ), patch.object(project_mcp, "record_advisory_assessment", return_value=recorded) as canonical:
            payload = project_mcp._record_project_advisory_assessment(arguments)
        canonical.assert_called_once_with(service, {key: value for key, value in arguments.items() if key != "project_ref"})
        self.assertEqual(payload["assessment"], recorded)
        self.assertTrue(payload["semantics"]["candidate_membership_is_not_affectedness_verdict"])
        self.assertFalse(payload["semantics"]["caller_supplies_verdict"])

    def test_project_assessment_rejects_identity_outside_current_candidates(self) -> None:
        detail = {
            "ok": True,
            "project": {"project_id": "prj_1"},
            "supply_chain": {"advisory_revalidation": {"reviews": []}},
        }
        arguments = {
            "project_ref": "prj_1",
            "event_revision_id": "rev_adv_other",
            "subject_revision": "sha256:other",
            "evidence": [],
            "basis": [],
        }
        with patch.object(base.TestamurProductService, "integrated", return_value=object()), patch.object(
            base, "project_with_advisory_reviews", return_value=detail
        ), patch.object(project_mcp, "record_advisory_assessment") as canonical:
            with self.assertRaisesRegex(ValueError, "not a current exact advisory candidate"):
                project_mcp._record_project_advisory_assessment(arguments)
        canonical.assert_not_called()

    def test_install_adds_tools_without_replacing_existing_tools(self) -> None:
        before = {tool["name"] for tool in base.TOOLS}
        project_mcp._install()
        after = {tool["name"] for tool in base.TOOLS}
        self.assertTrue(before.issubset(after))
        self.assertIn("testamur.project_advisory_revalidation", after)
        self.assertIn("testamur.record_advisory_assessment", after)
        self.assertIn("testamur.record_project_advisory_assessment", after)


if __name__ == "__main__":
    unittest.main()
