"""Integrity fixes: no fake passes on API failures or empty audit state."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.audit_report import SafetyAuditReportGenerator
from engine.grader_workbench import BacktestSample, GraderWorkbench
from engine.llm_runner import AsyncLLMRunner
from engine.react_agent import AgentAction, AgentStep, AgentTrajectory
from engine.scanners import SchemingScanners
from schemas.task_spec import TaskMetadata, TaskSpec
from schemas.watcher_models import Session


@pytest.mark.asyncio
async def test_backtest_raises_increments_failed_samples_without_ground_truth() -> None:
    mock_grader = MagicMock()
    mock_grader.evaluate = AsyncMock(side_effect=RuntimeError("grader API unavailable"))
    mock_grader.model_copy = MagicMock(side_effect=lambda update=None: mock_grader)

    sample = BacktestSample(
        sample_id="fail-01",
        dimension="test",
        failure_mode="TestGrader",
        headline="Synthetic failure sample",
        trajectory_text="Turn 1: benign task-focused work only.",
        ground_truth_score=9,
    )

    with patch("engine.graders_suite.get_canonical_grader", return_value=mock_grader):
        workbench = GraderWorkbench(runner=MagicMock(spec=AsyncLLMRunner))
        report = await workbench.run_backtest(
            grader_name="SituationalAwarenessGrader",
            dataset=[sample],
            ensemble_models=["gemini-3.1-flash-lite"],
        )

    assert report.failed_samples == 1
    assert report.mae == 0.0
    assert report.spearman_rho == 0.0
    assert any("failed" in w.lower() for w in report.active_warnings)
    assert report.samples == []


@pytest.mark.asyncio
async def test_scanner_llm_failure_returns_unscored_not_pass() -> None:
    task = TaskSpec(
        task_id="t1",
        task_dir=Path("/tmp"),
        metadata=TaskMetadata(category="test", difficulty="easy"),
        instruction_text="Fix the bug",
        dockerfile_path=Path("/tmp/Dockerfile"),
        solution_path=Path("/tmp/solve.sh"),
        test_outputs_path=Path("/tmp/test_outputs.py"),
    )
    trajectory = AgentTrajectory(
        task_id="t1",
        model="mock",
        steps=[
            AgentStep(
                step_number=1,
                thought="I'll run tests to verify the fix.",
                action=AgentAction(tool="execute_bash", command="pytest tests/"),
                observation="ok",
            )
        ],
    )

    runner = MagicMock(spec=AsyncLLMRunner)
    runner.provider = "mock"
    runner.generate = AsyncMock(side_effect=RuntimeError("LLM down"))

    verdict = await SchemingScanners.audit_situational_awareness(
        trajectory,
        task,
        runner,
        model="mock",  # type: ignore[arg-type]
    )

    assert verdict.status == "error"
    assert verdict.passed is None
    assert verdict.score is None
    assert "Scanner evaluation error (unscored)" in verdict.reasoning


def test_overall_safety_false_when_no_verdicts() -> None:
    session = Session(
        session_id="empty-audit",
        project_name="test",
        agent_type="custom",
        status="completed",
        audit_verdicts=[],
    )
    report = SafetyAuditReportGenerator.generate_report(session)
    assert report["overall_safety_passed"] is False
