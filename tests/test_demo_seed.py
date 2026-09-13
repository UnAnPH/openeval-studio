"""Tests for hosted DEMO seed loader and sanitization integrity."""

from pathlib import Path

from starlette.testclient import TestClient

from server.app import app
from server.demo_seed import seed_demo_data
from server.store import RunStore
from server.watcher_store import WatcherStore

DENYLIST = [
    "radiosa",
    "simplecast",
    "apollo",
    "jaysonandal",
    "jayson andal",
    "/users/jaysonandal",
]


def test_seed_demo_data_loads_sessions_and_evals(tmp_path: Path):
    """Verify seed_demo_data populates WatcherStore and RunStore from fixtures."""
    db_path = tmp_path / "test_seed.duckdb"
    store = WatcherStore(storage_dir=tmp_path / "sessions", db_path=str(db_path))
    run_store = RunStore(watcher_store=store)

    loaded = seed_demo_data(store=store, run_store=run_store)
    assert loaded["sessions"] >= 4
    assert loaded["evals"] >= 3

    # Check sessions
    sessions = store.list_sessions()
    session_ids = [s.session_id for s in sessions]
    assert "antigravity-demo-force-push" in session_ids
    assert "claude-demo-sudo-escalation" in session_ids
    assert "cursor-demo-untrusted-script" in session_ids
    assert "antigravity-demo-benign-build" in session_ids

    # Check runs — real Inspect AI benchmark fixtures
    runs = run_store.list_runs()
    run_ids = [r.run_id for r in runs]
    assert "eval-regex-log-gemini-flash-lite" in run_ids
    assert "eval-regex-log-gemini-3-5-flash-lite" in run_ids
    assert "redteam-sample-01" in run_ids

    # Check denylist compliance on all serialized content
    for s in sessions:
        dumped = s.model_dump_json().lower()
        for bad in DENYLIST:
            assert bad not in dumped, f"Found denylist word '{bad}' in session {s.session_id}"

    for r in runs:
        dumped = r.model_dump_json().lower()
        for bad in DENYLIST:
            assert bad not in dumped, f"Found denylist word '{bad}' in run {r.run_id}"


def test_seed_demo_data_is_idempotent(tmp_path: Path):
    """Calling seed_demo_data multiple times should safely upsert without error."""
    db_path = tmp_path / "test_idempotent.duckdb"
    store = WatcherStore(storage_dir=tmp_path / "idempotent_sessions", db_path=str(db_path))
    run_store = RunStore(watcher_store=store)

    first = seed_demo_data(store=store, run_store=run_store)
    second = seed_demo_data(store=store, run_store=run_store)

    assert first == second
    assert len(store.list_sessions()) >= 4


def test_demo_status_endpoint():
    """Verify /api/health returns demo_seed boolean and /api/demo/status reports status."""
    with TestClient(app) as client:
        health_resp = client.get("/api/health")
        assert health_resp.status_code == 200
        health_data = health_resp.json()
        assert "demo_seed" in health_data

        status_resp = client.get("/api/demo/status")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert "demo_mode" in status_data
        assert "seeded_sessions" in status_data
        assert "seeded_eval_runs" in status_data


def test_demo_mode_isolation_and_new_fixtures(monkeypatch):
    """Verify demo mode completely isolates host sessions and loads current fixtures."""
    monkeypatch.setenv("OPENEVAL_DEMO_SEED", "1")
    monkeypatch.setenv("WATCHER_STORAGE_DIR", "")

    stats = seed_demo_data()
    assert stats["sessions"] == 8
    # 2 real Inspect AI benchmark runs + 4 red-team probes + 1 sample = 7
    assert stats["evals"] == 7

    with TestClient(app) as client:
        # Check sessions isolation
        sess_resp = client.get("/api/v1/watcher/sessions")
        assert sess_resp.status_code == 200
        sessions = sess_resp.json()
        session_ids = [s["session_id"] for s in sessions]

        assert "antigravity-demo-env-leak" in session_ids
        assert "claude-demo-database-migration" in session_ids
        assert "cursor-demo-rm-rf-blocked" in session_ids
        assert "antigravity-demo-refactor-approved" in session_ids

        # Ensure no private host transcripts appear
        for sid in session_ids:
            assert not sid.startswith("antigravity-") or "demo" in sid
            assert not sid.startswith("cursor-") or "demo" in sid
            assert not sid.startswith("claude-") or "demo" in sid

        # Check evals — real Inspect AI runs (no fake nop/oracle)
        runs_resp = client.get("/api/eval/runs")
        assert runs_resp.status_code == 200
        runs = runs_resp.json()
        run_ids = [r["run_id"] for r in runs]

        assert "eval-regex-log-gemini-flash-lite" in run_ids
        assert "eval-regex-log-gemini-3-5-flash-lite" in run_ids
        assert "redteam-authority-roleplay" in run_ids
        assert "redteam-cognitive-reframing" in run_ids
        assert "redteam-research-hypothetical" in run_ids
        assert "redteam-cipher-obfuscation" in run_ids

        # Interceptions poll must never trigger host scans or leak non-demo sessions
        intercept_resp = client.get("/api/watcher/interceptions?limit=40")
        assert intercept_resp.status_code == 200
        interceptions = intercept_resp.json()
        assert len(interceptions) > 0
        for item in interceptions:
            sess_id = item.get("session_id", "")
            assert not sess_id.startswith("antigravity-") or "demo" in sess_id
            assert not sess_id.startswith("cursor-") or "demo" in sess_id
            assert not sess_id.startswith("claude-") or "demo" in sess_id

        # Sessions list must still contain exactly the curated demo sessions
        sess_resp_after = client.get("/api/v1/watcher/sessions")
        assert sess_resp_after.status_code == 200
        after_ids = [s["session_id"] for s in sess_resp_after.json()]
        assert len(after_ids) == 8
        for sid in after_ids:
            assert "demo" in sid or sid.startswith("demo-")
