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
            if s.org_id == "demo" or "-demo-" in (s.session_id or "")
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
        not in ("inspect_eval", "antigravity", "claude_code", "cursor", "codex", "opencode")
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


def _demo_sessions_exist(watcher_store: "WatcherStore") -> bool:
    """Whether any demo session currently exists in memory or in PostgreSQL."""
    from sqlalchemy import text

    with watcher_store._lock:
        if watcher_store._in_memory_demo_sessions:
            return True
        for s in watcher_store._sessions.values():
            if getattr(s, "org_id", "") == "demo" or "-demo-" in (s.session_id or ""):
                return True

    try:
        from server.db import SessionLocal, get_user_id_by_slug

        with SessionLocal() as db:
            demo_uid = get_user_id_by_slug(db, "demo")
            row = db.execute(
                text("SELECT 1 FROM sessions WHERE user_id = :uid LIMIT 1"), {"uid": demo_uid}
            ).fetchone()
            return row is not None
    except Exception:
        return False


def ensure_demo_timeline_fresh(
    watcher_store: "WatcherStore | None" = None,
    run_store: "RunStore | None" = None,
    force: bool = False,
) -> None:
    """Ensure demo fixtures have rolling timestamps matching the current calendar day."""
    global _last_demo_refresh
    from server.watcher_store import get_watcher_store

    now = datetime.now(UTC)
    now_ts = now.timestamp()
    ws = watcher_store or get_watcher_store()
    recently_refreshed = not force and _last_demo_refresh > 0 and now_ts - _last_demo_refresh < 300
    if recently_refreshed and _demo_sessions_exist(ws):
        return
    refresh_demo_timeline(watcher_store=ws, run_store=run_store, now=now)
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

    # 1. Idempotently ensure 'demo' and 'owner' users exist in the database (if PostgreSQL reachable)
    demo_user_id = 1
    try:
        with SessionLocal() as db:
            db.execute(
                text(
                    "INSERT INTO users (slug) VALUES ('demo'), ('owner') ON CONFLICT (slug) DO NOTHING;"
                )
            )
            db.commit()
            demo_uid_row = db.execute(text("SELECT id FROM users WHERE slug = 'demo'")).fetchone()
            if demo_uid_row:
                demo_user_id = int(demo_uid_row[0])
    except Exception as exc:
        logger.warning("Database unavailable during demo seed user initialization: %s", exc)

    # Scope all subsequent fixture writes exclusively to the 'demo' tenant. The ambient
    # context is restored afterwards so global seeding does not leak demo scope to
    # later code; callers that inject their own store keep the scope so they can read
    # the fixtures back from that store.
    caller_scoped_store = watcher_store is not None or store is not None
    slug_token = current_user_slug.set("demo")
    id_token = current_user_id.set(demo_user_id)

    root_dir = fixtures_dir or DEMO_FIXTURES_DIR
    ws = watcher_store or store or get_watcher_store()
    rs = run_store or global_run_store

    try:
        return _seed_demo_payload(
            ws=ws,
            rs=rs,
            root_dir=root_dir,
            demo_user_id=demo_user_id,
        )
    finally:
        if not caller_scoped_store:
            current_user_id.reset(id_token)
            current_user_slug.reset(slug_token)


