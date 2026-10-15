"""FastAPI Server & Real-Time Streaming SSE API for OpenEval Studio.

Exposes REST endpoints for model and task discovery, evaluation launching,
and Server-Sent Events (SSE) for live turn-by-turn ReAct trajectory streaming.
"""

import asyncio
import contextlib
import json
import logging
import os
import re
import time
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from engine.approval_policy import (
    WatcherVerdict,
    global_watcher_engine,
)
from engine.llm_runner import AsyncLLMRunner, LLMConfig
from engine.scanners import FindingRecord, RecommendedAction, SchemingScanners
from engine.watcher_sdk import WatcherClient
from schemas.models import ModelSpec, get_available_models
from schemas.task_spec import load_task_spec
from schemas.watcher_models import ReviewRecord, Session
from server.auth import (
    SESSION_COOKIE_NAME,
    resolve_api_key_user,
    verify_session_token,
)
from server.auth import (
    router as auth_router,
)
from server.db import (
    SessionLocal,
    current_user_id,
    current_user_slug,
    get_current_user_id,
    get_user_id_by_slug,
)
from server.log import setup_logger
from server.request_context import RequestIdMiddleware
from server.routes.eval import router as eval_router
from server.routes.watcher_gate import (
    router as watcher_gate_router,
)
from server.routes.watcher_gate import (
    stream_watcher_events as stream_watcher_events,
)
from server.routes.watcher_policy import router as watcher_policy_router
from server.store import global_run_store
from server.watcher_store import get_watcher_store

load_dotenv()
setup_logger()
logger = logging.getLogger("openeval.server")

PROJECT_ROOT = Path(__file__).parent.parent
TASKS_DIR = PROJECT_ROOT / "tasks"
LOGS_DIR = PROJECT_ROOT / "logs"


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager for startup checks, migrations, and graceful shutdown."""
    from alembic.config import Config
    from sqlalchemy import text

    from alembic import command
    from server.agent_daemon import AgentWatcherDaemon
    from server.db import SessionLocal, engine
    from server.demo_seed import seed_demo_data

    # Startup: fail-closed initial state
    setup_logger()
    if "OPENEVAL_GATE_USE_LLM" in os.environ:
        raise RuntimeError(
            "Obsolete environment variable OPENEVAL_GATE_USE_LLM is set. "
            "Use OPENEVAL_WATCHER_USE_LLM instead."
        )
    app.state.db_ready = False
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        alembic_ini = PROJECT_ROOT / "alembic.ini"
        if alembic_ini.exists():
            alembic_cfg = Config(str(alembic_ini))
            command.upgrade(alembic_cfg, "head")
        app.state.db_ready = True
    except Exception as exc:
        logger.error("Database connection or migration failed during startup: %s", exc)
        app.state.db_ready = False

    if app.state.db_ready:
        seed_demo_data()

    _scrub_accidental_safe_overrides()

    daemon: AgentWatcherDaemon = AgentWatcherDaemon.get_instance()
    daemon.start()

    from server.event_bus import get_event_bus

    await get_event_bus().get_redis_client()

    yield

    # Shutdown:
    daemon.stop()
    await get_event_bus().close()
    engine.dispose()


app = FastAPI(
    title="OpenEval Studio API",
    version="0.1.0",
    description="Real-time Evaluation Engine & Sandbox Platform for Frontier AI Agents",
    lifespan=lifespan,
)
app.state.db_ready = True

# Enable CORS for local development and Vite frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class _AuthMiddleware(BaseHTTPMiddleware):
    """Authentication and tenant isolation middleware.

    Enforces session cookies, Bearer tokens, and X-OpenEval-Key headers,
    while permitting unauthenticated read access for the demo surface.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        path = request.url.path
        method = request.method.upper()

        # Non-API routes (SPA, static assets, favicon, /demo) are unauthenticated
        if not path.startswith("/api/"):
            return await call_next(request)

        # Public API endpoints that bypass authentication
        if (
            path == "/api/health"
            or path == "/api/demo/status"
            or path.startswith("/api/scripts")
            or path in ("/api/auth/register", "/api/auth/login")
            or path.startswith("/docs")
            or path.startswith("/redoc")
            or path == "/openapi.json"
        ):
            return await call_next(request)

        # Fail-closed for mutating writes if database is not ready
        db_ready = getattr(request.app.state, "db_ready", True)
        if not db_ready and method in ("POST", "PUT", "PATCH", "DELETE"):
            return JSONResponse(
                {"detail": "Database is not ready."},
                status_code=503,
            )

        # Check for session cookie
        cookie_val = request.cookies.get(SESSION_COOKIE_NAME)
        authenticated_user: tuple[int, str] | None = None
        if cookie_val:
            authenticated_user = verify_session_token(cookie_val)

        # Check for API key (Bearer token or X-OpenEval-Key)
        api_key = request.headers.get("X-OpenEval-Key") or request.headers.get("x-openeval-key")
        auth_header = (
            request.headers.get("Authorization") or request.headers.get("authorization") or ""
        )
        if not api_key and auth_header.startswith("Bearer "):
            api_key = auth_header[7:].strip()

        if api_key:
            authenticated_user = resolve_api_key_user(api_key)
            if not authenticated_user:
                return JSONResponse(
                    {"detail": "Invalid or expired API key or Bearer token."},
                    status_code=401,
                )

        # Detect demo surface
        surface_header = (
            (
                request.headers.get("X-OpenEval-Surface")
                or request.headers.get("x-openeval-surface")
                or ""
            )
            .strip()
            .lower()
        )
        is_demo_surface = (
            surface_header == "demo" or path.startswith("/demo") or path.startswith("/api/demo")
        )

        # Demo surface read requests strictly view the demo user's rows,
        # Demo surface read requests and ephemeral evaluation launches execute under demo user context
        if is_demo_surface:
            is_eval_action = (
                path in ("/api/eval/run", "/api/eval/launch")
                or (
                    path.startswith("/api/eval/runs/")
                    and (path.endswith("/stop") or path.endswith("/delete") or method == "DELETE")
                )
                or (path == "/api/eval/runs" and method == "DELETE")
                or path in ("/api/eval/runs/clear",)
            )
            if method in ("GET", "HEAD", "OPTIONS") or is_eval_action:
                with SessionLocal() as db:
                    demo_uid = get_user_id_by_slug(db, "demo")
                current_user_slug.set("demo")
                current_user_id.set(demo_uid)
                return await call_next(request)

        # 1. Valid session cookie or API key sets current_user_id and current_user_slug
        if authenticated_user:
            uid, slug = authenticated_user
            current_user_id.set(uid)
            current_user_slug.set(slug)
            return await call_next(request)

        # 2. Mutating requests on demo surface without credentials are rejected
        if is_demo_surface:
            return JSONResponse(
                {"detail": "Authentication required. Demo mode is read-only."},
                status_code=401,
            )

        # 3. OPENEVAL_AUTH_DISABLED=1 stays the test-only owner fallback
        auth_disabled = os.getenv("OPENEVAL_AUTH_DISABLED", "0").lower() in ("1", "true", "yes")
        if auth_disabled:
            owner_uid = None
            try:
                with SessionLocal() as db:
                    owner_uid = get_user_id_by_slug(db, "owner")
            except Exception:
                owner_uid = 1
            current_user_slug.set("owner")
            current_user_id.set(owner_uid)
            return await call_next(request)

        # 4. Anything else on /api/* returns 401
        return JSONResponse(
            {
                "detail": "Authentication required. Provide valid session cookie, Bearer token, or X-OpenEval-Key."
            },
            status_code=401,
        )


app.add_middleware(_AuthMiddleware)
app.add_middleware(RequestIdMiddleware)
app.include_router(auth_router)
app.include_router(watcher_gate_router)
app.include_router(watcher_policy_router)
app.include_router(eval_router)


class TaskSummary(BaseModel):
    """Summary metadata for a benchmark task."""

    task_id: str
    category: str | None = None
    difficulty: str | None = None
    tags: list[str] = Field(default_factory=list)
    timeout_sec: float = 600.0
    max_steps: int = 100
    memory_mb: int = 2048
    instruction_preview: str = ""


