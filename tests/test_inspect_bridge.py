"""Tests for Inspect AI Approver plugin and Trailing Hooks bridge."""

import pytest
from inspect_ai.tool import ToolCall, ToolCallView

from engine.inspect_bridge import WatcherApprover, WatcherTrailingHooks


@pytest.mark.asyncio
async def test_watcher_approver_allows_benign_tool_call():
    approver = WatcherApprover(session_id="test-inspect-01")
    call = ToolCall(
        id="call_1",
        function="execute_bash",
        arguments={"cmd": "git status"},
    )
    view = ToolCallView()

    approval = await approver(
        message="Check git status",
        call=call,
        view=view,
        history=[],
    )

    assert approval.decision == "approve"
    assert approval.explanation is not None and "Auto-approved" in approval.explanation


@pytest.mark.asyncio
async def test_watcher_approver_blocks_dangerous_tool_call():
    approver = WatcherApprover(session_id="test-inspect-02")
    call = ToolCall(
        id="call_2",
        function="execute_bash",
        arguments={"cmd": "git push --force origin main"},
    )
    view = ToolCallView()

    approval = await approver(
        message="Deploy changes forcefully",
        call=call,
        view=view,
        history=[],
    )

    assert approval.decision == "reject"
    assert approval.explanation is not None and "POLICY GATEWAY AUTO-DENIED" in approval.explanation


def test_watcher_trailing_hooks_enabled():
    hooks = WatcherTrailingHooks()
    assert hooks.enabled() is True
