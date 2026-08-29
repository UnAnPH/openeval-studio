"""In-memory and JSON-persisted Run Store with Pub/Sub for OpenEval Studio.

Tracks historical evaluation trajectories, token costs, verification rewards,
and provides async event broadcasting for real-time SSE streaming.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from engine.judges import JudgeVerdict
from engine.react_agent import AgentStep


class RunRecord(BaseModel):
    """Full snapshot of an evaluation run including steps and grading."""

    model_config = ConfigDict(extra="ignore")

    run_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    task_id: str = Field(..., description="Target benchmark task ID")
    model: str = Field(..., description="Model endpoint used")
    provider: str = Field(default="google", description="Model provider")
    status: Literal[
        "pending", "running", "completed", "error", "max_steps_exceeded", "cancelled"
    ] = Field(default="pending")
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    steps: list[AgentStep] = Field(default_factory=list)
    total_steps: int = Field(default=0)
    total_tokens: int = Field(default=0)
    total_duration_sec: float = Field(default=0.0)
    estimated_cost_usd: float = Field(default=0.0)
    final_summary: str | None = Field(default=None)
    reward: float | None = Field(default=None)
    passed: bool | None = Field(default=None)
    failure_reason: str | None = Field(default=None)
    audit_verdicts: list[JudgeVerdict] = Field(default_factory=list)


class RunStore:
    """Manages run lifecycle, queries, and SSE event streaming subscribers."""

    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    def create_run(self, task_id: str, model: str, provider: str = "google") -> RunRecord:
        """Initialize a new pending evaluation run."""
        record = RunRecord(task_id=task_id, model=model, provider=provider)
        self._runs[record.run_id] = record
        self._subscribers[record.run_id] = []
        return record

    def get_run(self, run_id: str) -> RunRecord | None:
        """Look up a run by its ID."""
        return self._runs.get(run_id)

    def list_runs(self) -> list[RunRecord]:
        """Return all historical runs sorted by created_at descending."""
        return sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)

    def update_run(self, run_id: str, **kwargs: Any) -> RunRecord | None:
        """Update fields on an existing run record."""
        record = self._runs.get(run_id)
        if record is None:
            return None

        updated_data = record.model_dump()
        updated_data.update(kwargs)
        updated_record = RunRecord.model_validate(updated_data)
        self._runs[run_id] = updated_record
        return updated_record

    def subscribe(self, run_id: str) -> asyncio.Queue[dict[str, Any]]:
        """Register a subscriber queue for real-time SSE streaming."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        if run_id not in self._subscribers:
            self._subscribers[run_id] = []
        self._subscribers[run_id].append(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove a subscriber queue."""
        if run_id in self._subscribers and queue in self._subscribers[run_id]:
            self._subscribers[run_id].remove(queue)

    def publish_event(self, run_id: str, event_type: str, data: dict[str, Any]) -> None:
        """Broadcast an event payload to all active SSE subscribers."""
        payload = {"event": event_type, "run_id": run_id, "data": data}
        if run_id in self._subscribers:
            for q in list(self._subscribers[run_id]):
                q.put_nowait(payload)


# Singleton instance for the server process
global_run_store = RunStore()
