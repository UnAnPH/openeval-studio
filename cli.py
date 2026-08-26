"""Command-Line Interface for OpenEval Studio.

Usage:
    # List supported models:
    python cli.py list-models

    # Test Google Gemini:
    python cli.py test-llm --model gemini-3.7-flash

    # Run Single Evaluation:
    python cli.py run tasks/cancel-async-tasks --model gemini-3.7-flash

    # Run Benchmark Matrix Sweep:
    python cli.py sweep --models gemini-3.7-flash gemini-3.1-flash-lite --tasks all

    # Run Integrity Audit:
    python cli.py audit tasks/cancel-async-tasks
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from engine.judges import TrajectoryJudges
from engine.llm_runner import AsyncLLMRunner, ChatMessage, LLMConfig
from engine.react_agent import AgentStep, ReActAgent
from engine.sweep import BenchmarkSweeper, discover_all_tasks
from engine.verifier import VerifierRunner
from sandbox.docker_runner import DockerSandbox, DockerSandboxConfig, create_sandbox_for_task
from schemas.models import get_available_models, get_model_spec
from schemas.task_spec import load_task_spec

load_dotenv()


def print_banner() -> None:
    print("\n" + "=" * 75)
    print(" 🧪 OPENEVAL STUDIO — FRONTIER AGENT EVALUATION ENGINE")
    print("=" * 75 + "\n")


def cmd_list_models(args: argparse.Namespace) -> None:
    """Print formatted catalog of supported models, context limits, and pricing."""
    print_banner()
    provider_filter = args.provider if args.provider != "all" else None
    models = get_available_models(provider=provider_filter)

    print(f"{'ENDPOINT ID':<26} {'NAME':<24} {'TIER':<12} {'CONTEXT':<10} {'IN/OUT ($/1M)'}")
    print("-" * 75)
    for m in models:
        pricing = f"${m.input_cost_per_m:.2f} / ${m.output_cost_per_m:.2f}"
        if m.context_window < 1_000_000:
            ctx = f"{m.context_window // 1000}k"
        else:
            ctx = f"{m.context_window // 1_000_000}M"
        print(f"{m.id:<26} {m.name:<24} {m.tier:<12} {ctx:<10} {pricing}")
    print("\n💡 Run with: python cli.py run <task> --model <endpoint_id>\n")


async def cmd_test_llm(args: argparse.Namespace) -> None:
    """Test live connectivity against Google AI Studio or OpenAI."""
    print_banner()

    model = args.model
    is_openai = model.startswith("gpt-") or model.startswith("o1") or model.startswith("o3")
    provider = "openai" if is_openai else "google"

    if provider == "google":
        api_key = args.api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        key_var = "GEMINI_API_KEY"
    else:
        api_key = args.api_key or os.getenv("OPENAI_API_KEY")
        key_var = "OPENAI_API_KEY"

    if not api_key:
        print(f"❌ Error: {key_var} is not set in your environment or .env file.")
        print(f"   Add to .env: {key_var}=your-api-key-here")
        print("   Or pass via flag: python cli.py test-llm --api-key 'your-key'")
        sys.exit(1)

    print(f"📡 Testing {provider.upper()} endpoint with model: {model}...")
    runner = AsyncLLMRunner(api_key=api_key, provider=provider)

    messages = [
        ChatMessage(role="system", content="You are an AI benchmark evaluation assistant."),
        ChatMessage(
            role="user",
            content=f"Respond with: 'Connected to {provider.upper()} ({model})!' in 1 sentence.",
        ),
    ]

    try:
        config = LLMConfig(model=model, provider=provider, temperature=0.0)
        response = await runner.generate(messages, config=config)

        print("\n✅ Response Received Successfully!")
        print(f"🤖 Content:  {response.content.strip()}")
        print(f"⚡ Latency:  {response.usage.latency_ms} ms")
        print(
            f"📊 Tokens:   {response.usage.total_tokens} total "
            f"({response.usage.prompt_tokens} prompt / "
            f"{response.usage.completion_tokens} completion)"
        )
        print(f"\n🎉 {provider.upper()} Connection Verified! You are ready to run evaluations.")

    except Exception as e:
        print(f"\n❌ API Connection Failed: {e}")
        sys.exit(1)
    finally:
        await runner.close()


async def cmd_run_eval(args: argparse.Namespace) -> None:
    """Run an autonomous ReAct agent against a 5-file benchmark task."""
    print_banner()
    task_path = Path(args.task_dir)
    if not task_path.exists():
        print(f"❌ Error: Task directory '{task_path}' does not exist.")
        sys.exit(1)

    print(f"📂 Loading Task: {task_path.resolve().name}")
    try:
        task = load_task_spec(task_path)
    except Exception as e:
        print(f"❌ Task Validation Error: {e}")
        sys.exit(1)

    print(f"   • Category:   {task.metadata.category}")
    print(f"   • Timeout:    {task.agent.timeout_sec}s")
    print(f"   • Max Steps:  {task.agent.max_steps}")
    print(f"   • RAM Quota:  {task.environment.memory_mb} MB")

    model = args.model
    is_openai = model.startswith("gpt-") or model.startswith("o1") or model.startswith("o3")
    provider = "openai" if is_openai else "google"

    api_key = args.api_key
    if not api_key:
        if provider == "google":
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        else:
            api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        print(f"\n❌ Error: API key for {provider.upper()} is not set.")
        sys.exit(1)

    runner = AsyncLLMRunner(api_key=api_key, provider=provider)

    def _on_step_start(step_idx: int) -> None:
        print(f"\n⏳ [Step {step_idx}] Sending context to {model}...", end="", flush=True)

    def _on_step(step: AgentStep) -> None:
        print(f"\r--- [Step {step.step_number}] ---" + " " * 30, flush=True)
        print(f"💭 Thought: {step.thought}", flush=True)
        action_detail = (
            f"command='{step.action.command or ''}', path='{step.action.path or ''}'"
        )
        print(f"🛠️  Action:  {step.action.tool} ({action_detail})", flush=True)
        obs_preview = step.observation[:200] + ("..." if len(step.observation) > 200 else "")
        print(f"👁️  Obs:     {obs_preview}", flush=True)
        print(f"⚡ Latency: {step.latency_ms}ms | Tokens: {step.tokens_used}", flush=True)

    print(f"\n🚀 Initializing ReAct Agent with {model} ({provider.upper()})...", flush=True)

    try:
        sandbox = create_sandbox_for_task(task)
        await sandbox.start()
        print("🐳 Container sandbox started with network_mode=none.", flush=True)

        executor = sandbox.as_tool_executor()
        agent = ReActAgent(runner=runner, executor=executor)

        print("\n▶️  Starting ReAct Problem Solving Loop...", flush=True)
        trajectory = await agent.solve_task(
            task=task,
            config=LLMConfig(model=model, provider=provider, temperature=0.0),
            on_step_callback=_on_step,
            on_step_start=_on_step_start,
        )

        print("\n" + "=" * 75)
        print(" 📊 EVALUATION SUMMARY SCORECARD")
        print("=" * 75)
        print(f"• Task ID:        {trajectory.task_id}")
        print(f"• Model:          {trajectory.model}")
        print(f"• Status:         {trajectory.status.upper()}")
        print(f"• Total Steps:    {trajectory.total_steps}")
        print(f"• Total Tokens:   {trajectory.total_tokens}")
        print(f"• Wall Duration:  {trajectory.total_duration_sec}s")

        model_spec = get_model_spec(trajectory.model)
        if model_spec:
            est_cost = model_spec.estimate_cost(
                prompt_tokens=int(trajectory.total_tokens * 0.7),
                completion_tokens=int(trajectory.total_tokens * 0.3),
            )
            print(f"• Est. Run Cost:  ${est_cost:.6f} USD")

        if trajectory.final_summary:
            print(f"• Final Summary:  {trajectory.final_summary}")

        print("\n🧪 Running Verifier Test Suite...")
        verifier = VerifierRunner()
        score = await verifier.grade_container(sandbox, task)

        print(f"• Reward:         {score.reward} / 1.0")
        print(f"• Test Result:    {'✅ PASSED' if score.passed else '❌ FAILED'}")
        if score.failure_reason:
            print(f"• Failure Note:   {score.failure_reason}")

        print("\n🛡️  Running AI Safety & Alignment Trajectory Judges...")
        verdicts = await TrajectoryJudges.audit_full_trajectory(
            trajectory, task, runner, model=model
        )
        for v in verdicts:
            status_icon = "✅ PASSED" if v.passed else "🚨 FLAGGED"
            name_display = v.metric_name.replace("_", " ").title()
            print(f"• {name_display:<26} {status_icon} (Score: {v.score:.1f})")
            if not v.passed or v.flagged_steps:
                print(f"  └─ Notes: {v.reasoning}")

        await sandbox.stop()

    except Exception as e:
        print(f"\n❌ Execution Error: {e}")
    finally:
        await runner.close()


async def cmd_sweep(args: argparse.Namespace) -> None:
    """Execute automated multi-model benchmark matrix sweep."""
    print_banner()

    models = args.models
    raw_tasks = args.tasks

    if "all" in raw_tasks or not raw_tasks:
        tasks = discover_all_tasks("tasks")
    else:
        tasks = []
        for t_name in raw_tasks:
            p = Path("tasks") / t_name if not Path(t_name).exists() else Path(t_name)
            if p.exists():
                tasks.append(load_task_spec(p))

    if not tasks:
        print("❌ Error: No valid benchmark tasks discovered.")
        sys.exit(1)

    print("🚀 Starting Benchmark Matrix Sweep:")
    print(f"   • Models ({len(models)}): {', '.join(models)}")
    print(f"   • Tasks  ({len(tasks)}):  {', '.join(t.task_id for t in tasks)}")
    print(f"   • Total Runs: {len(models) * len(tasks)}")
    print(f"   • Output:     {args.output}\n")

    def _progress(current: int, total: int, model: str, task_id: str) -> None:
        print(f"[{current}/{total}] ⚙️  Evaluating {model} on '{task_id}'...", flush=True)

    sweeper = BenchmarkSweeper(output_file=Path(args.output))
    report = await sweeper.run_matrix_sweep(
        tasks=tasks,
        models=models,
        api_key=args.api_key,
        on_run_progress=_progress,
    )

    print("\n" + "=" * 75)
    print(" 🏆 BENCHMARK EVALUATION SCOREBOARD")
    print("=" * 75 + "\n")
    print(report.to_markdown_table())
    json_path = Path(args.output).with_suffix(".json")
    print(f"💾 Report saved successfully to: {args.output} and {json_path}\n")


async def cmd_audit_task(args: argparse.Namespace) -> None:
    """Run Oracle vs Nop integrity audit on a benchmark task."""
    print_banner()
    task_path = Path(args.task_dir)
    print(f"🔍 Auditing Benchmark Integrity for: {task_path.resolve().name}")

    try:
        task = load_task_spec(task_path)
    except Exception as e:
        print(f"❌ Task Validation Error: {e}")
        sys.exit(1)

    sandbox = DockerSandbox(
        config=DockerSandboxConfig(
            cpus=task.environment.cpus,
            memory_mb=task.environment.memory_mb,
            allow_internet=False,
        )
    )

    try:
        await sandbox.start()
        verifier = VerifierRunner()
        print("⚙️  Running Oracle and Nop verification suites in sandbox...")
        report = await verifier.run_full_benchmark_audit(sandbox, task)

        print("\n" + "=" * 75)
        print(" 📋 BENCHMARK INTEGRITY AUDIT REPORT")
        print("=" * 75)
        print(f"• Task ID:            {report.task_id}")
        oracle_status = "✅ PASS (1.0)" if report.oracle_passed else "❌ FAIL"
        nop_status = "✅ PASS (0.0)" if not report.nop_passed else "❌ FAILED (False Positive)"
        valid_status = "🌟 CERTIFIED" if report.is_benchmark_valid else "⚠️ INVALID"

        print(f"• Oracle Solvable:    {oracle_status}")
        print(f"• Nop Invariant:      {nop_status}")
        print(f"• Integrity Valid:    {valid_status}")
        print(f"• Audit Notes:        {report.audit_notes}")

        await sandbox.stop()

    except Exception as e:
        print(f"\n❌ Audit Failed: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenEval Studio CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list-models command
    p_models = subparsers.add_parser("list-models", help="List supported models and pricing")
    p_models.add_argument(
        "--provider", default="all", choices=["all", "google", "openai"], help="Filter by provider"
    )

    # test-llm command
    p_test = subparsers.add_parser("test-llm", help="Test LLM API connectivity")
    p_test.add_argument(
        "--model", default="gemini-3.1-flash-lite", help="Model endpoint ID"
    )
    p_test.add_argument("--api-key", default=None, help="Explicit API key")

    # run command
    p_run = subparsers.add_parser("run", help="Run ReAct agent against a task")
    p_run.add_argument("task_dir", help="Path to task folder (e.g. tasks/feed-sync-platform)")
    p_run.add_argument(
        "--model", default="gemini-3.1-flash-lite", help="Model endpoint ID"
    )
    p_run.add_argument("--api-key", default=None, help="Explicit API key")

    # sweep command
    p_sweep = subparsers.add_parser("sweep", help="Run multi-model benchmark matrix sweep")
    p_sweep.add_argument(
        "--models",
        nargs="+",
        default=["gemini-3.7-flash", "gemini-3.1-flash-lite"],
        help="Model IDs to benchmark",
    )
    p_sweep.add_argument(
        "--tasks",
        nargs="+",
        default=["all"],
        help="Task folder slugs or 'all'",
    )
    p_sweep.add_argument("--output", default="REPORT.md", help="Output markdown report path")
    p_sweep.add_argument("--api-key", default=None, help="Explicit API key")

    # audit command
    p_audit = subparsers.add_parser("audit", help="Run Oracle vs Nop integrity audit")
    p_audit.add_argument("task_dir", help="Path to task folder")

    args = parser.parse_args()

    if args.command == "list-models":
        cmd_list_models(args)
    elif args.command == "test-llm":
        asyncio.run(cmd_test_llm(args))
    elif args.command == "run":
        asyncio.run(cmd_run_eval(args))
    elif args.command == "sweep":
        asyncio.run(cmd_sweep(args))
    elif args.command == "audit":
        asyncio.run(cmd_audit_task(args))


if __name__ == "__main__":
    main()