def prune_legacy_and_deleted_runs(
    ws: "WatcherStore",
    rs: "RunStore",
    root_dir: Path,
    demo_user_id: int,
) -> int:
    """Purge legacy/zombie runs from PostgreSQL, WatcherStore in-memory caches, and disk."""
    from sqlalchemy import bindparam, text

    from server.db import SessionLocal

    valid_eval_ids: set[str] = set()
    evals_dir = root_dir / "evals"
    legacy_filenames = {
        "regex-log-gemini-3-5-flash-lite.json",
        "eval-regex-log-gemini-3-5-flash-lite.json",
    }
    legacy_ids = {
        "eval-regex-log-gemini-3-5-flash-lite",
        "regex-log-gemini-3-5-flash-lite",
    }
    if evals_dir.exists():
        for f in evals_dir.glob("*.json"):
            if f.name in legacy_filenames:
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                sid = data.get("session_id") or data.get("run_id")
                if sid and str(sid) not in legacy_ids:
                    valid_eval_ids.add(str(sid))
            except Exception:
                pass

    zombie_patterns = ("0d57b4", "regex-log-gemini-flash-lite", "20261019")
    zombie_models = {
        "big-pickle",
        "gemini-flash-lite-latest",
        "gemini-3.7-flash",
        "gemini-3.5-flash",
    }
    zombie_ids = {
        "d8fb2be2",
        "eval-regex-log-gemini-3-5-flash-lite",
        "regex-log-gemini-3-5-flash-lite",
    }

    to_delete_ids: set[str] = set(zombie_ids)
    pruned_count = 0

    # 1. Query PostgreSQL for matching zombie sessions
    try:
        with SessionLocal() as db:
            rows = db.execute(
                text("SELECT id, user_id, model, agent_type FROM sessions")
            ).fetchall()
            for r in rows:
                sid = str(r[0] or "")
                uid = r[1]
                model = str(r[2] or "")
                agent_type = str(r[3] or "")
                is_zombie = (
                    any(pat in sid for pat in zombie_patterns)
                    or sid in zombie_ids
                    or model in zombie_models
                    or model.endswith("-free")
                    or (
                        uid == demo_user_id
                        and (agent_type == "inspect_eval" or sid.startswith("eval-"))
                        and sid not in valid_eval_ids
                    )
                )
                if is_zombie:
                    to_delete_ids.add(sid)

            if to_delete_ids:
                id_list = list(to_delete_ids)
                with db.begin():
                    for chunk_start in range(0, len(id_list), 50):
                        chunk = id_list[chunk_start : chunk_start + 50]
                        db.execute(
                            text("DELETE FROM reviews WHERE session_id IN :ids").bindparams(
                                bindparam("ids", expanding=True)
                            ),
                            {"ids": chunk},
                        )
                        res = db.execute(
                            text("DELETE FROM sessions WHERE id IN :ids").bindparams(
                                bindparam("ids", expanding=True)
                            ),
                            {"ids": chunk},
                        )
                        pruned_count += int(getattr(res, "rowcount", 0) or 0)
    except Exception as exc:
        logger.debug("Database pruning failed (offline/fallback mode): %s", exc)

    # 2. Prune from disk (.runs/watcher/ and other storage dirs)
    if ws.storage_dir.exists():
        for p in ws.storage_dir.glob("*.json"):
            stem = p.stem
            is_zombie = (
                any(pat in stem for pat in zombie_patterns)
                or stem in zombie_ids
                or stem in to_delete_ids
                or (stem.startswith("eval-") and stem not in valid_eval_ids)
            )
            if not is_zombie:
                try:
                    payload = json.loads(p.read_text(encoding="utf-8"))
                    m = str(payload.get("model") or "")
                    sid = str(payload.get("session_id") or payload.get("run_id") or stem)
                    agent = str(payload.get("agent_type") or "")
                    if (
                        m in zombie_models
                        or m.endswith("-free")
                        or any(pat in sid for pat in zombie_patterns)
                        or sid in zombie_ids
                        or (agent == "inspect_eval" and sid not in valid_eval_ids)
                    ):
                        is_zombie = True
                        to_delete_ids.add(sid)
                except Exception:
                    pass

            if is_zombie:
                try:
                    p.unlink(missing_ok=True)
                    tmp_p = p.with_suffix(".tmp")
                    tmp_p.unlink(missing_ok=True)
                    to_delete_ids.add(stem)
                    pruned_count += 1
                except Exception as exc:
                    logger.debug("Failed to remove disk zombie file %s: %s", p, exc)

        # Remove redundant un-prefixed regex-log json if present on disk
        (ws.storage_dir / "eval-regex-log-gemini-3-5-flash-lite.json").unlink(missing_ok=True)
        (ws.storage_dir / "regex-log-gemini-3-5-flash-lite.json").unlink(missing_ok=True)
        (ws.storage_dir / "0d57b46d.json").unlink(missing_ok=True)

    # 3. Purge in-memory state and add tombstones to rs._deleted_ids
    rs._deleted_ids.update(to_delete_ids)
    with ws._lock:
        for did in to_delete_ids:
            ws._sessions.pop(did, None)
            ws._in_memory_demo_sessions.pop(did, None)
            for k in list(ws._sessions.keys()):
                if k == did or k.endswith(f":{did}"):
                    ws._sessions.pop(k, None)
            for k in list(ws._trajectories.keys()):
                if k == did or k.endswith(f":{did}"):
                    ws._trajectories.pop(k, None)
            for k in list(ws._reviews.keys()):
                if k == did or k.endswith(f":{did}"):
                    ws._reviews.pop(k, None)

    logger.info("Pruned %d legacy/zombie records and disk files", pruned_count)
    return pruned_count