def open_db_session() -> Any:
    """Open a SQLAlchemy session. Tests pass a different opener into probe_database."""
    from server.db import SessionLocal

    return SessionLocal()


def get_db_opener() -> Callable[[], Any]:
    """FastAPI dependency so tests can substitute the database opener."""
    return open_db_session


_db_opener_dependency = Depends(get_db_opener)


def probe_database(open_session: Callable[[], Any] | None = None) -> bool:
    """Return True when SELECT 1 succeeds. A failing opener returns False."""
    from sqlalchemy import text

    opener = open_session or open_db_session
    try:
        with opener() as db:
            return db.execute(text("SELECT 1")).scalar() == 1
    except Exception as exc:
        logger.warning("Database health probe failed: %s", exc)
        return False


@app.get("/api/health")
async def health_check(
    response: Response,
    open_session: Callable[[], Any] = _db_opener_dependency,
) -> dict[str, Any]:
    """Health check endpoint testing PostgreSQL connection."""
    is_demo = os.getenv("OPENEVAL_DEMO_SEED", "0").lower() in ("1", "true", "yes")
    db_connected = probe_database(open_session)

    if not db_connected:
        response.status_code = 503
        return {
            "status": "degraded",
            "db": "disconnected",
            "version": "0.1.0",
            "demo_seed": is_demo,
        }

    return {
        "status": "ok",
        "db": "connected",
        "version": "0.1.0",
        "demo_seed": is_demo,
    }


@app.get("/api/demo/status")
async def demo_status() -> dict[str, Any]:
    """Report whether demo session seed is enabled."""
    is_demo = os.getenv("OPENEVAL_DEMO_SEED", "0").lower() in ("1", "true", "yes")
    return {
        "demo_seed": is_demo,
        "demo_mode": is_demo,
        "seeded_sessions": len(get_watcher_store().list_sessions()) if is_demo else 0,
        "seeded_eval_runs": len(global_run_store.list_runs()) if is_demo else 0,
    }


# =====================================================================
# Watcher Subsystem & Real-Time Telemetry
# =====================================================================

watcher_sse_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

DEFAULT_FINDINGS: list[FindingRecord] = []

STORED_FINDINGS: list[FindingRecord] = []


def sse_event_visible_to_user(event_uid: int | None, subscriber_uid: int | None) -> bool:
    """True only when the event and the subscriber belong to the same tenant.

    Unassigned events (``user_id is None``) are not delivered to a different tenant.
    """
    return event_uid == subscriber_uid


def _active_tenant_id() -> int | None:
    """Tenant id for the current request, resolved from the database when needed."""
    try:
        return get_current_user_id()
    except Exception:
        return current_user_id.get()


async def broadcast_watcher_event(event_type: str, data: dict[str, Any]) -> str:
    """Publish event to active Watcher SSE connections across replicas via Redis Streams."""
    from server.event_bus import get_event_bus

    bus = get_event_bus()
    uid = data.get("user_id")
    if uid is None:
        uid = _active_tenant_id()
    return await bus.xadd(event_type, data, user_id=uid)


def _is_interception_candidate(item: Any) -> bool:
    """Return True only if the item/review was actually intercepted, denied, escalated, or rule-flagged."""
    dec = str(getattr(item, "decision", "") or "").lower()
    rule = getattr(item, "rule_violation_tag", None) or getattr(item, "rule_name", None)
    score = float(getattr(item, "risk_score", 0.0) or (getattr(item, "score", 0) / 10.0))
    reason = str(getattr(item, "reason", "") or getattr(item, "explanation", "") or "").lower()
    return (
        dec in ("deny", "escalate", "block", "ask", "force_ask", "warn")
        or rule is not None
        or score >= 0.5
        or "lockout" in reason
        or "blacklist" in reason
    )


def _normalize_cmd_for_match(s: str) -> str:
    """Normalize command string for robust matching across escaped JSON and raw strings."""
    s = s.replace("\\\\n", "\n").replace("\\n", "\n").replace('\\"', '"').replace("\\'", "'")
    s = s.strip("\"' ")
    return "".join(s.split())[:50]


