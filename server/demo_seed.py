"""Demo session and evaluation fixture seeder for OpenEval Studio.

Populates hermetic sessions, interception reviews, and evaluation run records
when OPENEVAL_DEMO_SEED=1.
"""

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from engine.approval_policy import WatcherVerdict, global_watcher_engine
from schemas.watcher_models import Session

if TYPE_CHECKING:
    from server.store import RunStore
    from server.watcher_store import WatcherStore

logger = logging.getLogger("openeval.server.demo_seed")

DEMO_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "demo"


def is_demo_seed_enabled() -> bool:
    """Check if OPENEVAL_DEMO_SEED is active."""
    return os.getenv("OPENEVAL_DEMO_SEED", "0").lower() in ("1", "true", "yes")


_last_demo_refresh: float = 0.0


def generate_rolling_timeline(now: datetime | None = None) -> list[datetime]:
    """Generate dynamic relative timestamps so demo data covers today, this week, last week, this month, and last month."""
    if now is None:
        now = datetime.now(UTC)

    today_start = datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=UTC)

    # 1. Today (7 sessions) - guaranteed >= today_start and <= now
    hours_since_start = max(0.5, (now - today_start).total_seconds() / 3600.0)
    today_slots = [
        max(today_start + timedelta(minutes=10), now - timedelta(minutes=15)),
        max(today_start + timedelta(minutes=25), now - timedelta(minutes=48)),
        max(today_start + timedelta(minutes=40), now - timedelta(hours=2, minutes=10)),
        today_start + timedelta(hours=min(14.0, max(0.2, hours_since_start * 0.85))),
        today_start + timedelta(hours=min(10.0, max(0.2, hours_since_start * 0.65))),
        today_start + timedelta(hours=min(6.0, max(0.2, hours_since_start * 0.45))),
        today_start + timedelta(hours=min(2.5, max(0.1, hours_since_start * 0.2))),
    ]
    today_slots = sorted(set(today_slots), reverse=True)

    # 2. This week (prior days) - ISO week (Monday = 0, Sunday = 6)
    iso_day = now.weekday()  # 0 = Mon, ..., 6 = Sun
    curr_week_start = today_start - timedelta(days=iso_day)
    this_week_slots: list[datetime] = []
    for d in range(1, iso_day + 1):
        day_date = today_start - timedelta(days=d)
        this_week_slots.append(day_date + timedelta(hours=14, minutes=20))
        this_week_slots.append(day_date + timedelta(hours=10, minutes=45))

    # If early in the week (Mon/Tue), ensure there are extra slots in preceding days
    while len(this_week_slots) < 6:
        extra_d = len(this_week_slots) + 1
        this_week_slots.append(curr_week_start - timedelta(days=extra_d, hours=4))

    # 3. Last week (strictly Monday to Sunday of previous week)
    last_week_start = curr_week_start - timedelta(days=7)
    last_week_slots = [
        last_week_start + timedelta(days=6, hours=16, minutes=30),
        last_week_start + timedelta(days=5, hours=11, minutes=15),
        last_week_start + timedelta(days=4, hours=15, minutes=40),
        last_week_start + timedelta(days=3, hours=9, minutes=20),
        last_week_start + timedelta(days=2, hours=14, minutes=10),
        last_week_start + timedelta(days=1, hours=10, minutes=50),
        last_week_start + timedelta(days=0, hours=13, minutes=25),
    ]

    # 4. Earlier this month (before last week, within current month)
    first_of_month = datetime(now.year, now.month, 1, 0, 0, 0, tzinfo=UTC)
    this_month_early_slots: list[datetime] = []
    if now.day > 7:
        for d in [2, 4, 6]:
            if d < now.day - 7:
                this_month_early_slots.append(datetime(now.year, now.month, d, 14, 0, tzinfo=UTC))
    if not this_month_early_slots:
        this_month_early_slots = [
            last_week_start - timedelta(days=2, hours=3),
            last_week_start - timedelta(days=4, hours=7),
        ]

    # 5. Last month (strictly inside last month)
    last_month_end = first_of_month - timedelta(microseconds=1)
    first_of_last_month = datetime(
        last_month_end.year, last_month_end.month, 1, 0, 0, 0, tzinfo=UTC
    )
    last_month_slots = [
        datetime(first_of_last_month.year, first_of_last_month.month, 5, 14, 0, tzinfo=UTC),
        datetime(first_of_last_month.year, first_of_last_month.month, 10, 10, 15, tzinfo=UTC),
        datetime(first_of_last_month.year, first_of_last_month.month, 15, 16, 30, tzinfo=UTC),
        datetime(first_of_last_month.year, first_of_last_month.month, 20, 12, 45, tzinfo=UTC),
        datetime(first_of_last_month.year, first_of_last_month.month, 25, 9, 20, tzinfo=UTC),
        datetime(first_of_last_month.year, first_of_last_month.month, 28, 17, 10, tzinfo=UTC),
    ]

    # 6. Two months ago (comparison baseline)
    two_months_end = first_of_last_month - timedelta(microseconds=1)
    first_of_two_months = datetime(
        two_months_end.year, two_months_end.month, 1, 0, 0, 0, tzinfo=UTC
    )
    two_months_slots = [
        datetime(first_of_two_months.year, first_of_two_months.month, 10, 11, 0, tzinfo=UTC),
        datetime(first_of_two_months.year, first_of_two_months.month, 20, 15, 30, tzinfo=UTC),
    ]

    return (
        today_slots
        + this_week_slots
        + last_week_slots
        + this_month_early_slots
        + last_month_slots
        + two_months_slots
    )


