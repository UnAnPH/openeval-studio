"""Automated Verifier & Benchmark Integrity Harness for OpenEval Studio.

Executes test suites in isolated sandboxes, enforces anti-cheat held-out verifier staging,
and computes Oracle vs. Nop benchmark validity audits.
"""

import logging
import time

from pydantic import BaseModel, ConfigDict, Field

from sandbox.docker_runner import DockerSandbox
from schemas.task_spec import TaskSpec

logger = logging.getLogger("openeval.engine.verifier")


class EvaluationScore(BaseModel):
    """Result of grading a task solution against the verifier test suite."""

    model_config = ConfigDict(extra="ignore")

    reward: float = Field(..., ge=0.0, le=1.0, description="Scalar score (1.0 = pass, 0.0 = fail)")
    passed: bool = Field(..., description="Whether the evaluation criteria were fully met")
    test_output: str = Field(default="", description="Captured stdout/stderr from pytest verifier")
    exit_code: int = Field(default=0, description="Exit code of the verifier process")
    duration_ms: float = Field(default=0.0, description="Grading wall-clock time in milliseconds")
    failure_reason: str | None = Field(default=None, description="Explanation if test failed")


class TaskVerificationReport(BaseModel):
    """Integrity audit report confirming benchmark validity (Oracle vs. Nop)."""

    task_id: str
    oracle_passed: bool = Field(..., description="Oracle run achieved reward 1.0")
    nop_passed: bool = Field(..., description="Untouched container passed (should be False)")
    is_benchmark_valid: bool = Field(
        ..., description="Valid if Oracle passes AND Nop fails (no false positives)"
    )
    oracle_score: EvaluationScore
    nop_score: EvaluationScore
    audit_notes: str = ""


class VerifierRunner:
    """Orchestrates test execution and anti-tampering verification inside Docker sandboxes."""

    def __init__(self, test_runner_cmd: str = "pytest /workspace/tests/test_outputs.py") -> None:
        self.test_runner_cmd = test_runner_cmd

    async def grade_container(
        self,
        sandbox: DockerSandbox,
        task: TaskSpec,
        stage_fresh_tests: bool = True,
    ) -> EvaluationScore:
        """Run the verifier test suite against the container's current state.

        Args:
            sandbox: Running DockerSandbox instance.
            task: Task specification.
            stage_fresh_tests: If True, stages a fresh copy of test_outputs.py from host
                              to prevent agent root tampering.

        Returns:
            EvaluationScore with reward (0.0 or 1.0).
        """
        start_time = time.perf_counter()

        if stage_fresh_tests:
            # Anti-cheat: overwrite /workspace/tests/test_outputs.py with ground-truth copy
            if task.test_outputs_path.exists():
                test_content = task.test_outputs_path.read_text(encoding="utf-8")
                await sandbox.write_file("/workspace/tests/test_outputs.py", test_content)

            test_sh_path = task.task_dir / "tests" / "test.sh"
            if test_sh_path.exists():
                await sandbox.write_file("/workspace/tests/test.sh", test_sh_path.read_text(encoding="utf-8"))
                await sandbox.exec_command("chmod +x /workspace/tests/test.sh")

        timeout = task.verifier.timeout_sec
        test_sh_path = task.task_dir / "tests" / "test.sh"
        cmd = "bash /workspace/tests/test.sh" if test_sh_path.exists() else self.test_runner_cmd

        result = await sandbox.exec_command(cmd, timeout_sec=timeout)
        duration_ms = round((time.perf_counter() - start_time) * 1000.0, 2)

        passed = result.exit_code == 0
        reward = 1.0 if passed else 0.0

        failure_reason = None
        if not passed:
            if result.exit_code == 124:
                failure_reason = f"Verifier timed out after {timeout} seconds."
            elif result.exit_code == 127:
                cmd_bin = cmd.split()[0] if cmd else "pytest"
                failure_reason = (
                    f"Verifier failed with exit code 127 (Command '{cmd_bin}' not found in container PATH. "
                    f"Ensure test dependencies/Python environment are installed in the Docker image)."
                )
            else:
                lines = result.stdout.strip().splitlines()
                tail_output = lines[-3:] if lines else []
                detail = f": {' | '.join(tail_output)}" if tail_output else ""
                failure_reason = f"Verifier failed with exit code {result.exit_code}{detail}"

        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]: {result.stderr}"

        return EvaluationScore(
            reward=reward,
            passed=passed,
            test_output=output.strip(),
            exit_code=result.exit_code,
            duration_ms=duration_ms,
            failure_reason=failure_reason,
        )

    async def verify_oracle(
        self,
        sandbox: DockerSandbox,
        task: TaskSpec,
    ) -> EvaluationScore:
        """Execute ground-truth solve.sh and grade container. Must yield reward 1.0."""
        # Stage and execute solution
        sol_content = task.solution_path.read_text(encoding="utf-8")
        await sandbox.write_file("/workspace/solution/solve.sh", sol_content)
        await sandbox.exec_command("chmod +x /workspace/solution/solve.sh")

        sol_exec = await sandbox.exec_command(
            "/workspace/solution/solve.sh", timeout_sec=task.verifier.timeout_sec
        )
        if sol_exec.exit_code != 0:
            logger.warning("Oracle solve.sh exited with code %d", sol_exec.exit_code)

        return await self.grade_container(sandbox, task)

    async def verify_nop(
        self,
        sandbox: DockerSandbox,
        task: TaskSpec,
    ) -> EvaluationScore:
        """Grade fresh untouched container without running solution. Must yield reward 0.0."""
        return await self.grade_container(sandbox, task)

    async def run_full_benchmark_audit(
        self,
        sandbox: DockerSandbox,
        task: TaskSpec,
    ) -> TaskVerificationReport:
        """Run both Oracle and Nop verifications to certify task validity."""
        # 1. Nop test (fresh container state)
        nop_score = await self.verify_nop(sandbox, task)
        nop_passed = nop_score.passed

        # 2. Oracle test (run solve.sh)
        oracle_score = await self.verify_oracle(sandbox, task)
        oracle_passed = oracle_score.passed

        is_valid = oracle_passed and (not nop_passed)

        notes = []
        if not oracle_passed:
            notes.append("FATAL: Oracle solve.sh failed to pass verifier tests.")
        if nop_passed:
            notes.append("FATAL: Nop run passed without modifications (false positive flaw).")
        if is_valid:
            notes.append("PASSED: Benchmark task is mathematically solvable and non-trivial.")

        return TaskVerificationReport(
            task_id=task.task_id,
            oracle_passed=oracle_passed,
            nop_passed=nop_passed,
            is_benchmark_valid=is_valid,
            oracle_score=oracle_score,
            nop_score=nop_score,
            audit_notes=" ".join(notes),
        )
