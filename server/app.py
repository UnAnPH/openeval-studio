"""FastAPI Server & Real-Time Streaming SSE API for OpenEval Studio.

Exposes REST endpoints for model and task discovery, evaluation launching,
and Server-Sent Events (SSE) for live turn-by-turn ReAct trajectory streaming.
"""

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from engine.judges import TrajectoryJudges
from engine.llm_runner import AsyncLLMRunner, LLMConfig
from engine.react_agent import AgentStep, ReActAgent
from engine.verifier import VerifierRunner
from sandbox.docker_runner import DockerSandbox, create_sandbox_for_task
from schemas.models import ModelSpec, get_available_models, get_default_model, get_model_spec
from schemas.task_spec import load_task_spec
from server.inspect_loader import list_inspect_run_records
from server.store import RunRecord, global_run_store

load_dotenv()
logger = logging.getLogger("openeval.server")

PROJECT_ROOT = Path(__file__).parent.parent
TASKS_DIR = PROJECT_ROOT / "tasks"
LOGS_DIR = PROJECT_ROOT / "logs"

app = FastAPI(
    title="OpenEval Studio API",
    version="0.1.0",
    description="Real-time Evaluation Engine & Sandbox Platform for Frontier AI Agents",
)

# Enable CORS for local development and Vite frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TaskSummary(BaseModel):
    """Summary metadata for a benchmark task."""

    task_id: str
    category: str | None = None
    difficulty: str | None = None
    tags: list[str] = Field(default_factory=list)
    timeout_sec: float = 600.0
    max_steps: int = 100
    memory_mb: int = 2048
    instruction_preview: str = ""


class LaunchEvalRequest(BaseModel):
    """Request payload to launch an autonomous evaluation."""

    task_id: str = Field(..., description="Task directory identifier (e.g. cancel-async-tasks)")
    model: str | None = Field(default=None, description="Target model ID (defaults to primary)")
    api_key: str | None = Field(default=None, description="Optional explicit API key override")


class LaunchEvalResponse(BaseModel):
    """Response returned upon successfully spawning an evaluation."""

    run_id: str
    task_id: str
    model: str
    status: str
    stream_url: str


