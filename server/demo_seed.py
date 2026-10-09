"""Demo session and evaluation fixture seeder for OpenEval Studio.

Populates hermetic sessions, interception reviews, and evaluation run records
when OPENEVAL_DEMO_SEED=1.
"""

import json
import logging
import os
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


from datetime import UTC, datetime, timedelta


def generate_rolling_timeline(now: datetime | None = None) -> list[datetime]:
    """Generate dynamic relative timestamps so demo data covers today, this week, last week, this month, and last month."""
    if now is None:
        now = datetime.now(UTC)

    # 1. Today (5 sessions)
    today_slots = [
        now - timedelta(hours=1, minutes=15),
        now - timedelta(hours=3, minutes=20),
        now - timedelta(hours=5, minutes=45),
        now - timedelta(hours=7, minutes=30),
        now - timedelta(hours=9, minutes=50),
    ]

    # 2. This week (prior days)
    js_day = (now.weekday() + 1) % 7
    curr_week_start = datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=UTC) - timedelta(days=js_day)
    this_week_slots: list[datetime] = []
    if js_day > 0:
        for d in range(1, min(js_day + 1, 7)):
            this_week_slots.append(now - timedelta(days=d, hours=3))
    while len(this_week_slots) < 6:
        this_week_slots.append(now - timedelta(hours=12 + len(this_week_slots) * 2))

    # 3. Last week (strictly inside last week)
    last_week_end = curr_week_start - timedelta(microseconds=1)
    last_week_slots = [
        last_week_end - timedelta(days=0, hours=5),
        last_week_end - timedelta(days=1, hours=8),
        last_week_end - timedelta(days=2, hours=4),
        last_week_end - timedelta(days=3, hours=9),
        last_week_end - timedelta(days=4, hours=6),
        last_week_end - timedelta(days=5, hours=3),
    ]

    # 4. Earlier this month
    first_of_month = datetime(now.year, now.month, 1, 0, 0, 0, tzinfo=UTC)
    if now.day > 7:
        this_month_early_slots = [
            datetime(now.year, now.month, 2, 14, 0, tzinfo=UTC),
            datetime(now.year, now.month, 4, 11, 30, tzinfo=UTC),
            datetime(now.year, now.month, 6, 16, 45, tzinfo=UTC),
        ]
    else:
        this_month_early_slots = [
            curr_week_start + timedelta(hours=2),
            curr_week_start + timedelta(hours=6),
            curr_week_start + timedelta(hours=10),
        ]

    # 5. Last month (strictly inside last month)
    last_month_end = first_of_month - timedelta(microseconds=1)
    first_of_last_month = datetime(last_month_end.year, last_month_end.month, 1, 0, 0, 0, tzinfo=UTC)
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
    first_of_two_months = datetime(two_months_end.year, two_months_end.month, 1, 0, 0, 0, tzinfo=UTC)
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
                target_dt = timeline[idx % len(timeline)]
                session.created_at = target_dt.isoformat()
                session.updated_at = (target_dt + timedelta(minutes=5)).isoformat()
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
