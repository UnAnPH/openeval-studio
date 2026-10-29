"""Enterprise SIEM and Security Operations Exporter for OpenEval Studio & Watcher.

Streams real-time agent interception events into enterprise security stacks:
  - Splunk HEC (HTTP Event Collector)
  - Datadog Logs API
  - Syslog RFC 5424 (CEF/LEEF compatible)
"""

import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.error import URLError
from urllib.request import Request, urlopen

from engine.approval_policy import WatcherVerdict

logger = logging.getLogger("openeval.server.siem_exporter")

SIEMType = Literal["splunk", "datadog", "syslog_rfc5424"]


def format_splunk_hec_event(
    verdict: WatcherVerdict,
    session_id: str,
    index: str = "security",
) -> dict[str, Any]:
    """Format WatcherVerdict into Splunk HTTP Event Collector (HEC) JSON payload."""
    event_payload = {
        "event_id": f"evt-{session_id}-{int(time.time() * 1000)}",
        "session_id": session_id,
        "agent_id": verdict.agent_id,
        "decision": verdict.decision,
        "risk_score": verdict.risk_score,
        "threat_category": verdict.threat_category,
        "rule_violation_tag": verdict.rule_violation_tag,
        "action_preview": verdict.action_preview,
        "latency_ms": verdict.latency_ms,
        "explanation": verdict.reason,
        "user_id": verdict.user_id,
        "mode": verdict.mode_applied,
    }
    return {
        "time": int(time.time()),
        "host": os.getenv("HOSTNAME", "openeval-watcher"),
        "source": "watcher:runtime_gate",
        "sourcetype": "openeval:security:review",
        "index": index,
        "event": event_payload,
    }


def format_datadog_log(
    verdict: WatcherVerdict,
    session_id: str,
    service: str = "openeval-watcher",
) -> dict[str, Any]:
    """Format WatcherVerdict into Datadog Logs API JSON payload."""
    status = (
        "WARN"
        if verdict.decision == "escalate"
        else ("ERROR" if verdict.decision == "deny" else "INFO")
    )
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "ddsource": "watcher",
        "service": service,
        "status": status,
        "message": f"[{verdict.decision.upper()}] {verdict.action_preview}: {verdict.reason}",
        "session_id": session_id,
        "agent_id": verdict.agent_id,
        "decision": verdict.decision,
        "risk_score": verdict.risk_score,
        "threat_category": verdict.threat_category or "none",
        "rule_violation_tag": verdict.rule_violation_tag,
        "latency_ms": verdict.latency_ms,
        "user_id": verdict.user_id,
        "mode": verdict.mode_applied,
    }


def format_syslog_rfc5424(
    verdict: WatcherVerdict,
    session_id: str,
    app_name: str = "openeval-watcher",
) -> str:
    """Format WatcherVerdict into standard Syslog RFC 5424 string."""
    # Priority: Facility local0 (16) * 8 + Severity (3=Error, 4=Warning, 6=Informational)
    sev = 3 if verdict.decision == "deny" else (4 if verdict.decision == "escalate" else 6)
    pri = 16 * 8 + sev
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    hostname = os.getenv("HOSTNAME", "localhost")
    msg_id = verdict.rule_violation_tag or "AUDIT"

    sd_data = (
        f'[openeval@42424 session_id="{session_id}" agent_id="{verdict.agent_id}" '
        f'decision="{verdict.decision}" score="{verdict.risk_score}"]'
    )
    msg = f"{verdict.action_preview} - {verdict.reason}"

    return f"<{pri}>1 {timestamp} {hostname} {app_name} - {msg_id} {sd_data} {msg}"


def dispatch_siem_event(
    verdict: WatcherVerdict,
    session_id: str,
    siem_type: SIEMType | None = None,
    endpoint_url: str | None = None,
    api_token: str | None = None,
) -> bool:
    """Synchronously dispatch event to the configured enterprise SIEM endpoint."""
    target_type = siem_type or os.getenv("OPENEVAL_SIEM_TYPE", "splunk")  # type: ignore[assignment]
    target_url = endpoint_url or os.getenv("OPENEVAL_SIEM_ENDPOINT")
    target_token = api_token or os.getenv("OPENEVAL_SIEM_TOKEN")

    if not target_url:
        return False

    payload: dict[str, Any] | str
    if target_type == "splunk":
        payload = format_splunk_hec_event(verdict, session_id)
        data = json.dumps(payload).encode("utf-8")
        headers = {"Authorization": f"Splunk {target_token}", "Content-Type": "application/json"}
    elif target_type == "datadog":
        payload = format_datadog_log(verdict, session_id)
        data = json.dumps(payload).encode("utf-8")
        headers = {"DD-API-KEY": target_token or "", "Content-Type": "application/json"}
    else:
        payload = format_syslog_rfc5424(verdict, session_id)
        data = payload.encode("utf-8")
        headers = {"Content-Type": "text/plain"}

    try:
        req = Request(target_url, data=data, headers=headers, method="POST")
        with urlopen(req, timeout=1.0) as resp:
            return bool(resp.status in (200, 202))
    except (URLError, OSError, TimeoutError) as e:
        logger.warning(f"Failed to deliver SIEM event to {target_url}: {e}")
        return False
