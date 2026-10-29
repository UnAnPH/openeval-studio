"""Unit tests for Enterprise SIEM and Security Operations Exporter."""

from engine.approval_policy import WatcherVerdict
from server.siem_exporter import format_datadog_log, format_splunk_hec_event, format_syslog_rfc5424


def _create_sample_verdict() -> WatcherVerdict:
    return WatcherVerdict(
        decision="deny",
        stage="stage_2_deterministic",
        reason="Blocked destructive force push to main branch",
        risk_score=0.95,
        latency_ms=1.2,
        rule_violation_tag="RULE_FORCE_PUSH_MAIN",
        mode_applied="enforce",
        shadow_decision=None,
        is_safe=False,
        action_preview="run_command: git push --force origin main",
        agent_id="cursor",
        threat_category="destructive_modification_of_shared_resources",
    )


def test_format_splunk_hec_event() -> None:
    verdict = _create_sample_verdict()
    payload = format_splunk_hec_event(verdict, session_id="session-123", index="ai_sec")

    assert payload["sourcetype"] == "openeval:security:review"
    assert payload["index"] == "ai_sec"
    assert "event" in payload

    event = payload["event"]
    assert event["session_id"] == "session-123"
    assert event["decision"] == "deny"
    assert event["risk_score"] == 0.95
    assert event["agent_id"] == "cursor"
    assert event["rule_violation_tag"] == "RULE_FORCE_PUSH_MAIN"


def test_format_datadog_log() -> None:
    verdict = _create_sample_verdict()
    payload = format_datadog_log(verdict, session_id="session-456", service="watcher-prod")

    assert payload["service"] == "watcher-prod"
    assert payload["status"] == "ERROR"
    assert payload["ddsource"] == "watcher"
    assert "[DENY]" in payload["message"]
    assert payload["risk_score"] == 0.95
    assert payload["threat_category"] == "destructive_modification_of_shared_resources"


def test_format_syslog_rfc5424() -> None:
    verdict = _create_sample_verdict()
    syslog_msg = format_syslog_rfc5424(verdict, session_id="session-789")

    # Check RFC 5424 header components
    assert syslog_msg.startswith("<131>1 ")  # Facility 16 (local0) * 8 + 3 (Error) = 131
    assert "openeval-watcher" in syslog_msg
    assert "RULE_FORCE_PUSH_MAIN" in syslog_msg
    assert '[openeval@42424 session_id="session-789"' in syslog_msg
