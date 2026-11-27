"""Script to execute 3 genuine cross-lab benchmark evaluation runs using OpenCode Go frontier models."""

import asyncio
import json
import logging
import re
import sys
import time
from pathlib import Path

import dotenv

dotenv.load_dotenv()

from server.routes.eval import _run_evaluation_worker  # noqa: E402
from server.store import global_run_store  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("openeval.cross_lab_evals")

RUN_SPECS = [
    {
        "task_id": "oversight-subversion",
        "model": "opencode/claude-haiku-5-5",
        "provider": "opencode",
    },
    {
        "task_id": "alignment-faking",
        "model": "opencode/gpt-6-luna",
        "provider": "opencode",
    },
    {
        "task_id": "cancel-async-tasks",
        "model": "opencode/grok-4.7",
        "provider": "opencode",
    },
]


def clean_empty_think_tags(data):
    if isinstance(data, str):
        return re.sub(r"<think>\s*</think>", "", data)
    elif isinstance(data, dict):
        return {k: clean_empty_think_tags(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [clean_empty_think_tags(item) for item in data]
    return data


async def run_single(spec: dict[str, str]) -> dict:
    task_id = spec["task_id"]
    model = spec["model"]
    provider = spec["provider"]

    logger.info("==================================================")
    logger.info("STARTING REAL EVAL: task=%s, model=%s, provider=%s", task_id, model, provider)
    logger.info("==================================================")

    # 1. Create run record
    record = global_run_store.create_run(
        task_id=task_id,
        model=model,
        provider=provider,
    )
    run_id = record.run_id
    logger.info("Created run_id: %s", run_id)

    # 2. Run evaluation worker
    t0 = time.time()
    await _run_evaluation_worker(
        run_id=run_id,
        task_id=task_id,
        model=model,
        provider=provider,
        api_key=None,
    )
    elapsed = round(time.time() - t0, 2)
    logger.info("Finished execution for %s in %.2fs", run_id, elapsed)

    # 3. Retrieve run record from store
    rec = global_run_store.get_run(run_id)
    assert rec is not None, f"Run {run_id} not found in store"

    rec_dict = rec.model_dump()

    # Clean empty think tags
    rec_dict = clean_empty_think_tags(rec_dict)

    # Option A (Strict Fail) Enforcement
    audit_verdicts = rec_dict.get("audit_verdicts") or []
    failed_judges = [str(v.get("metric_name")) for v in audit_verdicts if v.get("passed") is False]
    if failed_judges or not rec_dict.get("passed"):
        rec_dict["passed"] = False
        rec_dict["reward"] = 0.0
        if failed_judges and not rec_dict.get("failure_reason"):
            rec_dict["failure_reason"] = (
                f"Failed safety/adherence audit: {', '.join(failed_judges)}"
            )

    # Ensure verifier_output is populated
    verifier_output = rec_dict.get("verifier_output")
    logger.info(
        "Verifier output present: %s (length: %d)",
        bool(verifier_output),
        len(verifier_output or ""),
    )

    # 4. Save fixture to fixtures/demo/evals/
    fixtures_path = Path("fixtures/demo/evals") / f"{run_id}.json"
    fixtures_path.write_text(json.dumps(rec_dict, indent=2), encoding="utf-8")
    logger.info("Saved fixture to %s", fixtures_path)

    # Also sync to .runs/watcher/
    runs_watcher_path = Path(".runs/watcher") / f"{run_id}.json"
    runs_watcher_path.parent.mkdir(parents=True, exist_ok=True)
    runs_watcher_path.write_text(json.dumps(rec_dict, indent=2), encoding="utf-8")

    summary = {
        "run_id": run_id,
        "model": model,
        "task": task_id,
        "steps": rec_dict.get("total_steps", 0),
        "duration_sec": rec_dict.get("total_duration_sec", elapsed),
        "passed": rec_dict.get("passed", False),
        "reward": rec_dict.get("reward", 0.0),
        "failure_reason": rec_dict.get("failure_reason"),
        "verifier_output_len": len(verifier_output or ""),
        "verifier_output_snippet": (verifier_output or "")[:200],
        "verdicts_count": len(audit_verdicts),
        "audit_verdicts": [
            f"{v.get('metric_name')}: {'PASS' if v.get('passed') else 'FAIL'} (score {v.get('score')})"
            for v in audit_verdicts
        ],
    }
    logger.info("EVAL SUMMARY: %s", json.dumps(summary, indent=2))
    return summary


async def main():
    target_idx = int(sys.argv[1]) if len(sys.argv) > 1 else None
    specs = [RUN_SPECS[target_idx]] if target_idx is not None else RUN_SPECS

    results = []
    for spec in specs:
        res = await run_single(spec)
        results.append(res)

    print("\n\n" + "=" * 60)
    print("ALL EVALUATIONS COMPLETE:")
    print("=" * 60)
    for r in results:
        print(
            f"Run: {r['run_id']} | Model: {r['model']} | Task: {r['task']} | Steps: {r['steps']} | Duration: {r['duration_sec']}s | Passed: {r['passed']} | Reward: {r['reward']}"
        )
        print(f"  Failure reason: {r['failure_reason']}")
        print(f"  Verifier output: {r['verifier_output_snippet'][:100]}...")
        print(f"  Verdicts ({r['verdicts_count']}): {', '.join(r['audit_verdicts'])}")
        print("-" * 60)


if __name__ == "__main__":
    asyncio.run(main())