@app.get("/api/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "version": "0.1.0"}


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = PROJECT_ROOT / "tasks"


@app.get("/api/models", response_model=list[ModelSpec])
async def list_models(provider: str | None = Query(default=None)) -> list[ModelSpec]:
    """Retrieve catalog of supported evaluation models and token pricing."""
    if provider in ("google", "openai"):
        return get_available_models(provider=provider)  # type: ignore[arg-type]
    return get_available_models()


@app.get("/api/tasks", response_model=list[TaskSummary])
async def list_tasks() -> list[TaskSummary]:
    """Discover all 5-file benchmark tasks available in the tasks/ directory."""
    if not TASKS_DIR.exists():
        return []

    summaries: list[TaskSummary] = []
    for entry in sorted(TASKS_DIR.iterdir()):
        if entry.is_dir() and (entry / "task.toml").exists():
            try:
                spec = load_task_spec(entry)
                summaries.append(
                    TaskSummary(
                        task_id=spec.task_id,
                        category=spec.metadata.category,
                        difficulty=spec.metadata.difficulty,
                        tags=spec.metadata.tags,
                        timeout_sec=spec.agent.timeout_sec,
                        max_steps=spec.agent.max_steps,
                        memory_mb=spec.environment.memory_mb,
                        instruction_preview=spec.instruction_text[:140],
                    )
                )
            except Exception as e:
                logger.warning("Failed to load task %s: %s", entry.name, e)

    return summaries


active_run_tasks: dict[str, asyncio.Task[Any]] = {}
active_sandboxes: dict[str, DockerSandbox] = {}


async def _run_evaluation_worker(
    run_id: str,
    task_id: str,
    model: str,
    provider: str,
    api_key: str | None = None,
) -> None:
    """Background worker executing the ReAct agent in Docker and streaming SSE events."""
    task_path = TASKS_DIR / task_id
    if not task_path.exists():
        global_run_store.update_run(
            run_id, status="error", failure_reason=f"Task directory {task_path} not found"
        )
        global_run_store.publish_event(run_id, "error", {"message": "Task directory not found"})
        return

    global_run_store.update_run(run_id, status="running")
    global_run_store.publish_event(
        run_id, "start", {"task_id": task_id, "model": model, "provider": provider}
    )

    try:
        spec = load_task_spec(task_path)
    except Exception as exc:
        global_run_store.update_run(run_id, status="error", failure_reason=str(exc))
        global_run_store.publish_event(run_id, "error", {"message": str(exc)})
        return

    key = api_key
    if not key:
        if provider == "google":
            key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        else:
            key = os.getenv("OPENAI_API_KEY")

    if not key:
        err = f"API key for {provider.upper()} not provided or set in environment"
        global_run_store.update_run(run_id, status="error", failure_reason=err)
        global_run_store.publish_event(run_id, "error", {"message": err})
        return

    runner = AsyncLLMRunner(api_key=key, provider=provider)  # type: ignore[arg-type]

    def _on_step_start(step_idx: int) -> None:
        global_run_store.publish_event(run_id, "step_start", {"step_number": step_idx})

    def _on_step_complete(step: AgentStep) -> None:
        record = global_run_store.get_run(run_id)
        if record:
            record.steps.append(step)
            record.total_steps = len(record.steps)
            record.total_tokens += step.tokens_used
            global_run_store.update_run(
                run_id,
                steps=record.steps,
                total_steps=record.total_steps,
                total_tokens=record.total_tokens,
            )
        global_run_store.publish_event(run_id, "step_complete", step.model_dump())

    sandbox = None
    try:
        sandbox = create_sandbox_for_task(spec)
        active_sandboxes[run_id] = sandbox
        await sandbox.start()
        global_run_store.publish_event(run_id, "sandbox_ready", {"network": "none"})

        executor = sandbox.as_tool_executor()
        agent = ReActAgent(runner=runner, executor=executor)

        trajectory = await agent.solve_task(
            task=spec,
            config=LLMConfig(model=model, provider=provider, temperature=0.0),  # type: ignore[arg-type]
            on_step_callback=_on_step_complete,
            on_step_start=_on_step_start,
        )

        model_spec = get_model_spec(model)
        cost = (
            model_spec.estimate_cost(
                int(trajectory.total_tokens * 0.7), int(trajectory.total_tokens * 0.3)
            )
            if model_spec
            else 0.0
        )

        global_run_store.publish_event(run_id, "verifier_start", {})
        verifier = VerifierRunner()
        grade = await verifier.grade_container(sandbox, spec)

        # Run AI Safety & Alignment Trajectory Judges
        global_run_store.publish_event(run_id, "audits_start", {})
        verdicts = await TrajectoryJudges.audit_full_trajectory(
            trajectory, spec, runner, model=model
        )

        global_run_store.update_run(
            run_id,
            status=trajectory.status,
            total_duration_sec=trajectory.total_duration_sec,
            estimated_cost_usd=cost,
            final_summary=trajectory.final_summary,
            reward=grade.reward,
            passed=grade.passed,
            failure_reason=grade.failure_reason,
            audit_verdicts=verdicts,
        )

        global_run_store.publish_event(
            run_id,
            "completed",
            {
                "status": trajectory.status,
                "reward": grade.reward,
                "passed": grade.passed,
                "duration_sec": trajectory.total_duration_sec,
                "cost_usd": cost,
                "failure_reason": grade.failure_reason,
                "audit_verdicts": [v.model_dump() for v in verdicts],
            },
        )

        await sandbox.stop()

    except asyncio.CancelledError:
        logger.info("Run %s was cancelled by user request.", run_id)
        global_run_store.update_run(
            run_id, status="cancelled", failure_reason="Evaluation run was stopped by user."
        )
        global_run_store.publish_event(
            run_id, "completed", {"status": "cancelled", "failure_reason": "Run stopped by user"}
        )
    except Exception as e:
        logger.error("Run %s failed with exception: %s", run_id, e)
        global_run_store.update_run(run_id, status="error", failure_reason=str(e))
        global_run_store.publish_event(run_id, "error", {"message": str(e)})
    finally:
        active_sandboxes.pop(run_id, None)
        active_run_tasks.pop(run_id, None)
        if sandbox:
            with contextlib.suppress(Exception):
                await sandbox.stop()
        await runner.close()


@app.post("/api/eval/run", response_model=LaunchEvalResponse)
@app.post("/api/eval/launch", response_model=LaunchEvalResponse)
async def launch_eval(req: LaunchEvalRequest) -> LaunchEvalResponse:
    """Spawn an asynchronous evaluation run in the background."""
    task_path = TASKS_DIR / req.task_id
    if not task_path.exists() and not (PROJECT_ROOT / "datasets" / f"{req.task_id}.json").exists():
        raise HTTPException(status_code=404, detail=f"Task '{req.task_id}' not found in tasks/")

    model_id = req.model or get_default_model("google").id
    is_openai = (
        model_id.startswith("gpt-") or model_id.startswith("o1") or model_id.startswith("o3")
    )
    provider = "openai" if is_openai else "google"

    record = global_run_store.create_run(task_id=req.task_id, model=model_id, provider=provider)

    # Spawn asynchronous execution background task
    eval_task = asyncio.create_task(
        _run_evaluation_worker(
            run_id=record.run_id,
            task_id=req.task_id,
            model=model_id,
            provider=provider,
            api_key=req.api_key,
        )
    )
    active_run_tasks[record.run_id] = eval_task

    return LaunchEvalResponse(
        run_id=record.run_id,
        task_id=req.task_id,
        model=model_id,
        status="running",
        stream_url=f"/api/eval/stream/{record.run_id}",
    )


@app.post("/api/eval/runs/{run_id}/stop")
@app.post("/api/eval/stop/{run_id}")
async def stop_eval_run(run_id: str) -> dict[str, str]:
    """Cancel a running evaluation and stop its Docker sandbox immediately."""
    task = active_run_tasks.get(run_id)
    sandbox = active_sandboxes.get(run_id)

    if task and not task.done():
        task.cancel()
    if sandbox:
        await sandbox.stop()

    global_run_store.update_run(
        run_id, status="cancelled", failure_reason="Evaluation run was stopped by user."
    )
    global_run_store.publish_event(
        run_id, "completed", {"status": "cancelled", "failure_reason": "Run stopped by user"}
    )

    return {
        "status": "stopped",
        "run_id": run_id,
        "message": "Evaluation run stopped successfully.",
    }


@app.post("/api/eval/stop_all")
async def stop_all_runs() -> dict[str, Any]:
    """Emergency cancel all active evaluation runs and stop all Docker sandboxes."""
    stopped_count = 0
    for _run_id, task in list(active_run_tasks.items()):
        if not task.done():
            task.cancel()
            stopped_count += 1

    for _run_id, sandbox in list(active_sandboxes.items()):
        await sandbox.stop()

    active_run_tasks.clear()
    active_sandboxes.clear()

    return {"status": "all_stopped", "stopped_count": stopped_count}


@app.post("/api/eval/inspect_run")
async def launch_inspect_eval(req: LaunchEvalRequest) -> dict[str, str]:
    """Launch evaluation using official UK AISI eval_async in background."""
    task_snake = req.task_id.replace("-", "_")
    model_name = req.model or get_default_model("google").id

    # Map to inspect model identifier
    if not model_name.startswith("google/") and not model_name.startswith("openai/"):
        if (
            model_name.startswith("gpt-")
            or model_name.startswith("o1")
            or model_name.startswith("o3")
        ):
            inspect_model = f"openai/{model_name}"
        else:
            inspect_model = f"google/{model_name}"
    else:
        inspect_model = model_name

    async def _run_inspect_task() -> None:
        try:
            from inspect_ai import eval_async

            from inspect_tasks import TASKS_REGISTRY

            task_fn = TASKS_REGISTRY.get(task_snake)
            if task_fn:
                eval_task = task_fn()
                await eval_async(
                    tasks=[eval_task],
                    model=inspect_model,
                    log_dir=str(LOGS_DIR),
                )
            else:
                await eval_async(
                    tasks=f"inspect_tasks.py@{task_snake}",
                    model=inspect_model,
                    log_dir=str(LOGS_DIR),
                )
            logger.info("Inspect eval_async completed successfully for %s", task_snake)
        except Exception as e:
            logger.error("Inspect eval_async failed: %s", e)

    asyncio.create_task(_run_inspect_task())

    return {
        "status": "running",
        "task_id": req.task_id,
        "inspect_task": f"inspect_tasks.py@{task_snake}",
        "model": inspect_model,
        "message": "Inspect evaluation launched via native eval_async()!",
    }


@app.get("/api/eval/runs", response_model=list[RunRecord])
async def list_runs() -> list[RunRecord]:
    """Retrieve all historical evaluation runs from both local store and Inspect logs."""
    studio_runs = global_run_store.list_runs()
    inspect_runs = list_inspect_run_records(LOGS_DIR)

    seen_ids = set()
    combined: list[RunRecord] = []

    for r in studio_runs:
        if r.run_id not in seen_ids:
            seen_ids.add(r.run_id)
            combined.append(r)

    for r in inspect_runs:
        if r.run_id not in seen_ids:
            seen_ids.add(r.run_id)
            combined.append(r)

    combined.sort(key=lambda r: r.created_at, reverse=True)
    return combined


@app.get("/api/eval/runs/{run_id}", response_model=RunRecord)
async def get_run_details(run_id: str) -> RunRecord:
    """Retrieve details and complete step trajectory for a specific run."""
    record = global_run_store.get_run(run_id)
    if record:
        return record

    inspect_runs = list_inspect_run_records(LOGS_DIR)
    for r in inspect_runs:
        if r.run_id == run_id:
            return r

    raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")


@app.delete("/api/eval/runs/{run_id}")
async def delete_single_run(run_id: str) -> dict[str, str]:
    """Delete a specific evaluation run from memory store and disk logs."""
    # 1. Remove from in-memory run store
    global_run_store.delete_run(run_id)

    # 2. Remove matching .eval file from LOGS_DIR if present
    for eval_file in LOGS_DIR.glob("*.eval"):
        if run_id in eval_file.name:
            with contextlib.suppress(Exception):
                eval_file.unlink()

    return {"status": "deleted", "run_id": run_id}


@app.delete("/api/eval/runs")
async def clear_all_runs_endpoint() -> dict[str, str]:
    """Delete all evaluation runs from memory store and disk logs."""
    global_run_store.clear_runs()
    for eval_file in LOGS_DIR.glob("*.eval"):
        with contextlib.suppress(Exception):
            eval_file.unlink()
    return {"status": "all_deleted"}


@app.get("/api/eval/diff")
async def get_trajectory_diff(
    run_a: str = Query(..., description="First Run ID"),
    run_b: str = Query(..., description="Second Run ID"),
) -> dict[str, Any]:
    """Compare two evaluation runs and compute synchronized step-by-step diffs."""
    from engine.diff_engine import TrajectoryDiffEngine

    diff_summary = TrajectoryDiffEngine.load_and_compare(run_a, run_b, LOGS_DIR)
    if not diff_summary:
        raise HTTPException(
            status_code=404,
            detail=f"Could not find one or both runs ('{run_a}', '{run_b}') to diff.",
        )
    return diff_summary.to_dict()


@app.get("/api/eval/analytics/summary")
async def get_analytics_summary() -> dict[str, Any]:
    """Retrieve DuckDB-powered aggregate evaluation metrics and Pareto frontier."""
    from server.analytics_store import DuckDBTraceEngine

    engine = DuckDBTraceEngine(LOGS_DIR)
    leaderboard = engine.get_model_leaderboard()
    pareto = engine.get_cost_pareto_frontier()

    return {
        "leaderboard": leaderboard,
        "pareto_frontier": pareto,
        "total_evals_synced": len(leaderboard),
    }


class SearchRequest(BaseModel):
    """Request payload for transcript search."""

    query: str
    status: str | None = None
    task: str | None = None
    model: str | None = None
    max_results: int = 50


@app.post("/api/eval/search")
async def search_transcripts(req: SearchRequest) -> list[dict[str, Any]]:
    """Search thoughts, actions, commands, and failure modes across all evaluation trajectories."""
    from server.search_engine import TranscriptSearchEngine

    return TranscriptSearchEngine.search(
        query=req.query,
        logs_dir=LOGS_DIR,
        status_filter=req.status,
        task_filter=req.task,
        model_filter=req.model,
        max_results=req.max_results,
    )


run_audit_revisions: dict[str, Any] = {}


@app.get("/api/eval/runs/{run_id}/report")
async def get_safety_audit_report(run_id: str) -> dict[str, Any]:
    """Generate an automated & revisable safety audit report for a specific run."""
    from engine.audit_report import SafetyAuditReportGenerator

    record = await get_run_details(run_id)
    revision = run_audit_revisions.get(run_id)
    return SafetyAuditReportGenerator.generate_report(record, revision)


class ReviseReportRequest(BaseModel):
    """Request payload to save human audit review & sign-off."""

    reviewer_name: str
    sign_off_status: str
    reviewer_notes: str
    overridden_verdicts: dict[str, bool] = Field(default_factory=dict)


@app.post("/api/eval/runs/{run_id}/report/revise")
async def revise_safety_audit_report(run_id: str, req: ReviseReportRequest) -> dict[str, Any]:
    """Save human reviewer sign-off and update safety report."""
    from engine.audit_report import HumanAuditRevision, SafetyAuditReportGenerator

    record = await get_run_details(run_id)
    rev = HumanAuditRevision(
        reviewer_name=req.reviewer_name,
        sign_off_status=req.sign_off_status,
        reviewer_notes=req.reviewer_notes,
        overridden_verdicts=req.overridden_verdicts,
    )
    run_audit_revisions[run_id] = rev
    return SafetyAuditReportGenerator.generate_report(record, rev)


@app.get("/api/eval/export/sft")
async def export_sft_dataset(
    format: str = Query(
        default="openai_chat", description="Export format: openai_chat, sharegpt, dpo_pairs"
    ),
    only_passed: bool = Query(default=True, description="Filter only passing trajectories"),
    task_id: str | None = Query(default=None, description="Optional task filter"),
) -> dict[str, Any]:
    """Export evaluation trajectories formatted as SFT / DPO training datasets."""
    from engine.sft_exporter import SFTDatasetExporter

    data = SFTDatasetExporter.export_runs(
        logs_dir=LOGS_DIR,
        format_type=format,  # type: ignore[arg-type]
        only_passed=only_passed,
        task_id=task_id,
    )
    return {
        "format": format,
        "total_records": len(data),
        "dataset": data,
    }


@app.get("/api/eval/stream/{run_id}")
async def stream_run_events(run_id: str) -> StreamingResponse:
    """Server-Sent Events (SSE) endpoint streaming real-time ReAct thoughts and actions."""
    record = global_run_store.get_run(run_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    queue = global_run_store.subscribe(run_id)

    async def sse_event_generator() -> AsyncGenerator[str, None]:
        # Send initial snapshot event
        yield f"event: snapshot\ndata: {json.dumps(record.model_dump())}\n\n"

        try:
            while True:
                payload = await queue.get()
                event_type = payload.get("event", "message")
                data_str = json.dumps(payload.get("data", {}))
                yield f"event: {event_type}\ndata: {data_str}\n\n"

                if event_type in ("completed", "error"):
                    break
        finally:
            global_run_store.unsubscribe(run_id, queue)

    return StreamingResponse(
        sse_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
