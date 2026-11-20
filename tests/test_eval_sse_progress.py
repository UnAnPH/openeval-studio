"""Regression: eval SSE must survive step updates that include AgentStep objects."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from engine.react_agent import AgentTrajectory
from schemas.task_spec import load_task_spec
from schemas.watcher_models import Session
from server.routes.eval import _run_evaluation_worker
from server.store import global_run_store
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


@pytest.mark.asyncio
async def test_worker_emits_verifier_complete_before_audits_start(tmp_path: Path) -> None:
    """Phase 3 results must stream between verifier_start and audits_start."""
    task_dir = tmp_path / "sse-phase-task"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "task.toml").write_text(
        "[metadata]\ncategory = 'sys'\n"
        "[environment]\ncpus = 1\nmemory_mb = 512\nallow_internet = false\n"
        "[verifier]\ntimeout_sec = 30.0\n",
        encoding="utf-8",
    )
    (task_dir / "instruction.md").write_text("Do the thing", encoding="utf-8")
    (task_dir / "environment").mkdir()
    (task_dir / "environment" / "Dockerfile").write_text("FROM alpine\n", encoding="utf-8")
    (task_dir / "solution").mkdir()
    (task_dir / "solution" / "solve.sh").write_text("echo ok\n", encoding="utf-8")
    (task_dir / "tests").mkdir()
    (task_dir / "tests" / "test_outputs.py").write_text("assert True\n", encoding="utf-8")
    spec = load_task_spec(task_dir)

    run_id = "eval-test-sse-verifier-complete-001"
    global_run_store.create_run(
        task_id=spec.task_dir.name,
        model="gemini-3.1-flash-lite",
        run_id=run_id,
    )
    queue = global_run_store.subscribe(run_id)

    mock_sandbox = MagicMock()
    mock_sandbox.start = AsyncMock()
    mock_sandbox.stop = AsyncMock()
    mock_sandbox.as_tool_executor = MagicMock(return_value=AsyncMock(return_value="ok"))

    fake_trajectory = AgentTrajectory(
        task_id=spec.task_dir.name,
        model="gemini-3.1-flash-lite",
        status="completed",
        steps=[],
    )
    fake_grade = MagicMock(
        reward=1.0,
        passed=True,
        failure_reason=None,
        test_output="1 passed",
    )

    with (
        patch("server.routes.eval.TASKS_DIR", spec.task_dir.parent),
        patch("server.routes.eval.create_sandbox_for_task", return_value=mock_sandbox),
        patch("server.routes.eval.ReActAgent") as mock_agent_cls,
        patch("server.routes.eval.VerifierRunner") as mock_verifier_cls,
        patch(
            "server.routes.eval.TrajectoryJudges.audit_full_trajectory", new_callable=AsyncMock
        ) as mock_judge,
        patch(
            "server.routes.eval.SchemingScanners.scan_all", new_callable=AsyncMock
        ) as mock_scanner,
    ):
        mock_agent_instance = MagicMock()
        mock_agent_instance.solve_task = AsyncMock(return_value=fake_trajectory)
        mock_agent_cls.return_value = mock_agent_instance

        mock_verifier_instance = MagicMock()
        mock_verifier_instance.grade_container = AsyncMock(return_value=fake_grade)
        mock_verifier_cls.return_value = mock_verifier_instance

        mock_judge.return_value = []
        mock_scanner.return_value = []

        await _run_evaluation_worker(
            run_id=run_id,
            task_id=spec.task_dir.name,
            model="gemini-3.1-flash-lite",
            provider="google",
            api_key="test-dummy-key",
        )

    events: list[dict[str, Any]] = []
    while not queue.empty():
        events.append(queue.get_nowait())

    names = [e.get("event") for e in events]
    assert "verifier_start" in names
    assert "verifier_complete" in names
    assert "audits_start" in names
    assert (
        names.index("verifier_start")
        < names.index("verifier_complete")
        < names.index("audits_start")
    )

    payload = next(e for e in events if e.get("event") == "verifier_complete")["data"]
    assert payload["passed"] is True
    assert payload["reward"] == 1.0
    assert payload["failure_reason"] is None


@pytest.mark.asyncio
async def test_worker_aborts_before_verifier_and_audits_on_llm_api_error(tmp_path: Path) -> None:
    """Mid-run LLM API errors must terminate immediately and skip verifier & 4. LLM Judge Audits."""
    task_dir = tmp_path / "sse-error-task"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "task.toml").write_text(
        "[metadata]\ncategory = 'sys'\n"
        "[environment]\ncpus = 1\nmemory_mb = 512\nallow_internet = false\n"
        "[verifier]\ntimeout_sec = 30.0\n",
        encoding="utf-8",
    )
    (task_dir / "instruction.md").write_text("Do the thing", encoding="utf-8")
    (task_dir / "environment").mkdir()
    (task_dir / "environment" / "Dockerfile").write_text("FROM alpine\n", encoding="utf-8")
    (task_dir / "solution").mkdir()
    (task_dir / "solution" / "solve.sh").write_text("echo ok\n", encoding="utf-8")
    (task_dir / "tests").mkdir()
    (task_dir / "tests" / "test_outputs.py").write_text("assert True\n", encoding="utf-8")
    spec = load_task_spec(task_dir)

    run_id = "eval-test-sse-llm-error-001"
    global_run_store.create_run(
        task_id=spec.task_dir.name,
        model="gemma-4-31b-it",
        run_id=run_id,
    )
    queue = global_run_store.subscribe(run_id)

    mock_sandbox = MagicMock()
    mock_sandbox.start = AsyncMock()
    mock_sandbox.stop = AsyncMock()
    mock_sandbox.as_tool_executor = MagicMock(return_value=AsyncMock(return_value="ok"))

    err_msg = (
        "Google GenAI request failed for gemma-4-31b-it after 4 retries: "
        "503 UNAVAILABLE. This model is currently experiencing high demand."
    )
    failed_trajectory = AgentTrajectory(
        task_id=spec.task_dir.name,
        model="gemma-4-31b-it",
        status="error",
        error_message=err_msg,
        final_summary=err_msg,
        steps=[],
    )

    with (
        patch("server.routes.eval.TASKS_DIR", spec.task_dir.parent),
        patch("server.routes.eval.create_sandbox_for_task", return_value=mock_sandbox),
        patch("server.routes.eval.ReActAgent") as mock_agent_cls,
        patch("server.routes.eval.VerifierRunner") as mock_verifier_cls,
        patch(
            "server.routes.eval.TrajectoryJudges.audit_full_trajectory", new_callable=AsyncMock
        ) as mock_judge,
        patch(
            "server.routes.eval.SchemingScanners.scan_all", new_callable=AsyncMock
        ) as mock_scanner,
    ):
        mock_agent_instance = MagicMock()
        mock_agent_instance.solve_task = AsyncMock(return_value=failed_trajectory)
        mock_agent_cls.return_value = mock_agent_instance

        mock_verifier_instance = MagicMock()
        mock_verifier_instance.grade_container = AsyncMock()
        mock_verifier_cls.return_value = mock_verifier_instance

        await _run_evaluation_worker(
            run_id=run_id,
            task_id=spec.task_dir.name,
            model="gemma-4-31b-it",
            provider="google",
            api_key="test-dummy-key",
        )

    mock_verifier_instance.grade_container.assert_not_awaited()
    mock_judge.assert_not_awaited()
    mock_scanner.assert_not_awaited()
    mock_sandbox.stop.assert_awaited_once()

    events: list[dict[str, Any]] = []
    while not queue.empty():
        events.append(queue.get_nowait())

    names = [e.get("event") for e in events]
    assert "verifier_start" not in names
    assert "verifier_complete" not in names
    assert "audits_start" not in names
    assert "completed" in names

    completed_payload = next(e for e in events if e.get("event") == "completed")["data"]
    assert completed_payload["status"] == "error"
    assert completed_payload["passed"] is False
    assert completed_payload["reward"] is None
    assert completed_payload["skipped_verifier"] is True
    assert completed_payload["skipped_audits"] is True
    assert completed_payload["audit_verdicts"] == []
    assert completed_payload["failure_reason"].startswith("LLM API Error (gemma-4-31b-it):")

    rec = global_run_store.get_run(run_id)
    assert rec is not None
    assert rec.status == "error"
    assert rec.passed is False
    assert rec.reward is None
    assert rec.audit_verdicts == []
    assert rec.failure_reason is not None and "503 UNAVAILABLE" in rec.failure_reason
