"""Watcher safety gate endpoints for tool call evaluation, results, and resolution.

Extracts gate evaluate, result, resolve, and resolve-all routes from server/app.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, model_validator

from engine.approval_policy import (
    WatcherRequest,
    WatcherVerdict,
    global_watcher_engine,
)
from engine.redaction import redact_payload
from schemas.watcher_models import ProvenanceMetadata, ToolCall, ToolResult
from server.policy_gateway import PolicyGateway
from server.request_context import get_request_id
from server.watcher_store import get_watcher_store
from server.webhook import emit_watcher_webhook

router = APIRouter(prefix="/api/v1/watcher", tags=["watcher"])
logger = logging.getLogger("openeval.server.watcher_gate")


class WatcherResultRequest(BaseModel):
    session_id: str | None = None
    agent_id: str = "unknown"
    tool_name: str | None = None
    stdout: str | None = ""
    stderr: str | None = ""
    exit_code: int | None = 0
    tool_id: str | None = None
    review_id: str | None = None
    error: str | None = None


class ResolveInterceptionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: str | None = None
    review_id: str | None = None
    resolution: str | None = None
    action: str | None = None
    note: str | None = None
    notes: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "resolution" not in data and "action" in data:
                data["resolution"] = data["action"]
            if "note" not in data and "notes" in data:
                data["note"] = data["notes"]
            if not data.get("resolution"):
                data["resolution"] = "human_approved"
        return data


class ResolveInterceptionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str = "ok"
    session_id: str | None = None
    review_id: str | None = None
    resolution: str
    resolved_count: int
    timestamp: str


class ResolveAllReviewsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str = "ok"
    resolved_count: int
    timestamp: str


def _record_operator_resolution(item: Any, *, allow: bool, note: str) -> None:
    """Record an operator override without rewriting the original decision or score."""
    stamp = datetime.now(UTC).isoformat()
    item.human_override = "allow" if allow else "deny"
    item.resolution_status = "human_approved" if allow else "blocked"
    marker = f"[Operator {stamp}: {note}]"
    if hasattr(item, "explanation"):
        current = getattr(item, "explanation", "") or ""
        if marker not in current:
            item.explanation = f"{current} {marker}".strip()
    if hasattr(item, "reason"):
        current = getattr(item, "reason", "") or ""
        if marker not in current:
            item.reason = f"{current} {marker}".strip()


def _history_belongs_to_tenant(item: Any, uid: int | None) -> bool:
    """In-memory verdicts are updated only for the active tenant."""
    return getattr(item, "user_id", None) == uid


def _active_tenant_id() -> int | None:
    """Tenant id for the current request, resolved from the database when needed."""
    from server.db import current_user_id, get_current_user_id

    try:
        return get_current_user_id()
    except Exception:
        return current_user_id.get()


@router.post("/evaluate", response_model=WatcherVerdict)
async def evaluate_action_watcher_gateway(
    req: WatcherRequest,
    request: Request,
) -> WatcherVerdict:
    """Hook gateway: store PolicyGateway (rules + 1–10 thresholds) + mode only.

    Float deny/flag fields on WatcherConfig are legacy and do not affect decisions.
    """
    from server.app import broadcast_watcher_event

    db_ready = getattr(request.app.state, "db_ready", True)
    if not db_ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection is not ready.",
        )

    store = get_watcher_store()
    cfg = store.get_user_config()

    tenant_id = _active_tenant_id()

    # Paused mode: never block
    if cfg.mode == "paused":
        verdict = WatcherVerdict(
            decision="allow",
            stage="fallback",
            reason="Watcher paused — all actions allowed",
            risk_score=0.0,
            latency_ms=0.0,
            rule_violation_tag=None,
            mode_applied="paused",
            shadow_decision=None,
            is_safe=True,
            action_preview=f"{req.tool_name}",
            agent_id=req.agent_id or "unknown",
            user_id=tenant_id,
            provenance=req.provenance or ProvenanceMetadata(),
        )
        await broadcast_watcher_event("interception_event", verdict.model_dump())
        return verdict

    cmd = ""
    tool_action = ""
    path = ""
    if req.arguments:
        cmd = str(
            req.arguments.get("CommandLine")
            or req.arguments.get("command")
            or req.arguments.get("cmd")
            or req.arguments.get("command_line")
            or ""
        )
        tool_action = str(
            req.arguments.get("toolAction")
            or req.arguments.get("toolSummary")
            or req.arguments.get("Description")
            or ""
        )
        path = str(
            req.arguments.get("AbsolutePath")
            or req.arguments.get("TargetFile")
            or req.arguments.get("path")
            or req.arguments.get("file_path")
            or req.arguments.get("file")
            or ""
        )

    if not cmd and req.tool_input:
        cmd = req.tool_input

    if cmd:
        full_cmd = cmd
    elif req.arguments:
        try:
            full_cmd = json.dumps(req.arguments, indent=2)
        except Exception:
            full_cmd = str(req.arguments)
    else:
        full_cmd = req.tool_name

    command_tool_names = {
        "bash",
        "shell",
        "powershell",
        "terminal",
        "run_command",
        "execute_command",
        "cmd",
        "exec",
        "sh",
        "zsh",
        "execute_bash",
    }
    file_tool_names = {
        "write_to_file",
        "write_file",
        "edit_file",
        "replace_file_content",
        "create_file",
        "view_file",
        "read_file",
    }
    is_cmd_tool = (req.tool_name.lower() in command_tool_names) or (
        bool(cmd) and req.tool_name.lower() not in file_tool_names
    )

    tool_input = cmd or full_cmd if is_cmd_tool else path or req.tool_name

    diff_content = None
    if isinstance(req.arguments, dict):
        diff_content = (
            req.arguments.get("diff")
            or req.arguments.get("contents")
            or req.arguments.get("content")
            or req.arguments.get("CodeContent")
            or req.arguments.get("ReplacementContent")
        )
        if diff_content and not isinstance(diff_content, str):
            diff_content = str(diff_content)

    # Compute human-readable action title / summary
    if tool_action:
        action_preview = f"{req.tool_name}: {tool_action}"
    elif cmd:
        cmd_first = cmd.strip().splitlines()[0]
        action_preview = (
            f"{req.tool_name}: {cmd_first}"
            if not cmd_first.startswith(req.tool_name)
            else cmd_first
        )
    elif path:
        action_preview = f"{req.tool_name} {Path(path).name}"
    else:
        action_preview = req.tool_name

    # Resolve session before policy eval so reviews attach to a real session id
    session_id = req.session_id or f"session-live-{int(time.time() * 1000) % 100000000:08d}"

    # Rate Limiting check against runaway agent retry loops
    if os.getenv("OPENEVAL_DISABLE_RATE_LIMIT") != "1":
        from server.rate_limiter import get_rate_limiter

        rate_limiter = get_rate_limiter()
        rate_res = rate_limiter.check(session_id)
        if not rate_res.allowed:
            verdict = WatcherVerdict(
                decision="deny",
                stage="fallback",
                reason=f"Rate limit exceeded: agent burst throttled to prevent runaway loop (retry in {rate_res.retry_after_ms}ms)",
                risk_score=0.8,
                latency_ms=0.5,
                rule_violation_tag="RULE_RUNAWAY_BURST_RATE_LIMIT",
                mode_applied=cfg.mode,
                shadow_decision=None,
                is_safe=False,
                action_preview=f"{req.tool_name} [RATE LIMITED]",
                agent_id=req.agent_id or "unknown",
                user_id=tenant_id,
                provenance=req.provenance or ProvenanceMetadata(),
            )
            await broadcast_watcher_event("interception_event", verdict.model_dump())
            return verdict

    # Context Stripping Optimization:
    # Trajectory context boosts monitor fidelity, but tool outputs are stripped to minimize token overhead and latency.
    traj_context = ""
    try:
        store = get_watcher_store()
        existing_session = store.get_session(session_id)
        if existing_session and existing_session.trajectory:
            from server.policy_gateway import format_stripped_trajectory

            traj_context = format_stripped_trajectory(existing_session.trajectory)
    except Exception:
        pass

    # Shared gateway keeps the LLM circuit breaker across requests.
    # Evaluation runs off the event loop so a slow model cannot stall other hooks.
    gw = PolicyGateway.get_shared()
    rec = await asyncio.to_thread(
        gw.evaluate_tool_call,
        session_id,
        req.tool_name,
        tool_input,
        diff_content,
        req.thought_context or "",
        False,
        traj_context,
        req.provenance,
    )

    # Decision persisted after session ensure (below) so record_decision is not a no-op.

    mapped_decision = "allow"
    if rec.decision in ("block", "deny"):
        mapped_decision = "deny"
    elif rec.decision in ("escalate", "warn"):
        mapped_decision = "escalate"

    req_id = get_request_id()
    rec.request_id = req_id

    client_decision: str | None = None
    terminal_reason: str | None = None
    if mapped_decision == "deny":
        client_decision = "force_ask"
        if cfg.mode == "enforce":
            terminal_reason = "lockout"
    elif mapped_decision == "escalate":
        client_decision = "force_ask"

    verdict = WatcherVerdict(
        decision=mapped_decision,  # type: ignore[arg-type]
        stage=(
            "stage_2_deterministic"
            if rec.stage in ("rule", "threshold")
            else "stage_3_triage"
            if rec.stage == "triage"
            else "stage_4_deep_review"
        ),
        reason=rec.explanation or f"Policy: {rec.rule_name or rec.stage}",
        risk_score=float(rec.score) / 10.0 if rec.score else 0.5,
        latency_ms=rec.latency_ms,
        rule_violation_tag=rec.rule_name,
        mode_applied=cfg.mode,
        shadow_decision=None,
        is_safe=mapped_decision == "allow",
        action_preview=action_preview,
        agent_id=req.agent_id or "unknown",
        review_id=rec.id,
        session_id=session_id,
        human_override=None,
        resolution_status="pending",
        full_command=full_cmd,
        tool_result="Execution blocked by Watcher safety gate."
        if mapped_decision in ("deny", "block")
        else None,
        threat_category=rec.threat_category,
        user_id=tenant_id,
        client_decision=client_decision,
        terminal_reason=terminal_reason,
        provenance=rec.provenance,
        consent_disclosure=rec.consent_disclosure,
        request_id=req_id,
    )

    # Observe mode: never deny, but record shadow decision
    if cfg.mode == "observe":
        verdict = WatcherVerdict(
            decision="allow",
            stage=verdict.stage,
            reason=f"[observe] would {mapped_decision}: {verdict.reason}",
            risk_score=verdict.risk_score,
            latency_ms=verdict.latency_ms,
            rule_violation_tag=verdict.rule_violation_tag,
            mode_applied="observe",
            shadow_decision=mapped_decision,  # type: ignore[arg-type]
            is_safe=True,
            action_preview=verdict.action_preview,
            agent_id=verdict.agent_id,
            review_id=rec.id,
            session_id=session_id,
            human_override=None,
            resolution_status="pending",
            full_command=full_cmd,
            tool_result=None,
            threat_category=rec.threat_category,
            user_id=tenant_id,
            provenance=rec.provenance,
            consent_disclosure=rec.consent_disclosure,
            request_id=req_id,
        )

    await broadcast_watcher_event("interception_event", redact_payload(verdict.model_dump()))

    if verdict.decision in ("deny", "escalate", "reject") or (
        cfg.mode == "observe" and mapped_decision in ("deny", "escalate")
    ):
        emit_watcher_webhook(
            "watcher.decision",
            {
                "decision": verdict.decision,
                "shadow_decision": getattr(verdict, "shadow_decision", None),
                "reason": verdict.reason,
                "tool_name": req.tool_name,
                "agent_id": req.agent_id,
                "risk_score": verdict.risk_score,
                "rule": verdict.rule_violation_tag,
                "provenance": verdict.provenance.model_dump() if verdict.provenance else None,
                "consent_disclosure": verdict.consent_disclosure.model_dump()
                if verdict.consent_disclosure
                else None,
            },
        )

    # Persist live-gate session + decision into WatcherStore for the current user
    try:
        agent_id = req.agent_id or "antigravity"
        await asyncio.to_thread(
            store.ensure_live_session,
            session_id,
            agent_type=agent_id,
            title=f"[{agent_id}] Live gate ({session_id[:8]})",
        )
        tc = ToolCall(
            tool_name=req.tool_name,
            arguments=req.arguments or {},
            raw_input=tool_input or cmd or req.tool_name,
        )
        await asyncio.to_thread(store.append_trajectory_event, session_id, tc)
        await store.arecord_decision(rec)
        activity = verdict.decision
        if cfg.mode == "observe" and mapped_decision != "allow":
            activity = f"shadow:{mapped_decision}"
        new_status = (
            "blocked" if mapped_decision in ("deny", "reject", "block", "escalate") else "working"
        )
        await asyncio.to_thread(
            store.update_session,
            session_id,
            {
                "current_activity": f"{req.tool_name} → {activity}",
                "status": new_status,
            },
        )
        global_watcher_engine.record_interception(verdict)
    except Exception as exc:
        logger.debug("Failed persisting live-gate session %s: %s", session_id, exc)

    return verdict


@router.post("/result")
async def record_tool_result_watcher(req: WatcherResultRequest) -> dict[str, Any]:
    """Report execution stdout/stderr from PostToolUse agent hooks back to Watcher immediately."""
    from server.app import broadcast_watcher_event

    parts: list[str] = []
    if req.stdout:
        parts.append(req.stdout.strip())
    if req.stderr:
        parts.append(f"[stderr]\n{req.stderr.strip()}")
    if not parts and req.error:
        parts.append(f"Error: {req.error.strip()}")

    if parts:
        tool_result_text = "\n".join(parts)
    elif req.exit_code is not None and req.exit_code != 0:
        tool_result_text = f"Command exited with code {req.exit_code}"
    else:
        tool_result_text = "Command completed (exit code 0)"

    # 1. Update in-memory live engine history
    updated_live = False
    for item in reversed(global_watcher_engine._interception_history):
        match_rev = req.review_id and item.review_id == req.review_id
        match_sess = req.session_id and item.session_id == req.session_id
        if match_rev or (
            match_sess and (not item.tool_result or "Awaiting" in str(item.tool_result))
        ):
            item.tool_result = tool_result_text
            updated_live = True
            break
    if not updated_live and global_watcher_engine._interception_history and not req.session_id:
        last = global_watcher_engine._interception_history[-1]
        if not last.tool_result or "Awaiting" in str(last.tool_result):
            last.tool_result = tool_result_text

    # 2. Update canonical store session via append-only event logging
    store = get_watcher_store()
    if req.session_id:
        tr = ToolResult(
            tool_id=req.tool_id or str(uuid4())[:8],
            tool_name=req.tool_name or "tool",
            stdout=req.stdout or "",
            stderr=req.stderr or (req.error or ""),
            exit_code=req.exit_code or 0,
        )
        store.record_tool_result(req.session_id, tr)

    # 3. Broadcast SSE update so frontend live stream updates immediately
    await broadcast_watcher_event(
        "tool_result_event",
        {
            "session_id": req.session_id,
            "review_id": req.review_id,
            "tool_name": req.tool_name,
            "tool_result": tool_result_text,
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )

    return {
        "status": "recorded",
        "session_id": req.session_id,
        "tool_result": tool_result_text,
    }


@router.post("/gate/resolve", response_model=ResolveInterceptionResponse)
async def resolve_watcher_interception(
    req: ResolveInterceptionRequest,
) -> ResolveInterceptionResponse:
    """Resolve an intercepted tool call after operator authorization (PostToolUse or UI override)."""
    from server.app import (
        _is_interception_candidate,
        broadcast_watcher_event,
    )

    store = get_watcher_store()
    resolved_count = 0
    uid = _active_tenant_id()
    approved = req.resolution in ("human_approved", "allow")
    note = req.note or ("operator approved" if approved else "operator blocked")

    # 1. Update in-memory live engine history ONLY for this tenant's interceptions.
    # Original decision and risk_score stay intact for the audit trail.
    for item in global_watcher_engine._interception_history:
        if not _history_belongs_to_tenant(item, uid):
            continue
        match_sess = not req.session_id or item.session_id == req.session_id
        match_rev = not req.review_id or item.review_id == req.review_id
        if match_sess and match_rev and _is_interception_candidate(item):
            _record_operator_resolution(item, allow=approved, note=note)
            resolved_count += 1

    # 2. Update canonical WatcherStore sessions already scoped to this user via background thread.
    def _update_store_sessions() -> int:
        target_sessions = (
            [store.get_session(req.session_id)]
            if req.session_id
            else store.list_sessions(limit=200)
        )
        count = 0
        for sess in target_sessions:
            if not sess:
                continue
            updated = False
            for r in sess.trajectory.reviews:
                match_rev = not req.review_id or r.id == req.review_id
                if match_rev and _is_interception_candidate(r):
                    original_decision = r.decision
                    original_score = r.score
                    _record_operator_resolution(r, allow=approved, note=note)
                    r.decision = original_decision
                    r.score = original_score
                    updated = True
                    count += 1
            if updated:
                store.record_session(sess)
        return count

    resolved_count += await asyncio.to_thread(_update_store_sessions)

    payload = {
        "session_id": req.session_id,
        "review_id": req.review_id,
        "resolution": req.resolution,
        "resolved_count": resolved_count,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    await broadcast_watcher_event("interception_resolved", payload)
    return ResolveInterceptionResponse(status="ok", **payload)


@router.post("/reviews/resolve-all", response_model=ResolveAllReviewsResponse)
async def resolve_all_watcher_reviews() -> ResolveAllReviewsResponse:
    """Resolve/allow all currently blocked or escalated reviews across all sessions for clarity."""
    from server.app import (
        _is_interception_candidate,
        broadcast_watcher_event,
    )

    store = get_watcher_store()
    resolved_count = 0
    uid = _active_tenant_id()
    note = "Operator allowed for clarity"

    # 1. Update in-memory engine history only for this tenant.
    for item in global_watcher_engine._interception_history:
        if not _history_belongs_to_tenant(item, uid):
            continue
        if _is_interception_candidate(item):
            _record_operator_resolution(item, allow=True, note=note)
            resolved_count += 1

    # 2. WatcherStore.list_sessions is already WHERE user_id = active tenant.
    def _resolve_all_store() -> int:
        count = 0
        for sess in store.list_sessions(limit=200):
            updated = False
            for r in sess.trajectory.reviews:
                if _is_interception_candidate(r):
                    original_decision = r.decision
                    original_score = r.score
                    _record_operator_resolution(r, allow=True, note=note)
                    r.decision = original_decision
                    r.score = original_score
                    updated = True
                    count += 1
            if updated:
                if sess.status in ("failed", "error"):
                    sess.status = "completed"
                store.record_session(sess)
        return count

    resolved_count += await asyncio.to_thread(_resolve_all_store)

    payload = {
        "resolved_count": resolved_count,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    await broadcast_watcher_event("interception_resolved", payload)
    return ResolveAllReviewsResponse(status="ok", **payload)


_DEFAULT_STREAM_REQUEST: Any = None


@router.get("/stream")
async def stream_watcher_events(
    request: Request = _DEFAULT_STREAM_REQUEST,
    last_event_id: str | None = Query(default=None, alias="last_event_id"),
) -> StreamingResponse:
    """Server-Sent Events (SSE) endpoint streaming live Watcher telemetry & interceptions via Redis Streams."""
    from server.app import (
        _enrich_verdict_dict,
        _verdicts_from_store_reviews,
        sse_event_visible_to_user,
    )
    from server.auth import current_user_id, current_user_slug
    from server.event_bus import get_event_bus

    bus = get_event_bus()
    uid = current_user_id.get()
    if uid is None:
        try:
            uid = _active_tenant_id()
        except Exception:
            uid = None

    header_last_id = None
    if request is not None:
        header_last_id = request.headers.get("Last-Event-ID") or request.headers.get(
            "last-event-id"
        )
    last_id_str = last_event_id if isinstance(last_event_id, str) else None
    effective_last_id = (header_last_id or last_id_str or "").strip()

    async def sse_generator() -> AsyncGenerator[str, None]:
        store = get_watcher_store()
        stream_cursor = effective_last_id

        if effective_last_id:
            # Replay missed backlog from Redis Streams using XRANGE (<last_event_id> + COUNT 100
            missed_events = await bus.xrange(uid, effective_last_id, count=100)
            for stream_id, fields in missed_events:
                event_uid = fields.get("user_id")
                if not sse_event_visible_to_user(
                    int(str(event_uid)) if str(event_uid).isdigit() else None, uid
                ):
                    continue
                ev_type = fields.get("event", "message")
                ev_data = fields.get("data", {})
                data_str = json.dumps(ev_data) if not isinstance(ev_data, str) else ev_data
                yield f"id: {stream_id}\nevent: {ev_type}\ndata: {data_str}\n\n"
                stream_cursor = stream_id
        else:
            # Seed with in-memory live history + recent store blocks (survives API restart)
            live_hist = [
                v.model_dump()
                for v in global_watcher_engine.get_history(20)
                if (v.user_id is None and current_user_slug.get() in ("owner", "demo"))
                or (v.user_id is not None and v.user_id == uid)
            ]
            if current_user_slug.get() == "demo":
                live_hist = [
                    v
                    for v in live_hist
                    if "demo" in str(v.get("session_id", ""))
                    or str(v.get("session_id", "")).startswith("demo-")
                ]
            store_hist = await asyncio.to_thread(_verdicts_from_store_reviews, 20)
            seen: set[str] = set()
            history: list[dict[str, Any]] = []
            for row in live_hist + store_hist:
                key = f"{row.get('agent_id')}|{row.get('decision')}|{row.get('action_preview')}|{row.get('timestamp')}"
                if key in seen:
                    continue
                seen.add(key)
                history.append(_enrich_verdict_dict(row))
                if len(history) >= 40:
                    break
            init_data = {
                "status": "connected",
                "config": store.get_user_config().model_dump(),
                "history": history,
            }
            tail_id = await bus.get_latest_stream_id(uid)
            yield f"event: connected\ndata: {json.dumps(init_data)}\n\n"
            stream_cursor = tail_id

        # Continuous streaming via Redis Streams XREAD loop with 15-second heartbeat
        try:
            while True:
                try:
                    entries = await bus.xread(uid, stream_cursor, count=50, block_ms=15000)
                    if not entries:
                        yield "event: ping\ndata: {}\n\n"
                        continue

                    for stream_id, fields in entries:
                        stream_cursor = stream_id
                        event_uid = fields.get("user_id")
                        if not sse_event_visible_to_user(
                            int(str(event_uid)) if str(event_uid).isdigit() else None, uid
                        ):
                            continue
                        ev_type = fields.get("event", "message")
                        ev_data = fields.get("data", {})
                        data_str = json.dumps(ev_data) if not isinstance(ev_data, str) else ev_data
                        yield f"id: {stream_id}\nevent: {ev_type}\ndata: {data_str}\n\n"
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.debug("Error in SSE stream loop: %s", exc)
                    break
        finally:
            pass

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
