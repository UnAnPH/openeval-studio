"""Golden contracts for Watcher mode + evaluate store persistence."""

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
        "/api/watcher/config",
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


def test_mode_contracts_enforce_observe_paused(client: TestClient):
    store = get_watcher_store()
    store.reset_policy_to_defaults()
    store.clear_all_sessions()

    bad = {
        "tool_name": "bash",
        "arguments": {"cmd": "sudo rm -rf /"},
        "agent_id": "pytest",
    }

    _set_mode(client, "enforce")
    r = client.post("/api/watcher/evaluate", json={**bad, "session_id": "gold-enforce"})
    assert r.status_code == 200
    assert r.json()["decision"] in ("deny", "escalate")

    _set_mode(client, "observe")
    r = client.post("/api/watcher/evaluate", json={**bad, "session_id": "gold-observe"})
    body = r.json()
    assert body["decision"] == "allow"
    assert body.get("shadow_decision") in ("deny", "escalate")

    _set_mode(client, "paused")
    r = client.post("/api/watcher/evaluate", json={**bad, "session_id": "gold-paused"})
    assert r.json()["decision"] == "allow"

    _set_mode(client, "enforce")
    r = client.post(
        "/api/watcher/evaluate",
        json={
            "tool_name": "bash",
            "arguments": {"cmd": "echo hello"},
            "agent_id": "pytest",
            "session_id": "gold-allow",
        },
    )
    assert r.json()["decision"] == "allow"

    sessions = {s.session_id for s in store.list_sessions()}
    assert "gold-enforce" in sessions
    assert "gold-observe" in sessions

    findings = client.get("/api/watcher/findings").json()
    assert any(f["session_id"] == "gold-enforce" for f in findings)


def test_hermetic_eval_smoke_script():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "hermetic_eval_smoke.py"
    proc = subprocess.run([sys.executable, str(script)], cwd=root, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = root / "artifacts" / "hermetic-eval.json"
    assert artifact.exists()
    import json

    data = json.loads(artifact.read_text())
    assert data["integrity_ok"] is True
    assert data["pass_rate"]["nop"] == "0/1"
    assert data["pass_rate"]["oracle"] == "1/1"
