"""Multi-Model Benchmark Matrix Sweeper for OpenEval Studio.

Executes cross-model and cross-task evaluation sweeps, aggregates Pass@1
statistical metrics, token expenses, duration distributions, and outputs
comparative evaluation leaderboards and REPORT.md artifacts.
"""

import json
import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from engine.llm_runner import AsyncLLMRunner, LLMConfig
from engine.react_agent import ReActAgent
from engine.verifier import VerifierRunner
from sandbox.docker_runner import create_sandbox_for_task
from schemas.models import get_default_model, get_model_spec
from schemas.task_spec import TaskSpec, load_task_spec

logger = logging.getLogger("openeval.engine.sweep")


class SweepTaskResult(BaseModel):
    """Result of a single (task, model) evaluation run."""

    model_config = ConfigDict(extra="ignore")

    task_id: str
    model: str
    status: str
    reward: float = 0.0
    passed: bool = False
    tokens: int = 0
    duration_sec: float = 0.0
    cost_usd: float = 0.0
    failure_reason: str | None = None


class ModelBenchmarkSummary(BaseModel):
    """Aggregated evaluation metrics for a single model across tasks."""

    model: str
    total_tasks: int = 0
    passed_tasks: int = 0
    pass_rate: float = 0.0  # Pass@1 (0.0 to 1.0)
    total_tokens: int = 0
    avg_tokens_per_task: float = 0.0
    total_cost_usd: float = 0.0
    avg_duration_sec: float = 0.0


class SweepReport(BaseModel):
    """Complete summary of a benchmark evaluation matrix sweep."""

    sweep_id: str
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    models_evaluated: list[str] = Field(default_factory=list)
    tasks_evaluated: list[str] = Field(default_factory=list)
    results: list[SweepTaskResult] = Field(default_factory=list)
    model_summaries: dict[str, ModelBenchmarkSummary] = Field(default_factory=dict)

    def calculate_summaries(self) -> None:
        """Compute Pass@1, token averages, and costs per model."""
        summaries: dict[str, ModelBenchmarkSummary] = {}
        for m in self.models_evaluated:
            model_results = [r for r in self.results if r.model == m]
            total = len(model_results)
            passed = sum(1 for r in model_results if r.passed)
            tokens = sum(r.tokens for r in model_results)
            cost = sum(r.cost_usd for r in model_results)
            duration = sum(r.duration_sec for r in model_results)

            pass_rate = (passed / total) if total > 0 else 0.0
            avg_toks = (tokens / total) if total > 0 else 0.0
            avg_dur = (duration / total) if total > 0 else 0.0

            summaries[m] = ModelBenchmarkSummary(
                model=m,
                total_tasks=total,
                passed_tasks=passed,
                pass_rate=round(pass_rate, 4),
                total_tokens=tokens,
                avg_tokens_per_task=round(avg_toks, 1),
                total_cost_usd=round(cost, 6),
                avg_duration_sec=round(avg_dur, 2),
            )
        self.model_summaries = summaries

    def to_markdown_table(self) -> str:
        """Generate a clean GitHub-Flavored Markdown leaderboard table."""
        self.calculate_summaries()

        tasks_str = ", ".join(self.tasks_evaluated)
        models_str = ", ".join(self.models_evaluated)

        lines: list[str] = [
            "# 🏆 OpenEval Studio — Benchmark Evaluation Leaderboard\n",
            f"**Sweep Date:** {self.timestamp}  ",
            f"**Tasks Evaluated ({len(self.tasks_evaluated)}):** `{tasks_str}`  ",
            f"**Models Tested ({len(self.models_evaluated)}):** `{models_str}`\n",
            "## 📊 Model Capability Scoreboard (Pass@1)\n",
            "| Model | Pass@1 | Solved | Avg Tokens/Task | Total Cost ($) | Avg Duration |",
            "|:---|:---:|:---:|:---:|:---:|:---:|",
        ]

        # Sort models by pass rate descending, then cost ascending
        sorted_models = sorted(
            self.model_summaries.values(),
            key=lambda s: (s.pass_rate, -s.total_cost_usd),
            reverse=True,
        )

        for s in sorted_models:
            pass_pct = f"**{s.pass_rate * 100:.1f}%**"
            solved_str = f"{s.passed_tasks}/{s.total_tasks}"
            cost_str = f"${s.total_cost_usd:.5f}"
            lines.append(
                f"| `{s.model}` | {pass_pct} | {solved_str} | "
                f"{s.avg_tokens_per_task:,.0f} | {cost_str} | {s.avg_duration_sec:.1f}s |"
            )

        lines.append("\n## 🔍 Granular Task Run Breakdown\n")
        lines.append("| Task ID | Model | Status | Reward | Tokens | Cost ($) | Duration |")
        lines.append("|:---|:---|:---:|:---:|:---:|:---:|:---:|")

        for r in self.results:
            status_icon = "✅ PASS" if r.passed else "❌ FAIL"
            lines.append(
                f"| `{r.task_id}` | `{r.model}` | {status_icon} | "
                f"{r.reward:.1f} | {r.tokens:,} | ${r.cost_usd:.5f} | {r.duration_sec:.1f}s |"
            )

        return "\n".join(lines) + "\n"

    def save_report(self, output_path: Path) -> None:
        """Save report as Markdown and JSON side-by-side."""
        output_path.write_text(self.to_markdown_table(), encoding="utf-8")
        json_path = output_path.with_suffix(".json")
        json_path.write_text(json.dumps(self.model_dump(), indent=2), encoding="utf-8")


