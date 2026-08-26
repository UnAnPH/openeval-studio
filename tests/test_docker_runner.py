"""Unit tests for Docker Sandboxing Layer and ReAct Tool Adapter."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from engine.react_agent import AgentAction
from sandbox.docker_runner import (
    DockerSandbox,
    DockerSandboxConfig,
    create_sandbox_for_task,
)
from schemas.task_spec import load_task_spec


@pytest.fixture
def mock_docker_client():
    """Mock docker-py client with container exec_run and put_archive methods."""
    client = MagicMock()
    container = MagicMock()
    container.id = "mock_container_1234567890ab"

    # Default exec_run output: exit_code 0, (stdout_bytes, stderr_bytes)
    container.exec_run.return_value = MagicMock(
        exit_code=0,
        output=(b"Hello from sandbox\n", b""),
    )

    client.containers.run.return_value = container
    client.images.get.return_value = MagicMock()
    return client, container


@pytest.fixture
def sample_task(tmp_path: Path):
    """Create a temporary test task."""
    task_dir = tmp_path / "sandbox-eval"
    task_dir.mkdir()
    (task_dir / "task.toml").write_text(
        "[metadata]\ncategory = 'sys'\n"
        "[environment]\ncpus = 4\nmemory_mb = 8192\nallow_internet = false\n"
    )
    (task_dir / "instruction.md").write_text("Fix the system bug")
    (task_dir / "environment").mkdir()
    (task_dir / "environment" / "Dockerfile").write_text("FROM alpine\n")
    (task_dir / "solution").mkdir()
    (task_dir / "solution" / "solve.sh").write_text("echo 1\n")
    (task_dir / "tests").mkdir()
    (task_dir / "tests" / "test_outputs.py").write_text("assert True\n")
    return load_task_spec(task_dir)


def test_create_sandbox_for_task(sample_task, mock_docker_client) -> None:
    """Verify create_sandbox_for_task builds proper cgroup and network config."""
    client, _ = mock_docker_client
    sandbox = create_sandbox_for_task(sample_task, docker_client=client)

    assert sandbox.config.cpus == 4
    assert sandbox.config.memory_mb == 8192
    assert sandbox.config.allow_internet is False
    assert sandbox.config.image_tag == "openeval-task-sandbox-eval:latest"


@pytest.mark.asyncio
async def test_sandbox_lifecycle_and_exec(mock_docker_client) -> None:
    """Verify container start, exec_command, and stop lifecycle."""
    client, container = mock_docker_client
    config = DockerSandboxConfig(
        image_tag="python:3.11-slim",
        cpus=2,
        memory_mb=4096,
        allow_internet=False,
    )

    sandbox = DockerSandbox(config=config, docker_client=client)

    async with sandbox as sb:
        assert sb._is_started is True
        client.containers.run.assert_called_once()
        _, kwargs = client.containers.run.call_args
        assert kwargs["network_mode"] == "none"
        assert kwargs["mem_limit"] == 4096 * 1024 * 1024
        assert kwargs["nano_cpus"] == int(2 * 1e9)

        # Execute command
        result = await sb.exec_command("echo 'Testing'")
        assert result.exit_code == 0
        assert result.stdout == "Hello from sandbox\n"
        assert result.duration_ms >= 0.0

    # Stop should remove container
    container.remove.assert_called_once_with(force=True)
    assert sandbox._is_started is False


@pytest.mark.asyncio
async def test_sandbox_as_tool_executor(mock_docker_client) -> None:
    """Verify as_tool_executor adapter translates AgentAction into sandbox operations."""
    client, container = mock_docker_client
    config = DockerSandboxConfig(image_tag="ubuntu:22.04")
    sandbox = DockerSandbox(config=config, docker_client=client)

    await sandbox.start()
    executor = sandbox.as_tool_executor()

    # 1. Test execute_bash
    container.exec_run.return_value = MagicMock(
        exit_code=0,
        output=(b"requirements.txt\nmain.py\n", b""),
    )
    action_bash = AgentAction(
        thought="List files",
        tool="execute_bash",
        command="ls",
    )
    output_bash = await executor(action_bash)
    assert "main.py" in output_bash

    # 2. Test view_file
    container.exec_run.return_value = MagicMock(
        exit_code=0,
        output=(b"def test(): pass\n", b""),
    )
    action_view = AgentAction(
        thought="Read code",
        tool="view_file",
        path="/app/test.py",
    )
    output_view = await executor(action_view)
    assert "def test(): pass" in output_view

    # 3. Test write_file
    action_write = AgentAction(
        thought="Write patch",
        tool="write_file",
        path="/app/fix.py",
        content="x = 1",
    )
    output_write = await executor(action_write)
    assert "Successfully wrote" in output_write
    container.put_archive.assert_called_once()

    await sandbox.stop()
