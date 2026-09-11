"""Regression: eval SSE must survive step updates that include AgentStep objects."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from pydantic import BaseModel

from schemas.watcher_models import Session
from server.watcher_store import WatcherStore, _to_jsonable


class _FakeStep(BaseModel):
    step_number: int
    thought: str = "thinking"
    tokens_used: int = 10


def test_to_jsonable_dumps_nested_pydantic() -> None:
    payload = {"steps": [_FakeStep(step_number=1)], "total_steps": 1}
    encoded = json.dumps(_to_jsonable(payload))
    parsed = json.loads(encoded)
    assert parsed["steps"][0]["step_number"] == 1


def test_update_session_broadcast_is_json_serializable(tmp_path) -> None:
    store = WatcherStore(storage_dir=tmp_path / "sessions", db_path=":memory:")
    session = Session(project_name="demo", agent_type="inspect_eval", status="active")
    store.create_session(session)
    queue = store.subscribe(session.session_id)

    step = _FakeStep(step_number=1)
    store.update_session(session.session_id, {"steps": [step], "total_steps": 1})

    payload = None
    while not queue.empty():
        item = queue.get_nowait()
        if item.get("event") == "session_updated":
            payload = item
            break
    assert payload is not None
    # Must not raise — this is what killed the Launch SSE stream.
    data_str = json.dumps(payload["data"])
    assert "thinking" in data_str


@pytest.mark.asyncio
async def test_sse_generator_survives_session_updated_with_steps(tmp_path) -> None:
    """Simulate the stream_run_events encode path after a session_updated + step_complete."""
    store = WatcherStore(storage_dir=tmp_path / "s", db_path=":memory:")
    session = Session(project_name="demo", agent_type="inspect_eval", status="active")
    store.create_session(session)
    queue = store.subscribe(session.session_id)

    store.update_session(session.session_id, {"steps": [_FakeStep(step_number=1)]})
    store.broadcast_sync(session.session_id, "step_complete", _FakeStep(step_number=1).model_dump())
    store.broadcast_sync(session.session_id, "completed", {"status": "completed"})

    def _sse_dumps(data: Any) -> str:
        def _default(obj: Any) -> Any:
            if hasattr(obj, "model_dump") and callable(obj.model_dump):
                return obj.model_dump()
            return str(obj)

        return json.dumps(data, default=_default)

    events: list[str] = []
    for _ in range(3):
        payload = await asyncio.wait_for(queue.get(), timeout=1)
        event_type = payload.get("event", "message")
        data_str = _sse_dumps(payload.get("data", {}))
        events.append(event_type)
        assert isinstance(data_str, str)
        if event_type == "completed":
            break

    assert "session_updated" in events
    assert "step_complete" in events
    assert "completed" in events
