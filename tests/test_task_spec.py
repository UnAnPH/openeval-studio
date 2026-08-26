"""Unit tests for Benchmark Task Specification and Loader."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from schemas.task_spec import load_task_spec


@pytest.fixture
def mock_task_dir(tmp_path: Path) -> Path:
    """Create a temporary valid 5-file task directory."""
    task_dir = tmp_path / "mock-auth-eval"
    task_dir.mkdir()

    toml_content = """
    [metadata]
    category = "security_evals"
    expert_time_estimate_hours = 3.5
    difficulty = "hard"
    tags = ["jwt", "oauth"]

    [verifier]
    timeout_sec = 600.0
    allow_internet = false

    [agent]
    timeout_sec = 3600.0
    max_steps = 50
    temperature = 0.0

    [environment]
    build_timeout_sec = 1200.0
    cpus = 4
    memory_mb = 8192
    storage_mb = 20480
    gpus = 0
    allow_internet = false
    """
    (task_dir / "task.toml").write_text(toml_content)
    (task_dir / "instruction.md").write_text("# Objective\nFix the authorization bypass flaw.")

    env_dir = task_dir / "environment"
    env_dir.mkdir()
    (env_dir / "Dockerfile").write_text("FROM python:3.11-slim\nWORKDIR /app\n")

    sol_dir = task_dir / "solution"
    sol_dir.mkdir()
    (sol_dir / "solve.sh").write_text("#!/bin/bash\necho 'Solved' > /app/result.txt\n")

    tests_dir = task_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_outputs.py").write_text("def test_fix():\n    assert True\n")

    return task_dir


def test_load_valid_task(mock_task_dir: Path) -> None:
    """Verify that a valid task directory parses with all metadata and resource fields."""
    spec = load_task_spec(mock_task_dir)

    assert spec.task_id == "mock-auth-eval"
    assert spec.metadata.category == "security_evals"
    assert spec.metadata.expert_time_estimate_hours == 3.5
    assert spec.metadata.difficulty == "hard"
    assert "jwt" in spec.metadata.tags

    assert spec.verifier.timeout_sec == 600.0
    assert spec.verifier.allow_internet is False

    assert spec.agent.timeout_sec == 3600.0
    assert spec.agent.max_steps == 50
    assert spec.agent.temperature == 0.0

    assert spec.environment.cpus == 4
    assert spec.environment.memory_mb == 8192
    assert spec.environment.allow_internet is False

    assert "authorization bypass" in spec.instruction_text
    assert spec.dockerfile_path.exists()
    assert spec.solution_path.exists()
    assert spec.test_outputs_path.exists()


def test_load_bundled_feed_sync_task() -> None:
    """Verify that the bundled feed-sync-platform task parses cleanly."""
    repo_root = Path(__file__).resolve().parent.parent
    bundled_task = repo_root / "tasks" / "feed-sync-platform"
    assert bundled_task.is_dir()

    spec = load_task_spec(bundled_task)
    assert spec.task_id == "feed-sync-platform"
    assert spec.metadata.category == "software_engineering"
    assert spec.environment.cpus == 2
    assert spec.environment.memory_mb == 4096


def test_missing_task_toml_raises_error(tmp_path: Path) -> None:
    """Ensure FileNotFoundError is raised when task.toml is missing."""
    empty_dir = tmp_path / "bad-task"
    empty_dir.mkdir()
    (empty_dir / "instruction.md").write_text("Prompt")

    with pytest.raises(FileNotFoundError, match="Missing 'task.toml'"):
        load_task_spec(empty_dir)


def test_missing_instruction_md_raises_error(tmp_path: Path) -> None:
    """Ensure FileNotFoundError is raised when instruction.md is missing."""
    task_dir = tmp_path / "no-prompt"
    task_dir.mkdir()
    (task_dir / "task.toml").write_text("[metadata]\ncategory = 'general'\n")

    with pytest.raises(FileNotFoundError, match="Missing 'instruction.md'"):
        load_task_spec(task_dir)


def test_missing_oracle_solution_raises_error(mock_task_dir: Path) -> None:
    """Ensure FileNotFoundError is raised when solve.sh is missing."""
    (mock_task_dir / "solution" / "solve.sh").unlink()

    with pytest.raises(FileNotFoundError, match="Missing required task file 'Oracle Solution'"):
        load_task_spec(mock_task_dir)


def test_invalid_resource_quotas_raises_validation_error(tmp_path: Path) -> None:
    """Ensure Pydantic raises ValidationError on non-positive CPU or RAM allocations."""
    task_dir = tmp_path / "bad-resources"
    task_dir.mkdir()

    toml_content = """
    [metadata]
    category = "general"

    [environment]
    cpus = 0
    memory_mb = 100
    """
    (task_dir / "task.toml").write_text(toml_content)
    (task_dir / "instruction.md").write_text("Prompt")
    (task_dir / "environment").mkdir()
    (task_dir / "environment" / "Dockerfile").write_text("FROM alpine")
    (task_dir / "solution").mkdir()
    (task_dir / "solution" / "solve.sh").write_text("echo 1")
    (task_dir / "tests").mkdir()
    (task_dir / "tests" / "test_outputs.py").write_text("assert True")

    with pytest.raises(ValidationError):
        load_task_spec(task_dir)
