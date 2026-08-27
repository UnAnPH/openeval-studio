# AGENTS.md — Agent & LLM Contributor Guide 🤖

> **Architectural context, coding standards, and extension protocols for autonomous AI coding assistants working in the `openeval-studio` repository.**

---

## 🏛️ System Overview & Architecture

`openeval-studio` is a dual-engine evaluation workbench for autonomous coding agents:
1. **Engine A (Custom OpenEval Engine):** FastAPI backend + Async ReAct Agent Loop (`engine/react_agent.py`) + Docker Container Sandbox (`sandbox/docker_runner.py`) + SSE Event Streaming (`/api/eval/stream/{run_id}`) + Multi-dimensional LLM Judges (`engine/judges.py`).
2. **Engine B (UK AISI Inspect Bridge):** Native Inspect AI integration (`inspect_tasks.py`, `engine/inspect_bridge.py`, `server/inspect_loader.py`) executing via `eval_async()` and producing `.eval` log files in `logs/`.

---

## 📁 5-File Benchmark Task Contract

All benchmark challenges live in `tasks/<task-id>/` and MUST contain exactly 5 components:

1. **`task.toml`**: Validated by `schemas/task_spec.py::TaskSpec`. Defines `[task]` (metadata), `[agent]` (timeout, max_steps, memory_limit, cpu_quota), and `[verifier]`.
2. **`instruction.md`**: Prompt given to the agent.
3. **`environment/`**: Contains initial workspace files and a working `Dockerfile` based on `python:3.11-slim` or `ubuntu:22.04`.
4. **`solution/solve.sh`**: Known reference bash solution that completes the task.
5. **`tests/test_outputs.py`**: Held-out pytest suite injected ONLY during the verification step.

---

## 🛠️ How to Add a New Benchmark Task

1. Create directory `tasks/<my-task-name>/` following the 5-file standard.
2. Register the task in `inspect_tasks.py`:
   ```python
   @task
   def my_task_name() -> Task:
       """Description of task."""
       return build_inspect_task_for_dir("tasks/my-task-name")
   
   # Add to TASKS_REGISTRY
   TASKS_REGISTRY["my_task_name"] = my_task_name
   ```
3. Add a unit test in `tests/test_task_spec.py` or verify with:
   ```bash
   uv run python cli.py run tasks/my-task-name --model gemini-3.1-flash-lite
   ```

---

## 🛡️ How to Add a New LLM Safety Judge

1. In `engine/judges.py`:
   - Add the metric to `JudgeVerdict.metric_name`.
   - Implement `@staticmethod async def audit_<metric>(trajectory: AgentTrajectory, task: TaskSpec, runner: AsyncLLMRunner) -> JudgeVerdict:`.
   - Hook into `TrajectoryJudges.audit_full_trajectory`.
2. In `engine/inspect_bridge.py`:
   - Create `@scorer(metrics=[]) def <metric>_scorer(task_spec: TaskSpec) -> Scorer:`.
   - Attach to `build_inspect_task_for_dir()`.

---

## 💻 Code Standards & Quality Rules

* **Python:** Python 3.12+. Use strict typing (`from typing import ...`).
* **Data Models:** Use Pydantic v2 (`BaseModel`, `Field`, `ConfigDict(extra="ignore")`).
* **Frontend:** React 19, TypeScript, Tailwind CSS, Lucide icons.
* **Imports:** Cleanly sorted with Ruff (`uv run ruff check --fix .`).
* **Testing:** Every change must maintain **100% passing tests** (`uv run pytest tests/ -v`).
* **Type Checking:** Strict Mypy compliance (`uv run mypy schemas/ engine/ sandbox/ server/`).

---

## 🧪 Verification Commands (Run Before Submitting Work)

```bash
# 1. Run full unit test suite
uv run pytest tests/ -v

# 2. Run Ruff linter
uv run ruff check .

# 3. Run Mypy static type checker
uv run mypy schemas/ engine/ sandbox/ server/

# 4. Build Frontend bundle
cd ui && npm run build
```
