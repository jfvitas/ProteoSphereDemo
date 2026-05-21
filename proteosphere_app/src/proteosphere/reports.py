from __future__ import annotations

from typing import Any


def render_review_markdown(report: dict[str, Any]) -> str:
    metrics = report.get("overlap_metrics") or {}
    findings = report.get("overlap_findings") or {}
    lines = [
        f"# ProteoSphere Split Review: {report.get('title') or report.get('manifest_id')}",
        "",
        f"- Manifest: `{report.get('manifest_id')}`",
        f"- Entity kind: `{report.get('entity_kind')}`",
        f"- Verdict: `{report.get('verdict')}`",
        f"- Recommended action: {report.get('recommended_action')}",
        "",
        "## Reason Codes",
        "",
    ]
    codes = report.get("reason_codes") or []
    lines.extend([f"- `{code}`" for code in codes] or ["- None"])
    lines.extend(["", "## Overlap Metrics", ""])
    for key in sorted(metrics):
        lines.append(f"- `{key}`: `{metrics[key]}`")
    lines.extend(["", "## Key Findings", ""])
    for key in sorted(findings):
        values = findings[key]
        if values:
            preview = ", ".join(str(item) for item in values[:20])
            suffix = " ..." if len(values) > 20 else ""
            lines.append(f"- `{key}`: {preview}{suffix}")
    if not any(findings.values()):
        lines.append("- No train/held-out overlaps detected by deterministic checks.")
    lines.extend(["", "## Warnings", ""])
    lines.extend([f"- {warning}" for warning in report.get("warnings") or []] or ["- None"])
    lines.extend(["", "## Blockers", ""])
    lines.extend([f"- {blocker}" for blocker in report.get("blockers") or []] or ["- None"])
    return "\n".join(lines) + "\n"


def render_paper_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# ProteoSphere Paper Split Review",
        "",
        f"- Papers: `{len(report.get('papers') or [])}`",
        "",
        "| Paper | Verdict | Reason codes | Notes |",
        "|---|---|---|---|",
    ]
    for paper in report.get("papers") or []:
        codes = ", ".join(f"`{code}`" for code in paper.get("reason_codes") or []) or "None"
        notes = "; ".join(paper.get("warnings") or paper.get("blockers") or [])[:300]
        lines.append(
            f"| {paper.get('paper_id')} | `{paper.get('verdict')}` | {codes} | {notes} |"
        )
    return "\n".join(lines) + "\n"
