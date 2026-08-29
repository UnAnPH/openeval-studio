"""Unit Tests for Trajectory Diff Engine."""

from engine.diff_engine import TrajectoryDiffEngine
from engine.react_agent import AgentAction, AgentStep
from server.store import RunRecord


def test_trajectory_diff_engine_identical_runs() -> None:
    """Verify that comparing identical runs produces 0 deltas and no divergence."""
    step1 = AgentStep(
        step_number=1,
        thought="I need to list files",
        action=AgentAction(tool="execute_bash", command="ls -la"),
        observation="file1.txt file2.txt",
        latency_ms=100.0,
        tokens_used=50,
    )

    run_a = RunRecord(
        run_id="run_001",
        task_id="cancel-async-tasks",
        model="gemini-3.1-flash-lite",
        provider="google",
        status="completed",
        created_at="2026-08-29T10:00:00Z",
        steps=[step1],
        total_steps=1,
        total_tokens=50,
        total_duration_sec=2.5,
        estimated_cost_usd=0.0001,
        final_summary="Finished",
        reward=1.0,
        passed=True,
        failure_reason=None,
    )

    run_b = RunRecord(
        run_id="run_002",
        task_id="cancel-async-tasks",
        model="gemini-3.1-flash-lite",
        provider="google",
        status="completed",
        created_at="2026-08-29T10:05:00Z",
        steps=[step1],
        total_steps=1,
        total_tokens=50,
        total_duration_sec=2.5,
        estimated_cost_usd=0.0001,
        final_summary="Finished",
        reward=1.0,
        passed=True,
        failure_reason=None,
    )

    diff = TrajectoryDiffEngine.compare_runs(run_a, run_b)
    assert diff.outcome_shift == "both_passed"
    assert diff.duration_delta_sec == 0.0
    assert diff.tokens_delta == 0
    assert diff.divergence_step is None
    assert len(diff.step_diffs) == 1
    assert diff.step_diffs[0].tool_match is True
    assert diff.step_diffs[0].args_match is True
    assert diff.step_diffs[0].is_divergent is False


def test_trajectory_diff_engine_divergence_detection() -> None:
    """Verify detecting tool and reasoning divergence between two models."""
    step1_a = AgentStep(
        step_number=1,
        thought="I will search the repository",
        action=AgentAction(tool="execute_bash", command="grep -rn 'TODO' ."),
        observation="TODO: fix error",
        latency_ms=120.0,
        tokens_used=60,
    )
    step2_a = AgentStep(
        step_number=2,
        thought="Now I will edit the file to fix it",
        action=AgentAction(tool="write_file", path="app.py", content="print('fixed')"),
        observation="Success",
        latency_ms=150.0,
        tokens_used=80,
    )

    step1_b = AgentStep(
        step_number=1,
        thought="I will search the repository",
        action=AgentAction(tool="execute_bash", command="grep -rn 'TODO' ."),
        observation="TODO: fix error",
        latency_ms=110.0,
        tokens_used=55,
    )
    step2_b = AgentStep(
        step_number=2,
        thought="I am confused and will read random docs",
        action=AgentAction(tool="view_file", path="README.md"),
        observation="# Readme",
        latency_ms=200.0,
        tokens_used=120,
    )

    run_a = RunRecord(
        run_id="run_a",
        task_id="feed-sync-platform",
        model="gemini-3.7-preview",
        provider="google",
        status="completed",
        created_at="2026-08-29T12:00:00Z",
        steps=[step1_a, step2_a],
        total_steps=2,
        total_tokens=140,
        total_duration_sec=3.0,
        estimated_cost_usd=0.001,
        final_summary="Fixed issue",
        reward=1.0,
        passed=True,
        failure_reason=None,
    )

    run_b = RunRecord(
        run_id="run_b",
        task_id="feed-sync-platform",
        model="gpt-4o-mini",
        provider="openai",
        status="completed",
        created_at="2026-08-29T12:05:00Z",
        steps=[step1_b, step2_b],
        total_steps=2,
        total_tokens=175,
        total_duration_sec=5.0,
        estimated_cost_usd=0.0005,
        final_summary="Gave up",
        reward=0.0,
        passed=False,
        failure_reason="Tests failed",
    )

    diff = TrajectoryDiffEngine.compare_runs(run_a, run_b)
    assert diff.outcome_shift == "improved"  # Run A passed, Run B failed
    assert diff.divergence_step == 2
    assert "Tool divergence at Turn 2" in (diff.divergence_reason or "")
    assert diff.tokens_delta == -35  # Run A used 35 fewer tokens
    assert diff.duration_delta_sec == -2.0  # Run A was 2s faster

    diff_dict = diff.to_dict()
    assert diff_dict["divergence_step"] == 2
    assert len(diff_dict["step_diffs"]) == 2
