"""Tests for vendor hook payload schema drift (Q6 Nuance).

Verifies that:
1. extra="forbid" on WatcherRequest rejects unexpected vendor fields with HTTP 422.
2. Hook scripts (claude_code_watcher_hook, cursor_watcher_gate) fail closed to
   interactive lockout ("ask") when schema drift occurs and print clear diagnostics.
"""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from engine.approval_policy import WatcherRequest
from server.app import app


def test_watcher_request_extra_forbid():
    """Verify WatcherRequest strictly rejects unknown fields at the Pydantic boundary."""
    # Valid payload parses cleanly
    valid = WatcherRequest(
        tool_name="bash",
        arguments={"command": "ls -la"},
        agent_id="claude_code",
    )
    assert valid.tool_name == "bash"

    # Payload with unexpected field raises ValidationError with extra_forbidden
    with pytest.raises(ValidationError) as exc_info:
        WatcherRequest.model_validate(
            {
                "tool_name": "bash",
                "arguments": {"command": "ls -la"},
                "unexpected_vendor_field": "novel_value_123",
            }
        )

    errors = exc_info.value.errors()
    assert any(
        e["type"] == "extra_forbidden" and "unexpected_vendor_field" in e["loc"] for e in errors
    ), f"Expected extra_forbidden error for unexpected_vendor_field, got {errors}"


def test_gateway_rejects_payload_schema_drift():
    """Verify POST /api/v1/watcher/evaluate returns 422 on payload schema drift."""
    client = TestClient(app)
    resp = client.post(
        "/api/v1/watcher/evaluate",
        json={
            "tool_name": "bash",
            "arguments": {"command": "git status"},
            "vendor_unsupported_new_key": "drift_value",
            "session_id": "test-drift-sess",
        },
    )
    assert resp.status_code == 422
    data = resp.json()
    assert "detail" in data
    assert any(
        d.get("type") == "extra_forbidden" and "vendor_unsupported_new_key" in d.get("loc", [])
        for d in data["detail"]
    )


def test_claude_hook_fails_closed_on_schema_drift(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """Verify claude_code_watcher_hook emits interactive lockout on 422 schema drift."""
    import urllib.error

    from scripts import claude_code_watcher_hook

    # Mock server returning 422 with extra_forbidden
    mock_error_body = json.dumps(
        {
            "detail": [
                {
                    "type": "extra_forbidden",
                    "loc": ["body", "vendor_novel_flag"],
                    "msg": "Extra inputs are not permitted",
                }
            ]
        }
    ).encode("utf-8")

    err_fp = io.BytesIO(mock_error_body)
    http_err = urllib.error.HTTPError(
        url="http://127.0.0.1:8000/api/v1/watcher/evaluate",
        code=422,
        msg="Unprocessable Entity",
        hdrs=MagicMock(),
        fp=err_fp,
    )

    monkeypatch.setattr(
        "urllib.request.urlopen",
        MagicMock(side_effect=http_err),
    )

    # Run main() with a payload
    test_input = json.dumps(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
            "sessionId": "test-drift-session",
        }
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(test_input))

    claude_code_watcher_hook.main()

    captured = capsys.readouterr()
    # Check stderr for clear schema drift warning
    assert "payload schema drift" in captured.err.lower()
    assert "vendor_novel_flag" in captured.err

    # Check stdout for fail-closed lockout decision (permissionDecision: ask)
    out_json = json.loads(captured.out.strip())
    perm = out_json["hookSpecificOutput"]["permissionDecision"]
    reason = out_json["hookSpecificOutput"]["permissionDecisionReason"]
    assert perm == "ask"
    assert "schema drift" in reason.lower()
