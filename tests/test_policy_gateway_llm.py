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


def test_llm_circuit_opens_after_repeated_failures() -> None:
    import email.message
    import urllib.error

    def _raise(*_a: object, **_k: object) -> object:
        raise urllib.error.HTTPError(
            "https://example.invalid",
            404,
            "Not Found",
            hdrs=email.message.Message(),
            fp=None,  # type: ignore[arg-type]
        )

    gw = PolicyGateway(
        use_llm=True,
        llm_model="gemini-does-not-exist",
        api_key="fake-key-for-test",
        requester=_raise,
    )
    assert gw._llm_grade("sys", "user") is None
    assert gw._llm_grade("sys", "user") is None
    assert gw._llm_circuit_open is False
    assert gw._llm_grade("sys", "user") is None
    assert gw._llm_circuit_open is True
    # Further calls skip the network once the circuit is open
    assert gw._llm_grade("sys", "user") is None


def test_deep_review_uses_live_canonical_grader() -> None:
    from engine.graders_suite import reset_live_canonical_grader, update_live_canonical_grader

    captured_sys_prompts: list[str] = []

    def mock_requester(req: object, timeout: float = 1.0) -> object:
        import json
        from unittest.mock import MagicMock

        # Extract system prompt from body
        data = getattr(req, "data", b"")
        payload = json.loads(data.decode("utf-8"))
        sys_instruction = payload.get("systemInstruction", {}).get("parts", [{}])[0].get("text", "")
        captured_sys_prompts.append(sys_instruction)

        mock_resp = MagicMock()
        resp_data = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": "<decision_score>6</decision_score><decision_context>Live deep review</decision_context>"
                            }
                        ]
                    }
                }
            ]
        }
        mock_resp.read.return_value = json.dumps(resp_data).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    gw = PolicyGateway(use_llm=True, api_key="test-key", requester=mock_requester)

    # Update live PolicyGatewayDeepGrader
    custom_role = "ROLE_TEST_LIVE: Dual-Tier Deep Evaluator"
    update_live_canonical_grader("PolicyGatewayDeepGrader", {"c2_role": custom_role})

    res = gw._deep_review(
        tool_name="bash",
        tool_input="cat /etc/passwd",
        base_score=7,
    )
    assert res is not None
    assert captured_sys_prompts and custom_role in captured_sys_prompts[-1]

    # Reset live grader and verify
    reset_live_canonical_grader("PolicyGatewayDeepGrader")
    gw._deep_review(
        tool_name="bash",
        tool_input="cat /etc/passwd",
        base_score=7,
    )
    assert captured_sys_prompts and custom_role not in captured_sys_prompts[-1]