def refresh_demo_timeline(
    watcher_store: "WatcherStore | None" = None,
    run_store: "RunStore | None" = None,
    now: datetime | None = None,
) -> None:
    """Dynamically re-anchor timestamps of in-memory demo fixtures to current rolling time."""
    from server.store import global_run_store
    from server.watcher_store import get_watcher_store

    if now is None:
        now = datetime.now(UTC)

    ws = watcher_store or get_watcher_store()
    rs = run_store or global_run_store
    timeline = generate_rolling_timeline(now)

    with ws._lock:
        demo_sessions = [
            s
            for s in ws._sessions.values()
            if s.org_id == "demo" or (s.session_id and not s.session_id.startswith("eval-"))
        ]
    if not demo_sessions:
        seed_demo_data(ws, rs)
        return

    with ws._lock:
        for idx, session in enumerate(demo_sessions):
            target_dt = timeline[idx % len(timeline)]
            session.created_at = target_dt.isoformat()
            session.updated_at = (target_dt + timedelta(minutes=5)).isoformat()
            if session.trajectory:
                for m_idx, msg in enumerate(session.trajectory.messages):
                    msg.timestamp = target_dt.timestamp() + m_idx * 2.0
                for r_idx, rev in enumerate(session.trajectory.reviews):
                    rev.timestamp = (target_dt + timedelta(seconds=r_idx * 2 + 1)).isoformat()
                cache_key = next(
                    (key for key, cached in ws._sessions.items() if cached is session),
                    ws.memory_key(session.session_id),
                )
                ws._trajectories[cache_key] = session.trajectory
                ws._reviews[cache_key] = session.trajectory.reviews
            if session.steps:
                for s_idx, step in enumerate(session.steps):
                    step_ts = target_dt.timestamp() + s_idx * 3.0
                    if isinstance(step, dict):
                        step["timestamp"] = step_ts
                    elif hasattr(step, "timestamp"):
                        step.timestamp = step_ts

    for idx, run in enumerate(rs._runs.values()):
        if run.org_id == "demo" or (run.session_id and run.session_id.startswith("eval-")):
            target_dt = timeline[idx % len(timeline)]
            run.created_at = target_dt.isoformat()
            run.updated_at = (target_dt + timedelta(minutes=5)).isoformat()
            if hasattr(run, "steps") and run.steps:
                for s_idx, step in enumerate(run.steps):
                    step_ts = target_dt.timestamp() + s_idx * 3.0
                    if isinstance(step, dict):
                        step["timestamp"] = step_ts
                    elif hasattr(step, "timestamp"):
                        step.timestamp = step_ts

    non_demo_verdicts = [
        v
        for v in global_watcher_engine._interception_history
        if getattr(v, "user_id", None) != 1
        and getattr(v, "agent_id", "")
        not in ("inspect_eval", "antigravity", "claude_code", "cursor")
    ]
    global_watcher_engine._interception_history.clear()
    global_watcher_engine._interception_history.extend(non_demo_verdicts)

    with ws._lock:
        for s in demo_sessions:
            if not s.trajectory:
                continue
                for rev in s.trajectory.reviews:
                    if rev.decision in ("block", "deny", "escalate"):
                        verdict = WatcherVerdict(
                            decision="deny" if rev.decision in ("block", "deny") else "escalate",
                            stage="stage_2_deterministic",
                            reason=rev.explanation or f"Policy rule violation: {rev.rule_name}",
                            risk_score=(rev.score / 10.0) if rev.score else 0.9,
                            rule_violation_tag=rev.rule_name,
                            action_preview=rev.tool_input,
                            agent_id=s.agent_type,
                            session_id=s.session_id,
                            review_id=rev.id,
                            full_command=rev.tool_input,
                            tool_result="Execution blocked by OpenEval runtime gate.",
                            threat_category=rev.threat_category,
                            timestamp=rev.timestamp or s.created_at,
                            is_safe=False,
                            mode_applied="enforce",
                            user_id=1,
                        )
                        global_watcher_engine.record_interception(verdict)


def ensure_demo_timeline_fresh(
    watcher_store: "WatcherStore | None" = None,
    run_store: "RunStore | None" = None,
    force: bool = False,
) -> None:
    """Ensure demo fixtures have rolling timestamps matching the current calendar day."""
    global _last_demo_refresh
    now = datetime.now(UTC)
    now_ts = now.timestamp()
    if not force and _last_demo_refresh > 0 and now_ts - _last_demo_refresh < 300:
        return
    refresh_demo_timeline(watcher_store=watcher_store, run_store=run_store, now=now)
    _last_demo_refresh = now_ts