def _preview_key(value: str | None) -> str:
    """Normalize a review action preview so it can be matched against full tool calls."""
    return "".join(str(value or "").replace("...", " ").replace("…", " ").split())


def _full_command_for_review(rev: object, trajectory: object) -> str:
    """Resolve the unclipped command for a review from its session trajectory.

    Prefers the structured ``arguments.command`` / ``arguments.CommandLine`` on the
    matching tool call, then ``raw_input``, then the (possibly preview) review
    payload so demo Control cards never show a clipped command.
    """
    review_cmd = str(getattr(rev, "tool_input", "") or "")
    review_key = _preview_key(review_cmd)
    calls = list(getattr(trajectory, "tool_calls", None) or [])
    candidates: list[tuple[str, str]] = []
    for tc in calls:
        args = tc.arguments if isinstance(getattr(tc, "arguments", None), dict) else {}
        command = str(
            args.get("command") or args.get("CommandLine") or getattr(tc, "raw_input", "") or ""
        )
        if command:
            candidates.append((str(getattr(tc, "tool_name", "") or ""), command))

    for tool_name, command in candidates:
        if tool_name and tool_name != str(getattr(rev, "tool_name", "") or ""):
            continue
        if review_key and review_key in _preview_key(command):
            return command

    for _tool_name, command in candidates:
        if review_key and review_key in _preview_key(command):
            return command

    return review_cmd


