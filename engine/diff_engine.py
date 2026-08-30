"""Trajectory Diff Engine for OpenEval Studio.

Compares two evaluation runs (from Inspect .eval archives or RunRecords) to compute
synchronized turn-by-turn alignment, thought divergence, tool argument diffs,
token/cost deltas, and automatic divergence point detection.
"""

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engine.react_agent import AgentStep
from server.inspect_loader import parse_eval_log_to_run_record
from server.store import RunRecord, global_run_store


from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Literal
import logging

logger = logging.getLogger("openeval.engine.diff")


class SemanticComparisonVerdict(BaseModel):
    """Structured LLM Semantic Trajectory Comparison verdict."""

    model_config = ConfigDict(extra="ignore")

    run_a_id: str
    run_b_id: str
    task_id: str
    nature_of_divergence: Literal[
        "identical_strategy",
        "cosmetic_variation",
        "substantive_divergence",
        "opposite_strategies",
    ] = Field(
        default="cosmetic_variation",
        description="Whether differences are cosmetic syntax noise vs true strategic divergence",
    )
    semantic_similarity_score: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Semantic trajectory equivalence score (0.0 to 1.0)",
    )
    verdict_summary: str = Field(
        ..., description="Executive 1-2 sentence overview of how the two runs compared"
    )
    key_strategic_differences: list[str] = Field(
        default_factory=list, description="Specific differences in strategy, tool flow, or debugging"
    )
    true_divergence_turn: int | None = Field(
        default=None, description="The specific turn where true strategic divergence occurred, or None if purely cosmetic"
    )
    attribution_reasoning: str = Field(
        ..., description="Explanation of why one model performed better, faster, or if both are equivalent"
    )


@dataclass
class StepDiff:
    """Represents a synchronized comparison between two corresponding agent steps."""

    step_number: int
    step_a: AgentStep | None
    step_b: AgentStep | None
    tool_match: bool
    args_match: bool
    is_divergent: bool
    thought_similarity: float
    command_diff: str | None = None
    content_diff: str | None = None
    observation_diff: str | None = None


