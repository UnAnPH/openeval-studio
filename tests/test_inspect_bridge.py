"""Unit tests for Inspect AI Bridge & Task Definitions."""

from pathlib import Path

from engine.inspect_bridge import (
    build_inspect_task_for_dir,
    task_spec_to_sample,
)
from inspect_tasks import cancel_async_tasks, regex_log
from schemas.task_spec import load_task_spec


def test_task_spec_to_sample() -> None:
    """Verify conversion of TaskSpec to Inspect AI Sample."""
    spec = load_task_spec(Path("tasks/cancel-async-tasks"))
    sample = task_spec_to_sample(spec)

    assert sample.id == "cancel-async-tasks"
    assert "async run_tasks" in sample.input
    assert sample.metadata["category"] == "software-engineering"


def test_build_inspect_task_for_dir() -> None:
    """Verify building a valid Inspect Task object."""
    inspect_task = build_inspect_task_for_dir("tasks/regex-log")
    assert len(inspect_task.dataset) == 1
    assert inspect_task.dataset[0].id == "regex-log"
    assert inspect_task.solver is not None
    assert inspect_task.scorer is not None


def test_inspect_tasks_registry() -> None:
    """Verify all task definitions in inspect_tasks.py return valid Tasks."""
    task_async = cancel_async_tasks()
    assert task_async.dataset[0].id == "cancel-async-tasks"

    task_regex = regex_log()
    assert task_regex.dataset[0].id == "regex-log"


def test_list_inspect_run_records() -> None:
    """Verify parsing existing .eval archives into RunRecords."""
    from server.inspect_loader import list_inspect_run_records

    logs_dir = Path("logs")
    if logs_dir.exists():
        records = list_inspect_run_records(logs_dir)
        assert isinstance(records, list)
        if records:
            first = records[0]
            assert first.run_id.startswith("inspect_")
            assert first.model != ""
            assert isinstance(first.steps, list)
