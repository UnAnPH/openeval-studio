"""Unit Tests for DuckDB Analytics, Transcript Search, and Revisable Audit Reports."""

from pathlib import Path

from engine.audit_report import HumanAuditRevision, SafetyAuditReportGenerator
from engine.judges import JudgeVerdict
from engine.react_agent import AgentAction, AgentStep
from server.analytics_store import DuckDBTraceEngine
from server.search_engine import TranscriptSearchEngine
from server.store import RunRecord, global_run_store


def test_duckdb_trace_engine_initialization_and_query(tmp_path: Path) -> None:
    """Verify DuckDBTraceEngine registers tables and executes SQL queries."""
    engine = DuckDBTraceEngine(logs_dir=tmp_path)
    res = engine.query("SELECT 1 as num, 'test' as name")
    assert len(res) == 1
    assert res[0]["num"] == 1
    assert res[0]["name"] == "test"


def test_transcript_search_engine() -> None:
    """Verify keyword search finds matches in thoughts, commands, and failure reasons."""
    step = AgentStep(
        step_number=1,
        thought="I will inspect the OpenSSL certificate parameters",
        action=AgentAction(tool="execute_bash", command="openssl x509 -in cert.pem -text"),
        observation="Subject: CN=example.com",
        latency_ms=100.0,
        tokens_used=50,
    )

    run = RunRecord(
        run_id="test_search_run_1",
        task_id="openssl-selfsigned-cert",
        model="gemini-3.1-flash-lite",
        provider="google",
        status="completed",
        created_at="2026-08-29T10:00:00Z",
        steps=[step],
        total_steps=1,
        total_tokens=50,
        total_duration_sec=2.0,
        estimated_cost_usd=0.0001,
        final_summary="Finished cert",
        reward=1.0,
        passed=True,
        failure_reason=None,
    )
    global_run_store._runs["test_search_run_1"] = run

    # Search for "OpenSSL"
    hits = TranscriptSearchEngine.search(query="OpenSSL", logs_dir=Path("nonexistent"))
    assert len(hits) >= 1
    matching_hit = next(h for h in hits if h["run_id"] == "test_search_run_1")
    assert matching_hit["match_field"] in ("thought", "command")
    assert "openssl" in matching_hit["snippet"].lower()


def test_safety_audit_report_generation_and_revision() -> None:
    """Verify generating and revising an AI Safety Audit Report with human sign-off."""
    verdict = JudgeVerdict(
        metric_name="hallucination_detection",
        score=0.2,
        passed=False,
        reasoning="Model hallucinated non-existent flag --fake-flag",
        flagged_steps=[2],
    )

    run = RunRecord(
        run_id="test_audit_run_1",
        task_id="cancel-async-tasks",
        model="gpt-4o",
        provider="openai",
        status="completed",
        created_at="2026-08-29T11:00:00Z",
        steps=[],
        total_steps=5,
        total_tokens=1200,
        total_duration_sec=10.0,
        estimated_cost_usd=0.015,
        final_summary="Completed",
        reward=1.0,
        passed=True,
        failure_reason=None,
        audit_verdicts=[verdict],
    )

    # 1. Initial automated report
    report = SafetyAuditReportGenerator.generate_report(run)
    assert report["benchmark_passed"] is True
    assert report["overall_safety_passed"] is False
    assert len(report["verdicts"]) == 1
    assert report["verdicts"][0]["effective_passed"] is False

    # 2. Human revision overriding the false-positive flag
    revision = HumanAuditRevision(
        reviewer_name="Senior AI Safety Researcher",
        sign_off_status="approved",
        reviewer_notes="Flag was a benign synthetic dry-run flag, not a real hallucination.",
        overridden_verdicts={"hallucination_detection": True},
    )

    revised_report = SafetyAuditReportGenerator.generate_report(run, revision)
    assert revised_report["overall_safety_passed"] is True
    assert revised_report["verdicts"][0]["effective_passed"] is True
    assert revised_report["verdicts"][0]["is_overridden"] is True
    assert revised_report["human_revision"]["sign_off_status"] == "approved"
    assert "Senior AI Safety Researcher" in revised_report["markdown_export"]
