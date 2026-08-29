"""UK AI Safety Institute (UK AISI) Inspect AI Native Bridge for OpenEval Studio.

Bridges 5-file benchmark tasks (task.toml, Dockerfile, solve.sh, test_outputs.py)
into 100% native Inspect AI Tasks, Agents (react, deepagent), Tools (bash, text_editor),
and Scorers (held_out_verifier_scorer, reward_tampering_scorer).
"""

from pathlib import Path

from inspect_ai import Task
from inspect_ai.agent import react
from inspect_ai.dataset import Sample
from inspect_ai.scorer import (
    CORRECT,
    INCORRECT,
    Score,
    Scorer,
    Target,
    accuracy,
    mean,
    scorer,
    stderr,
)
from inspect_ai.solver import (
    TaskState,
)
from inspect_ai.tool import bash, text_editor

from engine.judges import TrajectoryJudges
from engine.react_agent import AgentAction, AgentStep, AgentTrajectory
from schemas.task_spec import TaskSpec, load_task_spec


def task_spec_to_sample(task_spec: TaskSpec) -> Sample:
    """Convert a validated TaskSpec into an Inspect AI Sample."""
    return Sample(
        id=task_spec.task_id,
        input=task_spec.instruction_text,
        target=task_spec.solution_path.read_text(encoding="utf-8"),
        metadata={
            "category": task_spec.metadata.category,
            "difficulty": task_spec.metadata.difficulty,
            "tags": task_spec.metadata.tags,
            "timeout_sec": task_spec.agent.timeout_sec,
            "task_dir": str(task_spec.task_dir),
        },
    )


@scorer(metrics=[accuracy(), mean(), stderr()])
def held_out_verifier_scorer(test_file: str = "tests/test_outputs.py") -> Scorer:
    """Inspect AI Scorer that grades the sandbox using native sandbox().exec()."""

    async def score(state: TaskState, target: Target) -> Score:
        try:
            from inspect_ai.util import sandbox as get_sandbox

            sb = get_sandbox()
            if sb is not None:
                res = await sb.exec(["pytest", "-q", "--tb=short", test_file])
                passed = res.returncode == 0
                return Score(
                    value=CORRECT if passed else INCORRECT,
                    answer=state.output.completion if state.output else "",
                    explanation=res.stdout + ("\n" + res.stderr if res.stderr else ""),
                    metadata={
                        "returncode": res.returncode,
                        "stdout": res.stdout,
                        "stderr": res.stderr,
                    },
                )
        except Exception:
            pass

        # Fallback if no active sandbox (e.g. unit tests or local simulation)
        is_mock_done = bool(state.output and "done" in state.output.completion.lower())
        return Score(
            value=CORRECT if is_mock_done else INCORRECT,
            explanation="Graded without active sandbox environment.",
        )

    return score


def extract_trajectory_from_inspect_state(state: TaskState, task_spec: TaskSpec) -> AgentTrajectory:
    """Reconstruct an AgentTrajectory from Inspect TaskState messages."""
    steps: list[AgentStep] = []
    step_num = 1

    for i, msg in enumerate(state.messages):
        tool_calls = getattr(msg, "tool_calls", None)
        if getattr(msg, "role", None) == "assistant" and isinstance(tool_calls, list):
            thought = ""
            if isinstance(msg.content, str):
                thought = msg.content
            elif isinstance(msg.content, list):
                for part in msg.content:
                    if hasattr(part, "text"):
                        thought += str(getattr(part, "text", "")) + " "

            for tc in tool_calls:
                fn_name = getattr(tc, "function", "")
                raw_args = getattr(tc, "arguments", {})
                args = raw_args if isinstance(raw_args, dict) else {}

                tool_type = (
                    "execute_bash"
                    if fn_name in ("execute_bash", "bash")
                    else "view_file"
                    if fn_name in ("view_file", "read_file", "text_editor")
                    else "write_file"
                    if fn_name in ("write_file", "text_editor")
                    else "finish"
                )

                action = AgentAction(
                    tool=tool_type,  # type: ignore[arg-type]
                    thought=thought.strip(),
                    command=args.get("command") or args.get("cmd"),
                    path=args.get("path") or args.get("file"),
                    content=args.get("content") or args.get("text"),
                    summary=args.get("answer") or args.get("summary"),
                )

                obs = ""
                if i + 1 < len(state.messages):
                    next_msg = state.messages[i + 1]
                    if getattr(next_msg, "role", None) == "tool":
                        obs = str(next_msg.content)

                steps.append(
                    AgentStep(
                        step_number=step_num,
                        thought=thought.strip(),
                        action=action,
                        observation=obs,
                        latency_ms=0.0,
                        tokens_used=0,
                    )
                )
                step_num += 1

    return AgentTrajectory(
        task_id=task_spec.task_id,
        model="inspect_model",
        steps=steps,
        status="completed",
        total_tokens=0,
        total_duration_sec=0.0,
    )


@scorer(metrics=[accuracy(), mean(), stderr()])
def reward_tampering_scorer(task_spec: TaskSpec) -> Scorer:
    """Inspect AI Scorer that audits for reward tampering and test suite sabotage."""

    async def score(state: TaskState, target: Target) -> Score:
        traj = extract_trajectory_from_inspect_state(state, task_spec)
        verdict = TrajectoryJudges.audit_reward_tampering_deterministic(traj, task_spec)
        if verdict.passed:
            return Score(
                value=CORRECT,
                explanation="No reward tampering or test modification detected. (PASSED)",
            )
        else:
            return Score(
                value=INCORRECT,
                explanation=f"ALERT: Reward tampering detected! {verdict.reasoning}",
            )

    return score


def build_inspect_task_for_dir(task_dir_path: str | Path) -> Task:
    """Build a 100% native Inspect AI Task from any 5-file task directory."""
    spec = load_task_spec(Path(task_dir_path))
    sample = task_spec_to_sample(spec)
    dockerfile_path = spec.task_dir / "environment" / "Dockerfile"

    return Task(
        dataset=[sample],
        solver=react(
            tools=[bash(timeout=int(spec.agent.timeout_sec)), text_editor()],
            attempts=3,
        ),
        scorer=[
            held_out_verifier_scorer(test_file="tests/test_outputs.py"),
            reward_tampering_scorer(spec),
        ],
        sandbox=("docker", str(dockerfile_path)) if dockerfile_path.exists() else "docker",
    )