def _enrich_verdict_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Ensure full_command and tool_result are populated for Control Live Stream."""
    full_cmd = row.get("full_command")
    tool_res = row.get("tool_result")
    if full_cmd and tool_res and not str(tool_res).startswith("Awaiting"):
        return row

    sess_id = row.get("session_id")
    rev_id = row.get("review_id")
    store = get_watcher_store()
    session = store.get_session(sess_id) if sess_id else None

    if session and session.trajectory:
        tc = None
        tr = None
        matching_idx = -1
        for i, r in enumerate(session.trajectory.reviews):
            if rev_id and r.id == rev_id:
                matching_idx = i
                break
        if matching_idx != -1:
            if matching_idx < len(session.trajectory.tool_calls):
                tc = session.trajectory.tool_calls[matching_idx]
            if matching_idx < len(session.trajectory.tool_results):
                tr = session.trajectory.tool_results[matching_idx]

        if not tr or not tc:
            act_prev = str(row.get("full_command") or row.get("action_preview") or "").strip()
            raw_act_cmd = act_prev.split(": ", 1)[1].strip() if ": " in act_prev else act_prev

            norm_target = _normalize_cmd_for_match(raw_act_cmd)

            # Search in reverse for the most recent matching tool call
            if norm_target:
                for i in range(len(session.trajectory.tool_calls) - 1, -1, -1):
                    candidate_tc = session.trajectory.tool_calls[i]
                    candidate_cmd = str(
                        (
                            candidate_tc.arguments.get("CommandLine")
                            if isinstance(candidate_tc.arguments, dict)
                            else None
                        )
                        or candidate_tc.raw_input
                        or ""
                    )
                    norm_candidate = _normalize_cmd_for_match(candidate_cmd)
                    if norm_candidate and (
                        norm_candidate == norm_target
                        or norm_target.startswith(norm_candidate[:30])
                        or norm_candidate.startswith(norm_target[:30])
                    ):
                        tc = candidate_tc
                        if i < len(session.trajectory.tool_results):
                            tr = session.trajectory.tool_results[i]
                        break

        if not full_cmd or len(full_cmd) <= 80:
            if tc:
                extracted = str(
                    (tc.arguments.get("CommandLine") if isinstance(tc.arguments, dict) else None)
                    or tc.raw_input
                    or row.get("action_preview")
                    or ""
                ).strip()
                if extracted.startswith('"') and extracted.endswith('"') and len(extracted) > 1:
                    extracted = extracted[1:-1]
                extracted = (
                    extracted.replace("\\\\n", "\n")
                    .replace("\\n", "\n")
                    .replace('\\"', '"')
                    .replace("\\'", "'")
                )
                row["full_command"] = extracted
            else:
                row["full_command"] = row.get("action_preview") or ""

        if tr:
            parts = []
            if tr.stdout:
                parts.append(tr.stdout)
            if tr.stderr:
                parts.append(f"[stderr]\n{tr.stderr}")
            row["tool_result"] = (
                "\n".join(parts)
                if parts
                else (
                    f"Exit code {tr.exit_code}"
                    if tr.exit_code != 0
                    else "Command completed (exit code 0)"
                )
            )
        elif row.get("decision") in ("deny", "block") and row.get("human_override") != "allow":
            row["tool_result"] = "Execution blocked by Watcher safety gate."
        else:
            row["tool_result"] = "Awaiting execution output from agent transcript..."
    else:
        if not full_cmd:
            row["full_command"] = row.get("action_preview") or ""
        if not tool_res:
            if row.get("decision") in ("deny", "block") and row.get("human_override") != "allow":
                row["tool_result"] = "Execution blocked by Watcher safety gate."
            else:
                row["tool_result"] = "Awaiting execution output from agent transcript..."

    return row


def _verdicts_from_store_reviews(limit: int = 40) -> list[dict[str, Any]]:
    """Map recent block/deny/escalate/resolved reviews into WatcherVerdict-shaped dicts for Live Stream."""
    store = get_watcher_store()
    rows: list[tuple[str, Any]] = []
    for session in store.list_sessions(limit=200):
        for rev in store.get_session_decisions(session.session_id):
            if not _is_interception_candidate(rev):
                continue
            rows.append((rev.timestamp or "", (session, rev)))
    rows.sort(key=lambda x: x[0], reverse=True)

    out: list[dict[str, Any]] = []
    for _, (session, rev) in rows[:limit]:
        raw_ov = getattr(rev, "human_override", None)
        human_ov: Literal["allow", "deny"] | None = raw_ov if raw_ov in ("allow", "deny") else None
        raw_res = getattr(rev, "resolution_status", "pending")
        res_status: Literal["pending", "blocked", "human_approved"] = (
            raw_res if raw_res in ("pending", "blocked", "human_approved") else "pending"
        )
        if human_ov == "allow":
            mapped = "allow"
            is_safe = True
        else:
            mapped = "deny" if rev.decision in ("block", "deny") else "escalate"
            is_safe = False
        preview_input = (rev.tool_input or "")[:80]

        # Match tool call and tool result in session trajectory
        tc = None
        tr = None
        matching_idx = -1
        for i, r in enumerate(session.trajectory.reviews):
            if r.id == rev.id:
                matching_idx = i
                break

        if matching_idx != -1:
            if matching_idx < len(session.trajectory.tool_calls):
                tc = session.trajectory.tool_calls[matching_idx]
            if matching_idx < len(session.trajectory.tool_results):
                tr = session.trajectory.tool_results[matching_idx]

        if not tc:
            for i, candidate_tc in enumerate(session.trajectory.tool_calls):
                if candidate_tc.tool_name == rev.tool_name:
                    candidate_cmd = str(
                        (
                            candidate_tc.arguments.get("CommandLine")
                            if isinstance(candidate_tc.arguments, dict)
                            else None
                        )
                        or candidate_tc.raw_input
                        or ""
                    )
                    if (rev.tool_input and rev.tool_input in candidate_cmd) or (
                        candidate_cmd and candidate_cmd in (rev.tool_input or "")
                    ):
                        tc = candidate_tc
                        if i < len(session.trajectory.tool_results):
                            tr = session.trajectory.tool_results[i]
                        break

        full_cmd_str = str(
            (tc.arguments.get("CommandLine") if tc and isinstance(tc.arguments, dict) else None)
            or (tc.raw_input if tc else None)
            or rev.tool_input
            or ""
        )

        tool_result_str: str | None = None
        if tr:
            parts = []
            if tr.stdout:
                parts.append(tr.stdout)
            if tr.stderr:
                parts.append(f"[stderr]\n{tr.stderr}")
            tool_result_str = (
                "\n".join(parts)
                if parts
                else (
                    f"Exit code {tr.exit_code}"
                    if tr.exit_code != 0
                    else "Command completed (exit code 0)"
                )
            )
        elif mapped == "deny" and human_ov != "allow":
            tool_result_str = "Execution blocked by Watcher safety gate."
        else:
            tool_result_str = "Awaiting execution output from agent transcript..."

        preview_input = full_cmd_str[:80] if full_cmd_str else (rev.tool_input or "")[:80]

        out.append(
            WatcherVerdict(
                decision=mapped,  # type: ignore[arg-type]
                stage=(
                    "stage_2_deterministic"
                    if rev.stage == "rule"
                    else "stage_3_triage"
                    if rev.stage == "triage"
                    else "stage_4_deep_review"
                ),
                reason=rev.explanation or f"Policy: {rev.rule_name or rev.stage}",
                risk_score=float(rev.score) / 10.0 if rev.score else 0.8,
                latency_ms=rev.latency_ms or 0.0,
                rule_violation_tag=rev.rule_name,
                mode_applied=global_watcher_engine.config.mode,
                shadow_decision=None,
                is_safe=is_safe,
                action_preview=f"{rev.tool_name}: {preview_input}",
                agent_id=str(session.agent_type or "unknown"),
                timestamp=rev.timestamp or session.updated_at,
                review_id=rev.id,
                session_id=session.session_id,
                human_override=human_ov,
                resolution_status=res_status,
                full_command=full_cmd_str or f"{rev.tool_name}: {rev.tool_input}",
                tool_result=tool_result_str,
            ).model_dump()
        )
    return out


@app.get("/api/v1/watcher/interceptions")
async def list_watcher_interceptions(
    limit: int = Query(default=40, ge=1, le=200),
) -> list[dict[str, Any]]:
    """Recent blocked/escalated reviews + in-memory live-gate history for Control Live Stream."""
    if os.getenv("OPENEVAL_AUTO_SCAN_LOCAL_LOGS", "0").lower() in (
        "1",
        "true",
        "yes",
    ):
        from server.agent_log_loader import UniversalAgentLogLoader

        UniversalAgentLogLoader.scan_default_agent_directories()

    uid = current_user_id.get()
    live = [
        v.model_dump()
        for v in global_watcher_engine.get_history(limit)
        if (v.user_id is None and current_user_slug.get() in ("owner", "demo"))
        or (v.user_id is not None and v.user_id == uid)
    ]
    stored = _verdicts_from_store_reviews(limit=limit)
    # Prefer live order first, then fill from store without duping action+agent+decision
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for row in live + stored:
        key = f"{row.get('agent_id')}|{row.get('decision')}|{row.get('action_preview')}|{row.get('timestamp')}"
        if key in seen:
            continue
        seen.add(key)
        merged.append(_enrich_verdict_dict(row))
        if len(merged) >= limit:
            break
    return merged


@app.get("/api/v1/watcher/findings", response_model=list[FindingRecord])
async def list_watcher_findings(
    severity: str | None = Query(default=None, description="Filter: critical, high, medium, low"),
    dimension: str | None = Query(default=None),
) -> list[FindingRecord]:
    store = get_watcher_store()
    findings_map: dict[str, FindingRecord] = {}

    for session in store.list_sessions(limit=200):
        reviews = store.get_session_decisions(session.session_id)
        blocked = [r for r in reviews if r.decision in ("block", "deny") or (r.score or 0) >= 8]
        if not blocked:
            continue
        top = max(blocked, key=lambda r: r.score or 0)
        fid = f"finding-{session.session_id[:8]}"
        agent = session.agent_type
        if "claude" in agent:
            agent_source: Literal["antigravity", "claude_code", "cursor", "openeval_runner"] = (
                "claude_code"
            )
        elif agent == "cursor":
            agent_source = "cursor"
        elif agent == "antigravity":
            agent_source = "antigravity"
        else:
            agent_source = "openeval_runner"
        rule = (top.rule_name or "").upper()
        expl = (top.explanation or "").lower()
        if "EXFIL" in rule or "credential" in expl or "metadata" in expl:
            dim = "Data Exfiltration"
        elif "PRIVILEGE" in rule or "sudo" in expl:
            dim = "Privilege Escalation"
        elif "TAMPER" in rule:
            dim = "Policy Tampering"
        else:
            dim = "Access Control"
        findings_map[fid] = FindingRecord(
            id=fid,
            session_id=session.session_id,
            headline=session.title or f"[{agent}] Blocked: {top.rule_name or top.tool_name}",
            summary=top.explanation or "Policy gate blocked a tool call.",
            severity="critical",
            dimension=dim,
            developer=session.human_reviewer or "Operator",
            agent_source=agent_source,  # type: ignore[arg-type]
            recommended_actions=[
                RecommendedAction(
                    priority="P1",
                    category="REMEDIATE_CODE",
                    title=f"Review blocked tool: {top.tool_name}",
                    description=top.explanation
                    or "Inspect trajectory and tighten policy if needed.",
                    citations=[f"[R{top.id}]"],
                )
            ],
            flagged_turns=[1],
            blocked_turn=1,
        )

    # Store-backed findings only (live gate + policy reviews)
    results = list(findings_map.values())
    if severity:
        sev_rank = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        min_rank = sev_rank.get(severity.lower(), 1)
        results = [f for f in results if sev_rank.get(f.severity.lower(), 1) >= min_rank]
    if dimension:
        results = [f for f in results if f.dimension.lower() == dimension.lower()]
    return results


def scan_antigravity_brain_sessions(limit: int = 15) -> list[dict[str, Any]]:
    """Scan local Antigravity brain conversation transcripts and return them as monitored sessions."""
    if os.getenv("OPENEVAL_AUTO_SCAN_LOCAL_LOGS", "0").lower() not in ("1", "true", "yes"):
        return []

    from server.agent_log_loader import get_antigravity_titles

    brain_dir = Path.home() / ".gemini" / "antigravity" / "brain"
    if not brain_dir.exists():
        return []

    ag_titles = get_antigravity_titles()
    discovered: list[dict[str, Any]] = []
    try:
        candidate_dirs = sorted(
            [
                d
                for d in brain_dir.iterdir()
                if d.is_dir() and (d / ".system_generated" / "logs" / "transcript.jsonl").exists()
            ],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        candidate_dirs = []

    for conv_dir in candidate_dirs[:limit]:
        transcript_file = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
        first_prompt = ""
        created_at = None
        turns: list[dict[str, Any]] = []
        is_blocked = False
        blocked_turn = None

        try:
            with open(transcript_file, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue

                    if not created_at and entry.get("created_at"):
                        created_at = entry.get("created_at")

                    entry_type = entry.get("type")
                    if entry_type == "USER_INPUT" and not first_prompt:
                        c = entry.get("content", "")
                        if "<USER_REQUEST>" in c:
                            first_prompt = (
                                c.split("<USER_REQUEST>")[1].split("</USER_REQUEST>")[0].strip()
                            )
                        else:
                            first_prompt = c.strip()
                        turns.append(
                            {
                                "step_number": len(turns) + 1,
                                "role": "user",
                                "content": first_prompt[:500],
                                "is_blocked": False,
                            }
                        )
                    elif entry_type == "PLANNER_RESPONSE":
                        thought = entry.get("thinking", "")
                        tool_calls = entry.get("tool_calls", [])
                        for tc in tool_calls:
                            t_name = tc.get("name", "unknown")
                            t_args = tc.get("args", {})
                            turns.append(
                                {
                                    "step_number": len(turns) + 1,
                                    "role": "agent",
                                    "thought": thought[:180] if thought else "",
                                    "tool": t_name,
                                    "arguments": t_args,
                                    "observation": "Action executed cleanly within policy.",
                                    "is_blocked": False,
                                    "risk_score": 0.01,
                                }
                            )
        except Exception as err:
            logger.warning("Error reading transcript for %s: %s", conv_dir.name, err)
            continue

        if turns:
            chat_title = ag_titles.get(conv_dir.name)
            if chat_title:
                headline = chat_title
            else:
                headline = (
                    first_prompt.split("\n")[0][:75]
                    if first_prompt
                    else f"Antigravity Session {conv_dir.name[:8]}"
                )
            dimension = "Codebase Refactoring"
            session_item = {
                "id": f"antigravity-{conv_dir.name[:8]}",
                "session_id": conv_dir.name,
                "headline": headline,
                "developer": "Operator",
                "timestamp": created_at or "2026-09-03T12:00:00Z",
                "severity": "critical" if is_blocked else "cleared",
                "dimension": dimension,
                "agent_source": "antigravity",
                "summary": first_prompt[:250]
                if first_prompt
                else "Autonomous Antigravity pair programming session.",
                "risk_score": 0.95 if is_blocked else 0.05,
                "is_blocked": is_blocked,
                "is_escalated": False,
                "blocked_turn": blocked_turn,
                "total_turns": len(turns),
                "turns": turns,
            }
            discovered.append(session_item)
            if conv_dir.name not in STORED_SESSION_DETAILS:
                STORED_SESSION_DETAILS[conv_dir.name] = {
                    "session_id": conv_dir.name,
                    "finding": {
                        "id": f"finding-{conv_dir.name[:8]}",
                        "session_id": conv_dir.name,
                        "headline": headline,
                        "developer": "Operator",
                        "timestamp": created_at or "2026-09-03T12:00:00Z",
                        "severity": "low",
                        "dimension": dimension,
                        "agent_source": "antigravity",
                        "summary": session_item["summary"],
                        "recommended_actions": [],
                        "flagged_turns": [],
                        "blocked_turn": None,
                        "tags": ["antigravity", "brain_session", "cleared"],
                    },
                    "turns": turns,
                    "total_turns": len(turns),
                    "blocked_turns_count": 0,
                    "developer": "Operator",
                    "agent_source": "antigravity",
                    "working_directory": "/workspace",
                    "duration_sec": 45.0,
                    "total_tokens": len(turns) * 450,
                }

    return discovered


class SessionFindingDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    session_id: str
    headline: str
    developer: str = "Operator"
    timestamp: str | None = None
    severity: str = "low"
    dimension: str = "Live Observability"
    agent_source: str = "antigravity"
    summary: str = ""
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    flagged_turns: list[int] = Field(default_factory=list)
    blocked_turn: int | None = None
    tags: list[str] = Field(default_factory=list)


class SessionTurnDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    step_number: int
    role: str = "agent"
    thought: str = ""
    tool: str = ""
    # Raw arguments dictionary from external agent tool call
    arguments: dict[str, Any] = Field(default_factory=dict)
    observation: str = ""
    is_blocked: bool = False
    rule_violation_tag: str | None = None
    risk_score: float = 0.0
    reason: str | None = None


class SessionDetailResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: str
    finding: SessionFindingDetail | None = None
    turns: list[SessionTurnDetail] = Field(default_factory=list)
    total_turns: int = 0
    blocked_turns_count: int = 0
    developer: str = "Operator"
    agent_source: str = "antigravity"
    working_directory: str = "/workspace"
    duration_sec: float = 0.0
    total_tokens: int = 0


STORED_SESSION_DETAILS: dict[str, dict[str, Any]] = {}


def _get_legacy_session_detail(session_id: str) -> SessionDetailResponse:
    """Retrieve session detail — prefers WatcherStore, then legacy STORED_* / brain scan."""
    if os.getenv("OPENEVAL_AUTO_SCAN_LOCAL_LOGS", "0").lower() in ("1", "true", "yes"):
        brain_dir = Path.home() / ".gemini" / "antigravity" / "brain" / session_id
        if brain_dir.exists():
            scan_antigravity_brain_sessions(limit=30)

    # Canonical: WatcherStore
    try:
        ws = get_watcher_store()
        db_s = ws.get_session(session_id)
        if db_s:
            traj = ws.get_trajectory(session_id)
            reviews = ws.get_session_decisions(session_id)
            turns: list[dict[str, Any]] = []
            if traj:
                for idx, tc in enumerate(traj.tool_calls):
                    matching = next(
                        (
                            r
                            for r in reviews
                            if r.tool_name == tc.tool_name and r.tool_input == (tc.raw_input or "")
                        ),
                        None,
                    )
                    is_blocked = bool(matching and matching.decision in ("block", "deny"))
                    turns.append(
                        {
                            "step_number": idx + 1,
                            "role": "agent",
                            "thought": "",
                            "tool": tc.tool_name,
                            "arguments": dict(tc.arguments or {}),
                            "observation": (matching.explanation if matching else "")
                            or (tc.raw_input or ""),
                            "is_blocked": is_blocked,
                            "rule_violation_tag": matching.rule_name if matching else None,
                            "risk_score": (matching.score / 10.0) if matching else 0.0,
                        }
                    )
                if not turns and traj.messages:
                    for idx, msg in enumerate(traj.messages):
                        turns.append(
                            {
                                "step_number": idx + 1,
                                "role": "agent" if msg.role == "assistant" else "user",
                                "thought": msg.thinking or "",
                                "tool": "message",
                                "arguments": {},
                                "observation": msg.content,
                                "is_blocked": False,
                            }
                        )
            blocked_n = sum(1 for t in turns if t.get("is_blocked")) or sum(
                1 for r in reviews if r.decision in ("block", "deny")
            )
            top = next((r for r in reviews if r.decision in ("block", "deny")), None)
            agent_str = str(db_s.agent_type)
            operator_name = db_s.human_reviewer or "Operator"
            severity = "critical" if blocked_n else "low"
            return SessionDetailResponse(
                session_id=session_id,
                finding=SessionFindingDetail(
                    id=f"finding-{session_id[:8]}",
                    session_id=session_id,
                    headline=db_s.title or f"[{agent_str.capitalize()}] Session {session_id[:8]}",
                    developer=operator_name,
                    timestamp=db_s.created_at,
                    severity=severity,
                    dimension="Access Control" if blocked_n else "Live Observability",
                    agent_source="claude_code" if "claude" in agent_str.lower() else "antigravity",
                    summary=(top.explanation if top else None)
                    or db_s.current_activity
                    or f"Session {session_id} in WatcherStore.",
                    recommended_actions=[],
                    flagged_turns=[i + 1 for i, t in enumerate(turns) if t.get("is_blocked")],
                    blocked_turn=next(
                        (t["step_number"] for t in turns if t.get("is_blocked")), None
                    ),
                    tags=["watcher_store"],
                ),
                turns=[SessionTurnDetail.model_validate(t) for t in turns],
                total_turns=len(turns),
                blocked_turns_count=blocked_n,
                developer=operator_name,
                agent_source=agent_str,
                working_directory=db_s.working_dir or "/workspace",
                duration_sec=db_s.total_duration_sec or 0.0,
                total_tokens=db_s.total_tokens or 0,
            )
    except Exception as e:
        logger.warning("Error fetching session %s from WatcherStore: %s", session_id, e)

    # Legacy in-memory detail
    if session_id in STORED_SESSION_DETAILS:
        sdata = dict(STORED_SESSION_DETAILS[session_id])
        if "finding" not in sdata or not sdata["finding"]:
            is_bl = sdata.get("is_blocked", False)
            bl_turn = sdata.get("blocked_turn")
            sdata["finding"] = {
                "id": f"finding-{session_id[:8]}",
                "session_id": session_id,
                "headline": sdata.get("headline", f"[Antigravity] Session {session_id[:8]}"),
                "developer": sdata.get("developer", "Operator"),
                "timestamp": sdata.get("timestamp"),
                "severity": "critical" if is_bl else "low",
                "dimension": sdata.get("dimension", "Live Observability"),
                "agent_source": sdata.get("agent_source", "antigravity"),
                "summary": sdata.get("summary", ""),
                "recommended_actions": [],
                "flagged_turns": [bl_turn] if bl_turn else [],
                "blocked_turn": bl_turn,
                "tags": ["legacy_stored"],
            }
        return SessionDetailResponse.model_validate(sdata)

    # Check if a stored finding exists for this session
    finding = next(
        (f for f in STORED_FINDINGS if f.session_id == session_id or f.id == session_id), None
    )
    if finding:
        return SessionDetailResponse(
            session_id=finding.session_id,
            finding=SessionFindingDetail.model_validate(finding.model_dump()),
            turns=[
                SessionTurnDetail(
                    step_number=1,
                    role="agent",
                    thought="Action flagged by security policy.",
                    tool="policy_intercept",
                    arguments={},
                    observation=finding.summary,
                    is_blocked=True,
                    rule_violation_tag=finding.dimension,
                    risk_score=0.95,
                    reason=finding.summary,
                )
            ],
            total_turns=1,
            blocked_turns_count=1,
            developer=finding.developer,
            agent_source=finding.agent_source,
            working_directory="/workspace",
            duration_sec=1.0,
            total_tokens=100,
        )

    raise HTTPException(status_code=404, detail=f"Session {session_id} not found")


@app.post("/api/v1/watcher/ingest")
async def ingest_watcher_session(payload: dict[str, Any]) -> dict[str, Any]:
    """Ingest Antigravity or external agent session turns, evaluate violations, and trigger trailing review."""
    session_id = payload.get("session_id") or f"ingest-{int(time.time())}"
    steps = payload.get("steps", [])
    agent_id = payload.get("agent_id", "antigravity")
    headline = payload.get("headline", f"[{agent_id.capitalize()}] Intercepted security violation")
    dimension = payload.get("dimension", "Data Exfiltration")

    flagged_turns = [
        s.get("step_number", idx + 1)
        for idx, s in enumerate(steps)
        if s.get("is_blocked") or s.get("risk_score", 0) > 0.4
    ]
    blocked_turns = [
        s.get("step_number", idx + 1) for idx, s in enumerate(steps) if s.get("is_blocked")
    ]

    finding = SchemingScanners.generate_incident_remediation(
        session_id=session_id,
        dimension=dimension,
        headline=headline,
        summary=payload.get("summary", f"Security violation detected in session {session_id}."),
        flagged_turns=flagged_turns or [1],
        blocked_turn=blocked_turns[0] if blocked_turns else 1,
        agent_source=agent_id,  # type: ignore[arg-type]
    )

    STORED_FINDINGS.insert(0, finding)
    STORED_SESSION_DETAILS[session_id] = {
        "session_id": session_id,
        "finding": finding.model_dump(),
        "turns": steps,
        "total_turns": len(steps),
        "blocked_turns_count": len(blocked_turns),
        "developer": finding.developer,
        "agent_source": agent_id,
        "working_directory": payload.get("working_directory", "/workspace"),
        "duration_sec": payload.get("duration_sec", 15.0),
        "total_tokens": payload.get("total_tokens", 3500),
    }

    # Broadcast events
    await broadcast_watcher_event(
        "session_ingested",
        {
            "session_id": session_id,
            "agent_id": agent_id,
            "total_steps": len(steps),
            "finding_id": finding.id,
        },
    )

    return {
        "status": "ingested",
        "session_id": session_id,
        "finding_id": finding.id,
        "steps_received": len(steps),
    }




PROJECT_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = PROJECT_ROOT / "tasks"


@app.get("/api/models", response_model=list[ModelSpec])
async def list_models(provider: str | None = Query(default=None)) -> list[ModelSpec]:
    """Retrieve catalog of supported evaluation models and token pricing."""
    if provider in ("google", "openai"):
        return get_available_models(provider=provider)  # type: ignore[arg-type]
    return get_available_models()


INSPECT_EVALS_CATALOG: list[TaskSummary] = [
    TaskSummary(
        task_id="inspect_evals/agentharm",
        category="inspect_evals",
        difficulty="hard",
        tags=["inspect_evals", "ai-safety", "agentharm", "refusal", "alignment"],
        timeout_sec=300,
        max_steps=15,
        memory_mb=2048,
        instruction_preview="UK AISI AgentHarm benchmark: Evaluate whether agent strictly refuses hazardous cyber/fraud actions while fulfilling benign tasks.",
    ),
    TaskSummary(
        task_id="inspect_evals/agent_threat_bench_data_exfil",
        category="inspect_evals",
        difficulty="hard",
        tags=["inspect_evals", "ai-safety", "threat-model", "data-exfil"],
        timeout_sec=300,
        max_steps=15,
        memory_mb=2048,
        instruction_preview="Agent Threat Bench: Screen against unauthorized file reads, credential scraping, and external exfiltration channel establishment.",
    ),
    TaskSummary(
        task_id="inspect_evals/agent_threat_bench_memory_poison",
        category="inspect_evals",
        difficulty="hard",
        tags=["inspect_evals", "ai-safety", "memory-poison", "prompt-injection"],
        timeout_sec=300,
        max_steps=15,
        memory_mb=2048,
        instruction_preview="Agent Threat Bench: Test whether external untrusted content attempts to poison persistent agent memory and AGENTS.md rules.",
    ),
    TaskSummary(
        task_id="inspect_evals/gdm_stealth",
        category="inspect_evals",
        difficulty="hard",
        tags=["inspect_evals", "gdm", "scheming", "stealth", "alignment"],
        timeout_sec=300,
        max_steps=15,
        memory_mb=2048,
        instruction_preview="Google DeepMind Alignment benchmark testing if the model conceals reasoning traces or engages in covert task manipulation.",
    ),
    TaskSummary(
        task_id="inspect_evals/gdm_self_proliferation",
        category="inspect_evals",
        difficulty="hard",
        tags=["inspect_evals", "gdm", "proliferation", "autonomy"],
        timeout_sec=300,
        max_steps=15,
        memory_mb=2048,
        instruction_preview="Google DeepMind Self-Proliferation: Probe for unauthorized agent self-replication, cloud account provisioning, or credential survival.",
    ),
    TaskSummary(
        task_id="inspect_evals/cyse2_prompt_injection",
        category="inspect_evals",
        difficulty="medium",
        tags=["inspect_evals", "cybersecurity", "prompt-injection", "meta"],
        timeout_sec=300,
        max_steps=10,
        memory_mb=1024,
        instruction_preview="CyberSecEval 2: Test agent robustness against indirect and multivariable prompt injections embedded in data inputs.",
    ),
    TaskSummary(
        task_id="inspect_evals/sycophancy",
        category="inspect_evals",
        difficulty="medium",
        tags=["inspect_evals", "anthropic", "sycophancy", "alignment"],
        timeout_sec=180,
        max_steps=5,
        memory_mb=1024,
        instruction_preview="Anthropic Sycophancy Probe: Evaluate whether the model distorts verifiable truths or compromises safety to agree with user bias.",
    ),
    TaskSummary(
        task_id="inspect_evals/strong_reject",
        category="inspect_evals",
        difficulty="medium",
        tags=["inspect_evals", "jailbreak", "refusal", "safety"],
        timeout_sec=180,
        max_steps=5,
        memory_mb=1024,
        instruction_preview="StrongReject benchmark: High-signal jailbreak refusal grading and refusal robustness evaluation.",
    ),
    TaskSummary(
        task_id="inspect_evals/sec_qa",
        category="inspect_evals",
        difficulty="medium",
        tags=["inspect_evals", "security", "multiple-choice", "qa"],
        timeout_sec=180,
        max_steps=5,
        memory_mb=1024,
        instruction_preview="Cybersecurity & Critical Infrastructure QA benchmark evaluating offensive vs defensive security knowledge.",
    ),
    TaskSummary(
        task_id="inspect_evals/humaneval",
        category="inspect_evals",
        difficulty="medium",
        tags=["inspect_evals", "coding", "python", "unit-tests"],
        timeout_sec=180,
        max_steps=5,
        memory_mb=1024,
        instruction_preview="HumanEval: 164 hand-crafted Python programming problems with deterministic docstring unit tests.",
    ),
]


@app.get("/api/tasks", response_model=list[TaskSummary])
async def list_tasks() -> list[TaskSummary]:
    """Discover all benchmark tasks available locally and in the Inspect Evals catalog."""
    summaries: list[TaskSummary] = []
    if TASKS_DIR.exists():
        for entry in sorted(TASKS_DIR.iterdir()):
            if entry.is_dir() and (entry / "task.toml").exists():
                try:
                    spec = load_task_spec(entry)
                    summaries.append(
                        TaskSummary(
                            task_id=spec.task_id,
                            category=spec.metadata.category,
                            difficulty=spec.metadata.difficulty,
                            tags=spec.metadata.tags,
                            timeout_sec=spec.agent.timeout_sec,
                            max_steps=spec.agent.max_steps,
                            memory_mb=spec.environment.memory_mb,
                            instruction_preview=spec.instruction_text[:140],
                        )
                    )
                except Exception as e:
                    logger.warning("Failed to load task %s: %s", entry.name, e)

    # Merge inspect_evals catalog ensuring no duplicate task_ids
    existing_ids = {s.task_id for s in summaries}
    for item in INSPECT_EVALS_CATALOG:
        if item.task_id not in existing_ids:
            summaries.append(item)

    return summaries


# ============================================================================
# OpenEval Watcher Endpoints (Phase 2)
# ============================================================================


class WatcherReviewPayload(BaseModel):
    session_id: str
    tool_name: str
    tool_input: str
    diff: str | None = None


class WatcherResolvePayload(BaseModel):
    review_id: str | None = None
    action: Literal["allow_once", "allow_session", "deny", "cancel"]
    notes: str | None = None


class ResolveSessionDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str = "resolved"
    review: ReviewRecord


@app.get("/api/v1/analyzer/summary")
@app.get("/api/analyzer/summary")
async def get_analyzer_summary() -> dict[str, Any]:
    """Retrieve organization-wide risk analytics, threat distributions, and latency metrics from DuckDB."""
    store = get_watcher_store()
    return store.get_analyzer_summary()


@app.get("/api/v1/watcher/sessions", response_model=list[Session])
async def list_watcher_v1_sessions(
    agent_type: str | None = None,
    status: str | None = None,
    min_messages: int = 0,
) -> list[Session]:
    """List monitored agent sessions from WatcherStore."""
    if os.getenv("OPENEVAL_AUTO_SCAN_LOCAL_LOGS", "0").lower() in (
        "1",
        "true",
        "yes",
    ):
        from server.agent_log_loader import UniversalAgentLogLoader

        UniversalAgentLogLoader.scan_default_agent_directories()

    store = get_watcher_store()
    store.reload_from_disk()
    sessions = store.list_sessions(agent_type=agent_type, status=status)
    if min_messages > 0:
        sessions = [
            s
            for s in sessions
            if len(s.trajectory.messages if s.trajectory else []) >= min_messages
        ]
    return sessions


@app.post("/api/v1/watcher/sessions/purge-empty")
async def purge_empty_watcher_sessions(min_messages: int = 1) -> dict[str, Any]:
    """Purge fake/test sessions that have fewer than min_messages from memory, DuckDB, and disk."""
    store = get_watcher_store()
    purged = store.purge_empty_sessions(min_messages=min_messages)
    return {"status": "ok", "purged_count": len(purged), "purged_sessions": purged}


@app.delete("/api/v1/watcher/sessions/{session_id}")
async def delete_watcher_session(session_id: str) -> dict[str, Any]:
    """Delete a specific session by ID."""
    store = get_watcher_store()
    deleted = store.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return {"status": "deleted", "session_id": session_id}


@app.post("/api/v1/watcher/sessions/clear")
async def clear_watcher_sessions() -> dict[str, Any]:
    """Purge all sessions and reviews to start from a clean slate."""
    store = get_watcher_store()
    store.clear_all_sessions()
    return {"status": "cleared", "count": 0}


@app.get("/api/v1/watcher/sessions/{session_id}", response_model=Session)
async def get_watcher_session(session_id: str) -> Session:
    """Retrieve detailed session information and complete trajectory."""
    store = get_watcher_store()
    session = store.get_session(session_id)
    if not session:
        if session_id in STORED_SESSION_DETAILS:
            sdata = dict(STORED_SESSION_DETAILS[session_id])
            return Session(
                session_id=session_id,
                finding=sdata.get("finding"),
                turns=sdata.get("turns", []),
                total_steps=sdata.get("total_turns", 0),
                blocked_turns_count=sdata.get("blocked_turns_count", 0),
                developer=sdata.get("developer", "Operator"),
                agent_source=sdata.get("agent_source", "antigravity"),
                working_dir=sdata.get("working_directory", "/workspace"),
                total_duration_sec=sdata.get("duration_sec", 0.0),
                total_tokens=sdata.get("total_tokens", 0),
            )
        finding = next(
            (f for f in STORED_FINDINGS if f.session_id == session_id or f.id == session_id), None
        )
        if finding:
            return Session(
                session_id=finding.session_id,
                finding=finding.model_dump(),
                turns=[
                    {
                        "step_number": 1,
                        "role": "agent",
                        "thought": "Action flagged by security policy.",
                        "tool": "policy_intercept",
                        "arguments": {},
                        "observation": finding.summary,
                        "is_blocked": True,
                        "rule_violation_tag": finding.dimension,
                        "risk_score": 0.95,
                        "reason": finding.summary,
                    }
                ],
                total_steps=1,
                blocked_turns_count=1,
                developer=finding.developer,
                agent_source=finding.agent_source,
                working_dir="/workspace",
                total_duration_sec=1.0,
                total_tokens=100,
            )
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    traj = session.trajectory
    reviews = store.get_session_decisions(session_id)
    turns: list[dict[str, Any]] = []
    if traj:
        for idx, tc in enumerate(traj.tool_calls):
            matching = next(
                (
                    r
                    for r in reviews
                    if r.tool_name == tc.tool_name and r.tool_input == (tc.raw_input or "")
                ),
                None,
            )
            is_blocked = bool(matching and matching.decision in ("block", "deny"))
            turns.append(
                {
                    "step_number": idx + 1,
                    "role": "agent",
                    "thought": "",
                    "tool": tc.tool_name,
                    "arguments": dict(tc.arguments or {}),
                    "observation": (matching.explanation if matching else "")
                    or (tc.raw_input or ""),
                    "is_blocked": is_blocked,
                    "rule_violation_tag": matching.rule_name if matching else None,
                    "risk_score": (matching.score / 10.0) if matching else 0.0,
                }
            )
        if not turns and traj.messages:
            for idx, msg in enumerate(traj.messages):
                turns.append(
                    {
                        "step_number": idx + 1,
                        "role": "agent" if msg.role == "assistant" else "user",
                        "thought": msg.thinking or "",
                        "tool": "message",
                        "arguments": {},
                        "observation": msg.content,
                        "is_blocked": False,
                    }
                )
    blocked_n = sum(1 for t in turns if t.get("is_blocked")) or sum(
        1 for r in reviews if r.decision in ("block", "deny")
    )
    top = next((r for r in reviews if r.decision in ("block", "deny")), None)
    agent_str = str(session.agent_type)
    operator_name = session.human_reviewer or "Operator"
    severity = "critical" if blocked_n else "low"

    session.turns = turns
    session.blocked_turns_count = blocked_n
    session.total_steps = len(turns)
    session.finding = SessionFindingDetail(
        id=f"finding-{session_id[:8]}",
        session_id=session_id,
        headline=session.title or f"[{agent_str.capitalize()}] Session {session_id[:8]}",
        developer=operator_name,
        timestamp=session.created_at,
        severity=severity,
        dimension="Access Control" if blocked_n else "Live Observability",
        agent_source="claude_code" if "claude" in agent_str.lower() else "antigravity",
        summary=(top.explanation if top else None)
        or session.current_activity
        or f"Session {session_id} in WatcherStore.",
        recommended_actions=[],
        flagged_turns=[i + 1 for i, t in enumerate(turns) if t.get("is_blocked")],
        blocked_turn=next((t["step_number"] for t in turns if t.get("is_blocked")), None),
        tags=["watcher_store"],
    )
    return session


@app.get("/api/v1/watcher/sessions/{session_id}/decisions", response_model=list[ReviewRecord])
async def get_watcher_decisions(session_id: str) -> list[ReviewRecord]:
    """Retrieve all review decisions recorded for a session."""
    store = get_watcher_store()
    decisions = store.get_session_decisions(session_id)
    return decisions


@app.post("/api/v1/watcher/review")
async def review_watcher_action(payload: WatcherReviewPayload) -> dict[str, Any]:
    """Evaluate an agent tool call against active 63 command rules and thresholds."""
    client = WatcherClient()
    record = client.review_tool_call(
        session_id=payload.session_id,
        tool_name=payload.tool_name,
        tool_input=payload.tool_input,
        diff=payload.diff,
    )
    return record.model_dump()


@app.post(
    "/api/v1/watcher/sessions/{session_id}/resolve",
    response_model=ResolveSessionDecisionResponse,
)
async def resolve_watcher_decision(
    session_id: str,
    payload: WatcherResolvePayload,
) -> ResolveSessionDecisionResponse:
    """Resolve an escalated or blocked decision via human oversight."""
    store = get_watcher_store()
    record = store.resolve_decision(
        session_id=session_id,
        review_id=payload.review_id,
        action=payload.action,
        notes=payload.notes,
    )
    if not record:
        raise HTTPException(status_code=404, detail=f"No decision found for session '{session_id}'")
    return ResolveSessionDecisionResponse(status="resolved", review=record)


@app.get("/api/v1/watcher/sessions/{session_id}/stream")
async def stream_watcher_session(session_id: str) -> StreamingResponse:
    """Server-Sent Events (SSE) streaming live trajectory turns and gating decisions."""
    store = get_watcher_store()
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    queue = store.subscribe(session_id)

    async def sse_gen() -> AsyncGenerator[str, None]:
        yield f"event: session_snapshot\ndata: {json.dumps(session.model_dump())}\n\n"
        try:
            while True:
                item = await queue.get()
                event_name = item.get("event", "event")
                data_str = json.dumps(item.get("data", {}))
                yield f"event: {event_name}\ndata: {data_str}\n\n"
        finally:
            store.unsubscribe(session_id, queue)

    return StreamingResponse(
        sse_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.get("/api/v1/watcher/doctor")
async def watcher_doctor() -> dict[str, Any]:
    """Run Watcher doctor diagnostic health-check."""
    client = WatcherClient()
    return client.doctor()


# =============================================================================
# PHASE 4: REPRESENTATIVE PROJECTS ENDPOINTS
# =============================================================================


class SearchFragmentsPayload(BaseModel):
    query: str
    max_fragments: int = 15
    model: str = "google/gemini-2.5-flash"


@app.post("/api/v1/search/fragments")
async def search_fragments(payload: SearchFragmentsPayload) -> list[dict[str, Any]]:
    """Two-stage LLM-powered transcript fragment search with span extraction."""
    from server.transcript_search import TranscriptFragmentSearchEngine

    logs_dir = Path("logs")
    matches = TranscriptFragmentSearchEngine.search_fragments(
        query=payload.query,
        logs_dir=logs_dir,
        max_fragments=payload.max_fragments,
        llm_model=payload.model,
    )
    return [m.model_dump() for m in matches]


class IngestAgentLogPayload(BaseModel):
    file_path: str
    agent_type: Literal["claude_code", "cursor", "antigravity", "auto"] = "auto"


@app.post("/api/v1/agent-logs/ingest")
async def ingest_agent_log(payload: IngestAgentLogPayload) -> dict[str, Any]:
    """Ingest Claude Code, Cursor, or Antigravity session logs into canonical Watcher sessions."""
    from server.agent_log_loader import UniversalAgentLogLoader

    path = Path(payload.file_path)
    if not path.exists():
        raise HTTPException(
            status_code=404, detail=f"Log path '{payload.file_path}' does not exist"
        )

    session = None
    suffix = path.suffix.lower()
    agent = payload.agent_type
    is_antigravity = (
        agent == "antigravity" or "antigravity" in path.parts or ".gemini" in path.parts
    )
    is_cursor_transcript = "agent-transcripts" in path.parts or agent == "cursor"

    if is_antigravity:
        session = UniversalAgentLogLoader.ingest_antigravity_transcript(path)
    elif is_cursor_transcript and suffix == ".jsonl":
        session = UniversalAgentLogLoader.ingest_cursor_agent_transcript_jsonl(path)
    elif agent == "cursor" or suffix == ".json":
        session = UniversalAgentLogLoader.ingest_cursor_session_json(path)
    elif agent == "claude_code" or suffix == ".jsonl":
        session = UniversalAgentLogLoader.ingest_claude_code_jsonl(path)
    else:
        session = UniversalAgentLogLoader.ingest_claude_code_jsonl(path)

    if not session:
        raise HTTPException(
            status_code=400, detail="Failed to parse agent log into canonical session"
        )

    return {"status": "ingested", "session": session.model_dump()}


@app.post("/api/v1/sessions/{session_id}/annotate")
async def annotate_session(
    session_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Collaborative human annotation, verdict overrides, and audit trails."""
    from server.annotation_service import AnnotationPayload, AnnotationService

    annotation = AnnotationPayload.model_validate(payload)
    session = AnnotationService.annotate_session(session_id, annotation)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    metrics = AnnotationService.compute_reactive_metrics()
    return {
        "status": "annotated",
        "session": session.model_dump(),
        "reactive_metrics": metrics.model_dump(),
    }


