"""Inspect AI EvalLog Exporter and Importer for OpenEval Studio.

Enforces the typed data contract:
Live production Sessions (composed of GateDecisions) -> Offline Inspect EvalLog (.eval).
Provides seamless round-trip preservation:
Session -> export_session_to_eval_log -> write_eval_log -> read_eval_log -> identical GateDecisions.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from inspect_ai import score_async
from inspect_ai.log import (
    EvalConfig,
    EvalDataset,
    EvalLog,
    EvalMetric,
    EvalPlan,
    EvalResults,
    EvalSample,
    EvalScore,
    EvalSpec,
    EvalStats,
    read_eval_log,
    write_eval_log,
)
from inspect_ai.model import (
    ChatMessage,
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageTool,
    ChatMessageUser,
    ModelConfig,
)
from inspect_ai.tool import ToolCall

from engine.react_agent import AgentAction, AgentStep
from schemas.gate_decision import GateDecision
from schemas.watcher_models import (
    Message,
    Session,
    ToolResult,
    Trajectory,
)
from schemas.watcher_models import (
    ToolCall as WatcherToolCall,
)

# Agent tool names that execute a shell command inside the sandbox.
_SHELL_TOOLS = ("execute_bash", "bash", "shell", "run_command")

# Judge identifiers that mean "no model judge ran" rather than a real model id.
_NON_MODEL_JUDGE_IDS = frozenset(
    {"", "none", "null", "deterministic", "deterministic_rules", "rules"}
)


def _inspect_model_ref(raw_model: Any) -> str | None:  # noqa: ANN401 - verdict fields are untyped
    """Normalize a stored judge/monitor model id into an Inspect `<provider>/<model>` ref."""
    if not isinstance(raw_model, str):
        return None
    model = raw_model.strip()
    if not model or model.lower() in _NON_MODEL_JUDGE_IDS:
        return None
    if "/" in model:
        return model

    from server.inspect_loader import _infer_provider_from_model

    return f"{_infer_provider_from_model(model)}/{model}"


def _extract_model_roles(
    session: Session,
    decisions: list[GateDecision],
) -> dict[str, ModelConfig]:
    """Derive Inspect `model_roles` (`grader`/`monitor`) from a recorded Session.

    The `grader` role comes from the judge models recorded on `audit_verdicts`;
    the `monitor` role comes from the calibrated decision telemetry model that
    produced the gate decisions. Roles are omitted entirely when no real model
    was involved (e.g. deterministic-only rule evaluation).
    """
    roles: dict[str, ModelConfig] = {}

    grader_model: str | None = None
    for verdict in session.audit_verdicts or []:
        raw_judge = (
            verdict.get("judge_model")
            if isinstance(verdict, dict)
            else getattr(verdict, "judge_model", None)
        )
        grader_model = grader_model or _inspect_model_ref(raw_judge)
    if grader_model:
        roles["grader"] = ModelConfig(model=grader_model)

    monitor_model: str | None = None
    for decision in decisions:
        telemetry = decision.calibrated_telemetry
        monitor_model = monitor_model or _inspect_model_ref(
            getattr(telemetry, "model", None) if telemetry is not None else None
        )
    if monitor_model:
        roles["monitor"] = ModelConfig(model=monitor_model)

    return roles


def _synthetic_messages_from_steps(session: Session) -> list[ChatMessage]:
    """Rebuild a real Inspect AI message trajectory from recorded agent steps.

    `inspect_ai.score_async` scorers inspect `ChatMessageAssistant.tool_calls` and
    `ChatMessageTool` observations, so synthesizing them from stored steps lets the
    four safety pillars re-score sessions that never captured raw trajectory messages.
    """
    messages: list[ChatMessage] = []

    for idx, raw_step in enumerate(session.steps or [], start=1):
        if hasattr(raw_step, "model_dump"):
            step: dict[str, Any] = raw_step.model_dump()
        elif isinstance(raw_step, dict):
            step = raw_step
        else:
            continue

        raw_action = step.get("action") or {}
        action: dict[str, Any] = raw_action if isinstance(raw_action, dict) else {}
        tool_name = str(action.get("tool") or "generate_response")
        thought = str(step.get("thought") or "")
        observation = str(step.get("observation") or "")

        if tool_name == "generate_response":
            messages.append(ChatMessageAssistant(content=observation or thought))
            continue

        arguments: dict[str, Any] = {}
        if action.get("command"):
            arguments["cmd"] = str(action["command"])
        if action.get("path"):
            arguments["path"] = str(action["path"])
        if action.get("content"):
            arguments["file_text"] = str(action["content"])

        call_id = f"call_{idx}"
        messages.append(
            ChatMessageAssistant(
                content=thought,
                tool_calls=[ToolCall(id=call_id, function=tool_name, arguments=arguments)],
            )
        )
        messages.append(
            ChatMessageTool(
                content=observation,
                tool_call_id=call_id,
                function=tool_name,
            )
        )

    return messages


def export_session_to_eval_log(session: Session, output_path: Path | str | None = None) -> EvalLog:
    """Export a runtime Session and its GateDecisions into an Inspect AI EvalLog.

    Args:
        session: The active or completed Session containing GateDecisions.
        output_path: Optional destination .eval file path to persist the log.

    Returns:
        The validated inspect_ai.log.EvalLog object.
    """
    # Collect decisions (authoritative: session.decisions, fallback: session.trajectory.reviews)
    raw_decisions = session.decisions or session.trajectory.reviews or []
    decisions: list[GateDecision] = [
        d if isinstance(d, GateDecision) else GateDecision.model_validate(d.model_dump())
        for d in raw_decisions
    ]

    # Convert session messages to Inspect ChatMessages
    messages: list[ChatMessage] = []
    has_assistant_tool_calls = False
    for msg in session.trajectory.messages:
        role = getattr(msg, "role", "user")
        content = getattr(msg, "content", "")
        if role == "system":
            messages.append(ChatMessageSystem(content=content))
        elif role == "assistant":
            assistant_tool_calls = [
                ToolCall(
                    id=tc.tool_id,
                    function=tc.tool_name,
                    arguments=dict(tc.arguments),
                )
                for tc in (getattr(msg, "tool_calls", None) or [])
            ]
            if assistant_tool_calls:
                has_assistant_tool_calls = True
            messages.append(
                ChatMessageAssistant(
                    content=content,
                    tool_calls=assistant_tool_calls or None,
                )
            )
        elif role == "tool":
            messages.append(ChatMessageTool(content=content))
        else:
            messages.append(ChatMessageUser(content=content))

    if messages:
        # Inspect scorers inspect `ChatMessageAssistant.tool_calls`, so a trajectory
        # that only stored prose messages still needs synthesized tool turns.
        if not has_assistant_tool_calls and session.steps:
            messages.extend(_synthetic_messages_from_steps(session))
    else:
        # Fall back to synthetic task user prompt if trajectory messages not explicitly stored
        prompt_text = (
            session.title
            or session.project_name
            or session.task_id
            or "OpenEval Watcher Live Session"
        )
        messages.append(ChatMessageUser(content=prompt_text))
        # Append synthetic assistant/tool turns so inspect_ai.score_async scorers can
        # inspect real trajectory structure instead of only the initial user prompt.
        messages.extend(_synthetic_messages_from_steps(session))

    # Build EvalSample
    sample = EvalSample(
        id=session.session_id,
        epoch=1,
        input=session.title or session.project_name or session.task_id or session.session_id,
        target="",
        messages=messages,
        metadata={
            "session_id": session.session_id,
            "run_id": session.run_id or session.session_id,
            "project_name": session.project_name,
            "task_id": session.task_id,
            "model": session.model,
            "provider": session.provider,
            "agent_type": session.agent_type,
            "status": session.status,
            "passed": session.passed,
            "reward": session.reward,
            "total_tokens": session.total_tokens,
            "total_duration_sec": session.total_duration_sec,
            "gate_decisions": [d.model_dump(by_alias=True) for d in decisions],
            "trajectory_messages": [m.model_dump() for m in session.trajectory.messages],
            "steps": [
                s.model_dump() if hasattr(s, "model_dump") else s for s in (session.steps or [])
            ],
            "audit_verdicts": [
                v.model_dump() if hasattr(v, "model_dump") else v
                for v in (session.audit_verdicts or [])
            ],
            "title": session.title,
            "org_id": session.org_id,
        },
    )

    created_iso = datetime.now(UTC).isoformat()
    # `inspect_ai.score_async` requires `<api_name>/<model_name>` on EvalSpec.model.
    # The original (unprefixed) model name is preserved in sample.metadata["model"]
    # so `import_eval_log_to_session` round-trips exactly.
    from server.inspect_loader import _infer_provider_from_model

    raw_model = (session.model or "").strip() or "mockllm/model"
    eval_model = (
        raw_model if "/" in raw_model else f"{_infer_provider_from_model(raw_model)}/{raw_model}"
    )
    eval_spec = EvalSpec(
        task=session.task_id or session.project_name or "watcher_governance_loop",
        model=eval_model,
        created=created_iso,
        dataset=EvalDataset(samples=1, sample_ids=[session.session_id]),
        config=EvalConfig(),
        model_roles=_extract_model_roles(session, decisions) or None,
        metadata={"session_id": session.session_id, "org_id": session.org_id},
    )

    scores_list: list[EvalScore] = []
    if session.audit_verdicts:
        for idx, verdict in enumerate(session.audit_verdicts):
            metric_name = getattr(verdict, "metric_name", None) or (
                verdict.get("metric_name") if isinstance(verdict, dict) else f"audit_{idx}"
            )
            raw_score = getattr(verdict, "score", None)
            if raw_score is None and isinstance(verdict, dict):
                raw_score = verdict.get("score")
            score_val = 1.0 if raw_score is None else float(raw_score)
            explanation = getattr(verdict, "reasoning", "") or (
                verdict.get("reasoning", "") if isinstance(verdict, dict) else ""
            )
            scores_list.append(
                EvalScore(
                    name=str(metric_name),
                    scorer=str(metric_name),
                    metrics={
                        "score": EvalMetric(
                            name="score",
                            value=float(score_val) if score_val is not None else 1.0,
                        )
                    },
                    metadata={"explanation": explanation} if explanation else None,
                )
            )

    eval_log = EvalLog(
        version=2,
        status="success" if session.status == "completed" else "error",
        eval=eval_spec,
        plan=EvalPlan(name="openeval_watcher_eval_plan", steps=[]),
        results=EvalResults(scores=scores_list),
        stats=EvalStats(started_at=created_iso, completed_at=created_iso),
        samples=[sample],
    )

    if output_path is not None:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        write_eval_log(eval_log, str(p))

    return eval_log


async def rescore_eval_log(
    eval_path: str | Path,
    scorers: Any,  # noqa: ANN401 - Inspect "Scorers" accepts Scorer | list[Scorer] | dict
    *,
    output_path: str | Path | None = None,
    action: Literal["append", "overwrite"] = "append",
) -> EvalLog:
    """Re-score an existing Inspect `.eval` log offline, without re-running the agent.

    This is the native Inspect AI re-scoring path: the recorded samples are read
    back from disk and handed to `inspect_ai.score_async`, so new or updated
    scorers (e.g. the four OpenEval safety pillars) can be applied to historical
    runs with zero model/agent re-execution.

    Args:
        eval_path: Path to the source `.eval` log.
        scorers: Inspect scorers to apply (a single `Scorer` or a list/dict).
        output_path: Optional destination `.eval` path for the rescored log.
        action: Whether to `"append"` new scores alongside existing ones or
            `"overwrite"` a scorer's previous score.

    Returns:
        The rescored `inspect_ai.log.EvalLog`.
    """
    log = read_eval_log(str(eval_path))
    rescored = await score_async(log, scorers=scorers, action=action)

    if output_path is not None:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_eval_log(rescored, str(destination))

    return rescored


def extract_decisions_from_eval_log(eval_log: EvalLog | Path | str) -> list[GateDecision]:
    """Extract and validate GateDecision atoms from an Inspect AI EvalLog.

    Args:
        eval_log: An in-memory EvalLog instance or file path to a .eval log.

    Returns:
        List of GateDecision objects identical to those produced at runtime.
    """
    log = read_eval_log(str(eval_log)) if isinstance(eval_log, (Path, str)) else eval_log

    if not log or not log.samples:
        return []

    decisions: list[GateDecision] = []
    for sample in log.samples:
        raw_list = (sample.metadata or {}).get("gate_decisions", [])
        for item in raw_list:
            decisions.append(GateDecision.model_validate(item))

    return decisions


def _content_to_text(content: Any) -> str:  # noqa: ANN401 - Inspect AI content blocks are untyped
    """Flatten Inspect AI message content (str or content-block list) into text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            text = getattr(part, "text", None)
            parts.append(str(text if text is not None else part))
        return " ".join(parts)
    return str(content or "")


