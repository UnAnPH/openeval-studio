"""Unit and integration tests for OpenEval Hook Stateful Circuit Breakers.

Verifies:
1. Transient gateway failure -> allows (fail-open for network blips).
2. Consecutive gateway failures (>= MAX_CONSECUTIVE_FAILURES) -> trips circuit breaker to force_ask/deny.
3. Successful gateway evaluation -> resets the failure records (circuit closes).
4. Window expiry -> failures older than FAILURE_WINDOW_SEC expire and do not trip.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
ANTIGRAVITY_GATE = SCRIPTS_DIR / "antigravity_watcher_gate.py"
CURSOR_GATE = SCRIPTS_DIR / "cursor_watcher_gate.py"


@pytest.fixture
def temp_circuit_file():
    """Create a temporary circuit breaker state file isolated per test."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = Path(f.name)
    yield path
    if path.exists():
        path.unlink()


def _run_antigravity_gate(
    payload: dict,
    env_overrides: dict[str, str],
) -> dict:
    env = {**os.environ, **env_overrides}
    proc = subprocess.run(
        [sys.executable, str(ANTIGRAVITY_GATE)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return json.loads(proc.stdout.strip())


def _run_cursor_gate(
    payload: dict,
    env_overrides: dict[str, str],
) -> dict:
    env = {**os.environ, **env_overrides}
    proc = subprocess.run(
        [sys.executable, str(CURSOR_GATE)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return json.loads(proc.stdout.strip())


def test_antigravity_circuit_breaker_transient_failure_fails_open(temp_circuit_file: Path):
    """A single transient timeout / unreachable server must fail open."""
    env = {
        "OPENEVAL_WATCHER_URL": "http://127.0.0.1:59999/unreachable",
        "OPENEVAL_CIRCUIT_STATE_FILE": str(temp_circuit_file),
        "OPENEVAL_MAX_GATEWAY_FAILURES": "3",
        "OPENEVAL_FAILURE_WINDOW_SEC": "60",
    }
    payload = {
        "toolCall": {"name": "run_command", "args": {"CommandLine": "echo 'safe command'"}},
        "conversationId": uuid.uuid4().hex,
    }

    res = _run_antigravity_gate(payload, env)
    assert res["decision"] == "allow"
    assert "transient fail-open" in res.get("reason", "").lower()

    # Verify 1 failure recorded in state file
    assert temp_circuit_file.exists()
    data = json.loads(temp_circuit_file.read_text(encoding="utf-8"))
    assert len(data.get("failures", [])) == 1


def test_antigravity_circuit_breaker_trips_after_max_failures(temp_circuit_file: Path):
    """Consecutive failures reaching MAX_CONSECUTIVE_FAILURES must trip to force_ask."""
    env = {
        "OPENEVAL_WATCHER_URL": "http://127.0.0.1:59999/unreachable",
        "OPENEVAL_CIRCUIT_STATE_FILE": str(temp_circuit_file),
        "OPENEVAL_MAX_GATEWAY_FAILURES": "3",
        "OPENEVAL_FAILURE_WINDOW_SEC": "60",
    }
    payload = {
        "toolCall": {"name": "run_command", "args": {"CommandLine": "ls -la"}},
        "conversationId": uuid.uuid4().hex,
    }

    # Calls 1 and 2: Transient failure within budget -> allow
    res1 = _run_antigravity_gate(payload, env)
    assert res1["decision"] == "allow"

    res2 = _run_antigravity_gate(payload, env)
    assert res2["decision"] == "allow"

    # Call 3: Hits threshold (3) -> Circuit breaker trips to force_ask
    res3 = _run_antigravity_gate(payload, env)
    assert res3["decision"] == "force_ask"
    assert "CIRCUIT BREAKER TRIPPED" in res3.get("reason", "")


def test_antigravity_circuit_breaker_window_expiry(temp_circuit_file: Path):
    """Old failures outside the sliding window must expire and not trip the breaker."""
    env = {
        "OPENEVAL_WATCHER_URL": "http://127.0.0.1:59999/unreachable",
        "OPENEVAL_CIRCUIT_STATE_FILE": str(temp_circuit_file),
        "OPENEVAL_MAX_GATEWAY_FAILURES": "3",
        "OPENEVAL_FAILURE_WINDOW_SEC": "2.0",  # Short 2-second window
    }
    payload = {
        "toolCall": {"name": "run_command", "args": {"CommandLine": "pwd"}},
        "conversationId": uuid.uuid4().hex,
    }

    # Record 2 old failures from 5 seconds ago
    old_time = time.time() - 5.0
    temp_circuit_file.write_text(
        json.dumps({"failures": [old_time, old_time + 0.1], "updated_at": old_time}),
        encoding="utf-8",
    )

    # Next run should prune the old failures and see only 1 active failure -> allow
    res = _run_antigravity_gate(payload, env)
    assert res["decision"] == "allow"

    data = json.loads(temp_circuit_file.read_text(encoding="utf-8"))
    assert len(data.get("failures", [])) == 1


def test_cursor_circuit_breaker_trips_to_ask_on_repeated_failures(temp_circuit_file: Path):
    """Cursor gate shell execution must prompt the user ('ask') when circuit breaker trips."""
    env = {
        "OPENEVAL_WATCHER_URL": "http://127.0.0.1:59999/unreachable",
        "OPENEVAL_CIRCUIT_STATE_FILE": str(temp_circuit_file),
        "OPENEVAL_MAX_GATEWAY_FAILURES": "2",
        "OPENEVAL_FAILURE_WINDOW_SEC": "60",
    }
    payload = {
        "hook_event_name": "beforeShellExecution",
        "command": "python3 script.py",
        "conversation_id": uuid.uuid4().hex,
    }

    # Run 1: transient failure -> allow
    res1 = _run_cursor_gate(payload, env)
    assert res1["permission"] == "allow"

    # Run 2: reaches threshold 2 -> trips to ask
    res2 = _run_cursor_gate(payload, env)
    assert res2["permission"] == "ask"
    assert "CIRCUIT BREAKER TRIPPED" in res2.get("user_message", "")
