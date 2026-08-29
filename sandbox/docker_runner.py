"""Docker Execution Sandbox & Container Manager for OpenEval Studio.

Provides zero-trust container isolation, resource throttling (CPU/RAM cgroups),
network isolation, automatic Dockerfile building, and ReAct tool execution hooks.
"""

import asyncio
import io
import logging
import tarfile
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from engine.react_agent import AgentAction, ToolExecutor
from schemas.task_spec import TaskSpec

logger = logging.getLogger("openeval.sandbox.docker")


class ExecResult(BaseModel):
    """Result of a command execution inside the Docker container."""

    exit_code: int = Field(..., description="Process exit code (0 for success)")
    stdout: str = Field(default="", description="Captured standard output")
    stderr: str = Field(default="", description="Captured standard error")
    duration_ms: float = Field(default=0.0, description="Execution wall-clock time in milliseconds")


class DockerSandboxConfig(BaseModel):
    """Container runtime configuration and resource boundaries."""

    model_config = ConfigDict(extra="ignore")

    image_tag: str = Field(default="openeval-base:latest", description="Docker image tag")
    dockerfile_path: Path | None = Field(default=None, description="Path to environment Dockerfile")
    cpus: int = Field(default=2, ge=1, description="CPU cores quota")
    memory_mb: int = Field(default=4096, ge=512, description="RAM limit in Megabytes")
    allow_internet: bool = Field(default=False, description="Whether container has network access")
    working_dir: str = Field(default="/workspace", description="Default working directory")
    timeout_sec: float = Field(default=60.0, description="Default command execution timeout")


