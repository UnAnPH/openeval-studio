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

    if is_demo_seed_enabled():
        global_watcher_engine._interception_history.clear()

    # 1. Seed sessions into WatcherStore under demo user
    if sessions_dir.exists():
        for f in sorted(sessions_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                session = Session.model_validate(data)
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
                        )
                        global_watcher_engine.record_interception(verdict)
                        seeded_interceptions += 1

            except Exception as exc:
                logger.error("Failed to seed session from %s: %s", f, exc)

    # 2. Seed evaluations into RunStore
    if evals_dir.exists():
        for f in sorted(evals_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                session = Session.model_validate(data)
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