class BenchmarkSweeper:
    """Orchestrates multi-model benchmark evaluation sweeps."""

    def __init__(self, output_file: Path | None = None) -> None:
        self.output_file = output_file or Path("REPORT.md")

    async def run_matrix_sweep(
        self,
        tasks: list[TaskSpec],
        models: list[str],
        api_key: str | None = None,
        on_run_progress: Callable[[int, int, str, str], None] | None = None,
    ) -> SweepReport:
        """Execute full combinatorial matrix sweep across models and tasks."""
        report = SweepReport(
            sweep_id=f"sweep-{int(time.time())}",
            models_evaluated=models,
            tasks_evaluated=[t.task_id for t in tasks],
        )

        total_runs = len(tasks) * len(models)
        current_run = 0

        for model_id in models:
            is_openai = (
                model_id.startswith("gpt-")
                or model_id.startswith("o1")
                or model_id.startswith("o3")
            )
            provider = "openai" if is_openai else "google"

            key = api_key
            if not key:
                if provider == "google":
                    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
                else:
                    key = os.getenv("OPENAI_API_KEY")

            if not key:
                logger.error("No API key available for %s", provider)
                continue

            runner = AsyncLLMRunner(api_key=key, provider=provider)  # type: ignore[arg-type]
            model_spec = get_model_spec(model_id) or get_default_model(provider)  # type: ignore[arg-type]

            try:
                for task in tasks:
                    current_run += 1
                    if on_run_progress:
                        on_run_progress(current_run, total_runs, model_id, task.task_id)

                    try:
                        sandbox = create_sandbox_for_task(task)
                        await sandbox.start()

                        executor = sandbox.as_tool_executor()
                        agent = ReActAgent(runner=runner, executor=executor)

                        trajectory = await agent.solve_task(
                            task=task,
                            config=LLMConfig(model=model_id, provider=provider, temperature=0.0),  # type: ignore[arg-type]
                        )

                        verifier = VerifierRunner()
                        grade = await verifier.grade_container(sandbox, task)
                        await sandbox.stop()

                        cost = model_spec.estimate_cost(
                            int(trajectory.total_tokens * 0.7),
                            int(trajectory.total_tokens * 0.3),
                        )

                        result = SweepTaskResult(
                            task_id=task.task_id,
                            model=model_id,
                            status=trajectory.status,
                            reward=grade.reward,
                            passed=grade.passed,
                            tokens=trajectory.total_tokens,
                            duration_sec=trajectory.total_duration_sec,
                            cost_usd=cost,
                            failure_reason=grade.failure_reason,
                        )
                        report.results.append(result)

                    except Exception as e:
                        logger.error("Run failed for %s on %s: %s", model_id, task.task_id, e)
                        report.results.append(
                            SweepTaskResult(
                                task_id=task.task_id,
                                model=model_id,
                                status="error",
                                reward=0.0,
                                passed=False,
                                failure_reason=str(e),
                            )
                        )
            finally:
                await runner.close()

        report.calculate_summaries()
        report.save_report(self.output_file)
        return report


def discover_all_tasks(task_dir: Path | str = "tasks") -> list[TaskSpec]:
    """Scan directory and return all valid TaskSpec objects."""
    base = Path(task_dir)
    tasks: list[TaskSpec] = []
    if not base.exists():
        return tasks

    for p in sorted(base.iterdir()):
        if p.is_dir() and (p / "task.toml").exists():
            try:
                tasks.append(load_task_spec(p))
            except Exception as e:
                logger.warning("Could not load task at %s: %s", p, e)
    return tasks
