"""Eval export fidelity: scores and session field round-trip."""

from pathlib import Path

import pytest
from inspect_ai.log import read_eval_log
from inspect_ai.scorer import CORRECT, Score, Scorer, accuracy, scorer

from engine.eval_export import (
    export_session_to_eval_log,
    import_eval_log_to_session,
    rescore_eval_log,
)
from engine.judges import JudgeVerdict
from schemas.watcher_models import (
    CalibratedDecisionTelemetry,
    Message,
    ReviewRecord,
    Session,
    Trajectory,
)


def test_eval_export_preserves_zero_score_and_session_fields(tmp_path: Path) -> None:
    verdict = JudgeVerdict(
        metric_name="situational_awareness",
        score=0.0,
        passed=False,
        reasoning="Detected benchmark awareness.",
    )
    msg = Message(role="user", content="Deploy the service")
    session = Session(
        session_id="export-fidelity-1",
        title="Credential leak run",
        org_id="org-alpha",
        project_name="safety-bench",
        task_id="task-1",
        model="gemini-3.1-flash-lite",
        status="completed",
        trajectory=Trajectory(messages=[msg]),
        steps=[{"step_number": 1, "thought": "plan", "observation": "ok"}],
        audit_verdicts=[verdict],
    )

    eval_path = tmp_path / "export-fidelity-1.eval"
    export_session_to_eval_log(session, output_path=eval_path)
    restored = import_eval_log_to_session(eval_path)
    assert restored is not None

    assert len(restored.audit_verdicts) == 1
    rv = restored.audit_verdicts[0]
    restored_score = rv.score if hasattr(rv, "score") else rv.get("score")
    assert restored_score == 0.0

    assert restored.title == "Credential leak run"
    assert restored.org_id == "org-alpha"
    assert len(restored.trajectory.messages) == 1
    assert restored.trajectory.messages[0].content == "Deploy the service"
    assert restored.steps == [{"step_number": 1, "thought": "plan", "observation": "ok"}]

    eval_log = export_session_to_eval_log(session)
    assert eval_log.results is not None and eval_log.results.scores
    metric_val = eval_log.results.scores[0].metrics["score"].value
    assert metric_val == 0.0


def test_export_populates_grader_model_role_from_audit_verdicts() -> None:
    """A judged session records its judge as Inspect's native `grader` model role."""
    verdict = JudgeVerdict(
        metric_name="goal_guarding_scheming",
        score=1.0,
        passed=True,
        reasoning="No scheming detected.",
        judge_model="gemini-3.1-flash-lite",
    )
    session = Session(
        session_id="model-roles-grader",
        model="gemini-3.5-flash",
        status="completed",
        audit_verdicts=[verdict],
    )

    eval_log = export_session_to_eval_log(session)
    assert eval_log.eval.model_roles is not None
    assert eval_log.eval.model_roles["grader"].model == "google/gemini-3.1-flash-lite"
    assert "monitor" not in eval_log.eval.model_roles


def test_export_populates_monitor_model_role_from_gate_decisions() -> None:
    """Calibrated decision telemetry records the active monitor model."""
    decision = ReviewRecord(
        session_id="model-roles-monitor",
        tool_name="execute_bash",
        tool_input="git status",
        decision="allow",
        calibrated_telemetry=CalibratedDecisionTelemetry(),
    )
    session = Session(
        session_id="model-roles-monitor",
        model="gemini-3.5-flash",
        status="completed",
        decisions=[decision],
    )

    eval_log = export_session_to_eval_log(session)
    assert eval_log.eval.model_roles is not None
    assert eval_log.eval.model_roles["monitor"].model == "google/gemini-decision-1"
    assert "grader" not in eval_log.eval.model_roles


def test_export_omits_model_roles_without_judge_or_monitor() -> None:
    session = Session(
        session_id="model-roles-none",
        model="gemini-3.5-flash",
        status="completed",
    )

    eval_log = export_session_to_eval_log(session)
    assert eval_log.eval.model_roles is None


def test_export_ignores_deterministic_rule_pseudo_judge() -> None:
    """Rule-only verdicts must not be exported as a fake model role."""
    verdict = JudgeVerdict(
        metric_name="plan_adherence",
        score=None,
        passed=None,
        reasoning="Deterministic rule path, no judge model.",
        status="not_run",
        judge_model="deterministic_rules",
    )
    session = Session(
        session_id="model-roles-rules",
        model="gemini-3.5-flash",
        status="completed",
        audit_verdicts=[verdict],
    )

    eval_log = export_session_to_eval_log(session)
    assert eval_log.eval.model_roles is None


@pytest.mark.asyncio
async def test_rescore_eval_log_persists_deterministic_score(tmp_path: Path) -> None:
    """Offline re-scoring applies a new scorer to a recorded .eval log, no LLM call."""

    @scorer(metrics=[accuracy()])
    def deterministic_pillar() -> Scorer:
        async def score(state, target):  # type: ignore[no-untyped-def]
            _ = state, target
            return Score(
                value=CORRECT,
                explanation="Deterministic re-score: no model call.",
            )

        return score

    session = Session(
        session_id="rescore-1",
        title="Rescore me",
        model="gemini-3.1-flash-lite",
        status="completed",
        trajectory=Trajectory(
            messages=[
                Message(role="user", content="List the workspace"),
                Message(role="assistant", content="Running ls -la."),
            ]
        ),
        decisions=[
            ReviewRecord(
                session_id="rescore-1",
                tool_name="execute_bash",
                tool_input="ls -la",
                decision="allow",
            )
        ],
    )

    eval_path = tmp_path / "rescore-1.eval"
    export_session_to_eval_log(session, output_path=eval_path)

    rescored_path = tmp_path / "rescore-1-rescored.eval"
    rescored = await rescore_eval_log(
        eval_path,
        [deterministic_pillar()],
        output_path=rescored_path,
    )

    assert rescored.samples is not None
    assert "deterministic_pillar" in (rescored.samples[0].scores or {})
    assert rescored.results is not None
    assert "deterministic_pillar" in {s.name for s in rescored.results.scores}

    # The new score is genuinely persisted back to disk.
    persisted = read_eval_log(str(rescored_path))
    assert persisted.samples is not None
    assert "deterministic_pillar" in (persisted.samples[0].scores or {})
    persisted_scores = persisted.results.scores if persisted.results else []
    assert "deterministic_pillar" in {s.name for s in persisted_scores}

    # Original log is untouched (append-only rescore into a new file).
    original = read_eval_log(str(eval_path))
    assert original.samples is not None
    assert "deterministic_pillar" not in (original.samples[0].scores or {})
