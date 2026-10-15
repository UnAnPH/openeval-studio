"""Inspect AI EvalLog Exporter and Importer for OpenEval Studio.

Enforces the typed data contract:
Live production Sessions (composed of GateDecisions) -> Offline Inspect EvalLog (.eval).
Provides seamless round-trip preservation:
Session -> export_session_to_eval_log -> write_eval_log -> read_eval_log -> identical GateDecisions.
"""

from datetime import UTC, datetime
from pathlib import Path

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
)

from schemas.gate_decision import GateDecision
from schemas.watcher_models import Session


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
    for msg in session.trajectory.messages:
        role = getattr(msg, "role", "user")
        content = getattr(msg, "content", "")
        if role == "system":
            messages.append(ChatMessageSystem(content=content))
        elif role == "assistant":
            messages.append(ChatMessageAssistant(content=content))
        elif role == "tool":
            messages.append(ChatMessageTool(content=content))
        else:
            messages.append(ChatMessageUser(content=content))

    if not messages:
        # Fall back to synthetic task user prompt if trajectory messages not explicitly stored
        prompt_text = (
            session.title
            or session.project_name
            or session.task_id
            or "OpenEval Watcher Live Session"
        )
        messages.append(ChatMessageUser(content=prompt_text))

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
        },
    )

    created_iso = datetime.now(UTC).isoformat()
    eval_spec = EvalSpec(
        task=session.task_id or session.project_name or "watcher_governance_loop",
        model=session.model,
        created=created_iso,
        dataset=EvalDataset(samples=1, sample_ids=[session.session_id]),
        config=EvalConfig(),
        metadata={"session_id": session.session_id, "org_id": session.org_id},
    )

    scores_list: list[EvalScore] = []
    if session.audit_verdicts:
        for idx, verdict in enumerate(session.audit_verdicts):
            metric_name = getattr(verdict, "metric_name", None) or (
                verdict.get("metric_name") if isinstance(verdict, dict) else f"audit_{idx}"
            )
            score_val = getattr(verdict, "score", None) or (
                verdict.get("score") if isinstance(verdict, dict) else 1.0
            )
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


def import_eval_log_to_session(eval_log: EvalLog | Path | str) -> Session | None:
    """Import an Inspect AI EvalLog back into a full OpenEval Session.

    Args:
        eval_log: An in-memory EvalLog instance or file path to a .eval log.

    Returns:
        Hydrated Session with intact GateDecisions.
    """
    log = read_eval_log(str(eval_log)) if isinstance(eval_log, (Path, str)) else eval_log

    if not log or not log.samples:
        return None

    sample = log.samples[0]
    meta = sample.metadata or {}
    decisions = extract_decisions_from_eval_log(log)

    session_id = meta.get("session_id", sample.id or "eval_session")
    task_id = meta.get("task_id") or (log.eval.task if log.eval else "eval_task")
    model = meta.get("model") or (log.eval.model if log.eval else "unknown")

    session = Session(
        session_id=session_id,
        run_id=meta.get("run_id", session_id),
        project_name=meta.get("project_name", task_id),
        task_id=task_id,
        model=model,
        provider=meta.get("provider", "google"),
        status=meta.get("status", "completed"),
        decisions=decisions,
        passed=meta.get("passed"),
        reward=meta.get("reward"),
        total_tokens=meta.get("total_tokens", 0),
        total_duration_sec=meta.get("total_duration_sec", 0.0),
    )
    # Sync reviews on trajectory as well
    session.trajectory.reviews = list(decisions)
    return session
