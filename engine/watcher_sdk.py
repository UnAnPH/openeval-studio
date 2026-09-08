"""Official Typed Python SDK for OpenEval Watcher.

Provides clean programmatic APIs for agent hooks (Claude Code, Cursor),
Inspect AI evaluators, and developer tools to record sessions, request review decisions,
and perform diagnostic checks.
"""

import time
from typing import Any, Literal
from uuid import uuid4

from schemas.watcher_models import (
    AgentType,
    Message,
    ReviewRecord,
    Session,
    ToolCall,
    ToolResult,
)
from server.command_rules import CommandRulesEngine
from server.policy_gateway import PolicyGateway
from server.watcher_store import WatcherStore, get_watcher_store


class WatcherClient:
    """Typed Python Client SDK for Watcher session management and gating."""

    def __init__(self, store: WatcherStore | None = None) -> None:
        self.store = store or get_watcher_store()

    def start_session(
        self,
        project_name: str,
        agent_type: AgentType = "inspect_eval",
        model: str = "google/gemini-2.5-flash",
        provider: str = "google",
        working_dir: str | None = None,
        org_id: str = "default_org",
    ) -> Session:
        """Register and start a new monitored agent session."""
        session = Session(
            session_id=str(uuid4())[:8],
            org_id=org_id,
            project_name=project_name,
            agent_type=agent_type,
            model=model,
            provider=provider,
            status="active",
            working_dir=working_dir,
            current_activity="Session initialized",
        )
        return self.store.create_session(session)

    def log_message(
        self,
        session_id: str,
        role: Literal["developer", "user", "assistant", "system"],
        content: str,
        thinking: str | None = None,
    ) -> Message:
        """Record a conversation turn in the session trajectory."""
        msg = Message(role=role, content=content, thinking=thinking, timestamp=time.time())
        self.store.append_trajectory_event(session_id, msg)
        return msg

    def record_tool_call(
        self,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        raw_input: str | None = None,
    ) -> ToolCall:
        """Log an initiated tool call and update the session's active state."""
        call = ToolCall(
            tool_id=str(uuid4())[:8],
            tool_name=tool_name,
            arguments=arguments or {},
            raw_input=raw_input,
            timestamp=time.time(),
        )
        self.store.append_trajectory_event(session_id, call)
        return call

    def record_tool_result(
        self,
        session_id: str,
        tool_id: str,
        tool_name: str,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
        duration_ms: float = 0.0,
        is_error: bool = False,
    ) -> ToolResult:
        """Record the execution outcome of a completed tool call."""
        res = ToolResult(
            tool_id=tool_id,
            tool_name=tool_name,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=duration_ms,
            is_error=is_error,
        )
        self.store.append_trajectory_event(session_id, res)
        return res

    def review_tool_call(
        self,
        session_id: str,
        tool_name: str,
        tool_input: str,
        diff: str | None = None,
        user_intent: str = "",
        prior_state_exposed: bool = False,
    ) -> ReviewRecord:
        """Evaluate a tool call against the active policy rules and thresholds."""
        policy = self.store.get_default_policy()
        gateway = PolicyGateway(
            command_engine=CommandRulesEngine(policy.command_rules),
            tool_thresholds=policy.tool_thresholds,
        )
        review = gateway.evaluate_tool_call(
            session_id=session_id,
            tool_name=tool_name,
            tool_input=tool_input,
            diff=diff,
            user_intent=user_intent,
            prior_state_exposed=prior_state_exposed,
        )
        return self.store.record_decision(review)

    def finish_session(
        self,
        session_id: str,
        passed: bool | None = None,
        reward: float | None = None,
        total_tokens: int = 0,
        total_duration_sec: float = 0.0,
        failure_reason: str | None = None,
    ) -> Session | None:
        """Mark a session as completed with final benchmark/eval metrics."""
        return self.store.update_session(
            session_id,
            {
                "status": "completed",
                "passed": passed,
                "reward": reward,
                "total_tokens": total_tokens,
                "total_duration_sec": total_duration_sec,
                "failure_reason": failure_reason,
                "current_activity": "Session finished",
            },
        )

    def get_session(self, session_id: str) -> Session | None:
        """Fetch session by ID."""
        return self.store.get_session(session_id)

    def list_sessions(
        self,
        agent_type: str | None = None,
        status: str | None = None,
    ) -> list[Session]:
        """List recorded sessions."""
        return self.store.list_sessions(agent_type=agent_type, status=status)

    def doctor(self) -> dict[str, Any]:
        """Execute Watcher doctor diagnostic checks."""
        db_alive = False
        try:
            res = self.store.con.execute("SELECT 1").fetchall()
            db_alive = bool(res and res[0][0] == 1)
        except Exception:
            db_alive = False

        storage_writable = False
        try:
            test_file = self.store.storage_dir / ".doctor_test"
            test_file.write_text("ok", encoding="utf-8")
            storage_writable = test_file.read_text(encoding="utf-8") == "ok"
            test_file.unlink(missing_ok=True)
        except Exception:
            storage_writable = False

        policy = self.store.get_default_policy()
        return {
            "status": "healthy" if (db_alive and storage_writable) else "degraded",
            "duckdb_connected": db_alive,
            "storage_dir": str(self.store.storage_dir),
            "storage_writable": storage_writable,
            "cached_sessions_count": len(self.store._sessions),
            "active_command_rules": len(policy.command_rules),
            "active_tool_thresholds": len(policy.tool_thresholds),
            "python_version": "3.12",
        }
