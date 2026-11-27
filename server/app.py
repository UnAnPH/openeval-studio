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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast, get_args

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from engine.approval_policy import (
    WatcherVerdict,
    global_watcher_engine,
)
from engine.gemini_key_pool import discover_gemini_keys
from engine.llm_runner import AsyncLLMRunner, LLMConfig
from engine.scanners import FindingRecord, RecommendedAction, SchemingScanners
from engine.watcher_sdk import WatcherClient
from schemas.models import ModelSpec, get_available_models
from schemas.task_spec import load_task_spec
from schemas.watcher_models import AgentType, ReviewRecord, Session, Trajectory, review_from_row
from server.annotation_service import AnnotationPayload
from server.auth import (
    SESSION_COOKIE_NAME,
    is_production_env,
    resolve_api_key_user,
    verify_session_token,
)
from server.auth import (
    router as auth_router,
)
from server.db import (
    _USER_ID_CACHE,
    SessionLocal,
    current_user_id,
    current_user_slug,
    get_current_user_id,
    get_user_id_by_slug,
)
from server.log import setup_logger
from server.rate_limiter import TokenBucketRateLimiter
from server.request_context import RequestIdMiddleware
from server.routes.eval import router as eval_router
from server.routes.legacy_sessions import (
    STORED_FINDINGS,
    STORED_SESSION_DETAILS,
    SessionFindingDetail,
)
from server.routes.legacy_sessions import (
    SessionDetailResponse as SessionDetailResponse,
)
from server.routes.legacy_sessions import (
    SessionTurnDetail as SessionTurnDetail,
)
from server.routes.legacy_sessions import (
    _get_legacy_session_detail as _get_legacy_session_detail,
)
from server.routes.legacy_sessions import (
    scan_antigravity_brain_sessions as scan_antigravity_brain_sessions,
)
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

# Unauthenticated demo POST burst guard so a hosted demo cannot be used as a free
# compute proxy. TokenBucketRateLimiter uses REDIS_URL when it is set (shared bucket
# across workers) and falls back to a thread-safe in-memory bucket otherwise.
# Refill is intentionally slow (1 token / 5s).
demo_post_rate_limiter = TokenBucketRateLimiter(
    capacity=10,
    refill_rate_per_sec=0.2,
    redis_url="",
)


def _request_client_ip(request: Request) -> str:
    """Best-effort client IP from the proxy-appended X-Forwarded-For hop.

    The left-most hops are client-supplied and therefore spoofable; the reverse
    proxy (Caddy) appends the real peer address as the last hop, so only that one
    is trusted. Falls back to the socket peer when the header is absent/empty.
    """
    forwarded = request.headers.get("X-Forwarded-For") or request.headers.get("x-forwarded-for")
    if forwarded:
        last_hop = forwarded.split(",")[-1].strip()
        if last_hop:
            return last_hop
    return request.client.host if request.client else "unknown"


def _resolve_demo_user_id() -> int | None:
    """Resolve the demo tenant id from the cache or PostgreSQL, without raising."""
    demo_uid = _USER_ID_CACHE.get("demo")
    if demo_uid is not None:
        return demo_uid
    try:
        with SessionLocal() as db:
            try:
                demo_uid = get_user_id_by_slug(db, "demo")
            finally:
                db.rollback()
    except Exception as exc:
        logger.debug("Demo user lookup failed: %s", exc)
        return None
    if demo_uid is not None:
        _USER_ID_CACHE["demo"] = demo_uid
    return demo_uid


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
            try:
                db.execute(text("SELECT 1"))
            finally:
                db.rollback()
        skip_migration = (
            os.getenv("OPENEVAL_ENV") == "production"
            or os.getenv("OPENEVAL_SKIP_LIFESPAN_MIGRATION") == "1"
        )
        if not skip_migration:
            alembic_ini = PROJECT_ROOT / "alembic.ini"
            if alembic_ini.exists():
                alembic_cfg = Config(str(alembic_ini))
                command.upgrade(alembic_cfg, "head")
        else:
            logger.info(
                "Alembic migration skipped in lifespan (already handled by container entrypoint)."
            )
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

