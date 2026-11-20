"""Unit tests for FastAPI Streaming Backend and RunStore."""

import importlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from engine.approval_policy import WatcherVerdict
from engine.verifier import VerifierRunner
from sandbox.docker_runner import DockerSandbox, DockerSandboxConfig, ExecResult
from server.app import app
from server.store import RunStore


def test_health_check() -> None:
    """Verify /api/health endpoint."""
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code in (200, 503)
    if response.status_code == 200:
        assert response.json()["status"] == "ok"
    else:
        assert response.json()["status"] == "degraded"


def test_list_models_endpoint() -> None:
    """Verify /api/models returns registered models."""
    client = TestClient(app)
    response = client.get("/api/models")
    assert response.status_code == 200
    models = response.json()
    assert len(models) == 25
    assert not any(m["id"] == "antigravity" for m in models)
    assert any(m["id"] == "gemini-decision-1" for m in models)
    assert any(m["id"] == "gemini-4-hydrogen" for m in models)
    assert any(m["id"] == "gemini-3.8-flash" for m in models)
    assert any(m["id"] == "opencode/deepseek-v4.1-flash" for m in models)


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
    record = store.create_run(task_id="test-task", model="gemini-3.8-flash")

    assert record.status == "pending"
    assert record.task_id == "test-task"
    assert record.run_id.startswith("eval-test-task-")

    # Test query
    fetched = store.get_run(record.run_id)
    assert fetched is not None
    assert fetched.model == "gemini-3.8-flash"

    # Test update
    updated = store.update_run(record.run_id, status="completed", reward=1.0, passed=True)
    assert updated is not None
    assert updated.status == "completed"
    assert updated.reward == 1.0
    assert updated.passed is True


def test_launch_eval_validation() -> None:
    """Verify 404 on nonexistent task and 400 on unrunnable/non-agent models."""
    client = TestClient(app)
    response = client.post("/api/eval/run", json={"task_id": "nonexistent-fake-task"})
    assert response.status_code == 404

    # Non-agent or non-eval models rejected with 400
    for bad_model in [
        "antigravity",
        "gemini-decision-1",
        "gemini-3.8-live",
        "gemini-3.8-live-extended-thinking",
    ]:
        res = client.post(
            "/api/eval/run",
            json={"task_id": "cancel-async-tasks", "model": bad_model},
        )
        assert res.status_code == 400, f"Expected 400 for model {bad_model}, got {res.status_code}"
        assert "cannot be evaluated" in res.json()["detail"]


def test_delete_run_endpoints() -> None:
    """Verify deleting single and all runs."""
    from server.store import global_run_store

    record = global_run_store.create_run(task_id="cancel-async-tasks", model="gemini-3.8-flash")
    client = TestClient(app)

    # Delete single run
    res = client.delete(f"/api/eval/runs/{record.run_id}")
    assert res.status_code == 200
    assert res.json()["status"] == "deleted"
    assert global_run_store.get_run(record.run_id) is None

    # Clear all runs
    global_run_store.create_run(task_id="regex-log", model="gemini-3.8-flash")
    res_all = client.delete("/api/eval/runs")
    assert res_all.status_code == 200
    assert res_all.json()["status"] == "all_deleted"
    assert len(global_run_store.list_runs()) == 0


def test_run_with_slash_in_task_id_and_legacy_slash_lookup() -> None:
    """Verify that benchmark task IDs with slashes produce safe run IDs, and legacy slash IDs resolve."""
    from server.store import global_run_store

    # 1. Normal creation with task containing slash
    run = global_run_store.create_run(task_id="inspect_evals/agentharm", model="gemini-3.8-flash")
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

    from server.app import lifespan

    monkeypatch.setenv("OPENEVAL_GATE_USE_LLM", "1")
    with pytest.raises(RuntimeError, match="Obsolete environment variable OPENEVAL_GATE_USE_LLM"):

        async def _run() -> None:
            async with lifespan(app):
                pass

        asyncio.run(_run())


