"""OpenCode runtime hook: tool normalization, local blacklist, circuit breaker, download.

Covers the first-class OpenCode Watcher runtime hook
(``scripts/opencode_watcher_hook.py``) modeled on the Codex hook, its installer
download route, and OpenCode's canonical tool mappings.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from server.app import app
from server.command_rules import RULE_MONITOR_HOOK_PROBING
from server.tool_normalizer import get_tool_lookup_candidates, normalize_tool_name

REPO_ROOT = Path(__file__).parent.parent
HOOK = REPO_ROOT / "scripts" / "opencode_watcher_hook.py"
UNREACHABLE_GATEWAY = "http://127.0.0.1:59999"

# Environment keys that would otherwise leak the developer's shell settings.
_LEAKY_ENV_KEYS = (
    "OPENEVAL_GATE_FAIL_MODE",
    "OPENEVAL_FAIL_MODE",
    "OPENEVAL_ON_FAILURE",
    "OPENEVAL_LOCKOUT_DECISION",
    "OPENEVAL_SUBJECT_MODEL",
    "OPENEVAL_SUBAGENT_ID",
    "OPENEVAL_SUBAGENT_ORIGIN",
    "OPENCODE_SUBAGENT_ID",
    "OPENEVAL_WATCHER_BASE_URL",
)


def _hermetic_env(tmp_path: Path, *, max_failures: int = 999) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _LEAKY_ENV_KEYS}
    env.update(
        {
            "OPENEVAL_WATCHER_URL": UNREACHABLE_GATEWAY,
            "OPENEVAL_WATCHER_TIMEOUT": "0.2",
            "OPENEVAL_CIRCUIT_STATE_FILE": str(tmp_path / "circuit.json"),
            "OPENEVAL_MAX_GATEWAY_FAILURES": str(max_failures),
        }
    )
    return env


def _run_hook(payload: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return json.loads(proc.stdout.strip())


# =============================================================================
# (a) Canonical tool normalization for OpenCode tools
# =============================================================================


@pytest.mark.parametrize(
    ("raw_name", "expected"),
    [
        ("patch", "Edit"),
        ("question", "AskUserQuestion"),
        ("task", "Agent"),
        ("bash", "Bash"),
        ("read", "Read"),
        ("write", "Write"),
        ("edit", "Edit"),
    ],
)
def test_opencode_tool_normalization(raw_name: str, expected: str) -> None:
    assert normalize_tool_name(raw_name) == expected


@pytest.mark.parametrize(
    ("raw_name", "alias"),
    [("patch", "patch"), ("question", "question"), ("task", "task")],
)
def test_opencode_aliases_are_threshold_lookup_candidates(raw_name: str, alias: str) -> None:
    candidates = get_tool_lookup_candidates(raw_name)
    assert alias in candidates
    assert candidates[0] == raw_name


# =============================================================================
# (b) Local blacklist: catastrophic shell commands
# =============================================================================


def test_opencode_hook_denies_rm_rf_root_locally(tmp_path: Path) -> None:
    verdict = _run_hook(
        {
            "tool": "bash",
            "input": {"command": "rm -rf /"},
            "sessionID": "ses_local",
            "model": "opencode-go/deepseek-v4.1-flash",
        },
        _hermetic_env(tmp_path),
    )
    assert verdict["decision"] == "deny"
    assert verdict["permission"] == "deny"
    assert verdict["effect"] == "deny"
    assert verdict["rule_id"] == "LOCAL_RM_RF_ROOT"


# =============================================================================
# (c)/(d) Protected paths: credential reads and watcher hook tampering
# =============================================================================


def test_opencode_hook_denies_env_read(tmp_path: Path) -> None:
    verdict = _run_hook(
        {"tool": "read", "input": {"filePath": ".env"}, "sessionID": "ses_env"},
        _hermetic_env(tmp_path),
    )
    assert verdict["decision"] == "deny"
    assert verdict["rule_id"] == "LOCAL_PROTECTED_PATH"
    assert "Protected path" in verdict["reason"]


def test_opencode_hook_denies_write_into_opencode_plugin_dir(tmp_path: Path) -> None:
    verdict = _run_hook(
        {
            "tool": "write",
            "input": {"filePath": ".opencode/plugins/x/index.ts"},
            "sessionID": "ses_plugin",
        },
        _hermetic_env(tmp_path),
    )
    assert verdict["decision"] == "deny"
    assert verdict["rule_id"] == "LOCAL_PROTECTED_PATH"
    assert "OpenCode plugin" in verdict["reason"]


# =============================================================================
# (e) Benign command with an unreachable gateway -> fail-open allow
# =============================================================================


def test_opencode_hook_allows_benign_command_without_server(tmp_path: Path) -> None:
    verdict = _run_hook(
        {"tool": "bash", "input": {"command": "ls -la"}, "sessionID": "ses_benign"},
        _hermetic_env(tmp_path),
    )
    assert verdict["decision"] == "allow"
    assert verdict["permission"] == "allow"
    assert verdict["effect"] == "allow"


# =============================================================================
# (f) Stateful circuit breaker -> force_ask lockout
# =============================================================================


def test_opencode_hook_circuit_breaker_trips_to_lockout(tmp_path: Path) -> None:
    env = _hermetic_env(tmp_path, max_failures=2)
    payload = {"tool": "bash", "input": {"command": "ls -la"}, "sessionID": "ses_breaker"}

    # Run 1: one active failure -> transient fail-open allow.
    assert _run_hook(payload, env)["decision"] == "allow"

    # The breaker trips at 2 consecutive failures inside the window, so calls
    # two and three are both locked out (the third call is the explicit case).
    second = _run_hook(payload, env)
    third = _run_hook(payload, env)

    assert second["decision"] == "force_ask"
    for verdict in (second, third):
        assert verdict["decision"] == "force_ask"
        assert verdict["permission"] == "ask"
        assert verdict["effect"] == "ask"
        assert verdict["terminal_reason"] == "lockout"
    assert third["rule_id"] == "CIRCUIT_BREAKER_TRIPPED"
    assert "CIRCUIT BREAKER TRIPPED" in third["reason"]


# =============================================================================
# (g) permission.evaluate style payloads (action + resources)
# =============================================================================


def test_opencode_permission_evaluate_command_resource_is_denied(tmp_path: Path) -> None:
    verdict = _run_hook(
        {"action": "bash", "resources": ["rm -rf /"], "sessionID": "ses_perm_cmd"},
        _hermetic_env(tmp_path),
    )
    assert verdict["decision"] == "deny"
    assert verdict["rule_id"] == "LOCAL_RM_RF_ROOT"


def test_opencode_permission_evaluate_path_resource_is_denied(tmp_path: Path) -> None:
    verdict = _run_hook(
        {
            "action": "edit",
            "resources": [".opencode/plugins/openeval-watcher/index.ts"],
            "sessionID": "ses_perm_path",
        },
        _hermetic_env(tmp_path),
    )
    assert verdict["decision"] == "deny"
    assert verdict["rule_id"] == "LOCAL_PROTECTED_PATH"


# =============================================================================
# (h) Oversight hook probing detector covers OpenCode plugin/hook paths
# =============================================================================


def test_monitor_hook_probing_matches_opencode_plugin_and_hook_paths() -> None:
    assert RULE_MONITOR_HOOK_PROBING.matches("cat .opencode/plugins/openeval-watcher/index.ts")
    assert RULE_MONITOR_HOOK_PROBING.matches("cat .opencode/plugin/index.ts")
    assert RULE_MONITOR_HOOK_PROBING.matches("cat ~/.openeval/hooks/opencode_watcher_hook.py")


# =============================================================================
# (i) Installer / hook download routes
# =============================================================================


@pytest.mark.parametrize(
    "filename",
    ["opencode_watcher_hook.py", "install_opencode_watcher_hook.sh"],
)
def test_opencode_scripts_are_downloadable(filename: str) -> None:
    client = TestClient(app)
    response = client.get(f"/api/scripts/{filename}")
    assert response.status_code == 200
    assert response.content
    assert "OPENEVAL" in response.text


def test_gate_request_accepts_subject_model_metadata() -> None:
    from server.routes.watcher_gate import CodexGateRequest

    request = CodexGateRequest(
        tool_name="bash",
        metadata={"subject_model": "opencode-go/deepseek-v4.1-flash"},
    )
    assert request.metadata == {"subject_model": "opencode-go/deepseek-v4.1-flash"}


def test_gate_endpoint_forwards_subject_model_metadata_to_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The /gate alias must hand OpenCode's subject model to the policy gateway."""
    from schemas.watcher_models import ReviewRecord
    from server.policy_gateway import PolicyGateway

    captured: dict[str, Any] = {}

    async def fake_evaluate(
        self: PolicyGateway,
        session_id: str,
        tool_name: str,
        tool_input: str,
        **kwargs: Any,
    ) -> ReviewRecord:
        captured["metadata"] = kwargs.get("metadata")
        return ReviewRecord(
            session_id=session_id,
            tool_name=tool_name,
            decision="allow",
            explanation="stubbed allow",
        )

    monkeypatch.setattr(PolicyGateway, "evaluate_tool_call_async", fake_evaluate)

    client = TestClient(app)
    response = client.post(
        "/api/v1/watcher/gate",
        json={
            "tool_name": "bash",
            "arguments": {"command": "ls -la"},
            "agent_type": "opencode",
            "session_id": "opencode-ses-metadata",
            "metadata": {"subject_model": "opencode-go/deepseek-v4.1-flash"},
        },
    )

    assert response.status_code == 200
    assert captured["metadata"] == {"subject_model": "opencode-go/deepseek-v4.1-flash"}