# Enable CORS for local development and the Vite frontend. Credentialed
# requests are only allowed when the origin list excludes the "*" wildcard, since
# browsers reject `Access-Control-Allow-Origin: *` together with credentials.
cors_origins = [
    o.strip()
    for o in os.getenv(
        "OPENEVAL_CORS_ORIGINS",
        "https://openeval.studio,http://localhost:8000,http://127.0.0.1:8000,http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=("*" not in cors_origins),
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
            or (
                method in ("GET", "HEAD")
                and (path.startswith("/api/tasks") or path == "/api/models")
            )
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
        surface_query = (request.query_params.get("surface") or "").strip().lower()
        is_demo_surface = (
            surface_header == "demo"
            or surface_query == "demo"
            or path.startswith("/demo")
            or path.startswith("/api/demo")
        )

        # Demo surface: unauthenticated read/search/compare runs under the demo user tenant.
        if is_demo_surface:
            is_read_method = method in ("GET", "HEAD", "OPTIONS")
            is_demo_search_or_compare = path in (
                "/api/eval/search",
                "/api/v1/search/fragments",
                "/api/v1/agent-logs/ingest",
                "/api/v1/watcher/graders/audit-session",
                "/api/v1/watcher/graders/backtest",
            ) or path.startswith("/api/eval/compare/")
            # Real evaluation launches are permitted on the demo surface so anonymous
            # visitors can run the product; they spend the demo IP rate-limit bucket.
            is_demo_eval_action = method == "POST" and (
                path in ("/api/eval/run", "/api/eval/launch")
                or (path.startswith("/api/eval/runs/") and path.endswith("/stop"))
                or path.startswith("/api/eval/stop/")
            )
            is_permitted_demo_action = (
                is_read_method or is_demo_search_or_compare or is_demo_eval_action
            )
            if is_permitted_demo_action:
                if method == "POST" and (is_demo_search_or_compare or is_demo_eval_action):
                    client_ip = _request_client_ip(request)
                    demo_limit = demo_post_rate_limiter.check(f"demo-post:{client_ip}")
                    if not demo_limit.allowed:
                        return JSONResponse(
                            status_code=429,
                            content={
                                "detail": (
                                    "Demo rate limit exceeded. "
                                    "Please sign in for unrestricted access."
                                )
                            },
                        )
                demo_uid = _resolve_demo_user_id()
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
            owner_uid = _USER_ID_CACHE.get("owner")
            if owner_uid is None:
                try:
                    with SessionLocal() as db:
                        try:
                            owner_uid = get_user_id_by_slug(db, "owner")
                        finally:
                            db.rollback()
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


@app.get("/api/v1/organization/instructions")
async def get_organization_instructions() -> dict[str, Any]:
    """Alias for /api/v1/watcher/instructions."""
    store = get_watcher_store()
    return {
        "instructions": store.get_custom_instructions(),
        "updated_at": datetime.now(UTC).isoformat(),
    }


@app.put("/api/v1/organization/instructions")
async def update_organization_instructions(payload: dict[str, Any]) -> dict[str, Any]:
    """Alias for /api/v1/watcher/instructions."""
    store = get_watcher_store()
    return store.set_custom_instructions(str(payload.get("instructions", "")))


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


class TaskFilePreview(BaseModel):
    """File path and preview content for benchmark environments/seed files."""

    path: str
    content: str
    size_bytes: int


class TaskDetail(BaseModel):
    """Detailed benchmark specification including full prompt, Dockerfile, task.toml, and seeds."""

    task_id: str
    category: str | None = None
    difficulty: str | None = None
    tags: list[str] = Field(default_factory=list)
    timeout_sec: float = 600.0
    max_steps: int = 100
    memory_mb: int = 2048
    cpus: int = 2
    allow_internet: bool = False
    instruction_text: str
    task_toml: str | None = None
    dockerfile: str | None = None
    verifier_code: str | None = None
    seed_files: list[TaskFilePreview] = Field(default_factory=list)


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
            res = db.execute(text("SELECT 1")).scalar() == 1
            if hasattr(db, "rollback"):
                db.rollback()
            return res
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
    db_connected = await asyncio.to_thread(probe_database, open_session)

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
    """Report whether demo session seed is enabled and how many fixtures are visible."""
    is_demo = os.getenv("OPENEVAL_DEMO_SEED", "0").lower() in ("1", "true", "yes")
    if not is_demo:
        return {
            "demo_seed": False,
            "demo_mode": False,
            "seeded_sessions": 0,
            "seeded_eval_runs": 0,
        }

    # Both stores scope reads to the active tenant, so measure the demo tenant
    # explicitly instead of inheriting the caller's (usually unauthenticated) context.
    slug_token = current_user_slug.set("demo")
    id_token = current_user_id.set(_resolve_demo_user_id())
    try:
        seeded_sessions = len(get_watcher_store().list_sessions())
        seeded_eval_runs = len(global_run_store.list_runs())
    finally:
        current_user_id.reset(id_token)
        current_user_slug.reset(slug_token)

    return {
        "demo_seed": True,
        "demo_mode": True,
        "seeded_sessions": seeded_sessions,
        "seeded_eval_runs": seeded_eval_runs,
    }


# =====================================================================
# Watcher Subsystem & Real-Time Telemetry
# =====================================================================

watcher_sse_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()


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


def _tool_call_target_text(tool_call: Any) -> str:
    """Extract the command/path payload from a tool call for review pairing."""
    raw_arguments = getattr(tool_call, "arguments", None)
    arguments: dict[str, Any] = raw_arguments if isinstance(raw_arguments, dict) else {}
    text = (
        arguments.get("command")
        or arguments.get("CommandLine")
        or arguments.get("path")
        or arguments.get("file_path")
        or arguments.get("AbsolutePath")
        or arguments.get("absolute_path")
        or arguments.get("TargetFile")
        or getattr(tool_call, "raw_input", None)
        or ""
    )
    return str(text)


def _find_tool_call_index(session: Any, rev: Any, preferred_idx: int = -1) -> int:
    """Content-first lookup of the tool call that a review actually evaluated.

    Prefers an exact command/path match (and a matching tool name), then a
    containment match. Only falls back to the review's positional index when the
    lists are parallel and the tool names agree, so a ``pytest`` call can never
    be paired with a ``cat .env`` review.
    """
    trajectory = getattr(session, "trajectory", None) if session is not None else None
    calls = list(getattr(trajectory, "tool_calls", None) or [])
    if not calls:
        return -1

    review_text = str(getattr(rev, "tool_input", "") or "")
    review_tool = str(getattr(rev, "tool_name", "") or "")
    review_key = _normalize_cmd_for_match(review_text)

    if review_key:
        candidate_keys = [_normalize_cmd_for_match(_tool_call_target_text(tc)) for tc in calls]
        # Exact matches first, preferring the same tool name.
        for require_tool in (True, False):
            for i, candidate_key in enumerate(candidate_keys):
                if require_tool and review_tool and calls[i].tool_name != review_tool:
                    continue
                if candidate_key and candidate_key == review_key:
                    return i
        # Then containment matches (e.g. ``src/config.py`` vs an absolute path).
        if len(review_key) >= 4:
            for require_tool in (True, False):
                for i, candidate_key in enumerate(candidate_keys):
                    if require_tool and review_tool and calls[i].tool_name != review_tool:
                        continue
                    if candidate_key and (
                        review_key in candidate_key or candidate_key in review_key
                    ):
                        return i

    reviews = list(getattr(trajectory, "reviews", None) or [])
    if (
        0 <= preferred_idx < len(calls)
        and len(reviews) == len(calls)
        and calls[preferred_idx].tool_name == review_tool
    ):
        return preferred_idx
    return -1


def _pair_review_with_tool_call(session: Any, rev: Any) -> tuple[Any | None, Any | None]:
    """Resolve a review to the aligned ``(tool_call, tool_result)`` pair."""
    trajectory = getattr(session, "trajectory", None) if session is not None else None
    if trajectory is None:
        return None, None
    preferred_idx = -1
    rev_id = getattr(rev, "id", None)
    for i, candidate in enumerate(trajectory.reviews or []):
        if rev_id and candidate.id == rev_id:
            preferred_idx = i
            break
    idx = _find_tool_call_index(session, rev, preferred_idx)
    if idx < 0:
        return None, None
    tool_calls = list(trajectory.tool_calls or [])
    tool_results = list(trajectory.tool_results or [])
    return tool_calls[idx], (tool_results[idx] if idx < len(tool_results) else None)


def _map_review_decision(
    rev: Any, human_ov: str | None, res_status: str
) -> tuple[Literal["allow", "deny", "escalate"], bool]:
    """Map a stored review decision to a verdict without escalating ordinary allows."""
    decision = str(getattr(rev, "decision", "") or "").lower()
    if human_ov == "allow" or res_status == "human_approved":
        return "allow", True
    if human_ov == "deny":
        return "deny", False
    if decision in ("block", "deny"):
        return "deny", False
    if decision in ("escalate", "ask", "force_ask"):
        return "escalate", False
    return "allow", True


def _enrich_verdict_dict(
    row: dict[str, Any], session_cache: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Ensure full_command and tool_result are populated for Control Live Stream."""
    full_cmd = row.get("full_command")
    tool_res = row.get("tool_result")
    if full_cmd and tool_res and not str(tool_res).startswith("Awaiting"):
        return row

    sess_id = row.get("session_id")
    rev_id = row.get("review_id")
    store = get_watcher_store()
    if session_cache is not None and sess_id in session_cache:
        session = session_cache[sess_id]
    else:
        session = store.get_session(sess_id) if sess_id else None
        if session_cache is not None and sess_id:
            session_cache[sess_id] = session

    if session and session.trajectory:
        tc = None
        tr = None
        matched_rev = None
        for r in session.trajectory.reviews:
            if rev_id and r.id == rev_id:
                matched_rev = r
                break
        if matched_rev is not None:
            tc, tr = _pair_review_with_tool_call(session, matched_rev)

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
                            (
                                candidate_tc.arguments.get("command")
                                or candidate_tc.arguments.get("CommandLine")
                            )
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

        if tc:
            extracted = str(
                (
                    tc.arguments.get("command") or tc.arguments.get("CommandLine")
                    if isinstance(tc.arguments, dict)
                    else None
                )
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
            # Enrich whenever the trajectory holds a longer command, even when the
            # stored payload was clipped (e.g. at 500 chars) rather than missing.
            if extracted and (not full_cmd or len(extracted) > len(str(full_cmd))):
                row["full_command"] = extracted
        elif not full_cmd:
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


def _verdicts_from_store_reviews(
    limit: int = 40, session_cache: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Map recent block/deny/escalate/resolved reviews into WatcherVerdict-shaped dicts for Live Stream."""
    store = get_watcher_store()
    rows: list[tuple[str, Any]] = []

    used_db = False
    db_rows = []
    try:
        with SessionLocal() as db:
            try:
                uid = get_current_user_id(db)
                db_rows = db.execute(
                    text(
                        """
                        SELECT r.id, r.session_id, r.tool_name, r.tool_input, r.decision,
                               r.score, r.stage, r.rule_name, r.explanation, r.diff,
                               r.latency_ms, r.created_at, r.request_id,
                               s.agent_type, s.title, s.updated_at
                        FROM reviews r
                        LEFT JOIN sessions s ON r.user_id = s.user_id AND r.session_id = s.id
                        WHERE r.user_id = :uid
                        ORDER BY r.created_at DESC
                        LIMIT :lim
                        """
                    ),
                    {"uid": uid, "lim": limit * 4},
                ).fetchall()
            finally:
                db.rollback()
        used_db = True
    except Exception as exc:
        logger.debug("Failed querying reviews from db: %s", exc)
        used_db = False

    # Load each referenced session payload once. Selecting ``s.payload`` in the
    # JOIN above duplicates the JSONB blob per review row and spikes RSS.
    payload_map: dict[str, Any] = {}
    session_obj_map: dict[str, Session] = {}
    if used_db:
        unique_sids = list(dict.fromkeys(str(r[1]) for r in db_rows if r[1]))
        if unique_sids:
            try:
                with SessionLocal() as db:
                    try:
                        uid = get_current_user_id(db)
                        for sid in unique_sids:
                            payload_row = db.execute(
                                text(
                                    "SELECT id, payload FROM sessions "
                                    "WHERE user_id = :uid AND id = :sid"
                                ),
                                {"uid": uid, "sid": sid},
                            ).fetchone()
                            if payload_row and payload_row[1]:
                                payload_map[sid] = payload_row[1]
                    finally:
                        db.rollback()
            except Exception as exc:
                logger.debug("Failed fetching session payloads for reviews: %s", exc)

    if used_db:
        for r in db_rows:
            sess_id = str(r[1])
            raw_payload = payload_map.get(sess_id)
            # Canonical mapper: relational columns + JSONB snapshot overlay.
            rev = review_from_row(r[:13], payload=raw_payload)
            if not _is_interception_candidate(rev):
                continue

            raw_agent = r[13]
            sess_agent: AgentType = (
                cast(AgentType, raw_agent)
                if isinstance(raw_agent, str) and raw_agent in get_args(AgentType)
                else "antigravity"
            )
            sess_title = str(r[14] or "")
            sess_updated = str(r[15] or r[11] or "")

            # Validate each session payload at most once so trajectory lookups
            # below do not re-parse the same JSONB blob per review row.
            session = session_obj_map.get(sess_id)
            if session is None and session_cache is not None:
                session = session_cache.get(sess_id)
            if session is None and raw_payload is not None:
                with contextlib.suppress(Exception):
                    session = Session.model_validate(
                        raw_payload if isinstance(raw_payload, dict) else json.loads(raw_payload)
                    )
            if session is None:
                session = Session(
                    session_id=sess_id,
                    agent_type=sess_agent,
                    title=sess_title,
                    updated_at=sess_updated,
                )
            session_obj_map[sess_id] = session
            if session_cache is not None:
                session_cache[sess_id] = session
            rows.append((rev.timestamp or "", (session, rev)))

    if not used_db:
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
        # Match tool call and tool result in session trajectory
        tc = None
        tr = None
        if session and session.trajectory and session.trajectory.reviews:
            for r in session.trajectory.reviews:
                if r.id == rev.id:
                    rov = getattr(r, "human_override", None)
                    if human_ov is None and rov in ("allow", "deny"):
                        human_ov = rov
                    break

            tc, tr = _pair_review_with_tool_call(session, rev)

        mapped, is_safe = _map_review_decision(rev, human_ov, res_status)

        full_cmd_str = str(
            (
                (tc.arguments.get("command") or tc.arguments.get("CommandLine"))
                if tc and isinstance(tc.arguments, dict)
                else None
            )
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
                decision=mapped,
                stage=(
                    "stage_2_deterministic"
                    if rev.stage in ("rule", "threshold")
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
                agent_id=str(session.agent_type if session else "unknown"),
                timestamp=rev.timestamp or (session.updated_at if session else ""),
                review_id=rev.id,
                session_id=session.session_id if session else rev.session_id,
                human_override=human_ov,
                resolution_status=res_status,
                full_command=full_cmd_str or f"{rev.tool_name}: {rev.tool_input}",
                tool_result=tool_result_str,
                calibrated_telemetry=rev.calibrated_telemetry,
                deep_review_status=(
                    rev.calibrated_telemetry.deep_review_status
                    if rev.calibrated_telemetry is not None
                    else getattr(rev, "metadata", {}).get("deep_review_status")
                ),
                request_id=getattr(rev, "request_id", None),
            ).model_dump()
        )
    return out


def _fetch_and_merge_interceptions(
    limit: int, uid: int | None, user_slug: str
) -> list[dict[str, Any]]:
    """Fetch live and stored interceptions and enrich them with session metadata in threadpool."""
    # Pull a wider pool than the requested page so a burst of recent ``allow``
    # tool calls from eval runs cannot evict older blocked reviews before merge.
    pool_limit = max(limit * 4, 200)
    live = [
        v.model_dump()
        for v in global_watcher_engine.get_history(pool_limit)
        if (v.user_id is None and user_slug in ("owner", "demo"))
        or (v.user_id is not None and v.user_id == uid)
    ]
    session_cache: dict[str, Any] = {}
    stored = _verdicts_from_store_reviews(limit=pool_limit, session_cache=session_cache)
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for row in live + stored:
        rid = row.get("review_id")
        ts_sec = str(row.get("timestamp") or "")[:19]
        key = rid if rid else f"{row.get('agent_id')}|{row.get('action_preview')}|{ts_sec}"
        if key in seen:
            continue
        seen.add(key)
        merged.append(_enrich_verdict_dict(row, session_cache=session_cache))

    merged.sort(key=lambda r: str(r.get("timestamp") or ""), reverse=True)

    # Reserve a quota for blocked/escalated rows so Control always surfaces
    # active interceptions even when recent allowed tool calls dominate.
    blocked_rows: list[dict[str, Any]] = []
    other_rows: list[dict[str, Any]] = []
    for row in merged:
        is_blocked = (
            str(row.get("decision") or "").lower() in ("deny", "block", "reject", "escalate")
            and row.get("human_override") != "allow"
            and row.get("resolution_status") != "human_approved"
        )
        (blocked_rows if is_blocked else other_rows).append(row)

    selected = blocked_rows + other_rows
    if blocked_rows:
        blocked_quota = max(limit // 2, min(20, limit))
        selected_blocked = blocked_rows[:blocked_quota]
        selected_other = other_rows[: max(0, limit - len(selected_blocked))]
        selected = selected_blocked + selected_other

    selected.sort(key=lambda r: str(r.get("timestamp") or ""), reverse=True)
    return selected[:limit]


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
    user_slug = current_user_slug.get()
    return await asyncio.to_thread(_fetch_and_merge_interceptions, limit, uid, user_slug)


def _map_agent_source(
    agent: str,
) -> Literal["antigravity", "claude_code", "cursor", "codex", "opencode", "openeval_runner"]:
    if "claude" in agent:
        return "claude_code"
    if agent == "cursor":
        return "cursor"
    if agent == "antigravity":
        return "antigravity"
    if agent == "codex":
        return "codex"
    if agent == "opencode":
        return "opencode"
    return "openeval_runner"


def _classify_finding_dimension(rule_name: str, explanation: str) -> str:
    rule = rule_name.upper()
    expl = explanation.lower()
    if "EXFIL" in rule or "credential" in expl or "metadata" in expl:
        return "Data Exfiltration"
    if "PRIVILEGE" in rule or "sudo" in expl:
        return "Privilege Escalation"
    if "TAMPER" in rule:
        return "Policy Tampering"
    return "Access Control"


def _build_finding_record(
    *,
    session_id: str,
    agent_type: str,
    title: str | None,
    developer: str | None,
    review_id: str,
    tool_name: str,
    rule_name: str | None,
    explanation: str | None,
) -> FindingRecord:
    agent_source = _map_agent_source(agent_type)
    dim = _classify_finding_dimension(rule_name or "", explanation or "")
    return FindingRecord(
        id=f"finding-{session_id[:8]}",
        session_id=session_id,
        headline=title or f"[{agent_type}] Blocked: {rule_name or tool_name}",
        summary=explanation or "Policy gate blocked a tool call.",
        severity="critical",
        dimension=dim,
        developer=developer or "Operator",
        agent_source=agent_source,
        recommended_actions=[
            RecommendedAction(
                priority="P1",
                category="REMEDIATE_CODE",
                title=f"Review blocked tool: {tool_name}",
                description=explanation or "Inspect trajectory and tighten policy if needed.",
                citations=[f"[R{review_id}]"],
            )
        ],
        flagged_turns=[1],
        blocked_turn=1,
    )


def _list_watcher_findings_sync(
    severity: str | None,
    dimension: str | None,
) -> list[FindingRecord]:
    store = get_watcher_store()
    findings_map: dict[str, FindingRecord] = {}
    used_db = False
    db_rows: list[Any] = []

    try:
        with SessionLocal() as db:
            try:
                uid = get_current_user_id(db)
                db_rows = list(
                    db.execute(
                        text(
                            """
                            SELECT r.id, r.session_id, r.tool_name, r.decision, r.score,
                                   r.rule_name, r.explanation, s.agent_type, s.title
                            FROM reviews r
                            LEFT JOIN sessions s
                                ON r.user_id = s.user_id AND r.session_id = s.id
                            WHERE r.user_id = :uid
                              AND (r.decision IN ('block', 'deny') OR COALESCE(r.score, 0) >= 8)
                            ORDER BY COALESCE(r.score, 0) DESC, r.created_at DESC
                            LIMIT 500
                            """
                        ),
                        {"uid": uid},
                    ).fetchall()
                )
            finally:
                db.rollback()
        used_db = True
    except Exception as exc:
        logger.debug("Findings DB query fell back to store scan: %s", exc)
        used_db = False

    if used_db:
        seen_sessions: set[str] = set()
        for row in db_rows:
            sess_id = str(row[1] or "")
            if not sess_id or sess_id in seen_sessions:
                continue
            seen_sessions.add(sess_id)
            fid = f"finding-{sess_id[:8]}"
            findings_map[fid] = _build_finding_record(
                session_id=sess_id,
                agent_type=str(row[7] or "antigravity"),
                title=str(row[8]) if row[8] else None,
                developer="Operator",
                review_id=str(row[0] or ""),
                tool_name=str(row[2] or "Tool"),
                rule_name=str(row[5] or ""),
                explanation=str(row[6] or ""),
            )

    if not used_db or not db_rows:
        for session in store.list_sessions(limit=200):
            reviews = (
                list(session.trajectory.reviews)
                if session.trajectory and session.trajectory.reviews
                else store.get_session_decisions(session.session_id)
            )
            blocked = [r for r in reviews if r.decision in ("block", "deny") or (r.score or 0) >= 8]
            if not blocked:
                continue
            top = max(blocked, key=lambda r: r.score or 0)
            fid = f"finding-{session.session_id[:8]}"
            findings_map[fid] = _build_finding_record(
                session_id=session.session_id,
                agent_type=session.agent_type,
                title=session.title,
                developer=session.human_reviewer,
                review_id=top.id,
                tool_name=top.tool_name,
                rule_name=top.rule_name,
                explanation=top.explanation,
            )

    results = list(findings_map.values())
    if severity:
        sev_rank = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        min_rank = sev_rank.get(severity.lower(), 1)
        results = [f for f in results if sev_rank.get(f.severity.lower(), 1) >= min_rank]
    if dimension:
        results = [f for f in results if f.dimension.lower() == dimension.lower()]
    return results


@app.get("/api/v1/watcher/findings", response_model=list[FindingRecord])
async def list_watcher_findings(
    severity: str | None = Query(default=None, description="Filter: critical, high, medium, low"),
    dimension: str | None = Query(default=None),
) -> list[FindingRecord]:
    return await asyncio.to_thread(_list_watcher_findings_sync, severity, dimension)


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

    traj_lines = []
    for s in steps:
        turn_num = s.get("step_number") or s.get("turn")
        tool = s.get("tool") or s.get("tool_name")
        cmd = s.get("command") or s.get("input") or s.get("arguments")
        thought = s.get("thought")
        if thought:
            traj_lines.append(f"Turn #{turn_num} [Thought]: {thought}")
        if tool:
            traj_lines.append(f"Turn #{turn_num} [Tool {tool}]: {cmd}")
    trajectory_context = "\n".join(traj_lines) if traj_lines else None

    remediation_runner: AsyncLLMRunner | None = None
    if discover_gemini_keys() or os.getenv("OPENAI_API_KEY"):
        try:
            remediation_runner = AsyncLLMRunner()
        except Exception:
            remediation_runner = None

    finding = await SchemingScanners.generate_incident_remediation(
        session_id=session_id,
        dimension=dimension,
        headline=headline,
        summary=payload.get("summary", f"Security violation detected in session {session_id}."),
        flagged_turns=flagged_turns or [1],
        blocked_turn=blocked_turns[0] if blocked_turns else 1,
        agent_source=agent_id,  # type: ignore[arg-type]
        developer=payload.get("developer", "Operator"),
        runner=remediation_runner,
        trajectory_text=trajectory_context,
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
    if provider in ("google", "openai", "opencode", "anthropic", "custom", "ollama", "vllm"):
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


_LEADING_HTML_COMMENT_RE = re.compile(r"^(?:\s*<!--.*?-->\s*)+", re.DOTALL)


def _strip_leading_html_comments(text: str) -> str:
    """Strip leading HTML comment blocks so task previews start with real prose."""
    return _LEADING_HTML_COMMENT_RE.sub("", text or "").strip()


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
                            instruction_preview=_strip_leading_html_comments(spec.instruction_text)[
                                :140
                            ],
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


@app.get("/api/tasks/{task_id:path}", response_model=TaskDetail)
async def get_task_detail(task_id: str) -> TaskDetail:
    """Retrieve full task specifications, instruction.md, Dockerfile, task.toml, and seed files."""
    candidates = [
        TASKS_DIR / task_id,
        TASKS_DIR / task_id.replace("/", "--"),
        TASKS_DIR / task_id.replace("--", "/"),
    ]

    target_dir: Path | None = None
    for cand in candidates:
        if cand.is_dir() and (cand / "task.toml").exists():
            target_dir = cand
            break

    if target_dir:
        try:
            spec = load_task_spec(target_dir)
            task_toml_text = (
                (target_dir / "task.toml").read_text(encoding="utf-8")
                if (target_dir / "task.toml").exists()
                else None
            )
            dockerfile_text = (
                spec.dockerfile_path.read_text(encoding="utf-8")
                if spec.dockerfile_path.exists()
                else None
            )
            verifier_text = (
                spec.test_outputs_path.read_text(encoding="utf-8")
                if spec.test_outputs_path.exists()
                else None
            )

            # Gather seed files in environment/
            env_dir = target_dir / "environment"
            seed_files: list[TaskFilePreview] = []
            if env_dir.is_dir():
                for p in sorted(env_dir.rglob("*")):
                    if p.is_file() and p.name != "Dockerfile":
                        try:
                            # Skip binary or very large files (>100KB)
                            content = p.read_text(encoding="utf-8", errors="replace")[:20000]
                            rel_path = str(p.relative_to(env_dir))
                            seed_files.append(
                                TaskFilePreview(
                                    path=rel_path,
                                    content=content,
                                    size_bytes=p.stat().st_size,
                                )
                            )
                        except Exception:
                            continue

            return TaskDetail(
                task_id=spec.task_id,
                category=spec.metadata.category,
                difficulty=spec.metadata.difficulty,
                tags=spec.metadata.tags,
                timeout_sec=spec.agent.timeout_sec,
                max_steps=spec.agent.max_steps,
                memory_mb=spec.environment.memory_mb,
                cpus=spec.environment.cpus,
                allow_internet=spec.environment.allow_internet,
                instruction_text=spec.instruction_text,
                task_toml=task_toml_text,
                dockerfile=dockerfile_text,
                verifier_code=verifier_text,
                seed_files=seed_files,
            )
        except Exception as e:
            logger.warning("Failed loading task spec for %s: %s", task_id, e)
            raise HTTPException(
                status_code=500, detail=f"Failed loading task {task_id}: {e}"
            ) from e

    # Fallback to INSPECT_EVALS_CATALOG
    for item in INSPECT_EVALS_CATALOG:
        if item.task_id == task_id or item.task_id == task_id.replace("--", "/"):
            return TaskDetail(
                task_id=item.task_id,
                category=item.category,
                difficulty=item.difficulty,
                tags=item.tags,
                timeout_sec=item.timeout_sec,
                max_steps=item.max_steps,
                memory_mb=item.memory_mb,
                cpus=2,
                allow_internet=False,
                instruction_text=item.instruction_preview
                or f"Inspect Evals benchmark suite: {item.task_id}",
                task_toml=f'[task]\nid = "{item.task_id}"\ncategory = "{item.category}"\n\n[environment]\nmemory_mb = {item.memory_mb}\nallow_internet = false',
                dockerfile="FROM python:3.11-slim\nWORKDIR /workspace\n# Hermetic Inspect AI Evaluation Sandbox\nRUN pip install inspect-ai pytest",
                verifier_code=f"# Native Inspect AI benchmark solver for {item.task_id}\nfrom inspect_ai import eval, task\n\n# Verified via Inspect AI automated rubric",
                seed_files=[],
            )

    raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")


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
    """Retrieve organization-wide risk analytics, threat distributions, and latency metrics from the Watcher store."""
    store = get_watcher_store()
    return await asyncio.to_thread(store.get_analyzer_summary)


def _to_session_summary(s: Session) -> Session:
    """Return a copy of Session with heavy trajectory/step message blobs stripped for dashboard views."""
    traj = s.trajectory
    derived_steps = (
        s.total_steps
        or len(s.steps)
        or len(s.turns)
        or (len(traj.tool_calls) if traj else 0)
        or (len(traj.reviews) if traj else 0)
    )
    slim_reviews = [
        r
        for r in (traj.reviews if traj else [])
        if r.decision in ("block", "deny", "reject", "ask", "escalate", "force_ask")
        or (r.score or 0) >= 8
        or r.human_override is not None
    ]
    slim_traj = Trajectory(
        session_id=traj.session_id if traj else s.session_id,
        reviews=slim_reviews,
    )
    return s.model_copy(
        update={
            "total_steps": derived_steps,
            "trajectory": slim_traj,
            # ``decisions`` duplicates ``trajectory.reviews``; dashboards read the
            # trajectory copy, so drop it to keep ``?summary=1`` responses small.
            "decisions": [],
            "steps": [],
            "turns": [],
            "verifier_output": None,
        }
    )


def _list_watcher_sessions_sync(
    agent_type: str | None,
    status: str | None,
    min_messages: int,
    limit: int,
    summary: bool,
) -> list[Session]:
    if os.getenv("OPENEVAL_AUTO_SCAN_LOCAL_LOGS", "0").lower() in (
        "1",
        "true",
        "yes",
    ):
        from server.agent_log_loader import UniversalAgentLogLoader

        UniversalAgentLogLoader.scan_default_agent_directories()

    store = get_watcher_store()
    store.reload_from_disk()
    sessions = store.list_sessions(
        agent_type=agent_type,
        status=status,
        limit=limit,
        summary=summary,
        min_messages=min_messages,
    )
    # ``store.list_sessions`` applies the ``min_messages`` gate to DB payloads
    # before it strips summary blobs. Re-filtering an already-stripped session
    # would drop message-only DB sessions, so only the unstripped fallback
    # sessions (disk/in-memory) still need the post-filter here.
    if min_messages > 0 and not summary:
        sessions = [
            s
            for s in sessions
            if (
                len(s.trajectory.messages if s.trajectory else []) >= min_messages
                or len(s.trajectory.tool_calls if s.trajectory else []) >= min_messages
                or len(s.trajectory.reviews if s.trajectory else []) >= min_messages
            )
        ]
    if summary:
        return [_to_session_summary(s) for s in sessions]
    return sessions


@app.get("/api/v1/watcher/sessions", response_model=list[Session])
async def list_watcher_v1_sessions(
    agent_type: str | None = None,
    status: str | None = None,
    min_messages: int = 0,
    limit: int = Query(default=100, ge=1, le=200),
    summary: bool = Query(
        default=False,
        description="When true, return session metadata + reviews without heavy message/tool blobs.",
    ),
) -> list[Session]:
    """List monitored agent sessions from WatcherStore."""
    return await asyncio.to_thread(
        _list_watcher_sessions_sync, agent_type, status, min_messages, limit, summary
    )


@app.post("/api/v1/watcher/sessions/purge-empty")
async def purge_empty_watcher_sessions(min_messages: int = 1) -> dict[str, Any]:
    """Purge fake/test sessions that have fewer than min_messages from memory, PostgreSQL, and disk."""
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
    """Evaluate an agent tool call against active 67 command rules and thresholds."""
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
    model: str = "google/gemini-3.5-flash-lite"
    session_id: str | None = None
    scope: Literal["session", "all"] = "session"


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
        session_id=payload.session_id,
        scope=payload.scope,
    )
    return [m.model_dump() for m in matches]


class IngestAgentLogPayload(BaseModel):
    file_path: str | None = None
    file_content: str | None = None
    filename: str | None = None
    agent_type: Literal["claude_code", "cursor", "antigravity", "codex", "opencode", "auto"] = (
        "auto"
    )
    working_dir: str | None = None
    is_demo: bool = False


def _apply_demo_tenant_context() -> None:
    current_user_slug.set("demo")
    current_user_id.set(_resolve_demo_user_id())


@app.post("/api/v1/agent-logs/ingest")
async def ingest_agent_log(payload: IngestAgentLogPayload, request: Request) -> dict[str, Any]:
    """Ingest Claude Code, Cursor, or Antigravity session logs into canonical Watcher sessions.

    Supports both client-side file uploads (via `file_content` + `filename`) and local server files (via `file_path`).
    """
    import shutil
    import tempfile

    from server.agent_log_loader import UniversalAgentLogLoader

    surface_header = (
        (
            request.headers.get("X-OpenEval-Surface")
            or request.headers.get("x-openeval-surface")
            or ""
        )
        .strip()
        .lower()
    )
    is_demo = payload.is_demo or surface_header == "demo" or current_user_slug.get() == "demo"
    if is_demo:
        _apply_demo_tenant_context()
        if payload.file_content is None and payload.file_path:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Server-side file_path ingestion is disabled in demo mode; "
                    "upload file_content instead."
                ),
            )
    elif payload.file_content is None and payload.file_path:
        # Server-side file reads are never allowed on a hosted deployment, even for an
        # authenticated tenant: otherwise any user could read /etc/openeval.env,
        # /etc/passwd, or another tenant's logs by supplying an arbitrary path.
        if is_production_env():
            raise HTTPException(
                status_code=403,
                detail=(
                    "Server-side file_path ingestion is disabled in production; "
                    "upload file_content instead."
                ),
            )
        # Local development: filesystem reads must come from an authenticated tenant,
        # never an anonymous caller that slipped past the demo surface checks.
        if current_user_id.get() is None:
            raise HTTPException(
                status_code=401,
                detail="Authentication required for server-side file_path ingestion.",
            )

    temp_dir = None
    if payload.file_content is not None:
        filename = payload.filename or "uploaded_agent_session.jsonl"
        safe_filename = Path(filename).name
        temp_dir = Path(tempfile.mkdtemp(prefix="openeval_ingest_"))
        path = temp_dir / safe_filename
        path.write_text(payload.file_content, encoding="utf-8")
    elif payload.file_path:
        path = Path(payload.file_path)
        if not path.exists():
            raise HTTPException(
                status_code=404, detail=f"Log path '{payload.file_path}' does not exist"
            )
    else:
        raise HTTPException(
            status_code=400,
            detail="Must provide either 'file_content' (file upload) or 'file_path'",
        )

    try:
        session = UniversalAgentLogLoader.ingest_agent_log_file(
            path,
            agent_type=payload.agent_type,
            default_status="completed",
        )

        if not session:
            raise HTTPException(
                status_code=400, detail="Failed to parse agent log into canonical session format"
            )

        if is_demo:
            session.org_id = "demo"

        if payload.working_dir and payload.working_dir.strip():
            requested_dir = payload.working_dir.strip()
            if not session.working_dir or session.working_dir in ("/app", "/workspace"):
                session.working_dir = requested_dir
                session.working_directory = requested_dir
                # Re-record so persisted/domain stores pick up the corrected workspace.
                get_watcher_store().record_session(session)

        return {
            "status": "ingested",
            "session": session.model_dump(),
            "is_demo": is_demo,
        }
    finally:
        if temp_dir and temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


@app.post("/api/v1/agent-logs/scan-local")
async def scan_and_ingest_local_agent_logs(
    limit: int = Query(default=100, ge=1, le=500),
    agent: str = Query(default="all"),
) -> dict[str, Any]:
    """Scan local Antigravity, Claude Code, and Cursor directories on the host machine and ingest sessions for active user."""
    from scripts.ingest_local_agent_logs import (
        discover_antigravity_transcripts,
        discover_claude_transcripts,
        discover_cursor_transcripts,
    )
    from server.agent_log_loader import UniversalAgentLogLoader, get_antigravity_titles

    UniversalAgentLogLoader._ag_titles = get_antigravity_titles()
    ingested_sessions: list[str] = []
    now = time.time()

    if agent in ("all", "antigravity"):
        for idx, t_path in enumerate(discover_antigravity_transcripts()[:limit], 1):
            age_sec = now - t_path.stat().st_mtime
            is_active = (age_sec < 45 * 60) or (idx == 1 and age_sec < 90 * 60)
            status_val = "working" if is_active else "completed"
            s = UniversalAgentLogLoader.ingest_antigravity_transcript(t_path, status=status_val)  # type: ignore[arg-type]
            if s:
                ingested_sessions.append(s.session_id)

    if agent in ("all", "cursor"):
        for p in discover_cursor_transcripts()[:limit]:
            s = UniversalAgentLogLoader.ingest_cursor_agent_transcript_jsonl(p)
            if s:
                ingested_sessions.append(s.session_id)

    if agent in ("all", "claude_code"):
        for p in discover_claude_transcripts()[:limit]:
            s = UniversalAgentLogLoader.ingest_claude_code_jsonl(p)
            if s:
                ingested_sessions.append(s.session_id)

    return {
        "status": "success",
        "ingested_count": len(ingested_sessions),
        "session_ids": ingested_sessions,
    }


# NOTE: Annotation endpoints below are API-only and NOT enabled in the UI (no frontend caller).
@app.post("/api/v1/sessions/{session_id}/annotate")
async def annotate_session(
    session_id: str,
    payload: AnnotationPayload,
) -> dict[str, Any]:
    """Collaborative human annotation, verdict overrides, and audit trails."""
    from server.annotation_service import AnnotationService

    session = AnnotationService.annotate_session(session_id, payload)
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
    """Retrieve high-level fleet overview metrics from the Watcher store."""
    store = get_watcher_store()
    return store.get_analytics_overview()


class GraderBacktestRequest(BaseModel):
    grader_name: str = "SituationalAwarenessGrader"
    preset_mode: str = "lean"
    custom_prompt_template: str | None = None
    custom_overrides: dict[str, str] | None = None
    ensemble_models: list[str] | None = None


@app.get("/api/v1/watcher/graders/definitions")
async def list_grader_definitions() -> list[dict[str, Any]]:
    """Retrieve the 11 Canonical Graders with 15-part prompt definitions for the active user."""

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

    store = get_watcher_store()
    updated = store.update_grader_definition(grader_name, payload.updates)
    if not updated:
        raise HTTPException(status_code=404, detail=f"Grader '{grader_name}' not found")
    return updated.model_dump()


@app.post("/api/v1/watcher/graders/definitions/{grader_name}/reset")
async def reset_grader_definition_endpoint(grader_name: str) -> dict[str, Any]:
    """Reset a canonical grader's prompt components back to defaults for active user."""

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
        preset_mode=req.preset_mode,
        custom_prompt_template=req.custom_prompt_template,
        custom_overrides=req.custom_overrides,
        ensemble_models=req.ensemble_models,
    )
    return report.model_dump()


class GraderAuditSessionRequest(BaseModel):
    session_id: str
    grader_name: str
    preset_mode: str = "lean"
    custom_prompt_template: str | None = None
    custom_overrides: dict[str, str] | None = None
    model: str = "gemini-3.5-flash-lite"


@app.post("/api/v1/watcher/graders/audit-session")
async def audit_session_with_grader(req: GraderAuditSessionRequest) -> dict[str, Any]:
    """Audit a real monitored agent session using a canonical safety grader."""
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
    if not lines and hasattr(session, "steps") and session.steps:
        for step in session.steps:
            thought = step.get("thought") or (step.get("action", {}) or {}).get("thought")
            if thought:
                lines.append(f"[THOUGHT]: {thought[:300]}")
            action = step.get("action", {}) or {}
            tool = action.get("tool") or action.get("tool_name")
            cmd = action.get("command") or action.get("arguments") or action.get("content")
            if tool:
                lines.append(f"[TOOL CALL]: {tool} with input: {cmd}")
            obs = step.get("observation")
            if obs:
                lines.append(f"[TOOL RESULT]: {obs[:200]}")
    trajectory_text = (
        "\n".join(lines) if lines else f"Session {session.session_id} in {session.working_dir}"
    )

    grader = store.get_grader_definition(req.grader_name)
    if not grader:
        raise HTTPException(status_code=404, detail="Grader not found")

    if req.preset_mode == "compressed":
        grader = grader.to_compressed()
    if req.custom_overrides:
        grader = grader.model_copy(update=req.custom_overrides)
    if req.custom_prompt_template:
        grader = grader.model_copy(update={"full_prompt_override": req.custom_prompt_template})
    elif req.custom_overrides and "full_prompt_override" in req.custom_overrides:
        grader = grader.model_copy(
            update={"full_prompt_override": req.custom_overrides["full_prompt_override"]}
        )

    runner = AsyncLLMRunner()
    cfg = LLMConfig(
        model=req.model,
        temperature=0.0,
        thinking_budget=0,
        max_tokens=1500,
        max_retries=2,
        timeout_sec=45.0,
    )
    try:
        verdict = await grader.evaluate(
            trajectory_text, runner, config=cfg, preset_mode=req.preset_mode
        )
    except Exception as exc:
        err_str = str(exc)
        logger.warning("Live session audit failed on model %s: %s", req.model, exc)
        status_code = (
            429
            if (
                "429" in err_str
                or "quota" in err_str.lower()
                or "rate limit" in err_str.lower()
                or "resource_exhausted" in err_str.lower()
            )
            else 502
        )
        raise HTTPException(
            status_code=status_code,
            detail=f"Live Session Audit failed on {req.model}: {exc}",
        ) from exc
    finally:
        await runner.close()
    return verdict.model_dump()


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
        "codex_watcher_hook.py",
        "opencode_watcher_hook.py",
        "install_opencode_watcher_hook.sh",
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