class DockerSandbox:
    """Manages the isolated Docker container lifecycle for agent task execution."""

    def __init__(
        self,
        config: DockerSandboxConfig,
        docker_client: Any = None,
    ) -> None:
        self.config = config
        self._docker_client = docker_client
        self.container: Any = None
        self._is_started = False

    @property
    def client(self) -> Any:
        """Lazy-load Docker client if not provided explicitly."""
        if self._docker_client is None:
            try:
                import docker  # type: ignore[import-untyped]

                self._docker_client = docker.from_env()
            except Exception as e:
                logger.warning("Failed to initialize Docker client from environment: %s", e)
                raise RuntimeError(
                    "Docker daemon is not reachable. Ensure Docker Desktop is running."
                ) from e
        return self._docker_client

    async def ensure_image_exists(self) -> None:
        """Check if image exists locally; if not and Dockerfile is provided, build it."""
        try:
            self.client.images.get(self.config.image_tag)
            logger.info("Found existing Docker image: %s", self.config.image_tag)
            return
        except Exception:
            logger.info("Image '%s' not found locally. Building...", self.config.image_tag)

        if self.config.dockerfile_path is None or not self.config.dockerfile_path.is_file():
            logger.info("No Dockerfile provided. Pulling '%s'...", self.config.image_tag)
            return

        dockerfile = self.config.dockerfile_path
        context_path = str(dockerfile.parent)

        def _build() -> Any:
            return self.client.images.build(
                path=context_path,
                dockerfile=dockerfile.name,
                tag=self.config.image_tag,
                rm=True,
            )

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _build)
        logger.info("Successfully built Docker image: %s", self.config.image_tag)

    async def start(self) -> None:
        """Create and start the isolated container with resource and network limits."""
        if self._is_started:
            return

        await self.ensure_image_exists()

        network_mode = "bridge" if self.config.allow_internet else "none"
        mem_bytes = self.config.memory_mb * 1024 * 1024
        nano_cpus = int(self.config.cpus * 1e9)

        try:
            self.container = self.client.containers.run(
                image=self.config.image_tag,
                command="tail -f /dev/null",
                detach=True,
                working_dir=self.config.working_dir,
                network_mode=network_mode,
                mem_limit=mem_bytes,
                nano_cpus=nano_cpus,
                pids_limit=256,
                remove=False,
            )
            self._is_started = True
            logger.info("Started Docker sandbox container ID: %s", self.container.id[:12])
        except Exception as e:
            logger.error("Failed to start Docker sandbox container: %s", e)
            raise RuntimeError(f"Docker sandbox startup failed: {e}") from e

    async def stop(self) -> None:
        """Stop and remove the container."""
        if self.container is not None:
            try:
                self.container.remove(force=True)
                logger.info("Removed Docker sandbox container ID: %s", self.container.id[:12])
            except Exception as e:
                logger.warning("Error stopping container: %s", e)
            finally:
                self.container = None
                self._is_started = False

    async def __aenter__(self) -> "DockerSandbox":
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.stop()

    async def exec_command(
        self,
        command: str,
        timeout_sec: float | None = None,
    ) -> ExecResult:
        """Execute a shell command inside the running container."""
        if not self._is_started or self.container is None:
            raise RuntimeError("Sandbox container is not running. Call start() first.")

        timeout = timeout_sec or self.config.timeout_sec
        start_time = time.perf_counter()

        def _run() -> Any:
            return self.container.exec_run(
                cmd=["/bin/bash", "-c", command],
                workdir=self.config.working_dir,
                demux=True,
            )

        try:
            loop = asyncio.get_running_loop()
            exec_output = await asyncio.wait_for(
                loop.run_in_executor(None, _run),
                timeout=timeout,
            )

            exit_code = exec_output.exit_code
            stdout_bytes, stderr_bytes = exec_output.output
            stdout_str = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            stderr_str = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ExecResult(
                exit_code=exit_code,
                stdout=stdout_str,
                stderr=stderr_str,
                duration_ms=round(duration_ms, 2),
            )

        except TimeoutError:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ExecResult(
                exit_code=124,
                stdout="",
                stderr=f"Command timed out after {timeout}s: {command}",
                duration_ms=round(duration_ms, 2),
            )

    def _resolve_path(self, container_path: str) -> str:
        """Ensure container paths are absolute within working_dir."""
        cleaned = container_path.strip()
        if not cleaned.startswith("/"):
            return f"{self.config.working_dir.rstrip('/')}/{cleaned}"
        return cleaned

    async def read_file(self, container_path: str) -> str:
        """Read text content of a file from the container."""
        full_path = self._resolve_path(container_path)
        res = await self.exec_command(f"cat '{full_path}'")
        if res.exit_code != 0:
            raise FileNotFoundError(f"Failed to read {full_path}: {res.stderr.strip()}")
        return res.stdout

    async def write_file(self, container_path: str, content: str) -> None:
        """Write text content to a file in the container using tar stream."""
        if not self._is_started or self.container is None:
            raise RuntimeError("Sandbox container is not running.")

        full_path = self._resolve_path(container_path)
        path = Path(full_path)
        dest_dir = str(path.parent)
        file_name = path.name

        # Guarantee parent directory exists inside container
        await self.exec_command(f"mkdir -p '{dest_dir}'")

        tar_stream = io.BytesIO()
        encoded = content.encode("utf-8")
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            tarinfo = tarfile.TarInfo(name=file_name)
            tarinfo.size = len(encoded)
            tarinfo.mtime = int(time.time())
            tar.addfile(tarinfo, io.BytesIO(encoded))

        tar_stream.seek(0)

        def _put() -> None:
            self.container.put_archive(path=dest_dir, data=tar_stream.read())

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _put)

    def as_tool_executor(self) -> ToolExecutor:
        """Return a callable ToolExecutor compatible with ReActAgent."""

        async def _execute(action: AgentAction) -> str:
            if action.tool == "execute_bash":
                if not action.command:
                    return "Error: execute_bash called without 'command'"
                result = await self.exec_command(action.command)
                output = result.stdout
                if result.stderr:
                    output += f"\n[stderr]: {result.stderr}"
                if result.exit_code != 0:
                    output += f"\n[exit code]: {result.exit_code}"
                return output.strip() or "(Command executed with no output)"

            if action.tool == "view_file":
                if not action.path:
                    return "Error: view_file called without 'path'"
                try:
                    return await self.read_file(action.path)
                except Exception as err:
                    return f"Error reading file: {err}"

            if action.tool == "write_file":
                if not action.path or action.content is None:
                    return "Error: write_file requires both 'path' and 'content'"
                try:
                    await self.write_file(action.path, action.content)
                    return f"Successfully wrote {len(action.content)} bytes to {action.path}"
                except Exception as err:
                    return f"Error writing file: {err}"

            return f"Unknown tool: {action.tool}"

        return _execute


def create_sandbox_for_task(
    task: TaskSpec,
    image_tag: str | None = None,
    docker_client: Any = None,
) -> DockerSandbox:
    """Factory creating a configured DockerSandbox from a validated TaskSpec."""
    tag = image_tag or f"openeval-task-{task.task_id}:latest"
    config = DockerSandboxConfig(
        image_tag=tag,
        dockerfile_path=task.dockerfile_path,
        cpus=task.environment.cpus,
        memory_mb=task.environment.memory_mb,
        allow_internet=task.environment.allow_internet,
        timeout_sec=task.verifier.timeout_sec,
    )
    return DockerSandbox(config=config, docker_client=docker_client)
