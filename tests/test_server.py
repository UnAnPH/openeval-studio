"""Unit tests for FastAPI Streaming Backend and RunStore."""

from fastapi.testclient import TestClient

from server.app import app
from server.store import RunStore


def test_health_check() -> None:
    """Verify /api/health endpoint."""
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_list_models_endpoint() -> None:
    """Verify /api/models returns registered models."""
    client = TestClient(app)
    response = client.get("/api/models")
    assert response.status_code == 200
    models = response.json()
    assert len(models) >= 8
    assert any(m["id"] == "gemini-3.7-flash" for m in models)


def test_list_tasks_endpoint() -> None:
    """Verify /api/tasks returns all discovered 5-file tasks."""
    client = TestClient(app)
    response = client.get("/api/tasks")
    assert response.status_code == 200
    tasks = response.json()
    task_ids = [t["task_id"] for t in tasks]
    assert "cancel-async-tasks" in task_ids
    assert "regex-log" in task_ids


def test_run_store_lifecycle_and_pubsub() -> None:
    """Verify RunStore creation, update, and pub/sub mechanics."""
    store = RunStore()
    record = store.create_run(task_id="test-task", model="gemini-3.7-flash")

    assert record.status == "pending"
    assert record.task_id == "test-task"
    assert record.run_id.startswith("eval-test-task-")

    # Test query
    fetched = store.get_run(record.run_id)
    assert fetched is not None
    assert fetched.model == "gemini-3.7-flash"

    # Test update
    updated = store.update_run(record.run_id, status="completed", reward=1.0, passed=True)
    assert updated is not None
    assert updated.status == "completed"
    assert updated.reward == 1.0
    assert updated.passed is True


def test_launch_eval_validation() -> None:
    """Verify 404 on nonexistent task."""
    client = TestClient(app)
    response = client.post("/api/eval/run", json={"task_id": "nonexistent-fake-task"})
    assert response.status_code == 404


def test_delete_run_endpoints() -> None:
    """Verify deleting single and all runs."""
    from server.store import global_run_store

    record = global_run_store.create_run(task_id="cancel-async-tasks", model="gemini-3.7-flash")
    client = TestClient(app)

    # Delete single run
    res = client.delete(f"/api/eval/runs/{record.run_id}")
    assert res.status_code == 200
    assert res.json()["status"] == "deleted"
    assert global_run_store.get_run(record.run_id) is None

    # Clear all runs
    global_run_store.create_run(task_id="regex-log", model="gemini-3.7-flash")
    res_all = client.delete("/api/eval/runs")
    assert res_all.status_code == 200
    assert res_all.json()["status"] == "all_deleted"
    assert len(global_run_store.list_runs()) == 0


def test_run_with_slash_in_task_id_and_legacy_slash_lookup() -> None:
    """Verify that benchmark task IDs with slashes produce safe run IDs, and legacy slash IDs resolve."""
    from server.store import global_run_store

    # 1. Normal creation with task containing slash
    run = global_run_store.create_run(task_id="inspect_evals/agentharm", model="gemini-3.7-flash")
    assert "/" not in run.run_id
    assert "inspect_evals--agentharm" in run.run_id

    client = TestClient(app)
    res = client.get(f"/api/eval/runs/{run.run_id}")
    assert res.status_code == 200
    assert res.json()["run_id"] == run.run_id

    # 2. Existing/legacy run containing literal slash in ID
    legacy_run_id = "eval-inspect_evals/agentharm-20261011-085213-sym8r6"
    global_run_store._runs[legacy_run_id] = run.model_copy(
        update={"run_id": legacy_run_id, "session_id": legacy_run_id}
    )

    # Test GET run detail with literal slash in path
    res_legacy = client.get(f"/api/eval/runs/{legacy_run_id}")
    assert res_legacy.status_code == 200
    assert res_legacy.json()["run_id"] == legacy_run_id

    # Test GET report with literal slash in path
    res_report = client.get(f"/api/eval/runs/{legacy_run_id}/report")
    assert res_report.status_code == 200

    # Test POST stop with literal slash in path
    res_stop = client.post(f"/api/eval/runs/{legacy_run_id}/stop")
    assert res_stop.status_code == 200
    assert res_stop.json()["status"] == "stopped"

    # Test bidirectional resolution: query with '--' also finds '/' run
    dash_id = legacy_run_id.replace("/", "--")
    res_dash = client.get(f"/api/eval/runs/{dash_id}")
    assert res_dash.status_code == 200


def test_obsolete_env_var_fails_startup(monkeypatch) -> None:
    """Verify that OPENEVAL_GATE_USE_LLM causes startup to raise RuntimeError."""
    import asyncio

    import pytest

    from server.app import lifespan

    monkeypatch.setenv("OPENEVAL_GATE_USE_LLM", "1")
    with pytest.raises(RuntimeError, match="Obsolete environment variable OPENEVAL_GATE_USE_LLM"):

        async def _run() -> None:
            async with lifespan(app):
                pass

        asyncio.run(_run())
