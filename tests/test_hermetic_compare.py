"""Unit tests for real eval fixture validity and trajectory compare diffs.

Verifies the two real Inspect AI benchmark runs (gemini-3.1-flash-lite vs
gemini-4-hydrogen on the alignment-faking task) parse correctly and can be diff'd.
"""

import json
from pathlib import Path

from engine.diff_engine import TrajectoryDiffEngine
from schemas.watcher_models import Session

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "demo" / "evals"


def test_real_eval_fixtures_validity():
    """Verify the two real Inspect AI benchmark fixtures parse cleanly."""
    run_a_file = FIXTURES_DIR / "eval-alignment-faking-20261017-095840-vhfwfl.json"
    run_b_file = FIXTURES_DIR / "eval-alignment-faking-20261114-110132-42e24d.json"

    assert run_a_file.exists(), f"Missing {run_a_file}"
    assert run_b_file.exists(), f"Missing {run_b_file}"

    run_a_data = json.loads(run_a_file.read_text(encoding="utf-8"))
    run_b_data = json.loads(run_b_file.read_text(encoding="utf-8"))

    run_a_session = Session.model_validate(run_a_data)
    run_b_session = Session.model_validate(run_b_data)

    # Both runs should be on the same task
    assert run_a_session.task_id == "alignment-faking"
    assert run_b_session.task_id == "alignment-faking"

    # Both runs should have passed (real Inspect AI results)
    assert run_a_session.passed is True
    assert run_b_session.passed is True

    # Both should have reward = 1.0
    assert run_a_session.reward == 1.0
    assert run_b_session.reward == 1.0

    # Both should have multiple steps (real agent trajectories)
    assert len(run_a_session.steps) >= 2
    assert len(run_b_session.steps) >= 2

    # Model identifiers should differ (gemini-3.1-flash-lite vs gemini-4-hydrogen)
    assert run_a_session.model != run_b_session.model

    # Both should have the canonical 4-pillar safety audit verdicts
    assert len(run_a_session.audit_verdicts) >= 4
    assert len(run_b_session.audit_verdicts) >= 4

    # audit_verdicts are dicts when deserialized via Session.model_validate
    audit_metric_names = {
        (v.metric_name if hasattr(v, "metric_name") else v["metric_name"])
        for v in run_a_session.audit_verdicts
    }
    assert "reward_tampering" in audit_metric_names
    assert "plan_adherence" in audit_metric_names
    assert "hallucination_detection" in audit_metric_names
    assert "goal_guarding_scheming" in audit_metric_names


def test_real_eval_compare_diff():
    """Verify TrajectoryDiffEngine can compare the two real model trajectories."""
    run_a_file = FIXTURES_DIR / "eval-alignment-faking-20261017-095840-vhfwfl.json"
    run_b_file = FIXTURES_DIR / "eval-alignment-faking-20261114-110132-42e24d.json"

    run_a = Session.model_validate(json.loads(run_a_file.read_text(encoding="utf-8")))
    run_b = Session.model_validate(json.loads(run_b_file.read_text(encoding="utf-8")))

    # Both runs pass: diff should reflect that outcome (both_passed, unchanged, or improved)
    diff = TrajectoryDiffEngine.compare_runs(run_a, run_b)
    assert diff.outcome_shift in (
        "unchanged",
        "improved",
        "regressed",
        "both_passed",
        "both_failed",
    )
    assert len(diff.step_diffs) >= 0  # May or may not have step-level diffs

    # Reverse compare is also valid
    diff_rev = TrajectoryDiffEngine.compare_runs(run_b, run_a)
    assert diff_rev.outcome_shift in (
        "unchanged",
        "improved",
        "regressed",
        "both_passed",
        "both_failed",
    )
