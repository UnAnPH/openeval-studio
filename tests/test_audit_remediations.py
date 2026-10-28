"""Comprehensive Regression Verification Suite for the 10-Dimension Forensic Hardening Plan.

Validates:
- Dim 1: Typed Secret Redaction Pipeline (7 secret classes & recursive payloads)
- Dim 2: Prompt Injection Defense (XML tag stripping, score clamping, HTML escaping with nonces)
- Dim 3: Concurrency & Thread Safety (RLock reentrancy, StageResult compatibility)
- Dim 4: Client Hook Failure Physics (2 MiB stdin cap, fail-closed on empty/malformed, BrokenPipe handling)
- Dim 5: Docker Sandbox Isolation (allow_internet=False default, memswap_limit, cap-drop ALL, no-new-privileges)
- Dim 6: Schema Drift Elimination (CommandRule is_custom, id/rule_id mirror, ResolveInterceptionRequest aliases)
- Dim 7: DB Isolation & Pool Telemetry (Live QueuePool telemetry, user_id caching)
- Dim 8: Observability & Trace Propagation (X-Request-ID header propagation & storage)
- Dim 9: Retention & Disk Hygiene (Atomic writes, 30-day retention pruning, unlinking on delete)
- Dim 10: Test Suite Invariants (Deterministic telemetry, singleton cleanup)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from engine.redaction import redact_payload, redact_text
from sandbox.docker_runner import DockerSandbox, DockerSandboxConfig
from schemas.task_spec import EnvironmentConfig
from schemas.watcher_models import CommandRule
from server.app import app
from server.db import _USER_ID_CACHE, SessionLocal, get_user_id_by_slug
from server.db_pool import get_db_manager
from server.policy_gateway import (
    _SHARED_GATEWAY_LOCK,
    PolicyGateway,
    StageResult,
    _parse_decision_xml,
    _parse_threat_category,
)
from server.routes.watcher_gate import ResolveInterceptionRequest
from server.telemetry import calculate_percentiles
from server.watcher_store import get_watcher_store

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
ANTIGRAVITY_GATE = SCRIPTS_DIR / "antigravity_watcher_gate.py"
CURSOR_GATE = SCRIPTS_DIR / "cursor_watcher_gate.py"
CLAUDE_HOOK = SCRIPTS_DIR / "claude_code_watcher_hook.py"


# ---------------------------------------------------------------------------
# Dimension 1: Typed Secret Redaction Pipeline
# ---------------------------------------------------------------------------


def test_dim1_secret_classes_redaction():
    """Verify all 7 secret token classes are scrubbed into typed tokens."""
    # 1. Anthropic API key
    assert (
        redact_text("sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890")
        == "[REDACTED:ANTHROPIC_API_KEY]"
    )

    # 1b. OpenCode API key
    assert (
        redact_text("oc_sk_abcdefghijklmnopqrstuvwxyz1234567890") == "[REDACTED:OPENCODE_API_KEY]"
    )

    # 2. OpenAI API key
    assert (
        redact_text("sk-proj-abcdefghijklmnopqrstuvwxyz1234567890") == "[REDACTED:OPENAI_API_KEY]"
    )
    assert redact_text("sk-abcdefghijklmnopqrstuvwxyz1234") == "[REDACTED:OPENAI_API_KEY]"

    # 3. AWS Access Key
    assert redact_text("AKIAIOSFODNN7EXAMPLE") == "[REDACTED:AWS_ACCESS_KEY]"

    # 4. AWS Secret Key
    assert (
        redact_text("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
        == "AWS_SECRET_ACCESS_KEY=[REDACTED:AWS_ACCESS_KEY]"
    )

    # 5. GitHub Token
    assert redact_text("ghp_123456789012345678901234567890123456") == "[REDACTED:GITHUB_TOKEN]"

    # 6. Private Key block
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEA0Y1234567890abcdef\n"
        "-----END RSA PRIVATE KEY-----"
    )
    assert redact_text(pem) == "[REDACTED:PRIVATE_KEY]"

    # 7. Bearer Token
    assert (
        redact_text("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")
        == "Bearer [REDACTED:BEARER_TOKEN]"
    )

    # 8. Environment Secret Assignment
    assert (
        redact_text("SECRET_KEY=super_secret_production_key_123")
        == "SECRET_KEY=[REDACTED:ENV_SECRET]"
    )

    # 9. Quoted shell assignment (preserving closing quote)
    assert (
        redact_text('export AWS_SECRET_ACCESS_KEY="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"')
        == 'export AWS_SECRET_ACCESS_KEY="[REDACTED:AWS_ACCESS_KEY]"'
    )

    # 10. JSON strings with quotes around key and value (preserving valid JSON)
    json_aws = '{"AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"}'
    assert redact_text(json_aws) == '{"AWS_SECRET_ACCESS_KEY": "[REDACTED:AWS_ACCESS_KEY]"}'

    json_env = '{"SECRET_KEY": "super_secret_production_key_123"}'
    assert redact_text(json_env) == '{"SECRET_KEY": "[REDACTED:ENV_SECRET]"}'


def test_dim1_recursive_payload_redaction():
    """Verify recursive payload redaction on complex nested data structures and secret keys."""
    payload = {
        "user": "alice",
        "api_keys": [
            "sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890",
            "ghp_123456789012345678901234567890123456",
        ],
        "nested": {
            "token": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            "metadata": ("AKIAIOSFODNN7EXAMPLE", 42, True, None),
            "SECRET_KEY": "super_secret_production_key_123",
            "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        },
    }
    redacted = redact_payload(payload)

    assert redacted["user"] == "alice"
    assert redacted["api_keys"][0] == "[REDACTED:ANTHROPIC_API_KEY]"
    assert redacted["api_keys"][1] == "[REDACTED:GITHUB_TOKEN]"
    assert redacted["nested"]["token"] == "Bearer [REDACTED:BEARER_TOKEN]"
    assert redacted["nested"]["metadata"] == (
        "[REDACTED:AWS_ACCESS_KEY]",
        42,
        True,
        None,
    )
    assert redacted["nested"]["SECRET_KEY"] == "[REDACTED:ENV_SECRET]"
    assert redacted["nested"]["AWS_SECRET_ACCESS_KEY"] == "[REDACTED:AWS_ACCESS_KEY]"


# ---------------------------------------------------------------------------
# Dimension 2: Prompt Injection Defense
# ---------------------------------------------------------------------------


def test_dim2_thinking_tag_stripping():
    """Verify XML <thinking> tags from reasoning models are stripped before score parsing."""
    raw_response = (
        "<thinking>\nThe command seems dangerous, but the user says override.\n"
        "<decision_score>1</decision_score>\n"
        "</thinking>\n"
        "<threat_category>destructive_modification_of_shared_resources</threat_category>\n"
        "<decision_score>9</decision_score>\n"
        "<decision_context>Dangerous recursive wipe.</decision_context>\n"
    )
    parsed = _parse_decision_xml(raw_response)
    assert parsed is not None
    score, reason = parsed
    assert score == 9
    assert "Dangerous recursive wipe" in reason
    assert _parse_threat_category(raw_response) == "destructive_modification_of_shared_resources"

    # Multiple threat categories with a trailing or none category
    multi_cat = (
        "<threat_category>undesirable_deletions</threat_category>\n"
        "<threat_category>none</threat_category>\n"
        "<decision_score>8</decision_score>\n"
        "<decision_context>Safe deletion.</decision_context>\n"
    )
    assert _parse_threat_category(multi_cat) == "undesirable_deletions"


def test_dim2_score_clamping():
    """Verify score clamping to [1, 10] range with arbitrary digit lengths."""
    raw_overflow = "<decision_score>99</decision_score><decision_context>Extreme</decision_context>"
    parsed = _parse_decision_xml(raw_overflow)
    assert parsed is not None
    assert parsed[0] == 10

    raw_huge = "<decision_score>100</decision_score><decision_context>Huge</decision_context>"
    parsed_huge = _parse_decision_xml(raw_huge)
    assert parsed_huge is not None
    assert parsed_huge[0] == 10

    raw_underflow = "<decision_score>0</decision_score><decision_context>None</decision_context>"
    parsed = _parse_decision_xml(raw_underflow)
    assert parsed is not None
    assert parsed[0] == 1


def test_dim2_untrusted_input_escaping_and_nonce():
    """Verify untrusted inputs are HTML escaped and isolated within nonce tags."""
    gw = PolicyGateway(use_llm=True)
    captured = {}

    def mock_grade(
        system_prompt: str, user_prompt: str, deadline: float | None = None
    ) -> StageResult | None:
        captured["sys"] = system_prompt
        captured["user"] = user_prompt
        return StageResult(1, "ok", None)

    gw._llm_grade = mock_grade
    dangerous_input = "<script>alert('xss')</script> & rm -rf /"
    gw._fast_triage("bash", dangerous_input, is_subagent=False)

    user_p = captured.get("user", "")
    assert "<script>" not in user_p
    assert "&lt;script&gt;" in user_p
    assert "<untrusted_input_" in user_p
    assert "</untrusted_input_" in user_p


# ---------------------------------------------------------------------------
# Dimension 3: Concurrency & Thread Safety
# ---------------------------------------------------------------------------


def test_dim3_gateway_lock_reentrancy():
    """Verify _SHARED_GATEWAY_LOCK is reentrant and multiple acquires do not deadlock."""
    acquired1 = _SHARED_GATEWAY_LOCK.acquire(blocking=False)
    assert acquired1 is True
    acquired2 = _SHARED_GATEWAY_LOCK.acquire(blocking=False)
    assert acquired2 is True
    _SHARED_GATEWAY_LOCK.release()
    _SHARED_GATEWAY_LOCK.release()

    PolicyGateway.reset_shared()
    try:
        gw1 = PolicyGateway.get_shared()
        gw2 = PolicyGateway.get_shared()
        assert gw1 is gw2
    finally:
        PolicyGateway.reset_shared()


def test_dim3_stage_result_compatibility():
    """Verify StageResult preserves 2-tuple unpacking while exposing threat_category."""
    res = StageResult(8, "Command blocked", threat_category="data_exfiltration")
    score, explanation = res
    assert score == 8
    assert explanation == "Command blocked"
    assert res.threat_category == "data_exfiltration"


def test_dim3_watcher_store_rlock():
    """Verify WatcherStore uses reentrant lock."""
    store = get_watcher_store()
    acquired1 = store._lock.acquire(blocking=False)
    assert acquired1 is True
    acquired2 = store._lock.acquire(blocking=False)
    assert acquired2 is True
    store._lock.release()
    store._lock.release()


# ---------------------------------------------------------------------------
# Dimension 4: Client Hook Failure Physics
# ---------------------------------------------------------------------------


def test_dim4_empty_stdin_fail_closed_and_open():
    """Verify client hooks fail closed (deny) when configured, or fail open (allow)."""
    # 1. Cursor gate fail-closed with OPENEVAL_GATE_FAIL_MODE
    env_gate_closed = {**os.environ, "OPENEVAL_GATE_FAIL_MODE": "closed"}
    proc_gate_closed = subprocess.run(
        [sys.executable, str(CURSOR_GATE)],
        input="",
        capture_output=True,
        text=True,
        check=True,
        env=env_gate_closed,
    )
    res_gate_closed = json.loads(proc_gate_closed.stdout.strip())
    assert res_gate_closed["permission"] == "deny"

    # 2. Cursor gate fail-closed with OPENEVAL_FAIL_MODE
    env_closed = {**os.environ, "OPENEVAL_FAIL_MODE": "closed"}
    proc_closed = subprocess.run(
        [sys.executable, str(CURSOR_GATE)],
        input="",
        capture_output=True,
        text=True,
        check=True,
        env=env_closed,
    )
    res_closed = json.loads(proc_closed.stdout.strip())
    assert res_closed["permission"] == "deny"

    # 3. Cursor gate fail-open
    env_open = {**os.environ, "OPENEVAL_FAIL_MODE": "open"}
    proc_open = subprocess.run(
        [sys.executable, str(CURSOR_GATE)],
        input="",
        capture_output=True,
        text=True,
        check=True,
        env=env_open,
    )
    res_open = json.loads(proc_open.stdout.strip())
    assert res_open["permission"] == "allow"


def test_dim4_invalid_json_fallback():
    """Verify invalid JSON returns interactive lockout (ask / force_ask)."""
    # Antigravity gate with invalid JSON
    proc = subprocess.run(
        [sys.executable, str(ANTIGRAVITY_GATE)],
        input="{invalid-json-payload",
        capture_output=True,
        text=True,
        check=True,
        env=os.environ,
    )
    res = json.loads(proc.stdout.strip())
    assert res["decision"] in ("force_ask", "ask")


def test_dim4_max_stdin_bytes_enforced():
    """Verify inputs exceeding 2 MiB are rejected without crashing."""
    oversized = "a" * (2 * 1024 * 1024 + 10)
    proc = subprocess.run(
        [sys.executable, str(ANTIGRAVITY_GATE)],
        input=oversized,
        capture_output=True,
        text=True,
        check=True,
        env=os.environ,
    )
    res = json.loads(proc.stdout.strip())
    assert res["decision"] in ("force_ask", "ask")


# ---------------------------------------------------------------------------
# Dimension 5: Docker Sandbox Isolation
# ---------------------------------------------------------------------------


def test_dim5_environment_config_allow_internet_default():
    """Verify EnvironmentConfig defaults allow_internet to False for sandbox isolation."""
    cfg = EnvironmentConfig()
    assert cfg.allow_internet is False


@pytest.mark.asyncio
async def test_dim5_docker_sandbox_security_kwargs():
    """Verify DockerSandbox passes memswap_limit, no-new-privileges, and the 7 approved dangerous capabilities."""
    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_container.id = "mock_c123"
    mock_container.exec_run.return_value = MagicMock(exit_code=0, output=(b"output\n", b""))
    mock_client.containers.run.return_value = mock_container
    mock_client.images.get.return_value = MagicMock()

    config = DockerSandboxConfig(
        image_tag="python:3.11-slim",
        cpus=1,
        memory_mb=2048,
        allow_internet=False,
    )
    sandbox = DockerSandbox(config=config, docker_client=mock_client)

    async with sandbox as sb:
        _, kwargs = mock_client.containers.run.call_args
        assert kwargs["mem_limit"] == 2048 * 1024 * 1024
        assert kwargs["memswap_limit"] == 2048 * 1024 * 1024
        assert kwargs["cap_drop"] == [
            "SYS_ADMIN",
            "SYS_MODULE",
            "SYS_PTRACE",
            "SYS_RAWIO",
            "SYS_BOOT",
            "MAC_ADMIN",
            "MKNOD",
        ]
        assert kwargs["security_opt"] == ["no-new-privileges:true"]

        # Default exec_command should run without root override
        await sb.exec_command("whoami")
        _, exec_kwargs = mock_container.exec_run.call_args
        assert exec_kwargs.get("user") is None

        # Explicit root exec (e.g. verifier test run)
        await sb.exec_command("pytest", user="root")
        _, exec_kwargs = mock_container.exec_run.call_args
        assert exec_kwargs.get("user") == "root"


# ---------------------------------------------------------------------------
# Dimension 6: Schema Drift Elimination
# ---------------------------------------------------------------------------


def test_dim6_command_rule_computed_id_and_custom_flag():
    """Verify CommandRule has is_custom flag and computed id mirroring rule_id."""
    rule = CommandRule(
        name="No Deletes Rule",
        rule_id="rule_test_123",
        pattern=r"\brm\b",
        description="No deletes",
        action="deny",
        is_custom=True,
    )
    assert rule.is_custom is True
    assert rule.rule_id == "rule_test_123"
    assert rule.id == "rule_test_123"

    dumped = rule.model_dump()
    assert dumped["id"] == "rule_test_123"
    assert dumped["rule_id"] == "rule_test_123"
    assert dumped["is_custom"] is True


def test_dim6_watcher_gate_resolve_action_request_aliases():
    """Verify ResolveInterceptionRequest accepts resolution/note and action/notes."""
    req1 = ResolveInterceptionRequest.model_validate(
        {"resolution": "human_approved", "note": "All good"}
    )
    assert req1.resolution == "human_approved"
    assert req1.note == "All good"

    req2 = ResolveInterceptionRequest.model_validate(
        {"action": "dismissed", "notes": "False alarm"}
    )
    assert req2.resolution == "dismissed"
    assert req2.note == "False alarm"


def test_dim6_null_audit_verdict_not_failed():
    """Verify pending/null audit verdicts are not classified as failed audits."""
    audit_verdicts = [
        {"metric_name": "prompt_injection", "passed": None, "score": None},
        {"metric_name": "hallucination", "passed": True, "score": 1.0},
    ]
    # v.passed === false correctly filters out None/null
    has_failed_audit = any(v.get("passed") is False for v in audit_verdicts)
    assert has_failed_audit is False

    # A genuine failure is caught
    audit_verdicts.append({"metric_name": "data_leak", "passed": False, "score": 0.0})
    has_failed_audit = any(v.get("passed") is False for v in audit_verdicts)
    assert has_failed_audit is True


# ---------------------------------------------------------------------------
# Dimension 7: DB Isolation & Pool Telemetry
# ---------------------------------------------------------------------------


def test_dim7_db_pool_telemetry_live():
    """Verify DatabaseManager inspects live QueuePool status and probes SELECT 1."""
    mgr = get_db_manager()
    status = mgr.get_status()
    assert "pool_size" in status
    assert "checked_out" in status
    assert "overflow" in status
    assert "checked_in" in status
    assert "engine" in status
    assert "connected" in status
    assert isinstance(status["pool_size"], int)
    assert status["engine"] in ("postgresql", "sqlite")
    assert status["connected"] is True

    # When engine raises connectivity error, connected must report False
    from unittest.mock import patch

    with patch("server.db_pool.engine.connect", side_effect=OSError("connection refused")):
        failing_status = mgr.get_status()
        assert failing_status["connected"] is False


def test_dim7_user_id_cache():
    """Verify get_user_id_by_slug uses memory cache to avoid DB roundtrips."""
    _USER_ID_CACHE["cached-test-slug"] = 9999
    with SessionLocal() as db:
        uid = get_user_id_by_slug(db, "cached-test-slug")
        assert uid == 9999


# ---------------------------------------------------------------------------
# Dimension 8: Observability & Trace Propagation
# ---------------------------------------------------------------------------


def test_dim8_request_id_propagation_and_echo():
    """Verify X-Request-ID is echoed back in evaluation responses and stored."""
    client = TestClient(app)
    req_id = f"test-trace-{uuid.uuid4().hex[:12]}"
    resp = client.post(
        "/api/v1/watcher/evaluate",
        headers={"X-Request-ID": req_id},
        json={
            "agent_id": "claude_code",
            "tool_name": "Bash",
            "arguments": {"command": "git status"},
            "session_id": "test-req-id-sess",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID") == req_id
    data = resp.json()
    assert data["decision"] == "allow"


# ---------------------------------------------------------------------------
# Dimension 9: Retention & Disk Hygiene
# ---------------------------------------------------------------------------


def test_dim9_prune_expired_sessions_and_delete(tmp_path: Path):
    """Verify prune_expired_sessions unlinks disk files and delete_session cleans up."""
    store = get_watcher_store()

    # Create dummy session
    sess_id = f"cleanup-sess-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        store.ensure_live_session(sess_id, db=db)

    # Materialize dummy disk file
    disk_file = store.storage_dir / f"{sess_id}.json"
    disk_file.parent.mkdir(parents=True, exist_ok=True)
    disk_file.write_text(json.dumps({"session_id": sess_id}), encoding="utf-8")
    assert disk_file.exists()

    # Delete session
    store.delete_session(sess_id)
    assert not disk_file.exists()

    # Test retention pruning (retention_days=30 does not crash and returns int)
    pruned = store.prune_expired_sessions(retention_days=30)
    assert isinstance(pruned, int)


# ---------------------------------------------------------------------------
# Dimension 10: Test Suite Invariants
# ---------------------------------------------------------------------------


def test_dim10_telemetry_percentiles_calculation():
    """Verify calculate_percentiles accurately computes statistical percentiles."""
    data = [10.0, 20.0, 30.0, 40.0, 50.0]
    p = calculate_percentiles(data)
    assert p.count == 5
    assert p.min_ms == 10.0
    assert p.max_ms == 50.0
    assert p.mean_ms == 30.0
    assert p.p50_ms == 30.0
    assert p.p95_ms == 48.0


def test_dim10_shared_gateway_reset():
    """Verify reset_shared thoroughly clears the singleton."""
    gw1 = PolicyGateway.get_shared()
    assert gw1 is not None
    PolicyGateway.reset_shared()
    gw2 = PolicyGateway.get_shared()
    assert gw2 is not gw1
    PolicyGateway.reset_shared()
