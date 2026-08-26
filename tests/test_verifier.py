"""Unit tests for Verifier & Grading Harness."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from engine.verifier import VerifierRunner
from sandbox.docker_runner import DockerSandbox, DockerSandboxConfig, ExecResult
from schemas.task_spec import load_task_spec


@pytest.fixture
def sample_task(tmp_path: Path):
    """Create a temporary test task with oracle and verifier files."""
    task_dir = tmp_path / "verifier-eval"
    task_dir.mkdir()
    (task_dir / "task.toml").write_text("[metadata]\ncategory = 'sec'\n")
    (task_dir / "instruction.md").write_text("Fix the bug")
    (task_dir / "environment").mkdir()
    (task_dir / "environment" / "Dockerfile").write_text("FROM alpine\n")
    (task_dir / "solution").mkdir()
    (task_dir / "solution" / "solve.sh").write_text("#!/bin/bash\necho 1 > /app/fixed\n")
    (task_dir / "tests").mkdir()
    (task_dir / "tests" / "test_outputs.py").write_text("def test(): assert True\n")
    return load_task_spec(task_dir)


@pytest.fixture
def mock_sandbox():
    """Mock DockerSandbox for verifier tests."""
    config = DockerSandboxConfig()
    sandbox = DockerSandbox(config=config, docker_client=MagicMock())
    sandbox._is_started = True
    sandbox.exec_command = AsyncMock()  # type: ignore[method-assign]
    sandbox.write_file = AsyncMock()  # type: ignore[method-assign]
    return sandbox


@pytest.mark.asyncio
async def test_grade_container_passing(sample_task, mock_sandbox) -> None:
    """Verify that a 0 exit code from pytest yields reward 1.0 and passed=True."""
    mock_sandbox.exec_command.return_value = ExecResult(
        exit_code=0,
        stdout="1 passed in 0.01s",
        stderr="",
        duration_ms=15.0,
    )

    verifier = VerifierRunner()
    score = await verifier.grade_container(mock_sandbox, sample_task)

    assert score.passed is True
    assert score.reward == 1.0
    assert score.exit_code == 0
    assert "1 passed" in score.test_output
    assert score.failure_reason is None
    mock_sandbox.write_file.assert_called_once()  # Anti-cheat fresh staging


@pytest.mark.asyncio
async def test_grade_container_failing(sample_task, mock_sandbox) -> None:
    """Verify that a non-zero exit code yields reward 0.0 and failure_reason."""
    mock_sandbox.exec_command.return_value = ExecResult(
        exit_code=1,
        stdout="",
        stderr="AssertionError: Expected 200 but got 500",
        duration_ms=20.0,
    )

    verifier = VerifierRunner()
    score = await verifier.grade_container(mock_sandbox, sample_task)

    assert score.passed is False
    assert score.reward == 0.0
    assert score.exit_code == 1
    assert "AssertionError" in score.test_output
    assert "Verifier failed with exit code 1" in (score.failure_reason or "")


@pytest.mark.asyncio
async def test_full_benchmark_audit_valid_task(sample_task, mock_sandbox) -> None:
    """Verify that a valid task (Nop fails, Oracle passes) produces a valid audit report."""
    call_count = 0

    async def mock_exec(cmd: str, timeout_sec: float | None = None) -> ExecResult:
        nonlocal call_count
        call_count += 1
        # Nop test run -> fail
        if call_count == 1:
            return ExecResult(exit_code=1, stdout="Failing initially", duration_ms=10.0)
        # Oracle solve.sh execution
        if "/workspace/solution/solve.sh" in cmd:
            return ExecResult(exit_code=0, stdout="Solved", duration_ms=5.0)
        # Oracle test run -> pass
        return ExecResult(exit_code=0, stdout="1 passed", duration_ms=10.0)

    mock_sandbox.exec_command.side_effect = mock_exec

    verifier = VerifierRunner()
    report = await verifier.run_full_benchmark_audit(mock_sandbox, sample_task)

    assert report.nop_passed is False
    assert report.oracle_passed is True
    assert report.is_benchmark_valid is True
    assert "PASSED: Benchmark task is mathematically solvable" in report.audit_notes