class _RecordingSandbox:
    """Duck-typed sandbox that records exec_command invocations in order."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, str | None]] = []

    async def exec_command(
        self,
        command: str,
        timeout_sec: float | None = None,
        user: str | None = None,
        **kwargs: Any,
    ) -> ExecResult:
        self.commands.append((command, user))
        if command.startswith("mkdir -p /logs/verifier"):
            return ExecResult(exit_code=0)
        if command.startswith("cat /logs/verifier/reward.txt"):
            return ExecResult(exit_code=1)
        if command.startswith("test -f"):
            return ExecResult(exit_code=1)
        return ExecResult(exit_code=0, stdout="ok")

    async def copy_directory(self, src_path: Any, dest_container_path: str) -> None:
        raise AssertionError("copy_directory should not run without a tests/ directory")


class _FakeVerifierConfig:
    timeout_sec = 60.0


class _FakeTask:
    """Minimal TaskSpec stand-in for verifier staging tests."""

    task_id = "fake-task"

    def __init__(self, task_dir: Any) -> None:
        self.task_dir = task_dir
        self.verifier = _FakeVerifierConfig()


@pytest.mark.asyncio
async def test_grade_container_creates_logs_verifier_as_root(tmp_path) -> None:
    """grade_container must pre-create /logs/verifier as root for test.sh scripts."""
    sandbox = _RecordingSandbox()
    task = _FakeTask(tmp_path)

    score = await VerifierRunner().grade_container(sandbox, task)  # type: ignore[arg-type]

    assert score.passed is True
    assert sandbox.commands[0] == ("mkdir -p /logs/verifier", "root")
    verifier_index = next(
        i
        for i, (cmd, _) in enumerate(sandbox.commands)
        if cmd == "pytest /workspace/tests/test_outputs.py"
    )
    assert verifier_index > 0


@pytest.mark.asyncio
async def test_copy_directory_creates_destination_as_root(tmp_path, monkeypatch) -> None:
    """copy_directory must mkdir the destination as root on non-root images."""
    src = tmp_path / "tests"
    src.mkdir()
    (src / "test_outputs.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    sandbox = DockerSandbox(DockerSandboxConfig())
    sandbox._is_started = True
    sandbox.container = type("Container", (), {"put_archive": lambda self, **kwargs: None})()

    calls: list[tuple[str, str | None]] = []

    async def _exec_command(
        command: str,
        timeout_sec: float | None = None,
        user: str | None = None,
        **kwargs: Any,
    ) -> ExecResult:
        calls.append((command, user))
        return ExecResult(exit_code=0)

    monkeypatch.setattr(sandbox, "exec_command", _exec_command)

    await sandbox.copy_directory(src, "/tests")

    assert calls == [("mkdir -p '/tests'", "root")]


class _FakeHistoryEngine:
    """Minimal WatcherEngine stand-in exposing newest-first interception history."""

    def __init__(self, verdicts: list[WatcherVerdict]) -> None:
        self._verdicts = verdicts
        self.requested_limits: list[int] = []

    def get_history(self, limit: int = 50) -> list[WatcherVerdict]:
        self.requested_limits.append(limit)
        return list(self._verdicts[:limit])


def test_fetch_and_merge_interceptions_preserves_blocked_rows_under_allow_burst(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control stream must keep blocked verdicts when recent allow calls dominate."""
    server_app = importlib.import_module("server.app")
    now = datetime.now(UTC)
    verdicts: list[WatcherVerdict] = []

    # 50 newer allowed tool calls from recent eval runs (newest first).
    for i in range(50):
        verdicts.append(
            WatcherVerdict(
                decision="allow",
                stage="stage_2_deterministic",
                timestamp=(now - timedelta(seconds=i)).isoformat(),
                action_preview=f"read_file: src/module_{i}.py",
                full_command=f"read_file src/module_{i}.py",
                tool_result="Command completed (exit code 0)",
                agent_id="opencode",
                review_id=f"allow-{i}",
            )
        )

    # 25 older denied/escalated interceptions that must survive the burst.
    for i in range(25):
        verdicts.append(
            WatcherVerdict(
                decision="deny",
                stage="stage_2_deterministic",
                timestamp=(now - timedelta(minutes=10, seconds=i)).isoformat(),
                reason="Destructive shell command blocked",
                rule_violation_tag="destructive_rm",
                action_preview=f"execute_bash: rm -rf /tmp/scratch_{i}",
                full_command=f"rm -rf /tmp/scratch_{i}",
                tool_result="Execution blocked by OpenEval runtime gate.",
                agent_id="opencode",
                review_id=f"deny-{i}",
                is_safe=False,
            )
        )

    engine = _FakeHistoryEngine(verdicts)

    def _no_stored_reviews(
        limit: int = 40, session_cache: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(server_app, "global_watcher_engine", engine)
    monkeypatch.setattr(server_app, "_verdicts_from_store_reviews", _no_stored_reviews)

    rows = server_app._fetch_and_merge_interceptions(limit=40, uid=None, user_slug="demo")

    assert engine.requested_limits == [200]
    assert len(rows) == 40

    blocked = [
        r
        for r in rows
        if str(r.get("decision") or "").lower() in ("deny", "block", "reject", "escalate")
    ]
    assert len(blocked) >= 20
    assert all(str(r.get("review_id") or "").startswith("deny-") for r in blocked)

    timestamps = [str(r.get("timestamp") or "") for r in rows]
    assert timestamps == sorted(timestamps, reverse=True)