def _seed_demo_payload(
    ws: "WatcherStore",
    rs: "RunStore",
    root_dir: Path,
    demo_user_id: int,
) -> dict[str, int]:
    """Write demo fixtures into the stores while the demo tenant context is active."""

    prune_legacy_and_deleted_runs(
        ws=ws,
        rs=rs,
        root_dir=root_dir,
        demo_user_id=demo_user_id,
    )

    try:
        from sqlalchemy import text

        from server.db import SessionLocal

        with SessionLocal() as db, db.begin():
            db.execute(text("DELETE FROM reviews WHERE user_id = :uid"), {"uid": demo_user_id})
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to prune old demo reviews: %s", exc)

    sessions_dir = root_dir / "sessions"
    evals_dir = root_dir / "evals"

    seeded_sessions = 0
    seeded_interceptions = 0
    seeded_evals = 0

    timeline = generate_rolling_timeline()

    def _sync_reviews_to_db(session: Session) -> None:
        if not session.trajectory or not session.trajectory.reviews:
            return
        try:
            from sqlalchemy import text

            from engine.redaction import redact_text
            from server.db import SessionLocal

            with SessionLocal() as db, db.begin():
                for rev in session.trajectory.reviews:
                    rule_name = str(rev.rule_name or getattr(rev, "threat_category", "") or "")
                    req_id = getattr(rev, "request_id", None)
                    db.execute(
                        text(
                            """
                            INSERT INTO reviews (
                                id, user_id, session_id, tool_name, tool_input,
                                decision, score, stage, rule_name, explanation,
                                diff, latency_ms, request_id, created_at
                            ) VALUES (
                                :id, :user_id, :session_id, :tool_name, :tool_input,
                                :decision, :score, :stage, :rule_name, :explanation,
                                :diff, :latency_ms, :request_id, NOW()
                            )
                            ON CONFLICT (id) DO UPDATE SET
                                user_id = EXCLUDED.user_id,
                                decision = EXCLUDED.decision,
                                score = EXCLUDED.score,
                                stage = EXCLUDED.stage,
                                rule_name = EXCLUDED.rule_name,
                                explanation = EXCLUDED.explanation,
                                latency_ms = EXCLUDED.latency_ms,
                                request_id = COALESCE(EXCLUDED.request_id, reviews.request_id);
                            """
                        ),
                        {
                            "id": rev.id,
                            "user_id": demo_user_id,
                            "session_id": session.session_id,
                            "tool_name": rev.tool_name,
                            "tool_input": redact_text(rev.tool_input or ""),
                            "decision": rev.decision,
                            "score": rev.score,
                            "stage": rev.stage,
                            "rule_name": rule_name,
                            "explanation": redact_text(rev.explanation or ""),
                            "diff": redact_text(rev.diff or ""),
                            "latency_ms": rev.latency_ms,
                            "request_id": req_id,
                        },
                    )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Review database insert failed: %s", exc)

    # 1. Seed sessions into WatcherStore under demo user with rolling timestamps
    if sessions_dir.exists():
        session_files = sorted(sessions_dir.glob("*.json"))
        active_marked = 0
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

                # Keep exactly two non-blocked demo sessions live so /demo always
                # exercises the Sessions "Live" tab regardless of seed age.
                has_blocked_review = any(
                    rev.decision in ("block", "deny", "escalate")
                    for rev in (session.trajectory.reviews or [])
                )
                if active_marked < 2 and session.status != "blocked" and not has_blocked_review:
                    session.status = "active"
                    if not session.current_activity or session.current_activity == "completed":
                        session.current_activity = "Monitoring active tool calls"
                    session.updated_at = datetime.now(UTC).isoformat()
                    active_marked += 1

                ws.record_session(session, is_seeded=True)
                _sync_reviews_to_db(session)
                disk_path = ws.storage_dir / f"{session.session_id}.json"
                disk_path.write_text(json.dumps(session.model_dump(), indent=2), encoding="utf-8")
                seeded_sessions += 1

                # Seed reviews into global_watcher_engine for live Control stream
                for rev in session.trajectory.reviews:
                    if rev.decision in ("block", "deny", "escalate"):
                        full_command = _full_command_for_review(rev, session.trajectory)
                        verdict = WatcherVerdict(
                            decision="deny" if rev.decision in ("block", "deny") else "escalate",
                            stage="stage_2_deterministic",
                            reason=rev.explanation or f"Policy rule violation: {rev.rule_name}",
                            risk_score=(rev.score / 10.0) if rev.score else 0.9,
                            rule_violation_tag=rev.rule_name,
                            action_preview=full_command,
                            agent_id=session.agent_type,
                            session_id=session.session_id,
                            review_id=rev.id,
                            full_command=full_command,
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
        eval_files = sorted(
            evals_dir.glob("*.json"),
            key=lambda p: (
                0 if p.name == "eval-build-pov-ray-20261119-214347-rskns8.json" else 1,
                p.name,
            ),
        )
        legacy_eval_files = {
            "regex-log-gemini-3-5-flash-lite.json",
            "eval-regex-log-gemini-3-5-flash-lite.json",
        }
        legacy_eval_ids = {
            "eval-regex-log-gemini-3-5-flash-lite",
            "regex-log-gemini-3-5-flash-lite",
        }
        for idx, f in enumerate(eval_files):
            if f.name in legacy_eval_files or f.stem in legacy_eval_ids:
                continue
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
                ws.record_session(session, is_seeded=True)
                _sync_reviews_to_db(session)
                rs.save_run(session, is_seeded=True)
                disk_path = ws.storage_dir / f"{session.session_id}.json"
                disk_path.write_text(json.dumps(session.model_dump(), indent=2), encoding="utf-8")
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
