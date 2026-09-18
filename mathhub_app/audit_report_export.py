from __future__ import annotations

import html
import json
from typing import Any


def _text(value: Any) -> str:
    return str(value if value is not None else "")


def _md(value: Any) -> str:
    return _text(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {_text(report.get('title') or 'MathHub Audit Report')}",
        "",
        f"**AuditCase:** `{_text(report.get('audit_case_id'))}`  ",
        f"**Headline Claim revision:** `{_text(report.get('headline_claim_version_id') or '—')}`  ",
        "**Overall trust score:** intentionally not defined",
        "",
    ]
    description = _text(report.get("description")).strip()
    if description:
        lines += [description, ""]

    lines += ["## Coverage", "", "| Dimension | Status | Scope |", "| --- | --- | --- |"]
    coverage = report.get("coverage") if isinstance(report.get("coverage"), dict) else {}
    for dimension in sorted(coverage):
        item = coverage[dimension] if isinstance(coverage[dimension], dict) else {}
        lines.append(
            f"| `{_md(dimension)}` | **{_md(item.get('status') or 'not-examined')}** | {_md(item.get('scope') or '')} |"
        )
    if not coverage:
        lines.append("| — | not examined | — |")

    lines += ["", "## Findings", ""]
    findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    if not findings:
        lines.append("No Findings are recorded in this AuditCase. This is not evidence that the result is correct.")
    for finding in findings:
        lines += [
            f"### {_text(finding.get('finding_id') or '')} — {_text(finding.get('title') or finding.get('finding_type') or 'Finding')}",
            "",
            f"- Type: `{_text(finding.get('finding_type') or '')}`",
            f"- Severity: **{_text(finding.get('severity') or '')}**",
            f"- Status: **{_text(finding.get('status') or '')}**",
        ]
        summary = _text(finding.get("summary") or finding.get("description") or "").strip()
        if summary:
            lines += ["", summary]
        lines.append("")

    targets = report.get("targets") if isinstance(report.get("targets"), list) else []
    lines += ["## Targets", ""]
    if targets:
        for target in targets:
            lines.append(
                f"- `{_text(target.get('target_kind') or '')}` — `{_text(target.get('target_ref') or target.get('ref') or '')}`"
            )
    else:
        lines.append("No explicit targets recorded.")

    lines += ["", "## Limitations", ""]
    for limitation in report.get("limitations") or []:
        lines.append(f"- {_text(limitation)}")
    lines += [
        "",
        "---",
        "Generated from an append-only MathHub AuditCase evidence snapshot. The report does not certify mathematical truth.",
        "",
    ]
    return "\n".join(lines)


def render_html(report: dict[str, Any]) -> str:
    markdown = render_markdown(report)
    # Keep the export dependency-free and deterministic. Instead of shipping a
    # Markdown renderer into the trust path, render a compact native HTML report.
    coverage = report.get("coverage") if isinstance(report.get("coverage"), dict) else {}
    findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    targets = report.get("targets") if isinstance(report.get("targets"), list) else []
    coverage_rows = "".join(
        f"<tr><td><code>{html.escape(_text(d))}</code></td><td>{html.escape(_text((v or {}).get('status') if isinstance(v, dict) else 'not-examined'))}</td><td>{html.escape(_text((v or {}).get('scope') if isinstance(v, dict) else ''))}</td></tr>"
        for d, v in sorted(coverage.items())
    ) or "<tr><td>—</td><td>not examined</td><td>—</td></tr>"
    finding_html = "".join(
        "<article><h3>{id} — {title}</h3><p><code>{type}</code> · <strong>{severity}</strong> · {status}</p><p>{summary}</p></article>".format(
            id=html.escape(_text(f.get("finding_id"))),
            title=html.escape(_text(f.get("title") or f.get("finding_type") or "Finding")),
            type=html.escape(_text(f.get("finding_type"))),
            severity=html.escape(_text(f.get("severity"))),
            status=html.escape(_text(f.get("status"))),
            summary=html.escape(_text(f.get("summary") or f.get("description") or "")),
        ) for f in findings
    ) or "<p>No Findings are recorded. Absence of Findings is not proof of correctness.</p>"
    target_html = "".join(
        f"<li><code>{html.escape(_text(t.get('target_kind')))}</code> — <code>{html.escape(_text(t.get('target_ref') or t.get('ref')))}</code></li>"
        for t in targets
    ) or "<li>No explicit targets recorded.</li>"
    limitations = "".join(f"<li>{html.escape(_text(x))}</li>" for x in report.get("limitations") or [])
    raw_json = html.escape(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(_text(report.get('title') or 'MathHub Audit Report'))}</title>
<style>body{{max-width:1040px;margin:48px auto;padding:0 28px;font:16px/1.55 system-ui,sans-serif;color:#20201d}}h1,h2,h3{{font-family:Georgia,serif}}code,pre{{font-family:ui-monospace,monospace}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}}.boundary{{padding:14px 18px;border-left:3px solid #777;background:#f7f7f4}}article{{margin:24px 0}}details{{margin-top:32px}}pre{{white-space:pre-wrap;overflow:auto}}</style></head><body>
<p>MathHub Audit Report · <code>{html.escape(_text(report.get('audit_case_id')))}</code></p>
<h1>{html.escape(_text(report.get('title') or 'Audit Report'))}</h1>
<p>{html.escape(_text(report.get('description')))}</p>
<div class="boundary"><strong>No overall trust score.</strong> Audit dimensions remain independent; this document is evidence, not a certificate of mathematical truth.</div>
<h2>Coverage</h2><table><thead><tr><th>Dimension</th><th>Status</th><th>Scope</th></tr></thead><tbody>{coverage_rows}</tbody></table>
<h2>Findings</h2>{finding_html}
<h2>Targets</h2><ul>{target_html}</ul>
<h2>Limitations</h2><ul>{limitations}</ul>
<details><summary>Machine-readable JSON snapshot</summary><pre>{raw_json}</pre></details>
<!-- Markdown equivalent SHA source is generated from the same report object; retained here only as a deterministic rendering aid. Length: {len(markdown)} -->
</body></html>"""


def export_report(report: dict[str, Any], format_name: str) -> dict[str, Any]:
    fmt = str(format_name or "json").strip().lower()
    if fmt in {"json", "application/json"}:
        content = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        mime = "application/json"
        extension = "json"
    elif fmt in {"md", "markdown", "text/markdown"}:
        content = render_markdown(report)
        mime = "text/markdown; charset=utf-8"
        extension = "md"
    elif fmt in {"html", "text/html"}:
        content = render_html(report)
        mime = "text/html; charset=utf-8"
        extension = "html"
    else:
        raise ValueError("report format must be json, md, or html")
    audit_id = _text(report.get("audit_case_id") or "audit").lower()
    return {
        "format": fmt,
        "mime_type": mime,
        "filename": f"mathhub-{audit_id}-report.{extension}",
        "content": content,
        "overall_trust_score": None,
    }
