"""Inspect AI Evaluation Log Loader for OpenEval Studio.

Parses official UK AISI .eval log archives and exposes them as structured
RunRecord objects for the web dashboard and leaderboard.
"""

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from inspect_ai.log import read_eval_log

from engine.judges import JudgeVerdict
from engine.react_agent import AgentAction, AgentStep
from schemas.models import get_model_spec
from schemas.watcher_models import (
    Message,
    ReviewRecord,
    Session,
    SessionStatus,
    ToolCall,
    ToolResult,
    Trajectory,
)

logger = logging.getLogger("openeval.server.inspect_loader")

# Inspect AI scorer names -> canonical OpenEval safety pillar names.
CANONICAL_SCORER_MAP: dict[str, str] = {
    "reward_tampering_scorer": "reward_tampering",
    "reward_tampering": "reward_tampering",
    "plan_adherence_scorer": "plan_adherence",
    "plan_adherence": "plan_adherence",
    "grounding_and_citation_scorer": "hallucination_detection",
    "grounding_and_citation": "hallucination_detection",
    "hallucination_detection": "hallucination_detection",
    "cybersecurity_bounds_scorer": "cybersecurity_bounds",
    "cybersecurity_bounds": "cybersecurity_bounds",
    "alignment_faking_scorer": "alignment_faking",
    "alignment_faking": "alignment_faking",
}


def _score_value_to_float(val: Any) -> float | None:  # noqa: ANN401 - Inspect AI values are untyped
    """Normalize an Inspect AI score value into a 0.0-1.0 float (or None if unscored)."""
    if isinstance(val, bool):
        return 1.0 if val else 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        token = val.strip().upper()
        if token in ("C", "CORRECT", "P", "PASS", "TRUE"):
            return 1.0
        if token in ("I", "INCORRECT", "F", "FAIL", "FALSE"):
            return 0.0
    return None


# Provider prefixes Inspect AI uses on EvalSpec.model (`<provider>/<model>`).
_KNOWN_MODEL_PROVIDERS: frozenset[str] = frozenset(
    {"openai", "anthropic", "google", "opencode", "deepseek", "qwen", "mistral"}
)


def _infer_provider_from_model(raw_model: str) -> str:
    """Infer a provider slug from an Inspect model id.

    Handles both provider-prefixed ids (``anthropic/claude-3-7-sonnet``) and bare
    model names (``gpt-4o``). ``opencode`` is a routing prefix, not a real
    provider, so it falls through to the model-name heuristics.
    """
    model = (raw_model or "").strip()
    clean = model
    if "/" in model:
        prefix, _, rest = model.partition("/")
        prefix_lower = prefix.strip().lower()
        if prefix_lower in _KNOWN_MODEL_PROVIDERS and prefix_lower != "opencode":
            return prefix_lower
        clean = rest.strip() or model

    lowered = clean.lower()
    if lowered.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai"
    if lowered.startswith("claude-"):
        return "anthropic"
    if lowered.startswith("deepseek-"):
        return "deepseek"
    if lowered.startswith("qwen"):
        return "qwen"
    if lowered.startswith("glm-"):
        return "zhipu"
    return "google"


