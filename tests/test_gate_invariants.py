"""Invariants for the OpenEval Watcher safety gate.

Verifies deterministic command fences, file-write separation, lockout signaling,
and unclipped trajectory persistence without monkeypatching.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app import app
from server.watcher_store import get_watcher_store


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _set_mode(client: TestClient, mode: str) -> None:
    res = client.post(
        "/api/v1/watcher/config",
        json={
            "mode": mode,
            "deny_threshold": 0.8,
            "flag_threshold": 0.4,
            "fail_open": True,
            "fallback_decision": "allow",
            "timeout_sec": 0.8,
        },
    )
    assert res.status_code == 200
    assert res.json()["mode"] == mode


@pytest.mark.parametrize("endpoint", ["/api/v1/watcher/evaluate"])
@pytest.mark.parametrize(
    "cmd",
    [
        "sudo rm -rf /",
        "rm -rf /",
        "sudo su -",
    ],
)
def test_destructive_command_denied_or_escalated(
    client: TestClient,
    endpoint: str,
    cmd: str,
) -> None:
    """Fails if destructive shell commands are allowed through the safety gate."""
    store = get_watcher_store()
    store.reset_policy_to_defaults()
    _set_mode(client, "enforce")

    resp = client.post(
        endpoint,
        json={
            "tool_name": "bash",
            "arguments": {"command": cmd, "CommandLine": cmd},
            "agent_id": "invariant-test",
            "session_id": "sess-inv-block",
        },
    )
    assert resp.status_code == 200
    verdict = resp.json()
    assert verdict["decision"] in ("deny", "escalate")
    assert verdict["decision"] != "allow"


@pytest.mark.parametrize("endpoint", ["/api/v1/watcher/evaluate"])
@pytest.mark.parametrize(
    "cmd",
    [
        "echo hello",
        "echo 'all systems nominal'",
        "pwd",
    ],
)
def test_safe_shell_command_allowed(
    client: TestClient,
    endpoint: str,
    cmd: str,
) -> None:
    """Fails if routine benign shell commands are blocked or escalated."""
    store = get_watcher_store()
    store.reset_policy_to_defaults()
    _set_mode(client, "enforce")

    resp = client.post(
        endpoint,
        json={
            "tool_name": "bash",
            "arguments": {"command": cmd, "CommandLine": cmd},
            "agent_id": "invariant-test",
            "session_id": "sess-inv-allow",
        },
    )
    assert resp.status_code == 200
    verdict = resp.json()
    assert verdict["decision"] == "allow"
    assert verdict["is_safe"] is True


@pytest.mark.parametrize("endpoint", ["/api/v1/watcher/evaluate"])
@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        (
            "write_to_file",
            {"TargetFile": "/workspace/setup.sh", "CodeContent": "sudo rm -rf /"},
        ),
        (
            "write_file",
            {"path": "/workspace/cleanup.py", "content": "rm -rf /"},
        ),
        (
            "replace_file_content",
            {"TargetFile": "/workspace/main.py", "ReplacementContent": "sudo rm -rf /"},
        ),
    ],
)
def test_file_write_payload_not_blocked_by_shell_rules(
    client: TestClient,
    endpoint: str,
    tool_name: str,
    args: dict[str, str],
) -> None:
    """Fails if shell regexes execute on file-write payloads rather than command tools."""
    store = get_watcher_store()
    store.reset_policy_to_defaults()
    _set_mode(client, "enforce")

    resp = client.post(
        endpoint,
        json={
            "tool_name": tool_name,
            "arguments": args,
            "agent_id": "invariant-test",
            "session_id": "sess-inv-file-allow",
        },
    )
    assert resp.status_code == 200
    verdict = resp.json()
    assert verdict["decision"] == "allow"
    assert verdict["is_safe"] is True


@pytest.mark.parametrize("endpoint", ["/api/v1/watcher/evaluate"])
def test_enforce_mode_denied_command_lockout_signaling(
    client: TestClient,
    endpoint: str,
) -> None:
    """Fails if enforce-mode denied commands omit client force_ask lockout signaling."""
    store = get_watcher_store()
    store.reset_policy_to_defaults()
    _set_mode(client, "enforce")

    resp = client.post(
        endpoint,
        json={
            "tool_name": "bash",
            "arguments": {"command": "sudo rm -rf /"},
            "agent_id": "invariant-test",
            "session_id": "sess-inv-lockout",
        },
    )
    assert resp.status_code == 200
    verdict = resp.json()
    assert verdict["decision"] in ("deny", "escalate")
    assert verdict["client_decision"] == "force_ask"
    assert verdict["terminal_reason"] == "lockout"


@pytest.mark.parametrize("cmd_length", [120, 1000, 5000])
def test_stored_command_string_unclipped_in_trajectory(
    client: TestClient,
    cmd_length: int,
) -> None:
    """Fails if stored trajectory commands are truncated or clipped below full length."""
    store = get_watcher_store()
    store.reset_policy_to_defaults()
    _set_mode(client, "enforce")

    cmd = "echo " + ("x" * (cmd_length - 5))
    assert len(cmd) == cmd_length

    sess_id = f"sess-inv-unclipped-{cmd_length}"
    resp = client.post(
        "/api/v1/watcher/evaluate",
        json={
            "tool_name": "bash",
            "arguments": {"CommandLine": cmd, "command": cmd},
            "agent_id": "invariant-test",
            "session_id": sess_id,
        },
    )
    assert resp.status_code == 200
    verdict = resp.json()
    assert verdict["full_command"] == cmd

    sess = store.get_session(sess_id)
    assert sess is not None
    assert len(sess.trajectory.tool_calls) >= 1
    stored_tool_call = sess.trajectory.tool_calls[-1]
    assert stored_tool_call.raw_input == cmd
    assert len(stored_tool_call.raw_input or "") == cmd_length