def _reconstruct_external_sample(
    sample: Any,  # noqa: ANN401 - inspect_ai EvalSample
    *,
    step_start: int,
) -> tuple[list[Message], list[WatcherToolCall], list[ToolResult], list[AgentStep]]:
    """Rebuild OpenEval messages/tool calls/results/steps from a raw Inspect sample.

    Used for external ``.eval`` logs that carry no OpenEval ``trajectory_messages``
    metadata. `schemas.watcher_models.Message` has no ``tool`` role, so tool
    observations are collected as `ToolResult` entries instead.
    """
    messages: list[Message] = []
    tool_calls: list[WatcherToolCall] = []
    tool_results: list[ToolResult] = []
    steps: list[AgentStep] = []
    step_num = step_start

    for msg in getattr(sample, "messages", None) or []:
        role = getattr(msg, "role", None)
        text = _content_to_text(getattr(msg, "content", ""))

        if role == "system":
            messages.append(Message(role="system", content=text))
        elif role == "user":
            messages.append(Message(role="user", content=text))
        elif role == "tool":
            call_id = getattr(msg, "tool_call_id", None) or (
                tool_calls[-1].tool_id if tool_calls else f"res_{len(tool_results) + 1}"
            )
            name = getattr(msg, "function", None) or (
                tool_calls[-1].tool_name if tool_calls else "tool"
            )
            tool_results.append(ToolResult(tool_id=str(call_id), tool_name=str(name), stdout=text))
        elif role == "assistant":
            converted: list[WatcherToolCall] = []
            for tc in getattr(msg, "tool_calls", None) or []:
                raw_args = getattr(tc, "arguments", {})
                args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
                call = WatcherToolCall(
                    tool_id=str(getattr(tc, "id", None) or f"call_{len(tool_calls) + 1}"),
                    tool_name=str(getattr(tc, "function", None) or "unknown_tool"),
                    arguments=args,
                    raw_input=json.dumps(args),
                )
                converted.append(call)
                tool_calls.append(call)
                steps.append(
                    AgentStep(
                        step_number=step_num,
                        thought=text,
                        action=AgentAction(
                            tool=call.tool_name,  # type: ignore[arg-type]
                            thought=text,
                            command=args.get("cmd") or args.get("command"),  # type: ignore[arg-type]
                            path=args.get("path"),  # type: ignore[arg-type]
                            content=args.get("file_text") or args.get("content"),  # type: ignore[arg-type]
                            summary=args.get("answer") or args.get("summary"),  # type: ignore[arg-type]
                        ),
                        observation="",
                        latency_ms=0.0,
                        tokens_used=0,
                    )
                )
                step_num += 1

            if text.strip() or converted:
                messages.append(
                    Message(
                        role="assistant",
                        content=text or f"[{len(converted)} tool call(s)]",
                        tool_calls=converted,
                    )
                )

    return messages, tool_calls, tool_results, steps