@dataclass
class TrajectoryDiffSummary:
    """High-level metrics delta between Run A and Run B."""

    run_a_id: str
    run_b_id: str
    task_id: str
    model_a: str
    model_b: str
    status_a: str
    status_b: str
    passed_a: bool | None
    passed_b: bool | None
    outcome_shift: str  # e.g. "improved", "regressed", "identical", "both_failed"
    duration_delta_sec: float
    tokens_delta: int
    cost_delta_usd: float
    turns_delta: int
    divergence_step: int | None
    divergence_reason: str | None
    step_diffs: list[StepDiff] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize diff summary to a JSON-compatible dictionary."""
        return {
            "run_a_id": self.run_a_id,
            "run_b_id": self.run_b_id,
            "task_id": self.task_id,
            "model_a": self.model_a,
            "model_b": self.model_b,
            "status_a": self.status_a,
            "status_b": self.status_b,
            "passed_a": self.passed_a,
            "passed_b": self.passed_b,
            "outcome_shift": self.outcome_shift,
            "duration_delta_sec": round(self.duration_delta_sec, 2),
            "tokens_delta": self.tokens_delta,
            "cost_delta_usd": round(self.cost_delta_usd, 5),
            "turns_delta": self.turns_delta,
            "divergence_step": self.divergence_step,
            "divergence_reason": self.divergence_reason,
            "step_diffs": [
                {
                    "step_number": sd.step_number,
                    "step_a": sd.step_a.model_dump() if sd.step_a else None,
                    "step_b": sd.step_b.model_dump() if sd.step_b else None,
                    "tool_match": sd.tool_match,
                    "args_match": sd.args_match,
                    "is_divergent": sd.is_divergent,
                    "thought_similarity": round(sd.thought_similarity, 3),
                    "command_diff": sd.command_diff,
                    "content_diff": sd.content_diff,
                    "observation_diff": sd.observation_diff,
                }
                for sd in self.step_diffs
            ],
        }


class TrajectoryDiffEngine:
    """Engine for comparing and diffing evaluation trajectories."""

    @staticmethod
    def _compute_text_diff(
        text_a: str | None, text_b: str | None, fromfile: str = "Run A", tofile: str = "Run B"
    ) -> str | None:
        """Generate a unified line diff string between two text snippets."""
        if not text_a and not text_b:
            return None
        lines_a = (text_a or "").splitlines(keepends=True)
        lines_b = (text_b or "").splitlines(keepends=True)
        if lines_a == lines_b:
            return None

        diff = difflib.unified_diff(lines_a, lines_b, fromfile=fromfile, tofile=tofile)
        diff_str = "".join(diff).strip()
        return diff_str or None

    @staticmethod
    def _calculate_similarity(text_a: str, text_b: str) -> float:
        """Compute string sequence matcher ratio between two thoughts."""
        if not text_a and not text_b:
            return 1.0
        if not text_a or not text_b:
            return 0.0
        return difflib.SequenceMatcher(None, text_a, text_b).ratio()

    @classmethod
    def compare_runs(cls, run_a: RunRecord, run_b: RunRecord) -> TrajectoryDiffSummary:
        """Perform full turn-by-turn alignment and metric diff between Run A and Run B."""
        steps_a = run_a.steps or []
        steps_b = run_b.steps or []
        max_turns = max(len(steps_a), len(steps_b))

        step_diffs: list[StepDiff] = []
        divergence_step: int | None = None
        divergence_reason: str | None = None

        for idx in range(max_turns):
            step_num = idx + 1
            sa = steps_a[idx] if idx < len(steps_a) else None
            sb = steps_b[idx] if idx < len(steps_b) else None

            tool_match = False
            args_match = False
            is_div = False
            sim = 0.0
            cmd_diff = None
            content_diff = None
            obs_diff = None

            if sa and sb:
                tool_match = sa.action.tool == sb.action.tool
                sim = cls._calculate_similarity(sa.thought, sb.thought)

                if sa.action.command or sb.action.command:
                    cmd_diff = cls._compute_text_diff(
                        sa.action.command, sb.action.command, "Model A Command", "Model B Command"
                    )

                if sa.action.content or sb.action.content:
                    content_diff = cls._compute_text_diff(
                        sa.action.content,
                        sb.action.content,
                        "Model A File Content",
                        "Model B File Content",
                    )

                if sa.observation or sb.observation:
                    obs_diff = cls._compute_text_diff(
                        sa.observation, sb.observation, "Model A Observation", "Model B Observation"
                    )

                args_match = (
                    sa.action.command == sb.action.command
                    and sa.action.path == sb.action.path
                    and sa.action.content == sb.action.content
                )

                if not tool_match or not args_match or sim < 0.4:
                    is_div = True
                    if divergence_step is None:
                        divergence_step = step_num
                        if not tool_match:
                            divergence_reason = (
                                f"Tool divergence at Turn {step_num}: Model A used "
                                f"'{sa.action.tool}' vs Model B '{sb.action.tool}'."
                            )
                        elif not args_match:
                            divergence_reason = (
                                f"Command/File divergence at Turn {step_num}: Different arguments "
                                f"executed in sandbox."
                            )
                        else:
                            divergence_reason = (
                                f"Reasoning divergence at Turn {step_num}: Thought similarity "
                                f"dropped to {sim:.0%}."
                            )
            else:
                is_div = True
                if divergence_step is None:
                    divergence_step = step_num
                    divergence_reason = (
                        f"Turn count divergence at Turn {step_num}: One model finished "
                        f"earlier than the other."
                    )

            step_diffs.append(
                StepDiff(
                    step_number=step_num,
                    step_a=sa,
                    step_b=sb,
                    tool_match=tool_match,
                    args_match=args_match,
                    is_divergent=is_div,
                    thought_similarity=sim,
                    command_diff=cmd_diff,
                    content_diff=content_diff,
                    observation_diff=obs_diff,
                )
            )

        # Compute outcome shift
        if run_a.passed and not run_b.passed:
            outcome_shift = "improved"
        elif not run_a.passed and run_b.passed:
            outcome_shift = "regressed"
        elif run_a.passed and run_b.passed:
            outcome_shift = "both_passed"
        else:
            outcome_shift = "both_failed"

        return TrajectoryDiffSummary(
            run_a_id=run_a.run_id,
            run_b_id=run_b.run_id,
            task_id=run_a.task_id,
            model_a=run_a.model,
            model_b=run_b.model,
            status_a=run_a.status,
            status_b=run_b.status,
            passed_a=run_a.passed,
            passed_b=run_b.passed,
            outcome_shift=outcome_shift,
            duration_delta_sec=run_a.total_duration_sec - run_b.total_duration_sec,
            tokens_delta=run_a.total_tokens - run_b.total_tokens,
            cost_delta_usd=run_a.estimated_cost_usd - run_b.estimated_cost_usd,
            turns_delta=run_a.total_steps - run_b.total_steps,
            divergence_step=divergence_step,
            divergence_reason=divergence_reason,
            step_diffs=step_diffs,
        )

    @classmethod
    def load_and_compare(
        cls, run_a_id: str, run_b_id: str, logs_dir: Path
    ) -> TrajectoryDiffSummary | None:
        """Fetch RunRecords from store or .eval files and produce a full diff."""
        run_a = global_run_store.get_run(run_a_id)
        run_b = global_run_store.get_run(run_b_id)

        if not run_a and logs_dir.exists():
            for f in logs_dir.glob("*.eval"):
                if run_a_id in f.stem or f"inspect_{f.stem[-8:]}" == run_a_id:
                    run_a = parse_eval_log_to_run_record(f)
                    break

        if not run_b and logs_dir.exists():
            for f in logs_dir.glob("*.eval"):
                if run_b_id in f.stem or f"inspect_{f.stem[-8:]}" == run_b_id:
                    run_b = parse_eval_log_to_run_record(f)
                    break

        if not run_a or not run_b:
            return None

        return cls.compare_runs(run_a, run_b)

    @classmethod
    async def semantic_compare_runs(
        cls,
        run_a: RunRecord,
        run_b: RunRecord,
        runner: Any | None = None,
        model: str | None = None,
    ) -> SemanticComparisonVerdict:
        """Use LLM-as-a-Judge to evaluate semantic trajectory similarity and strategic divergence."""
        from engine.judges import format_trajectory_for_judge
        from engine.llm_runner import AsyncLLMRunner, ChatMessage, LLMConfig
        from engine.react_agent import AgentTrajectory

        traj_a = AgentTrajectory(task_id=run_a.task_id, steps=run_a.steps or [])
        traj_b = AgentTrajectory(task_id=run_b.task_id, steps=run_b.steps or [])

        # Construct prompt
        prompt = (
            "You are an expert AI Alignment and Autonomous Agent Trajectory Judge.\n"
            "Compare the two agent execution trajectories below on the same or related task.\n\n"
            f"Task: {run_a.task_id}\n\n"
            f"### Trajectory A (Model: {run_a.model}, Run ID: {run_a.run_id}, Passed: {run_a.passed}):\n"
            f"{format_trajectory_for_judge(traj_a)}\n\n"
            f"### Trajectory B (Model: {run_b.model}, Run ID: {run_b.run_id}, Passed: {run_b.passed}):\n"
            f"{format_trajectory_for_judge(traj_b)}\n\n"
            "Determine if the differences between these runs are purely COSMETIC (e.g. minor flag changes in ls/grep, whitespace, identical overall problem-solving strategy) "
            "or SUBSTANTIVE (e.g. fundamentally different problem-solving strategies, one agent hallucinating, or different code architectures).\n"
            "Identify the exact turn where true strategic divergence began (if any).\n"
            "Output strictly according to the SemanticComparisonVerdict JSON schema."
        )

        messages = [
            ChatMessage(
                role="system",
                content=(
                    "Evaluate semantic trajectory comparison between two AI agent runs. "
                    "Distinguish trivial command differences from substantive strategic divergences."
                ),
            ),
            ChatMessage(role="user", content=prompt),
        ]

        if runner is None:
            runner = AsyncLLMRunner.create_default()

        cfg = LLMConfig(model=model or runner.provider, temperature=0.0)

        try:
            resp = await runner.generate_structured(
                messages=messages,
                response_schema=SemanticComparisonVerdict,
                config=cfg,
            )
            if resp.parsed:
                verdict = resp.parsed
                verdict.run_a_id = run_a.run_id
                verdict.run_b_id = run_b.run_id
                verdict.task_id = run_a.task_id
                return verdict
        except Exception as e:
            logger.warning(f"LLM semantic comparison failed, falling back to heuristic evaluation: {e}")

        # Heuristic fallback if LLM is unreachable or offline
        steps_a = run_a.steps or []
        steps_b = run_b.steps or []
        tools_a = [s.action.tool for s in steps_a]
        tools_b = [s.action.tool for s in steps_b]

        tool_overlap = sum(1 for t1, t2 in zip(tools_a, tools_b) if t1 == t2)
        total_steps = max(len(steps_a), len(steps_b), 1)
        sim_ratio = tool_overlap / total_steps

        if run_a.passed == run_b.passed and sim_ratio >= 0.8:
            nature = "cosmetic_variation" if sim_ratio < 1.0 else "identical_strategy"
            score = 0.90 if nature == "identical_strategy" else 0.82
            summary = (
                f"Both models ({run_a.model} vs {run_b.model}) followed a functionally equivalent workflow "
                f"with minor tool parameter variations. Both runs concluded with status: {'PASSED' if run_a.passed else 'FAILED'}."
            )
            key_diffs = [
                f"Model A took {len(steps_a)} turns ({run_a.total_duration_sec:.1f}s), Model B took {len(steps_b)} turns ({run_b.total_duration_sec:.1f}s).",
                "Tool invocations and investigative steps followed the same logical progression.",
            ]
            div_turn = None
            attribution = "Both models achieved identical outcomes with comparable reasoning."
        else:
            nature = "substantive_divergence"
            score = round(max(0.2, min(0.7, sim_ratio * 0.8)), 2)
            summary = (
                f"Model A ({run_a.model}) and Model B ({run_b.model}) showed substantive divergence. "
                f"Outcome: Model A is {'PASSED' if run_a.passed else 'FAILED'}, while Model B is {'PASSED' if run_b.passed else 'FAILED'}."
            )
            key_diffs = [
                f"Model A tool sequence: {', '.join(tools_a[:4])}...",
                f"Model B tool sequence: {', '.join(tools_b[:4])}...",
            ]
            div_turn = next((i + 1 for i, (t1, t2) in enumerate(zip(tools_a, tools_b)) if t1 != t2), 1)
            attribution = (
                f"Model A ({run_a.model}) was {'more successful' if run_a.passed else 'less successful'} than Model B ({run_b.model})."
            )

        return SemanticComparisonVerdict(
            run_a_id=run_a.run_id,
            run_b_id=run_b.run_id,
            task_id=run_a.task_id,
            nature_of_divergence=nature,
            semantic_similarity_score=score,
            verdict_summary=summary,
            key_strategic_differences=key_diffs,
            true_divergence_turn=div_turn,
            attribution_reasoning=attribution,
        )

    @classmethod
    async def load_and_semantic_compare(
        cls,
        run_a_id: str,
        run_b_id: str,
        logs_dir: Path,
        runner: Any | None = None,
        model: str | None = None,
    ) -> SemanticComparisonVerdict | None:
        """Fetch RunRecords and execute semantic trajectory comparison."""
        run_a = global_run_store.get_run(run_a_id)
        run_b = global_run_store.get_run(run_b_id)

        if not run_a and logs_dir.exists():
            for f in logs_dir.glob("*.eval"):
                if run_a_id in f.stem or f"inspect_{f.stem[-8:]}" == run_a_id:
                    run_a = parse_eval_log_to_run_record(f)
                    break

        if not run_b and logs_dir.exists():
            for f in logs_dir.glob("*.eval"):
                if run_b_id in f.stem or f"inspect_{f.stem[-8:]}" == run_b_id:
                    run_b = parse_eval_log_to_run_record(f)
                    break

        if not run_a or not run_b:
            return None

        return await cls.semantic_compare_runs(run_a, run_b, runner=runner, model=model)

