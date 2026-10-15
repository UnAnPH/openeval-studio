"""Watcher policy and runtime configuration endpoints.

Extracts watcher mode config, analyzer summary, command rules,
and tool thresholds from server/app.py into an APIRouter.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from engine.approval_policy import WatcherConfig
from schemas.watcher_models import Policy
from server.auth import current_user_id
from server.watcher_store import get_watcher_store

router = APIRouter(prefix="/api/v1/watcher", tags=["watcher"])
logger = logging.getLogger("openeval.server.watcher_policy")


class FleetOverviewResponse(BaseModel):
    """Fleet-wide analytics summary for the watcher analyzer."""

    org_id: str
    session_count: int
    blocked_decisions: int
    escalated_decisions: int
    fleet: dict[str, object] = Field(default_factory=dict)


class RuleUpdatePayload(BaseModel):
    """Payload for updating an individual command rule action."""

    rule_name: str
    action: Literal["allow", "triage", "human", "deny", "off"]


class ThresholdUpdatePayload(BaseModel):
    """Payload for updating score thresholds on a single tool."""

    tool_name: str
    auto_approve: bool = False
    escalate_ge: int | None = None
    auto_deny_ge: int | None = None
    always_escalate: bool = False
    auto_approve_le: int = 3


class ResetPolicyResponse(BaseModel):
    """Response returned when policy rules are reset to default."""

    status: Literal["reset"] = "reset"
    policy: Policy


class UpdateRuleResponse(BaseModel):
    """Response returned when a command rule action is updated."""

    status: Literal["updated"] = "updated"
    rule_name: str
    action: str


class UpdateThresholdResponse(BaseModel):
    """Response returned when a tool threshold is updated."""

    status: Literal["updated"] = "updated"
    threshold: ThresholdUpdatePayload


class UpdateThresholdsResponse(BaseModel):
    """Response returned when multiple tool thresholds are bulk updated."""

    status: Literal["updated"] = "updated"
    count: int


@router.get("/config", response_model=WatcherConfig)
async def get_watcher_config() -> WatcherConfig:
    """Get Watcher mode settings for active user. Thresholds live in /api/v1/watcher/policy."""
    return get_watcher_store().get_user_config()


@router.get("/analyzer", response_model=FleetOverviewResponse)
async def watcher_analyzer_lite() -> FleetOverviewResponse:
    """Analyzer-lite: org-level blocked/escalated counts from WatcherStore."""
    store = get_watcher_store()
    overview = store.get_analytics_overview()
    fleet_dict: dict[str, object] = dict(overview)
    return FleetOverviewResponse(
        org_id="default_org",
        session_count=int(overview.get("total_sessions", 0)),
        blocked_decisions=int(overview.get("blocked_incidents", 0)),
        escalated_decisions=int(overview.get("escalated_count", 0)),
        fleet=fleet_dict,
    )


@router.post("/config", response_model=WatcherConfig)
async def update_watcher_config(new_config: WatcherConfig) -> WatcherConfig:
    """Update runtime mode (enforce/observe/paused) for active user."""
    from server.app import broadcast_watcher_event

    store = get_watcher_store()
    updated = store.update_user_config(new_config)
    cfg_dict = updated.model_dump()
    cfg_dict["user_id"] = current_user_id.get()
    await broadcast_watcher_event("config_update", cfg_dict)
    return updated


@router.get("/policy", response_model=Policy)
async def get_watcher_policy() -> Policy:
    """Retrieve active policy configuration, 63 command rules, and tool thresholds."""
    store = get_watcher_store()
    return store.get_default_policy()


@router.post("/policy/reset", response_model=ResetPolicyResponse)
async def reset_watcher_policy() -> ResetPolicyResponse:
    """Reset command rules and tool thresholds to built-in Watcher defaults."""
    store = get_watcher_store()
    policy = store.reset_policy_to_defaults()
    return ResetPolicyResponse(policy=policy)


@router.post("/policy/rule", response_model=UpdateRuleResponse)
async def update_rule(payload: RuleUpdatePayload) -> UpdateRuleResponse:
    """Update action on a specific command rule."""
    store = get_watcher_store()
    ok = store.update_command_rule(payload.rule_name, payload.action)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Rule '{payload.rule_name}' not found")
    return UpdateRuleResponse(rule_name=payload.rule_name, action=payload.action)


@router.post("/policy/threshold", response_model=UpdateThresholdResponse)
async def update_threshold(payload: ThresholdUpdatePayload) -> UpdateThresholdResponse:
    """Update threshold limits for a tool."""
    store = get_watcher_store()
    ok = store.update_tool_threshold(
        tool_name=payload.tool_name,
        auto_approve=payload.auto_approve,
        escalate_ge=payload.escalate_ge,
        auto_deny_ge=payload.auto_deny_ge,
        always_escalate=payload.always_escalate,
        auto_approve_le=payload.auto_approve_le,
    )
    if not ok:
        raise HTTPException(
            status_code=404, detail=f"Tool '{payload.tool_name}' not found in thresholds"
        )
    return UpdateThresholdResponse(threshold=payload)


@router.post("/policy/thresholds", response_model=UpdateThresholdsResponse)
async def update_thresholds(payloads: list[ThresholdUpdatePayload]) -> UpdateThresholdsResponse:
    """Bulk update threshold limits for multiple tools."""
    store = get_watcher_store()
    for p in payloads:
        store.update_tool_threshold(
            tool_name=p.tool_name,
            auto_approve=p.auto_approve,
            escalate_ge=p.escalate_ge,
            auto_deny_ge=p.auto_deny_ge,
            always_escalate=p.always_escalate,
            auto_approve_le=p.auto_approve_le,
        )
    return UpdateThresholdsResponse(count=len(payloads))
