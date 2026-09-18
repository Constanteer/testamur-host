from __future__ import annotations

from pathlib import Path
from typing import Any

from audit_registry import AuditRegistry, audit_ref


class AuditCatalog:
    """Stable product read model over append-only AuditRegistry evidence."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.audit = AuditRegistry(self.path)

    def page(self, limit: int = 40) -> dict[str, Any]:
        limit = max(1, min(int(limit), 200))
        with self.audit.connect() as conn:
            rows = conn.execute(
                "SELECT id FROM audit_cases ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        cases = [self._summary(self.audit.get_audit_case(audit_ref(int(row["id"])))) for row in rows]
        return {
            "schema": "mathhub-audit-catalog-v1",
            "audit_cases": cases,
            "count": len(cases),
        }

    def get(self, audit_case_id: str) -> dict[str, Any]:
        case = self.audit.get_audit_case(audit_case_id)
        return {
            "schema": "mathhub-audit-workspace-v1",
            **case,
            "summary": self._coverage_summary(case),
            "trust_boundary": {
                "overall_trust_score": None,
                "statement": "Audit dimensions are intentionally independent. Absence of findings is not a proof of correctness.",
            },
        }

    def report(self, audit_case_id: str) -> dict[str, Any]:
        workspace = self.get(audit_case_id)
        return {
            "schema": "mathhub-audit-report-v1",
            "audit_case_id": workspace["audit_case_id"],
            "title": workspace["title"],
            "description": workspace.get("description", ""),
            "headline_claim_version_id": workspace.get("headline_claim_version_id"),
            "targets": workspace.get("targets", []),
            "coverage": workspace.get("coverage", {}),
            "coverage_history": workspace.get("coverage_history", []),
            "findings": workspace.get("findings", []),
            "summary": workspace.get("summary", {}),
            "limitations": [
                "This report records the evidence available to this MathHub AuditCase; it is not a certificate of mathematical truth.",
                "Coverage marked complete applies only to that named audit dimension and recorded scope.",
                "Publisher assertions remain distinguishable from independently reproduced MathHub evidence.",
            ],
            "overall_trust_score": None,
        }

    @staticmethod
    def _coverage_summary(case: dict[str, Any]) -> dict[str, Any]:
        coverage = case.get("coverage") if isinstance(case.get("coverage"), dict) else {}
        counts = {"not-examined": 0, "partial": 0, "complete": 0, "issue-found": 0}
        dimensions = []
        for key in sorted(coverage):
            item = coverage[key]
            status = str(item.get("status") or "not-examined") if isinstance(item, dict) else "not-examined"
            counts[status] = counts.get(status, 0) + 1
            dimensions.append({"dimension": key, "status": status})
        findings = case.get("findings") if isinstance(case.get("findings"), list) else []
        open_findings = [
            item for item in findings
            if str(item.get("status") or "") not in {"resolved", "rejected", "superseded"}
        ]
        return {
            "dimensions": dimensions,
            "counts": counts,
            "finding_count": len(findings),
            "open_finding_count": len(open_findings),
            "critical_open_findings": sum(
                1 for item in open_findings if item.get("severity") == "critical"
            ),
        }

    def _summary(self, case: dict[str, Any]) -> dict[str, Any]:
        summary = self._coverage_summary(case)
        return {
            "audit_case_id": case["audit_case_id"],
            "title": case["title"],
            "description": case.get("description", ""),
            "headline_claim_version_id": case.get("headline_claim_version_id"),
            "created_by": case.get("created_by", ""),
            "created_at": case.get("created_at", ""),
            "summary": summary,
            "coverage": case.get("coverage", {}),
        }
