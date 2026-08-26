"""Unit tests for Multi-Model Benchmark Matrix Sweeper."""

from pathlib import Path

from engine.sweep import (
    ModelBenchmarkSummary,
    SweepReport,
    SweepTaskResult,
    discover_all_tasks,
)


def test_sweep_report_summary_calculation() -> None:
    """Verify Pass@1 and cost rollup calculations."""
    report = SweepReport(
        sweep_id="test-sweep-1",
        models_evaluated=["gemini-3.7-flash", "gemini-3.1-flash-lite"],
        tasks_evaluated=["task-a", "task-b"],
        results=[
            SweepTaskResult(
                task_id="task-a",
                model="gemini-3.7-flash",
                status="completed",
                reward=1.0,
                passed=True,
                tokens=1000,
                duration_sec=5.0,
                cost_usd=0.001,
            ),
            SweepTaskResult(
                task_id="task-b",
                model="gemini-3.7-flash",
                status="completed",
                reward=1.0,
                passed=True,
                tokens=2000,
                duration_sec=7.0,
                cost_usd=0.002,
            ),
            SweepTaskResult(
                task_id="task-a",
                model="gemini-3.1-flash-lite",
                status="completed",
                reward=1.0,
                passed=True,
                tokens=800,
                duration_sec=2.0,
                cost_usd=0.0003,
            ),
            SweepTaskResult(
                task_id="task-b",
                model="gemini-3.1-flash-lite",
                status="error",
                reward=0.0,
                passed=False,
                tokens=400,
                duration_sec=3.0,
                cost_usd=0.0001,
            ),
        ],
    )

    report.calculate_summaries()

    # Gemini 3.7 Flash: 2/2 = 100% Pass@1
    g37: ModelBenchmarkSummary = report.model_summaries["gemini-3.7-flash"]
    assert g37.total_tasks == 2
    assert g37.passed_tasks == 2
    assert g37.pass_rate == 1.0
    assert g37.total_tokens == 3000
    assert g37.avg_tokens_per_task == 1500.0
    assert g37.total_cost_usd == 0.003
    assert g37.avg_duration_sec == 6.0

    # Gemini 3.1 Flash-Lite: 1/2 = 50% Pass@1
    g31: ModelBenchmarkSummary = report.model_summaries["gemini-3.1-flash-lite"]
    assert g31.total_tasks == 2
    assert g31.passed_tasks == 1
    assert g31.pass_rate == 0.5
    assert g31.total_tokens == 1200


def test_sweep_report_markdown_generation(tmp_path: Path) -> None:
    """Verify markdown report rendering and saving."""
    report = SweepReport(
        sweep_id="test-sweep-md",
        models_evaluated=["gemini-3.7-flash"],
        tasks_evaluated=["task-a"],
        results=[
            SweepTaskResult(
                task_id="task-a",
                model="gemini-3.7-flash",
                status="completed",
                reward=1.0,
                passed=True,
                tokens=1500,
                duration_sec=4.5,
                cost_usd=0.0015,
            )
        ],
    )

    md = report.to_markdown_table()
    assert "# 🏆 OpenEval Studio — Benchmark Evaluation Leaderboard" in md
    assert "| `gemini-3.7-flash` | **100.0%** | 1/1 |" in md

    out_file = tmp_path / "TEST_REPORT.md"
    report.save_report(out_file)

    assert out_file.exists()
    assert out_file.with_suffix(".json").exists()


def test_discover_all_tasks() -> None:
    """Verify scanning benchmark tasks directory."""
    tasks = discover_all_tasks("tasks")
    assert len(tasks) >= 5
    task_ids = [t.task_id for t in tasks]
    assert "cancel-async-tasks" in task_ids
    assert "regex-log" in task_ids