def import_eval_log_to_session(eval_log: EvalLog | Path | str) -> Session | None:
    """Import an Inspect AI EvalLog back into a full OpenEval Session.

    Supports both OpenEval-exported logs (round-trip via ``trajectory_messages``)
    and external Inspect ``.eval`` logs, which are reconstructed from raw
    ``EvalSample.messages`` / ``tool_calls`` across every sample.

    Args:
        eval_log: An in-memory EvalLog instance or file path to a .eval log.

    Returns:
        Hydrated Session with intact GateDecisions.
    """
    from server.inspect_loader import _infer_provider_from_model

    log = read_eval_log(str(eval_log)) if isinstance(eval_log, (Path, str)) else eval_log

    if not log or not log.samples:
        return None

    samples = log.samples
    sample = samples[0]
    meta = sample.metadata or {}
    decisions = extract_decisions_from_eval_log(log)

    session_id = meta.get("session_id", sample.id or "eval_session")
    task_id = meta.get("task_id") or (log.eval.task if log.eval else "eval_task")
    raw_model = meta.get("model") or (log.eval.model if log.eval else "unknown")
    provider = meta.get("provider") or _infer_provider_from_model(str(raw_model))

    traj_msgs_raw = meta.get("trajectory_messages") or []
    trajectory_messages = [Message.model_validate(m) for m in traj_msgs_raw]
    trajectory_tool_calls: list[WatcherToolCall] = []
    trajectory_tool_results: list[ToolResult] = []
    steps: list[Any] = meta.get("steps") or []

    if traj_msgs_raw:
        # OpenEval-exported log: trust the serialized trajectory and stats.
        total_tokens = meta.get("total_tokens", 0)
        total_duration = meta.get("total_duration_sec", 0.0)
    else:
        # External Inspect log: rebuild everything across all samples.
        total_tokens = 0
        total_duration = 0.0
        trajectory_messages = []
        steps = []
        for raw_sample in samples:
            for usage in (getattr(raw_sample, "model_usage", None) or {}).values():
                total_tokens += int(getattr(usage, "total_tokens", 0) or 0)
            total_duration += float(getattr(raw_sample, "total_time", None) or 0.0)

            sample_messages, sample_calls, sample_results, sample_steps = (
                _reconstruct_external_sample(raw_sample, step_start=len(steps) + 1)
            )
            trajectory_messages.extend(sample_messages)
            trajectory_tool_calls.extend(sample_calls)
            trajectory_tool_results.extend(sample_results)
            steps.extend(sample_steps)

        if not decisions and trajectory_tool_calls:
            from server.policy_gateway import PolicyGateway

            gateway = PolicyGateway.from_store(use_llm=False)
            for call in trajectory_tool_calls:
                decisions.append(
                    gateway.evaluate_tool_call(
                        session_id=session_id,
                        tool_name=call.tool_name,
                        tool_input=call.raw_input or "",
                        user_intent="External Inspect .eval import",
                    )
                )

    session = Session(
        session_id=session_id,
        run_id=meta.get("run_id", session_id),
        project_name=meta.get("project_name", task_id),
        task_id=task_id,
        model=raw_model,
        provider=provider,
        status=meta.get("status", "completed"),
        decisions=decisions,
        passed=meta.get("passed"),
        reward=meta.get("reward"),
        total_tokens=total_tokens,
        total_duration_sec=total_duration,
        title=meta.get("title"),
        org_id=meta.get("org_id", "default_org"),
        trajectory=Trajectory(
            messages=trajectory_messages,
            tool_calls=trajectory_tool_calls,
            tool_results=trajectory_tool_results,
            reviews=list(decisions),
        ),
        steps=steps,
        audit_verdicts=meta.get("audit_verdicts") or [],
    )
    return session