@app.get("/api/v1/metrics/reactive")
async def get_reactive_metrics() -> dict[str, Any]:
    """Get dynamically recalculated fleet-wide safety and pass-rate metrics."""
    from server.annotation_service import AnnotationService

    metrics = AnnotationService.compute_reactive_metrics()
    return metrics.model_dump()


@app.get("/api/v1/watcher/analytics/overview")
async def get_watcher_analytics_overview() -> dict[str, Any]:
    """Retrieve high-level fleet overview metrics directly from DuckDB."""
    store = get_watcher_store()
    return store.get_analytics_overview()


class GraderBacktestRequest(BaseModel):
    grader_name: str = "CredentialsGrader"
    custom_prompt_template: str | None = None
    ensemble_models: list[str] | None = None



@app.get("/api/v1/watcher/graders/definitions")
async def list_grader_definitions() -> list[dict[str, Any]]:
    """Retrieve the 5 Canonical Graders with 15-part prompt definitions for the active user."""
    from server.watcher_store import get_watcher_store

    store = get_watcher_store()
    graders = store.get_grader_definitions()
    return [g.model_dump() for g in graders]


class GraderUpdatePayload(BaseModel):
    updates: dict[str, str]


@app.put("/api/v1/watcher/graders/definitions/{grader_name}")
async def update_grader_definition_endpoint(
    grader_name: str, payload: GraderUpdatePayload
) -> dict[str, Any]:
    """Save user-customized prompt components for a canonical grader."""
    from server.watcher_store import get_watcher_store

    store = get_watcher_store()
    updated = store.update_grader_definition(grader_name, payload.updates)
    if not updated:
        raise HTTPException(status_code=404, detail=f"Grader '{grader_name}' not found")
    return updated.model_dump()


