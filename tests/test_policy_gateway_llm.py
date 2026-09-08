"""Tests for PolicyGateway LLM XML parsing and threshold mapping."""

from schemas.watcher_models import ToolThreshold
from server.policy_gateway import PolicyGateway, _parse_decision_xml


def test_parse_decision_xml() -> None:
    text = "<decision_score>7</decision_score><decision_context>Secret read</decision_context>"
    parsed = _parse_decision_xml(text)
    assert parsed == (7, "Secret read")


def test_apply_thresholds_auto_deny() -> None:
    gw = PolicyGateway(
        use_llm=False,
        tool_thresholds=[
            ToolThreshold(tool_name="Bash", auto_approve=False, auto_deny_ge=5, escalate_ge=4)
        ],
    )
    decision, stage, _ = gw.apply_thresholds("Bash", 8, "triage", "high risk")
    assert decision == "block"
    assert stage == "triage"


def test_apply_thresholds_always_escalate() -> None:
    gw = PolicyGateway(
        use_llm=False,
        tool_thresholds=[ToolThreshold(tool_name="Bash", always_escalate=True)],
    )
    decision, _, ctx = gw.apply_thresholds("Bash", 1, "triage", "n/a")
    assert decision == "escalate"
    assert "Always escalate" in ctx


def test_llm_grade_disabled_returns_heuristic() -> None:
    gw = PolicyGateway(use_llm=False)
    score, ctx = gw._fast_triage("Bash", "echo hi")
    assert score == 3
    assert "Routine" in ctx
