"""Benchmark Task Specification & Parser.

Parses and strictly validates the 5-file evaluation task contract:
1. task.toml              - Resource and execution configurations
2. instruction.md         - The task prompt presented to the agent
3. environment/Dockerfile - Agent container image definition
4. solution/solve.sh      - Oracle ground-truth implementation
5. tests/test_outputs.py  - Automated verifier assertions
"""

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaskMetadata(BaseModel):
    """Metadata describing task domain and authoring specs."""

    model_config = ConfigDict(extra="ignore")

    category: str = Field(..., description="Task category (e.g. software_engineering, security)")
    expert_time_estimate_hours: float | None = Field(
        default=None, description="Estimated time for a human expert to solve"
    )
    difficulty: str | None = Field(default=None, description="Qualitative difficulty level")
    tags: list[str] = Field(default_factory=list, description="Keywords and topic tags")


class VerifierConfig(BaseModel):
    """Configuration for the independent verifier execution."""

    model_config = ConfigDict(extra="ignore")

    timeout_sec: float = Field(default=900.0, description="Max execution time for tests (seconds)")
    allow_internet: bool = Field(
        default=False, description="Whether the verifier container has internet access"
    )
    env_vars: dict[str, str] = Field(
        default_factory=dict, description="Custom environment variables injected into verifier"
    )


class AgentConfig(BaseModel):
    """Configuration for the agent scaffold & execution boundaries."""

    model_config = ConfigDict(extra="ignore")

    timeout_sec: float = Field(
        default=18000.0, description="Max total wall-clock time for agent run"
    )
    max_steps: int = Field(default=100, description="Maximum ReAct thought-action loop steps")
    temperature: float = Field(
        default=0.0, description="LLM sampling temperature (0.0 for deterministic benchmarking)"
    )


class EnvironmentConfig(BaseModel):
    """Container resource limits and networking isolation rules."""

    model_config = ConfigDict(extra="ignore")

    build_timeout_sec: float = Field(default=6000.0, description="Max Docker build time in seconds")
    cpus: int = Field(default=2, ge=1, le=64, description="CPU core quota")
    memory_mb: int = Field(default=4096, ge=512, description="RAM limit in Megabytes")
    storage_mb: int = Field(default=10240, ge=1024, description="Disk storage quota in Megabytes")
    gpus: int = Field(default=0, ge=0, description="Number of GPUs attached")
    allow_internet: bool = Field(
        default=True, description="Whether agent container has network access during task"
    )

    @field_validator("cpus", "memory_mb", "storage_mb")
    @classmethod
    def validate_positive_resources(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Resource allocation must be strictly positive")
        return v


class TaskSpec(BaseModel):
    """Complete in-memory representation of a validated 5-file evaluation task."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_id: str = Field(..., description="Unique slug identifying the task")
    task_dir: Path = Field(..., description="Absolute path to the task directory")
    metadata: TaskMetadata
    verifier: VerifierConfig = Field(default_factory=VerifierConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)
    instruction_text: str = Field(..., description="The exact prompt read from instruction.md")

    dockerfile_path: Path
    solution_path: Path
    test_outputs_path: Path

    def validate_files_exist(self) -> None:
        """Ensure all 5 files exist and have non-zero content."""
        required_files = [
            ("Dockerfile", self.dockerfile_path),
            ("Oracle Solution", self.solution_path),
            ("Verifier Tests", self.test_outputs_path),
        ]
        for name, path in required_files:
            if not path.is_file():
                raise FileNotFoundError(f"Missing required task file '{name}': {path}")
            if path.stat().st_size == 0:
                raise ValueError(f"Task file '{name}' is empty: {path}")

        if not self.instruction_text.strip():
            raise ValueError(f"instruction.md in {self.task_dir} cannot be empty")


def load_task_spec(task_path: str | Path) -> TaskSpec:
    """Load, parse, and validate a 5-file benchmark task directory.

    Args:
        task_path: Path to the task directory containing task.toml.

    Returns:
        A strictly validated TaskSpec instance.

    Raises:
        FileNotFoundError: If task.toml or any required benchmark file is missing.
        ValueError: If TOML syntax is invalid or fields fail Pydantic validation.
    """
    directory = Path(task_path).resolve()
    if not directory.is_dir():
        raise FileNotFoundError(f"Task directory does not exist: {directory}")

    toml_file = directory / "task.toml"
    if not toml_file.is_file():
        raise FileNotFoundError(f"Missing 'task.toml' in {directory}")

    instruction_file = directory / "instruction.md"
    if not instruction_file.is_file():
        raise FileNotFoundError(f"Missing 'instruction.md' in {directory}")

    dockerfile = directory / "environment" / "Dockerfile"
    solution = directory / "solution" / "solve.sh"
    tests = directory / "tests" / "test_outputs.py"

    try:
        with open(toml_file, "rb") as f:
            raw_config: dict[str, Any] = tomllib.load(f)
    except Exception as e:
        raise ValueError(f"Failed to parse TOML in {toml_file}: {e}") from e

    with open(instruction_file, encoding="utf-8") as f:
        instruction_content = f.read()

    metadata_data = raw_config.get("metadata", {})
    if not metadata_data:
        metadata_data = {"category": raw_config.get("category", "general")}

    spec = TaskSpec(
        task_id=directory.name,
        task_dir=directory,
        metadata=TaskMetadata(**metadata_data),
        verifier=VerifierConfig(**raw_config.get("verifier", {})),
        agent=AgentConfig(**raw_config.get("agent", {})),
        environment=EnvironmentConfig(**raw_config.get("environment", {})),
        instruction_text=instruction_content,
        dockerfile_path=dockerfile,
        solution_path=solution,
        test_outputs_path=tests,
    )

    spec.validate_files_exist()
    return spec
