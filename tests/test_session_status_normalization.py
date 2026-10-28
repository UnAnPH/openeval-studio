from datetime import UTC, datetime, timedelta
from uuid import uuid4

from schemas.watcher_models import ReviewRecord, Session, Trajectory
from server.db import current_user_slug
from server.watcher_store import (
    WatcherStore,
    is_session_blocked,
    normalize_session_status,
)


def test_is_session_blocked_logic():
    # 1. Unblocked session
    sess = Session(
        session_id="test-unblocked",
        status="working",
        trajectory=Trajectory(session_id="test-unblocked"),
    )
    assert not is_session_blocked(sess)

    # 2. Session with block review
    rev_block = ReviewRecord(
        id=str(uuid4()),
        session_id="test-blocked-1",
        tool_name="bash",
        tool_input="rm -rf /",
        decision="block",
        score=9,
    )
    sess_blocked = Session(
        session_id="test-blocked-1",
        status="working",
        trajectory=Trajectory(session_id="test-blocked-1", reviews=[rev_block]),
    )
    assert is_session_blocked(sess_blocked)

    # 3. Session with operator human_override allow
    rev_override = ReviewRecord(
        id=str(uuid4()),
        session_id="test-override",
        tool_name="bash",
        tool_input="rm -rf /",
        decision="block",
        score=9,
        human_override="allow",
        resolution_status="human_approved",
    )
    sess_override = Session(
        session_id="test-override",
        status="working",
        trajectory=Trajectory(session_id="test-override", reviews=[rev_override]),
    )
    assert not is_session_blocked(sess_override)


def test_normalize_session_status():
    now = datetime.now(UTC)

    # 1. Blocked session normalized to "blocked", never "working"
    rev_block = ReviewRecord(
        id=str(uuid4()),
        session_id="test-norm-block",
        tool_name="bash",
        decision="block",
    )
    sess_blocked = Session(
        session_id="test-norm-block",
        status="working",
        trajectory=Trajectory(session_id="test-norm-block", reviews=[rev_block]),
    )
    normalized = normalize_session_status(sess_blocked)
    assert normalized.status == "blocked"

    # 2. Fresh session remains working
    sess_fresh = Session(
        session_id="test-norm-fresh",
        status="working",
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
    )
    assert normalize_session_status(sess_fresh).status == "working"

    # 3. Stale session (> 120s old) normalized to closed
    old_time = (now - timedelta(minutes=10)).isoformat()
    sess_stale = Session(
        session_id="test-norm-stale",
        status="working",
        created_at=old_time,
        updated_at=old_time,
    )
    assert normalize_session_status(sess_stale).status == "closed"

    # 4. Demo mode session is never working -> closed
    current_user_slug.set("demo")
    try:
        sess_demo = Session(
            session_id="test-norm-demo",
            status="working",
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
            org_id="demo",
        )
        assert normalize_session_status(sess_demo).status == "closed"
    finally:
        current_user_slug.set("owner")


def test_watcher_store_block_decision_marks_session_blocked(tmp_path):
    store = WatcherStore(storage_dir=tmp_path)
    session_id = f"test-store-{uuid4().hex[:8]}"

    # Create fresh working session
    sess = Session(
        session_id=session_id,
        status="working",
        title="Active Working Session",
    )
    store.create_session(sess)
    sess_init = store.get_session(session_id)
    assert sess_init is not None
    assert sess_init.status == "working"

    # Record block decision
    rev_block = ReviewRecord(
        id=str(uuid4()),
        session_id=session_id,
        tool_name="execute_command",
        tool_input="cat /etc/shadow",
        decision="block",
        score=9,
    )
    store.record_decision(rev_block)

    # Session status must be blocked
    reloaded = store.get_session(session_id)
    assert reloaded is not None
    assert reloaded.status == "blocked"

    # Guard: update_session attempting to restore status="working" stays blocked
    store.update_session(session_id, {"status": "working", "current_activity": "trying again"})
    guarded = store.get_session(session_id)
    assert guarded is not None
    assert guarded.status == "blocked"