def seed_demo_data(
    watcher_store: "WatcherStore | None" = None,
    run_store: "RunStore | None" = None,
    fixtures_dir: Path | None = None,
    store: "WatcherStore | None" = None,
) -> dict[str, int]:
    """Seed demo sessions, interception verdicts, and evaluation runs into stores under slug 'demo'."""
    from sqlalchemy import text

    from server.db import SessionLocal, current_user_id, current_user_slug
    from server.store import global_run_store
    from server.watcher_store import get_watcher_store

    # 1. Idempotently ensure 'demo' and 'owner' users exist in the database
    with SessionLocal() as db:
        db.execute(
            text(
                "INSERT INTO users (slug) VALUES ('demo'), ('owner') ON CONFLICT (slug) DO NOTHING;"
            )
        )
        db.commit()
        demo_uid_row = db.execute(text("SELECT id FROM users WHERE slug = 'demo'")).fetchone()
        demo_user_id = int(demo_uid_row[0]) if demo_uid_row else 1

    # Scope all subsequent fixture writes exclusively to the 'demo' tenant
    current_user_slug.set("demo")
    current_user_id.set(demo_user_id)

    root_dir = fixtures_dir or DEMO_FIXTURES_DIR
    ws = watcher_store or store or get_watcher_store()
    rs = run_store or global_run_store

    sessions_dir = root_dir / "sessions"
    evals_dir = root_dir / "evals"

    seeded_sessions = 0
    seeded_interceptions = 0
    seeded_evals = 0

    timeline = generate_rolling_timeline()

    # 1. Seed sessions into WatcherStore under demo user with rolling timestamps
    if sessions_dir.exists():
        session_files = sorted(sessions_dir.glob("*.json"))
        for idx, f in enumerate(session_files):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                session = Session.model_validate(data)

                # Assign rolling dynamic timestamp so sessions populate Today, This week, Last week, This month, and Last month
                target_dt = timeline[idx % len(timeline)]
                session.created_at = target_dt.isoformat()
                session.updated_at = (target_dt + timedelta(minutes=5)).isoformat()
                for m_idx, msg in enumerate(session.trajectory.messages):
                    msg.timestamp = target_dt.timestamp() + m_idx * 2.0
                for r_idx, rev in enumerate(session.trajectory.reviews):
                    rev.timestamp = (target_dt + timedelta(seconds=r_idx * 2 + 1)).isoformat()

                ws.record_session(session)
                seeded_sessions += 1

                # Seed reviews into global_watcher_engine for live Control stream
                for rev in session.trajectory.reviews:
                    if rev.decision in ("block", "deny", "escalate"):
                        verdict = WatcherVerdict(
                            decision="deny" if rev.decision in ("block", "deny") else "escalate",
                            stage="stage_2_deterministic",
                            reason=rev.explanation or f"Policy rule violation: {rev.rule_name}",
                            risk_score=(rev.score / 10.0) if rev.score else 0.9,
                            rule_violation_tag=rev.rule_name,
                            action_preview=rev.tool_input,
                            agent_id=session.agent_type,
                            session_id=session.session_id,
                            review_id=rev.id,
                            full_command=rev.tool_input,
                            tool_result="Execution blocked by OpenEval runtime gate.",
                            threat_category=rev.threat_category,
                            timestamp=rev.timestamp or session.created_at,
                            is_safe=False,
                            mode_applied="enforce",
                            user_id=demo_user_id,
                        )
                        global_watcher_engine.record_interception(verdict)
                        seeded_interceptions += 1

            except Exception as exc:
                logger.error("Failed to seed session from %s: %s", f, exc)

    # 2. Seed evaluations into RunStore with rolling timestamps
    if evals_dir.exists():
        eval_files = sorted(evals_dir.glob("*.json"))
        for idx, f in enumerate(eval_files):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                session = Session.model_validate(data)
                session.org_id = "demo"
                target_dt = timeline[idx % len(timeline)]
                session.created_at = target_dt.isoformat()
                session.updated_at = (target_dt + timedelta(minutes=5)).isoformat()
                if session.steps:
                    for s_idx, step in enumerate(session.steps):
                        step_ts = target_dt.timestamp() + s_idx * 3.0
                        if isinstance(step, dict):
                            step["timestamp"] = step_ts
                        elif hasattr(step, "timestamp"):
                            step.timestamp = step_ts
                rs.save_run(session)
                seeded_evals += 1
            except Exception as exc:
                logger.error("Failed to seed eval from %s: %s", f, exc)

    logger.info(
        "Demo seed completed: %d sessions, %d interceptions, %d evals",
        seeded_sessions,
        seeded_interceptions,
        seeded_evals,
    )

    return {
        "sessions": seeded_sessions,
        "interceptions": seeded_interceptions,
        "evals": seeded_evals,
    }
