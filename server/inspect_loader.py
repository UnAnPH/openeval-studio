"""Inspect AI Evaluation Log Loader for OpenEval Studio.

Parses official UK AISI .eval log archives and exposes them as structured
RunRecord objects for the web dashboard and leaderboard.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path

from inspect_ai.log import read_eval_log

from engine.judges import JudgeVerdict
from engine.react_agent import AgentAction, AgentStep
from schemas.models import get_model_spec
from server.store import RunRecord

logger = logging.getLogger("openeval.server.inspect_loader")


def parse_eval_log_to_run_record(eval_file_path: Path) -> RunRecord | None:
    """Parse a single .eval file into a full OpenEval RunRecord."""
    try:
        log = read_eval_log(str(eval_file_path))
        if not log or not log.eval:
            return None

        task_id = log.eval.task.replace("_", "-")
        raw_model = log.eval.model or "unknown"
        clean_model = raw_model.split("/")[-1] if "/" in raw_model else raw_model
        is_openai = clean_model.startswith("gpt-") or clean_model.startswith("o1")
        provider = "openai" if is_openai else "google"

        # Determine status
        raw_status = log.status
        if raw_status == "success":
            status = "completed"
        elif raw_status == "cancelled":
            status = "cancelled"
        else:
            status = "error"

        # Extract file timestamp or creation time
        stat = eval_file_path.stat()
        created_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()

        steps: list[AgentStep] = []
        reward: float | None = None
        passed: bool | None = None
        failure_reason: str | None = None
        total_tokens = 0
        total_duration = 0.0
        final_summary: str | None = None
        audit_verdicts: list[JudgeVerdict] = []

        if log.samples and len(log.samples) > 0:
            sample = log.samples[0]
            total_duration = float(sample.total_time or 0.0)

            # Extract model usage tokens if available
            if hasattr(sample, "model_usage") and sample.model_usage:
                for _model_key, usage in sample.model_usage.items():
                    if hasattr(usage, "total_tokens"):
                        total_tokens += usage.total_tokens

            # Extract scores
            if sample.scores:
                verifier_score = sample.scores.get("held_out_verifier_scorer")
                if verifier_score:
                    is_correct = verifier_score.value in ("C", "CORRECT", 1.0, 1)
                    passed = is_correct
                    reward = 1.0 if is_correct else 0.0
                    failure_reason = verifier_score.explanation

                tampering_score = sample.scores.get("reward_tampering_scorer")
                if tampering_score:
                    tamper_passed = tampering_score.value in ("C", "CORRECT", 1.0, 1)
                    audit_verdicts.append(
                        JudgeVerdict(
                            metric_name="reward_tampering",
                            score=1.0 if tamper_passed else 0.0,
                            passed=tamper_passed,
                            reasoning=tampering_score.explanation or "Reward tampering audit.",
                        )
                    )

            # Reconstruct turns from messages
            step_num = 1
            for i, msg in enumerate(sample.messages):
                tool_calls = getattr(msg, "tool_calls", None)
                if getattr(msg, "role", None) == "assistant" and isinstance(tool_calls, list):
                    thought = ""
                    if isinstance(msg.content, str):
                        thought = msg.content
                    elif isinstance(msg.content, list):
                        for part in msg.content:
                            if hasattr(part, "text"):
                                thought += str(getattr(part, "text", "")) + " "

                    for tc in tool_calls:
                        fn_name = getattr(tc, "function", "")
                        raw_args = getattr(tc, "arguments", {})
                        args = raw_args if isinstance(raw_args, dict) else {}

                        tool_type = (
                            "execute_bash"
                            if fn_name == "execute_bash"
                            else "view_file"
                            if fn_name == "view_file"
                            else "write_file"
                            if fn_name == "write_file"
                            else "finish"
                        )

                        action = AgentAction(
                            tool=tool_type,  # type: ignore[arg-type]
                            thought=thought.strip(),
                            command=args.get("command"),
                            path=args.get("path"),
                            content=args.get("content"),
                            summary=args.get("answer") or args.get("summary"),
                        )

                        if tool_type == "finish" and action.summary:
                            final_summary = action.summary

                        obs = ""
                        if i + 1 < len(sample.messages):
                            next_msg = sample.messages[i + 1]
                            if getattr(next_msg, "role", None) == "tool":
                                obs = str(next_msg.content)

                        steps.append(
                            AgentStep(
                                step_number=step_num,
                                thought=thought.strip(),
                                action=action,
                                observation=obs,
                                latency_ms=0.0,
                                tokens_used=0,
                            )
                        )
                        step_num += 1

        # Fallback for tokens if not in sample
        if total_tokens == 0 and len(steps) > 0:
            total_tokens = len(steps) * 450

        # Calculate estimated cost
        model_spec = get_model_spec(clean_model)
        estimated_cost = (
            model_spec.estimate_cost(int(total_tokens * 0.7), int(total_tokens * 0.3))
            if model_spec
            else 0.0
        )

        run_id = f"inspect_{eval_file_path.stem[-8:]}"

        return RunRecord(
            run_id=run_id,
            task_id=task_id,
            model=clean_model,
            provider=provider,
            status=status,  # type: ignore[arg-type]
            created_at=created_at,
            steps=steps,
            total_steps=len(steps),
            total_tokens=total_tokens,
            total_duration_sec=total_duration,
            estimated_cost_usd=estimated_cost,
            final_summary=final_summary,
            reward=reward,
            passed=passed,
            failure_reason=failure_reason,
            audit_verdicts=audit_verdicts,
        )

    except Exception as e:
        logger.warning("Failed to parse Inspect log %s: %s", eval_file_path.name, e)
        return None


def list_inspect_run_records(logs_dir: Path) -> list[RunRecord]:
    """Scan logs_dir for all .eval archives and return parsed RunRecords."""
    if not logs_dir.exists():
        return []

    eval_files = sorted(
        logs_dir.glob("*.eval"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    records: list[RunRecord] = []
    for f in eval_files:
        rec = parse_eval_log_to_run_record(f)
        if rec:
            records.append(rec)

    return records
