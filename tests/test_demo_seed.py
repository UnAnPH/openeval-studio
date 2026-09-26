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
        # Check sessions isolation via demo surface
        demo_headers = {"X-OpenEval-Surface": "demo"}
        sess_resp = client.get("/api/v1/watcher/sessions", headers=demo_headers)
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
        runs_resp = client.get("/api/eval/runs", headers=demo_headers)
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
        intercept_resp = client.get("/api/watcher/interceptions?limit=40", headers=demo_headers)
        assert intercept_resp.status_code == 200
        interceptions = intercept_resp.json()
        assert len(interceptions) > 0
        for item in interceptions:
            sess_id = item.get("session_id", "")
            assert not sess_id.startswith("antigravity-") or "demo" in sess_id
            assert not sess_id.startswith("cursor-") or "demo" in sess_id
            assert not sess_id.startswith("claude-") or "demo" in sess_id

        # Sessions list must still contain the curated demo sessions
        sess_resp_after = client.get("/api/v1/watcher/sessions", headers=demo_headers)
        assert sess_resp_after.status_code == 200
        after_ids = [s["session_id"] for s in sess_resp_after.json()]
        assert len(after_ids) >= 8
        assert "antigravity-demo-env-leak" in after_ids
        for sid in [
            "antigravity-demo-env-leak",
            "claude-demo-database-migration",
            "cursor-demo-rm-rf-blocked",
            "antigravity-demo-refactor-approved",
        ]:
            assert sid in after_ids


def test_unauthenticated_get_with_demo_seed_returns_401(monkeypatch):
    """Unauthenticated GET /api/v1/watcher/sessions with OPENEVAL_DEMO_SEED=1 and no surface header returns 401."""
    monkeypatch.setenv("OPENEVAL_DEMO_SEED", "1")
    monkeypatch.setenv("OPENEVAL_AUTH_DISABLED", "0")
    seed_demo_data()

    with TestClient(app) as client:
        # No credentials, no surface header -> 401
        res = client.get("/api/v1/watcher/sessions")
        assert res.status_code == 401
        assert "Authentication required" in res.json().get("detail", "")

        # Same request with X-OpenEval-Surface: demo -> 200 with only demo rows
        demo_res = client.get("/api/v1/watcher/sessions", headers={"X-OpenEval-Surface": "demo"})
        assert demo_res.status_code == 200
        demo_sessions = demo_res.json()
        assert len(demo_sessions) > 0
        demo_ids = [s["session_id"] for s in demo_sessions]
        assert "antigravity-demo-env-leak" in demo_ids


def test_alice_cookie_with_demo_seed_returns_only_alice(monkeypatch):
    """A cookie for user alice on /api/v1/watcher/sessions returns only Alice's rows while seed flag is on."""
    from sqlalchemy import text

    from schemas.watcher_models import Session
    from server.db import SessionLocal, current_user_id, current_user_slug
    from server.watcher_store import get_watcher_store

    monkeypatch.setenv("OPENEVAL_DEMO_SEED", "1")
    monkeypatch.setenv("OPENEVAL_AUTH_DISABLED", "0")
    seed_demo_data()

    with SessionLocal() as db:
        db.execute(text("DELETE FROM users WHERE slug = 'alice_seed_test'"))
        db.commit()

    with TestClient(app) as client:
        # Register alice
        reg = client.post(
            "/api/auth/register",
            json={"username": "alice_seed_test", "password": "password123"},
        )
        assert reg.status_code == 200

        # Insert a session for alice
        with SessionLocal() as db:
            alice_uid = db.execute(
                text("SELECT id FROM users WHERE slug = 'alice_seed_test'")
            ).scalar()

        current_user_slug.set("alice_seed_test")
        current_user_id.set(alice_uid)
        store = get_watcher_store()
        alice_session = Session(
            session_id="alice-custom-session-xyz",
            agent_type="claude_code",
            status="active",
            created_at="2026-09-26T12:00:00Z",
        )
        store.record_session(alice_session)

        # GET /api/v1/watcher/sessions using Alice's session cookie (which client persists from register)
        res = client.get("/api/v1/watcher/sessions")
        assert res.status_code == 200
        sessions = res.json()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "alice-custom-session-xyz"


def test_seeding_twice_leaves_owner_unchanged_and_preserves_history():
    """Seeding twice leaves the owner row count unchanged and does not require in-memory history clear."""
    from sqlalchemy import text

    from engine.approval_policy import WatcherVerdict, global_watcher_engine
    from server.db import SessionLocal

    # Add an interception history item
    test_verdict = WatcherVerdict(
        decision="deny",
        stage="stage_2_deterministic",
        reason="Preserved in-memory item",
        risk_score=0.9,
        action_preview="cat /etc/shadow",
        agent_id="test-agent",
        session_id="test-preserve-session",
        is_safe=False,
    )
    global_watcher_engine.record_interception(test_verdict)

    with SessionLocal() as db:
        initial_owner_count = db.execute(
            text("SELECT count(*) FROM users WHERE slug = 'owner'")
        ).scalar()

    seed_demo_data()
    seed_demo_data()

    with SessionLocal() as db:
        final_owner_count = db.execute(
            text("SELECT count(*) FROM users WHERE slug = 'owner'")
        ).scalar()

    assert final_owner_count == initial_owner_count
    # In-memory history was not cleared by seed_demo_data
    history = [v.session_id for v in global_watcher_engine.get_history(limit=50)]
    assert "test-preserve-session" in history
