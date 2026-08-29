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
