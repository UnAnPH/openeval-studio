"""Eval export fidelity: scores and session field round-trip."""

from pathlib import Path

from engine.eval_export import export_session_to_eval_log, import_eval_log_to_session
from engine.judges import JudgeVerdict
from schemas.watcher_models import Message, Session, Trajectory


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