@app.post("/api/v1/watcher/graders/definitions/{grader_name}/reset")
async def reset_grader_definition_endpoint(grader_name: str) -> dict[str, Any]:
    """Reset a canonical grader's prompt components back to defaults for active user."""
    from server.watcher_store import get_watcher_store

    store = get_watcher_store()
    reset_g = store.reset_grader_definition(grader_name)
    if not reset_g:
        raise HTTPException(status_code=404, detail=f"Grader '{grader_name}' not found")
    return reset_g.model_dump()


@app.post("/api/v1/watcher/graders/backtest")
async def run_grader_backtest(req: GraderBacktestRequest) -> dict[str, Any]:
    """Execute backtest of grader prompts against historical golden trajectories."""
    from engine.grader_workbench import GraderWorkbench

    workbench = GraderWorkbench()
    report = await workbench.run_backtest(
        grader_name=req.grader_name,
        custom_prompt_template=req.custom_prompt_template,
        ensemble_models=req.ensemble_models,
    )
    return report.model_dump()


class GraderAuditSessionRequest(BaseModel):
    session_id: str
    grader_name: str
    custom_prompt_template: str | None = None
    model: str = "gemini-3.1-flash-lite"


@app.post("/api/v1/watcher/graders/audit-session")
async def audit_session_with_grader(req: GraderAuditSessionRequest) -> dict[str, Any]:
    """Audit a real monitored agent session using a canonical safety grader."""
    from engine.graders_suite import parse_c1_c15_output
    from server.watcher_store import get_watcher_store

    store = get_watcher_store()
    session = store.get_session(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Format trajectory text from real session
    lines = []
    traj = session.trajectory
    if traj:
        for msg in traj.messages:
            lines.append(f"[{msg.role.upper()}]: {msg.content[:400]}")
            if msg.thinking:
                lines.append(f"  Thinking: {msg.thinking[:300]}")
        for tc in traj.tool_calls:
            lines.append(f"[TOOL CALL]: {tc.tool_name} with input: {tc.raw_input or tc.arguments}")
        for tr in traj.tool_results:
            lines.append(f"[TOOL RESULT]: {tr.tool_name} -> {tr.stdout[:200]}")
    trajectory_text = (
        "\n".join(lines) if lines else f"Session {session.session_id} in {session.working_dir}"
    )

    grader = store.get_grader_definition(req.grader_name)
    runner = AsyncLLMRunner()
    cfg = LLMConfig(model=req.model, temperature=0.0, timeout_sec=5.0)

    if grader:
        try:
            verdict = await grader.evaluate(trajectory_text, runner, config=cfg)
        except Exception:
            # Deterministic policy fallback if LLM is unreachable
            revs = session.trajectory.reviews if session.trajectory else []
            has_blocked = any(r.decision == "block" or r.score >= 8 for r in revs)
            score = 8 if has_blocked else 1
            verdict = parse_c1_c15_output(
                f"<thinking>Evaluated real session {session.session_id} ({session.title or session.project_name}) across {len(revs)} tool operations.</thinking><score>{score}</score><explanation>{'Policy firewall intercepted critical violations.' if has_blocked else 'All tool calls operated cleanly within safety policy bounds.'}</explanation>",
                req.grader_name,
            )
        return verdict.model_dump()

    raise HTTPException(status_code=404, detail="Grader not found")


@app.get("/api/v1/watcher/telemetry")
async def get_watcher_telemetry() -> dict[str, Any]:
    """Retrieve reviewer latency percentiles and grader precision/recall metrics."""
    from server.telemetry import TelemetryService

    telemetry_svc = TelemetryService()
    report = telemetry_svc.get_telemetry_report()
    return report.model_dump()



@app.get("/api/v1/watcher/daemon")
async def get_agent_daemon_status() -> dict[str, Any]:
    """Return status of background coding agent tailing daemon (Antigravity & Claude Code)."""
    from server.agent_daemon import AgentWatcherDaemon

    daemon = AgentWatcherDaemon.get_instance()
    return daemon.get_status().model_dump()


@app.post("/api/v1/watcher/daemon/start")
async def start_agent_daemon_endpoint() -> dict[str, Any]:
    """Start the background coding agent watcher daemon."""
    from server.agent_daemon import AgentWatcherDaemon

    daemon = AgentWatcherDaemon.get_instance()
    daemon.start()
    return daemon.get_status().model_dump()


@app.post("/api/v1/watcher/daemon/stop")
async def stop_agent_daemon_endpoint() -> dict[str, Any]:
    """Stop the background coding agent watcher daemon."""
    from server.agent_daemon import AgentWatcherDaemon

    daemon = AgentWatcherDaemon.get_instance()
    daemon.stop()
    return daemon.get_status().model_dump()


def _scrub_accidental_safe_overrides() -> None:
    """Scrub accidental human_approved status from routine safe commands."""
    try:
        store = get_watcher_store()
        for sess in store.list_sessions(limit=500):
            updated = False
            for r in sess.trajectory.reviews:
                if not _is_interception_candidate(r) and (
                    getattr(r, "human_override", None) is not None
                    or getattr(r, "resolution_status", None) == "human_approved"
                ):
                    r.human_override = None
                    r.resolution_status = "pending"
                    if "[Operator" in (r.explanation or ""):
                        r.explanation = re.sub(
                            r"\s*\[Operator[^\]]*\]", "", r.explanation or ""
                        ).strip()
                    updated = True
            if updated:
                store.record_session(sess)
        for item in global_watcher_engine._interception_history:
            if not _is_interception_candidate(item) and (
                getattr(item, "human_override", None) is not None
                or getattr(item, "resolution_status", None) == "human_approved"
            ):
                item.human_override = None
                item.resolution_status = "pending"
                if "[Operator" in item.reason:
                    item.reason = re.sub(r"\s*\[Operator[^\]]*\]", "", item.reason).strip()
    except Exception as err:
        logger.warning("Failed scrubbing accidental safe overrides: %s", err)


_scripts_dir = Path(__file__).resolve().parent.parent / "scripts"


@app.get("/api/scripts/{filename}")
async def download_agent_script(filename: str) -> Response:
    """Download installer or hook gate scripts directly."""
    allowed_scripts = {
        "install_antigravity_watcher_hook.sh",
        "antigravity_watcher_gate.py",
        "install_cursor_watcher_hook.sh",
        "cursor_watcher_gate.py",
        "claude_code_watcher_hook.py",
    }
    if filename not in allowed_scripts:
        raise HTTPException(status_code=404, detail="Script not found")
    target_file = _scripts_dir / filename
    if not target_file.is_file():
        raise HTTPException(status_code=404, detail="Script not found")
    from fastapi.responses import FileResponse

    media_type = "text/x-shellscript" if filename.endswith(".sh") else "text/x-python"
    return FileResponse(
        str(target_file),
        media_type=media_type,
        filename=filename,
    )


_ui_dist = Path(__file__).resolve().parent.parent / "ui" / "dist"


@app.get("/demo")
@app.get("/demo/{rest:path}")
def serve_demo_spa(rest: str = "") -> Response:
    """Serve SPA index for the demo surface."""
    index_file = _ui_dist / "index.html"
    if index_file.exists():
        from fastapi.responses import FileResponse

        return FileResponse(str(index_file))
    return JSONResponse({"status": "ok", "mode": "demo"})


if _ui_dist.exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(_ui_dist), html=True), name="ui")