def parse_eval_log_to_run_record(eval_file_path: Path) -> Session | None:
    """Parse a single .eval file into a full OpenEval RunRecord."""
    try:
        log = read_eval_log(str(eval_file_path))
        if not log or not log.eval:
            return None

        task_id = log.eval.task.replace("_", "-")
        raw_model = log.eval.model or "unknown"
        clean_model = raw_model.split("/")[-1] if "/" in raw_model else raw_model
        provider = _infer_provider_from_model(raw_model)

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
        trajectory_messages: list[Message] = []
        trajectory_tool_calls: list[ToolCall] = []
        trajectory_tool_results: list[ToolResult] = []
        trajectory_reviews: list[ReviewRecord] = []
        reward: float | None = None
        passed: bool | None = None
        failure_reason: str | None = None
        total_tokens = 0
        total_duration = 0.0
        final_summary: str | None = None
        audit_verdicts: list[JudgeVerdict] = []

        sample_rewards: list[float] = []
        sample_failures: list[str] = []
        audit_buckets: dict[str, list[tuple[float, str]]] = {}
        step_num = 1

        for sample in log.samples or []:
            total_duration += float(sample.total_time or 0.0)

            # Extract model usage tokens if available
            if getattr(sample, "model_usage", None):
                for _model_key, usage in sample.model_usage.items():
                    if hasattr(usage, "total_tokens"):
                        total_tokens += usage.total_tokens

            # --- Scores: held-out verifier reward + 4-pillar safety audit ---
            sample_scores = getattr(sample, "scores", None) or {}
            has_verifier = "held_out_verifier_scorer" in sample_scores

            if sample_scores:
                if has_verifier:
                    verifier_score = sample_scores["held_out_verifier_scorer"]
                    fval = _score_value_to_float(getattr(verifier_score, "value", None))
                    if fval is not None:
                        sample_rewards.append(fval)
                        expl = str(getattr(verifier_score, "explanation", "") or "").strip()
                        if fval < 0.5 and expl:
                            sample_failures.append(expl)

                for scorer_name, score_obj in sample_scores.items():
                    if scorer_name == "held_out_verifier_scorer":
                        continue
                    fval = _score_value_to_float(getattr(score_obj, "value", None))
                    if fval is None:
                        continue
                    expl = str(getattr(score_obj, "explanation", "") or "").strip()
                    canonical = CANONICAL_SCORER_MAP.get(scorer_name)
                    if canonical is not None:
                        audit_buckets.setdefault(canonical, []).append(
                            (fval, expl or f"Evaluated by {scorer_name}.")
                        )
                    else:
                        audit_buckets.setdefault(scorer_name.removesuffix("_scorer"), []).append(
                            (fval, expl or f"Evaluated by {scorer_name}.")
                        )
                        if not has_verifier:
                            sample_rewards.append(fval)
                            if fval < 0.5 and expl:
                                sample_failures.append(expl)

            # --- Trajectory reconstruction (shared across all samples) ---
            steps_before_sample = len(steps)
            last_assistant_text = ""

            for i, msg in enumerate(sample.messages or []):
                msg_role = getattr(msg, "role", None)
                msg_content = getattr(msg, "content", "")
                content_str = (
                    msg_content
                    if isinstance(msg_content, str)
                    else " ".join(
                        str(getattr(p, "text", "")) for p in msg_content if hasattr(p, "text")
                    )
                    if isinstance(msg_content, list)
                    else str(msg_content)
                )

                if msg_role == "user":
                    trajectory_messages.append(Message(role="user", content=content_str))
                    continue
                elif msg_role == "tool":
                    tr_id = getattr(msg, "tool_call_id", None) or (
                        trajectory_tool_calls[-1].tool_id
                        if trajectory_tool_calls
                        else f"res_{len(trajectory_tool_results) + 1}"
                    )
                    tr_name = (
                        trajectory_tool_calls[-1].tool_name if trajectory_tool_calls else "tool"
                    )
                    tr_obj = ToolResult(tool_id=tr_id, tool_name=tr_name, stdout=content_str)
                    trajectory_tool_results.append(tr_obj)
                    continue

                tool_calls = getattr(msg, "tool_calls", None)
                if msg_role == "assistant" and isinstance(tool_calls, list):
                    thought = content_str
                    if thought.strip():
                        last_assistant_text = thought.strip()
                    step_tcs: list[ToolCall] = []

                    for tc in tool_calls:
                        fn_name = getattr(tc, "function", "")
                        raw_args = getattr(tc, "arguments", {})
                        args = raw_args if isinstance(raw_args, dict) else {}

                        if fn_name in ("execute_bash", "bash"):
                            tool_type = "execute_bash"
                            cmd = args.get("cmd") or args.get("command")
                            path = None
                            content = None
                            # Detect code mutations written via bash/python scripts
                            if cmd:
                                import re

                                open_match = re.search(
                                    r'open\s*\(\s*["\']([^"\']+)["\']\s*,\s*["\']w["\']\s*\)', cmd
                                )
                                redir_match = re.search(
                                    r"(?:>{1,2}|tee)\s+([a-zA-Z0-9_\./\-]+)", cmd
                                )
                                if open_match:
                                    path = open_match.group(1)
                                    tool_type = "write_file"
                                    content = cmd
                                elif redir_match and not cmd.strip().startswith("ls"):
                                    cand_path = redir_match.group(1)
                                    if "/" in cand_path or cand_path.endswith(
                                        (".txt", ".py", ".sh", ".json", ".md", ".log")
                                    ):
                                        path = cand_path
                                        tool_type = "write_file"
                                        content = cmd
                        elif fn_name == "text_editor":
                            editor_cmd = args.get("command")
                            if editor_cmd == "view":
                                tool_type = "view_file"
                                cmd = None
                                path = args.get("path")
                                content = None
                            else:
                                tool_type = "write_file"
                                cmd = None
                                path = args.get("path")
                                content = (
                                    args.get("file_text")
                                    or args.get("new_str")
                                    or args.get("content")
                                )
                        elif fn_name in ("view_file", "read_file"):
                            tool_type = "view_file"
                            cmd = None
                            path = args.get("path")
                            content = None
                        elif fn_name in ("write_file", "edit_file"):
                            tool_type = "write_file"
                            cmd = None
                            path = args.get("path")
                            content = args.get("content") or args.get("text")
                        else:
                            tool_type = "finish"
                            cmd = None
                            path = None
                            content = None

                        action = AgentAction(
                            tool=tool_type,  # type: ignore[arg-type]
                            thought=thought.strip(),
                            command=cmd,
                            path=path,
                            content=content,
                            summary=args.get("answer") or args.get("summary") or args.get("text"),
                        )

                        if tool_type == "finish" and action.summary:
                            final_summary = action.summary

                        obs = ""
                        if i + 1 < len(sample.messages):
                            next_msg = sample.messages[i + 1]
                            if getattr(next_msg, "role", None) == "tool":
                                obs = str(next_msg.content)

                        raw_inp = str(
                            cmd
                            or path
                            or args.get("command")
                            or args.get("path")
                            or json.dumps(args)
                        )
                        tc_id = getattr(tc, "id", None) or f"call_{len(trajectory_tool_calls) + 1}"
                        tc_obj = ToolCall(
                            tool_id=tc_id,
                            tool_name=str(fn_name or tool_type),
                            arguments=args,
                            raw_input=raw_inp,
                        )
                        trajectory_tool_calls.append(tc_obj)
                        step_tcs.append(tc_obj)

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

                    trajectory_messages.append(
                        Message(
                            role="assistant",
                            content=thought or f"[{len(step_tcs)} tool call(s)]",
                            tool_calls=step_tcs,
                        )
                    )
                elif msg_role == "assistant":
                    # Plain assistant message without tool calls (e.g. final completion).
                    if content_str.strip():
                        last_assistant_text = content_str.strip()
                        trajectory_messages.append(Message(role="assistant", content=content_str))

            # Guarantee non-tool completions still surface as an agent step.
            if len(steps) == steps_before_sample:
                sample_completion = str(
                    getattr(getattr(sample, "output", None), "completion", "") or ""
                )
                fallback_text = sample_completion or last_assistant_text
                if fallback_text:
                    steps.append(
                        AgentStep(
                            step_number=step_num,
                            thought="Inspect AI assistant turn",
                            action=AgentAction(
                                thought="Execute completion",
                                tool="generate_response",
                                content=fallback_text,
                                summary=fallback_text,
                            ),
                            observation=fallback_text,
                            latency_ms=0.0,
                            tokens_used=0,
                        )
                    )
                    step_num += 1
                    if not final_summary:
                        final_summary = fallback_text

        if sample_rewards:
            reward = round(sum(sample_rewards) / len(sample_rewards), 4)
            passed = reward >= 0.5
            if passed:
                failure_reason = None
            else:
                failure_reason = (
                    "; ".join(dict.fromkeys(sample_failures)) or "Failed Inspect AI evaluation."
                )

        for metric_name, entries in audit_buckets.items():
            avg_score = round(sum(s for s, _ in entries) / len(entries), 4)
            reasoning = next(
                (e for _, e in entries if e), f"Scored by Inspect AI {metric_name} scorer."
            )
            audit_verdicts.append(
                JudgeVerdict(
                    metric_name=metric_name,
                    status="scored",
                    score=avg_score,
                    passed=avg_score >= 0.5,
                    reasoning=reasoning,
                )
            )

        for pillar in ("plan_adherence", "hallucination_detection", "cybersecurity_bounds"):
            if not any(v.metric_name == pillar for v in audit_verdicts):
                audit_verdicts.append(
                    JudgeVerdict.not_run(
                        metric_name=pillar,
                        reason="Scorer not evaluated in Inspect log.",
                    )
                )

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

        if not trajectory_reviews and trajectory_tool_calls:
            from server.policy_gateway import PolicyGateway

            gw = PolicyGateway.from_store(use_llm=False)
            for tc in trajectory_tool_calls:
                rec = gw.evaluate_tool_call(
                    session_id=run_id,
                    tool_name=tc.tool_name,
                    tool_input=tc.raw_input or "",
                    user_intent=f"UK AISI Inspect evaluation on {task_id}",
                )
                trajectory_reviews.append(rec)

        blocked_revs = [
            r
            for r in trajectory_reviews
            if (r.decision in ("block", "deny", "escalate", "reject") or r.score >= 8)
            and r.human_override != "allow"
            and r.resolution_status != "human_approved"
        ]
        final_status: SessionStatus = (
            "blocked" if blocked_revs else "completed" if status == "completed" else "failed"
        )
        blocked_count = len(blocked_revs)

        trajectory = Trajectory(
            session_id=run_id,
            messages=trajectory_messages,
            tool_calls=trajectory_tool_calls,
            tool_results=trajectory_tool_results,
            reviews=trajectory_reviews,
        )

        return Session(
            session_id=run_id,
            run_id=run_id,
            task_id=task_id,
            model=clean_model,
            provider=provider,
            status=final_status,
            trajectory=trajectory,
            decisions=trajectory_reviews,
            blocked_count=blocked_count,
            blocked_turns_count=blocked_count,
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


_INSPECT_RECORD_CACHE: dict[Path, tuple[int, int, Session | None]] = {}
_INSPECT_CACHE_LOCK = threading.Lock()


def list_inspect_run_records(logs_dir: Path) -> list[Session]:
    """Scan logs_dir for all .eval archives and return parsed Sessions."""
    if not logs_dir.exists():
        return []

    eval_files = sorted(
        logs_dir.glob("*.eval"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    records: list[Session] = []
    for f in eval_files:
        try:
            stat = f.stat()
            sig = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            continue
        with _INSPECT_CACHE_LOCK:
            cached = _INSPECT_RECORD_CACHE.get(f)
        if cached is not None and cached[0] == sig[0] and cached[1] == sig[1]:
            rec = cached[2]
        else:
            rec = parse_eval_log_to_run_record(f)
            with _INSPECT_CACHE_LOCK:
                if len(_INSPECT_RECORD_CACHE) >= 256:
                    _INSPECT_RECORD_CACHE.clear()
                _INSPECT_RECORD_CACHE[f] = (sig[0], sig[1], rec)
        if rec:
            records.append(rec)

    return records
