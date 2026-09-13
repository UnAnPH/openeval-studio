"""Tests for Cursor Watcher Gate script and endpoint integration."""

import json
import subprocess
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import app

GATE_SCRIPT = Path(__file__).parent.parent / "scripts" / "cursor_watcher_gate.py"


def _run_gate(payload: dict) -> dict:
    if "conversation_id" not in payload:
        payload["conversation_id"] = uuid.uuid4().hex
    proc = subprocess.run(
        [sys.executable, str(GATE_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout.strip())


def test_cursor_gate_shell_dangerous_prompts_user() -> None:
    """Dangerous shell commands must return permission: 'ask' so Cursor prompts the user."""
    res = _run_gate(
        {
            "hook_event_name": "beforeShellExecution",
            "command": "git push --force origin main",
            "conversation_id": uuid.uuid4().hex,
        }
    )
    assert res["permission"] == "ask"
    assert "git force push" in res["user_message"]


def test_cursor_gate_shell_benign_allows() -> None:
    """Benign commands must be allowed without prompting."""
    res = _run_gate(
        {
            "hook_event_name": "beforeShellExecution",
            "command": "ls -la",
            "conversation_id": uuid.uuid4().hex,
        }
    )
    assert res["permission"] == "allow"
    assert res["continue"] is True


def test_cursor_gate_pre_tool_use_shell_delegates_to_before_shell() -> None:
    """preToolUse for Shell tool must return allow so beforeShellExecution can prompt."""
    res = _run_gate(
        {
            "hook_event_name": "preToolUse",
            "tool_name": "Shell",
            "tool_input": {"command": "git push --force origin main"},
            "conversation_id": uuid.uuid4().hex,
        }
    )
    assert res["permission"] == "allow"


def test_cursor_gate_pre_tool_use_write_benign_file_allowed() -> None:
    """Writing to standard project files must be allowed even if content contains shell keywords."""
    res = _run_gate(
        {
            "hook_event_name": "preToolUse",
            "tool_name": "Write",
            "tool_input": {
                "path": "/workspace/server/app.py",
                "contents": "import os\nos.system('rm -rf /tmp/cache')\nload_dotenv('.env')",
            },
            "conversation_id": uuid.uuid4().hex,
        }
    )
    assert res["permission"] == "allow"


def test_cursor_gate_pre_tool_use_write_sensitive_file_denied() -> None:
    """Writing to .env or secret credentials must be denied."""
    res = _run_gate(
        {
            "hook_event_name": "preToolUse",
            "tool_name": "Write",
            "tool_input": {
                "path": "/workspace/.env",
                "contents": "SECRET_KEY=12345",
            },
            "conversation_id": uuid.uuid4().hex,
        }
    )
    assert res["permission"] == "deny"
    assert "blocked" in res["user_message"].lower()


def test_cursor_gate_pre_tool_use_read_credentials_denied() -> None:
    """Reading private keys must be denied."""
    res = _run_gate(
        {
            "hook_event_name": "preToolUse",
            "tool_name": "Read",
            "tool_input": {"path": "/home/user/.ssh/id_rsa"},
            "conversation_id": uuid.uuid4().hex,
        }
    )
    assert res["permission"] == "deny"


def test_evaluate_action_file_tool_does_not_apply_command_rules() -> None:
    """Rule 6: evaluate_action_watcher_gateway must not treat file-write payloads as shell commands."""
    client = TestClient(app)
    # File write containing shell keywords (rm -rf, sudo, .env in code text)
    res = client.post(
        "/api/watcher/evaluate",
        json={
            "agent_id": "cursor",
            "tool_name": "Write",
            "arguments": {
                "path": "/workspace/server/service.py",
                "contents": "def clean():\n    os.system('rm -rf /tmp/build')\n",
            },
            "session_id": "test-file-write-session",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["decision"] == "allow"
    assert data["is_safe"] is True


def test_evaluate_action_file_tool_blocks_sensitive_file_path() -> None:
    """Writing directly to .env must be blocked by Sensitive files rule."""
    client = TestClient(app)
    res = client.post(
        "/api/watcher/evaluate",
        json={
            "agent_id": "cursor",
            "tool_name": "Write",
            "arguments": {
                "path": "/workspace/.env",
                "contents": "SECRET_KEY=leaked",
            },
            "session_id": "test-file-write-env-session",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["decision"] in ("deny", "block")
    assert data["rule_violation_tag"] == "Sensitive files"
