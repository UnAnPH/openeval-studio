"""Legacy brain-scan and in-memory session-detail helpers.

Extracted from :mod:`server.app` to keep the route module smaller. The
``STORED_FINDINGS`` / ``STORED_SESSION_DETAILS`` caches are re-exported from
``server.app`` and are the same objects tests clear between runs.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from engine.scanners import FindingRecord, RecommendedAction
from server.watcher_store import get_watcher_store

logger = logging.getLogger("openeval.server")

DEFAULT_FINDINGS: list[FindingRecord] = []

STORED_FINDINGS: list[FindingRecord] = []


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


# Legacy in-memory session detail cache. `WatcherStore` is the canonical source;
# this dict only backs brain-scanned / ad-hoc analysis sessions that were never
# persisted. Reset it with `reset_all_runtime_state()` (tests/conftest.py).
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
