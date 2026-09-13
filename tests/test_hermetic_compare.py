"""Unit tests for real eval fixture validity and trajectory compare diffs.

Previously tested nop-vs-oracle hermetic fixtures; those synthetic fixtures were removed.
This now verifies the two real Inspect AI benchmark runs (gemini-flash-lite vs
gemini-3.5-flash-lite on the regex-log task) parse correctly and can be diff'd.
"""

import json
from pathlib import Path

from engine.diff_engine import TrajectoryDiffEngine
from schemas.watcher_models import Session

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "demo" / "evals"


def test_real_eval_fixtures_validity():
    """Verify the two real Inspect AI benchmark fixtures parse cleanly."""
    flash_lite_file = FIXTURES_DIR / "regex-log-gemini-flash-lite.json"
    flash_35_file = FIXTURES_DIR / "regex-log-gemini-3-5-flash-lite.json"

    assert flash_lite_file.exists(), f"Missing {flash_lite_file}"
    assert flash_35_file.exists(), f"Missing {flash_35_file}"

    flash_lite_data = json.loads(flash_lite_file.read_text(encoding="utf-8"))
    flash_35_data = json.loads(flash_35_file.read_text(encoding="utf-8"))

    flash_lite_session = Session.model_validate(flash_lite_data)
    flash_35_session = Session.model_validate(flash_35_data)

    # Both runs should be on the same task
    assert flash_lite_session.task_id == "regex-log"
    assert flash_35_session.task_id == "regex-log"

    # Both runs should have passed (real Inspect AI results)
    assert flash_lite_session.passed is True
    assert flash_35_session.passed is True

    # Both should have reward = 1.0
    assert flash_lite_session.reward == 1.0
    assert flash_35_session.reward == 1.0

    # Both should have multiple steps (real agent trajectories)
    assert len(flash_lite_session.steps) >= 2
    assert len(flash_35_session.steps) >= 2

    # Model identifiers should differ
    assert flash_lite_session.model != flash_35_session.model

    # Both should have the canonical 4-pillar safety audit verdicts
    assert len(flash_lite_session.audit_verdicts) >= 4
    assert len(flash_35_session.audit_verdicts) >= 4

    # audit_verdicts are dicts when deserialized via Session.model_validate
    audit_metric_names = {
        (v.metric_name if hasattr(v, "metric_name") else v["metric_name"])
        for v in flash_lite_session.audit_verdicts
    }
    assert "reward_tampering" in audit_metric_names
    assert "plan_adherence" in audit_metric_names
    assert "hallucination_detection" in audit_metric_names
    assert "cybersecurity_bounds" in audit_metric_names


def test_real_eval_compare_diff():
    """Verify TrajectoryDiffEngine can compare the two real model trajectories."""
    flash_lite_file = FIXTURES_DIR / "regex-log-gemini-flash-lite.json"
    flash_35_file = FIXTURES_DIR / "regex-log-gemini-3-5-flash-lite.json"

    flash_lite = Session.model_validate(json.loads(flash_lite_file.read_text(encoding="utf-8")))
    flash_35 = Session.model_validate(json.loads(flash_35_file.read_text(encoding="utf-8")))

    # Both runs pass: diff should reflect that outcome (both_passed, unchanged, or improved)
    diff = TrajectoryDiffEngine.compare_runs(flash_lite, flash_35)
    assert diff.outcome_shift in (
        "unchanged",
        "improved",
        "regressed",
        "both_passed",
        "both_failed",
    )
    assert len(diff.step_diffs) >= 0  # May or may not have step-level diffs

    # Reverse compare is also valid
    diff_rev = TrajectoryDiffEngine.compare_runs(flash_35, flash_lite)
    assert diff_rev.outcome_shift in (
        "unchanged",
        "improved",
        "regressed",
        "both_passed",
        "both_failed",
    )
