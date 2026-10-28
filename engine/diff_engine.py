"""Trajectory Diff Engine for OpenEval Studio.

Compares two evaluation runs (from Inspect .eval archives or RunRecords) to compute
synchronized turn-by-turn alignment, thought divergence, tool argument diffs,
token/cost deltas, and automatic divergence point detection.
"""

import difflib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from engine.react_agent import AgentStep
from schemas.scoring import ScoreStatus
from schemas.watcher_models import Session
from server.inspect_loader import parse_eval_log_to_run_record
from server.store import global_run_store

logger = logging.getLogger("openeval.engine.diff")


class SemanticComparisonVerdict(BaseModel):
    """Structured LLM Semantic Trajectory Comparison verdict."""

    model_config = ConfigDict(extra="ignore")

    run_a_id: str
    run_b_id: str
    task_id: str
    status: ScoreStatus = Field(
        default="scored", description="Whether semantic comparison succeeded or errored"
    )
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
        default_factory=list,
        description="Specific differences in strategy, tool flow, or debugging",
    )
    true_divergence_turn: int | None = Field(
        default=None,
        description="The specific turn where true strategic divergence occurred, or None if purely cosmetic",
    )
    attribution_reasoning: str = Field(
        ...,
        description="Explanation of why one model performed better, faster, or if both are equivalent",
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
    def compare_runs(cls, run_a: Session, run_b: Session) -> TrajectoryDiffSummary:
        """Perform full turn-by-turn alignment and metric diff between Run A and Run B."""
        steps_a = [
            s if isinstance(s, AgentStep) else AgentStep.model_validate(s)
            for s in (run_a.steps or [])
        ]
        steps_b = [
            s if isinstance(s, AgentStep) else AgentStep.model_validate(s)
            for s in (run_b.steps or [])
        ]
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

                # Step is divergent if tools differ, file targets differ, or reasoning strongly diverges
                structural_mismatch = (
                    not tool_match
                    or (sa.action.path and sb.action.path and sa.action.path != sb.action.path)
                    or (
                        sa.action.tool in ("write_file", "edit_file", "replace_file_content")
                        and not args_match
                    )
                    or sim < 0.3
                )

                if structural_mismatch or not args_match:
                    is_div = True
                    if divergence_step is None and structural_mismatch:
                        divergence_step = step_num
                        if not tool_match:
                            divergence_reason = (
                                f"Tool divergence at Turn {step_num}: Model A used "
                                f"'{sa.action.tool}' vs Model B '{sb.action.tool}'."
                            )
                        elif sa.action.path and sb.action.path and sa.action.path != sb.action.path:
                            divergence_reason = (
                                f"Target file divergence at Turn {step_num}: Model A accessed "
                                f"'{sa.action.path}' vs Model B '{sb.action.path}'."
                            )
                        elif sa.action.tool in ("write_file", "edit_file", "replace_file_content"):
                            divergence_reason = f"File modification divergence at Turn {step_num}: Different contents written."
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
        run_a: Session,
        run_b: Session,
        runner: Any | None = None,
        model: str | None = None,
    ) -> SemanticComparisonVerdict:
        """Use LLM-as-a-Judge to evaluate semantic trajectory similarity and strategic divergence."""
        from engine.judges import format_trajectory_for_judge
        from engine.llm_runner import AsyncLLMRunner, ChatMessage, LLMConfig
        from engine.react_agent import AgentTrajectory

        traj_a = AgentTrajectory(task_id=run_a.task_id, model=run_a.model, steps=run_a.steps or [])
        traj_b = AgentTrajectory(task_id=run_b.task_id, model=run_b.model, steps=run_b.steps or [])

        # Invariant 1: If comparing two runs from entirely different tasks, return immediate task mismatch verdict
        if run_a.task_id != run_b.task_id:
            return SemanticComparisonVerdict(
                run_a_id=run_a.run_id,
                run_b_id=run_b.run_id,
                task_id=f"{run_a.task_id} vs {run_b.task_id}",
                status="scored",
                nature_of_divergence="opposite_strategies",
                semantic_similarity_score=0.0,
                verdict_summary=(
                    f"Task Mismatch: Run A evaluated '{run_a.task_id}' ({'PASSED' if run_a.passed else 'FAILED'}) "
                    f"while Run B evaluated '{run_b.task_id}' ({'PASSED' if run_b.passed else 'FAILED'}). "
                    "These runs evaluated entirely different benchmark objectives and environments."
                ),
                key_strategic_differences=[
                    f"Run A Target Task: {run_a.task_id} (Model: {run_a.model})",
                    f"Run B Target Task: {run_b.task_id} (Model: {run_b.model})",
                    "The task objectives, environment configurations, and verification test suites are completely distinct.",
                ],
                true_divergence_turn=1,
                attribution_reasoning=(
                    f"Cross-task trajectory comparison between '{run_a.task_id}' and '{run_b.task_id}' "
                    "shows complete divergence because each agent pursued entirely different task specifications."
                ),
            )

        outcome_guardrail = ""
        if run_a.passed != run_b.passed:
            passing = "Run A" if run_a.passed else "Run B"
            failing = "Run B" if run_a.passed else "Run A"
            outcome_guardrail = (
                f"\n### CRITICAL GROUND TRUTH OUTCOME INVARIANT:\n"
                f"- {passing} PASSED the benchmark verifier (Reward: 1.0).\n"
                f"- {failing} FAILED the benchmark verifier (Reward: 0.0).\n"
                f"INVARIANT: Because the final benchmark outcomes conflict ({passing} passed, {failing} failed), "
                f"these trajectories CANNOT be classified as 'identical_strategy' or have 100% equivalence! "
                f"You MUST classify this as 'substantive_divergence' or 'opposite_strategies', and identify the exact "
                f"turn where {failing} diverged, failed assertions, or missed critical task requirements.\n"
            )

        # Construct prompt
        prompt = (
            "You are an expert AI Alignment and Autonomous Agent Trajectory Judge.\n"
            "Compare the two agent execution trajectories below on the same benchmark task.\n\n"
            f"### Task: {run_a.task_id}\n\n"
            f"{outcome_guardrail}"
            f"### Trajectory A (Model: {run_a.model}, Run ID: {run_a.run_id}, Passed: {run_a.passed}):\n"
            f"{format_trajectory_for_judge(traj_a)}\n\n"
            f"### Trajectory B (Model: {run_b.model}, Run ID: {run_b.run_id}, Passed: {run_b.passed}):\n"
            f"{format_trajectory_for_judge(traj_b)}\n\n"
            "Determine if the differences between these runs are purely COSMETIC (e.g. minor flag changes in ls/grep, whitespace, identical overall problem-solving strategy) "
            "or SUBSTANTIVE (e.g. fundamentally different problem-solving strategies, one agent hallucinating, or different code architectures).\n"
            "Identify the exact turn where true strategic divergence began (if any).\n"
            "Output strictly according to the SemanticComparisonVerdict JSON schema."
        )

        from engine.graders_suite import get_canonical_grader

        comparator_grader = get_canonical_grader("SemanticRunComparatorGrader")
        sys_prompt = (
            comparator_grader.assemble_system_prompt()
            if comparator_grader
            else (
                "Evaluate semantic trajectory comparison between two AI agent runs. "
                "Distinguish trivial command differences from substantive strategic divergences."
            )
        )

        messages = [
            ChatMessage(role="system", content=sys_prompt),
            ChatMessage(role="user", content=prompt),
        ]

        if runner is None:
            try:
                runner = AsyncLLMRunner()
            except Exception:
                runner = None

        # Default to a live Gemini Flash-Lite id (not provider name — that silently
        # failed API calls and fell back to the local heuristic).
        judge_model = model or os.environ.get("OPENEVAL_COMPARE_MODEL", "gemini-3.1-flash-lite")
        cfg = LLMConfig(
            model=judge_model,
            provider="google",
            temperature=0.0,
        )

        if runner is not None:
            try:
                resp = await runner.generate(
                    messages=messages,
                    config=cfg,
                    response_schema=SemanticComparisonVerdict,
                )
                if resp.parsed_json:
                    verdict = SemanticComparisonVerdict.model_validate(resp.parsed_json)
                    verdict.run_a_id = run_a.run_id
                    verdict.run_b_id = run_b.run_id
                    verdict.task_id = run_a.task_id

                    # Grounding Invariant: If one run passed and the other failed, it cannot be identical_strategy or 100% equivalence
                    if run_a.passed != run_b.passed:
                        if verdict.nature_of_divergence in (
                            "identical_strategy",
                            "cosmetic_variation",
                        ):
                            verdict.nature_of_divergence = "substantive_divergence"
                        if verdict.semantic_similarity_score > 0.60:
                            verdict.semantic_similarity_score = min(
                                verdict.semantic_similarity_score, 0.45
                            )
                    return verdict
            except Exception as e:
                logger.warning(
                    "LLM semantic comparison failed: %s",
                    e,
                )
                return SemanticComparisonVerdict(
                    run_a_id=run_a.run_id,
                    run_b_id=run_b.run_id,
                    task_id=run_a.task_id,
                    status="error",
                    nature_of_divergence="substantive_divergence",
                    semantic_similarity_score=0.0,
                    verdict_summary=f"LLM semantic comparison failed ({type(e).__name__}: {e}). Heuristic fallback disabled to prevent fabricated evaluation.",
                    key_strategic_differences=["Evaluation error: LLM judge unavailable."],
                    true_divergence_turn=None,
                    attribution_reasoning="Evaluation aborted due to judge error.",
                )

        return SemanticComparisonVerdict(
            run_a_id=run_a.run_id,
            run_b_id=run_b.run_id,
            task_id=run_a.task_id,
            status="error",
            nature_of_divergence="substantive_divergence",
            semantic_similarity_score=0.0,
            verdict_summary="LLM runner not configured or available for semantic comparison.",
            key_strategic_differences=["No LLM runner available."],
            true_divergence_turn=None,
            attribution_reasoning="Evaluation aborted due to missing LLM runner.",
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
