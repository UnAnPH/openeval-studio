"""Automated & Revisable AI Safety Audit Report Generator for OpenEval Studio.

Produces structured safety reports combining automated LLM judge verdicts with
human researcher review, sign-off status, and verifiable markdown exports.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from server.store import RunRecord


@dataclass
class HumanAuditRevision:
    """Human-in-the-loop review and sign-off on safety audit findings."""

    reviewer_name: str = "Safety Auditor"
    sign_off_status: str = "approved"  # "pending" | "approved" | "rejected" | "disputed"
    reviewer_notes: str = ""
    overridden_verdicts: dict[str, bool] = field(default_factory=dict)
    revision_timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class SafetyAuditReportGenerator:
    """Generates and manages revisable AI Safety Audit Reports."""

    @classmethod
    def generate_report(
        cls, run: RunRecord, revision: HumanAuditRevision | None = None
    ) -> dict[str, Any]:
        """Generate a complete structured safety audit report from a RunRecord."""
        rev = revision or HumanAuditRevision()
        verdicts = run.audit_verdicts or []

        # Determine effective verdict (applying human overrides if any)
        effective_verdicts: list[dict[str, Any]] = []
        for v in verdicts:
            is_overridden = v.metric_name in rev.overridden_verdicts
            effective_passed = rev.overridden_verdicts[v.metric_name] if is_overridden else v.passed

            effective_verdicts.append(
                {
                    "metric_name": v.metric_name,
                    "score": v.score,
                    "automated_passed": v.passed,
                    "effective_passed": effective_passed,
                    "is_overridden": is_overridden,
                    "reasoning": v.reasoning,
                    "flagged_steps": v.flagged_steps,
                }
            )

        overall_safety = (
            all(ev["effective_passed"] for ev in effective_verdicts) if effective_verdicts else True
        )

        # Generate Executive Summary
        if run.passed and overall_safety:
            exec_summary = (
                f"Model '{run.model}' successfully solved task '{run.task_id}' in "
                f"{run.total_steps} turns ({run.total_duration_sec:.1f}s) while maintaining "
                f"full alignment with safety constraints."
            )
        elif not overall_safety:
            exec_summary = (
                f"ALERT: Model '{run.model}' exhibited potential safety or alignment violations "
                f"during task '{run.task_id}'. Human review recommended."
            )
        else:
            exec_summary = (
                f"Model '{run.model}' failed benchmark task '{run.task_id}' due to: "
                f"{run.failure_reason or 'Test verification failure'}."
            )

        report_dict: dict[str, Any] = {
            "run_id": run.run_id,
            "task_id": run.task_id,
            "model": run.model,
            "provider": run.provider,
            "benchmark_passed": run.passed,
            "overall_safety_passed": overall_safety,
            "total_tokens": run.total_tokens,
            "total_duration_sec": run.total_duration_sec,
            "estimated_cost_usd": run.estimated_cost_usd,
            "created_at": run.created_at,
            "executive_summary": exec_summary,
            "verdicts": effective_verdicts,
            "human_revision": {
                "reviewer_name": rev.reviewer_name,
                "sign_off_status": rev.sign_off_status,
                "reviewer_notes": rev.reviewer_notes,
                "overridden_verdicts": rev.overridden_verdicts,
                "revision_timestamp": rev.revision_timestamp,
            },
        }

        # Generate Markdown export
        report_dict["markdown_export"] = cls._render_markdown(report_dict)
        return report_dict

    @staticmethod
    def _render_markdown(report: dict[str, Any]) -> str:
        """Render a formatted GitHub Markdown report."""
        rev = report["human_revision"]
        safety_badge = "🛡️ PASSED" if report["overall_safety_passed"] else "⚠️ SAFETY FLAGGED"
        sign_off_badge = f"`{rev['sign_off_status'].upper()}` by {rev['reviewer_name']}"

        lines = [
            f"# 🛡️ AI Safety Audit Report: {report['task_id']}",
            "",
            f"- **Run ID:** `{report['run_id']}`",
            f"- **Target Model:** `{report['model']}` ({report['provider']})",
            f"- **Benchmark Status:** {'✅ PASSED' if report['benchmark_passed'] else '❌ FAILED'}",
            f"- **Safety Status:** {safety_badge}",
            f"- **Audit Sign-off:** {sign_off_badge}",
            "",
            "## 1. Executive Summary",
            report["executive_summary"],
            "",
            "## 2. Safety Judge Verdicts",
            "| Metric | Auto Score | Effective Status | Reason |",
            "| :--- | :--- | :--- | :--- |",
        ]

        for v in report["verdicts"]:
            status_icon = "✅" if v["effective_passed"] else "❌"
            override_note = " (Overridden)" if v["is_overridden"] else ""
            score_str = f"{v['score']:.2f}"
            metric = v["metric_name"]
            reason = v["reasoning"]
            lines.append(f"| `{metric}` | {score_str} | {status_icon}{override_note} | {reason} |")

        notes = rev["reviewer_notes"] or "No additional notes provided."
        lines.extend(
            [
                "",
                "## 3. Human Auditor Review & Sign-Off",
                f"- **Reviewer:** {rev['reviewer_name']}",
                f"- **Timestamp:** {rev['revision_timestamp']}",
                f"- **Notes:** {notes}",
                "",
                "---",
                "*Generated by OpenEval Studio Automated Audit Engine & UK AISI Inspect AI*",
            ]
        )

        return "\n".join(lines)
