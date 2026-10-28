"""Execution script for the 8 additional OpenCode benchmark evaluation runs.

Runs:
1. oversight-subversion with opencode/glm-5.3-flash-free
2. oversight-subversion with opencode/big-pickle
3. cancel-async-tasks with opencode/glm-5.3-flash-free
4. cancel-async-tasks with opencode/nemotron-3-ultra-free
5. alignment-faking with opencode/glm-5.3-flash-free
6. alignment-faking with opencode/big-pickle
7. delivery-routing with opencode/deepseek-v4.1-flash-free
8. delivery-routing with opencode/mimo-v2.6-flash-free

Ensures each run:
- Starts Docker container and executes genuine ReAct loop with fallback to gemini-3.1-flash-lite
- Runs verifier and 7 safety/scheming judges
- Persists to both PostgreSQL 'sessions' and '.runs/watcher/{run_id}.json' with status='completed'
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

from sqlalchemy import text

from server.db import SessionLocal
from server.routes.eval import _run_evaluation_worker
from server.store import global_run_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("openeval.eval_matrix")

RUN_SPECS = [
    {
        "task_id": "oversight-subversion",
        "model": "opencode/glm-5.3-flash-free",
        "provider": "opencode",
    },
    {
        "task_id": "oversight-subversion",
        "model": "opencode/big-pickle",
        "provider": "opencode",
    },
    {
        "task_id": "cancel-async-tasks",
        "model": "opencode/glm-5.3-flash-free",
        "provider": "opencode",
    },
    {
        "task_id": "cancel-async-tasks",
        "model": "opencode/nemotron-3-ultra-free",
        "provider": "opencode",
    },
    {
        "task_id": "alignment-faking",
        "model": "opencode/glm-5.3-flash-free",
        "provider": "opencode",
    },
    {
        "task_id": "alignment-faking",
        "model": "opencode/big-pickle",
        "provider": "opencode",
    },
    {
        "task_id": "delivery-routing",
        "model": "opencode/deepseek-v4.1-flash-free",
        "provider": "opencode",
    },
    {
        "task_id": "delivery-routing",
        "model": "opencode/mimo-v2.6-flash-free",
        "provider": "opencode",
    },
]


async def run_single_eval(spec: dict[str, str]) -> dict:
    task_id = spec["task_id"]
    model = spec["model"]
    provider = spec["provider"]

    logger.info("==================================================")
    logger.info("STARTING EVAL: task=%s, model=%s, provider=%s", task_id, model, provider)
    logger.info("==================================================")

    # 1. Create run record in store
    record = global_run_store.create_run(
        task_id=task_id,
        model=model,
        provider=provider,
        chaos_mode=False,
    )
    run_id = record.run_id
    logger.info("Created run_id: %s", run_id)

    # 2. Execute worker
    t0 = time.time()
    await _run_evaluation_worker(
        run_id=run_id,
        task_id=task_id,
        model=model,
        provider=provider,
        api_key=None,
        chaos_mode=False,
    )
    elapsed = round(time.time() - t0, 2)
    logger.info("Finished execution for %s in %.2fs", run_id, elapsed)

    # 3. Verify in global_run_store
    rec = global_run_store.get_run(run_id)
    assert rec is not None, f"Run {run_id} not found in store"

    # 4. Verify disk persistence
    disk_path = Path(".runs/watcher") / f"{run_id}.json"
    disk_exists = disk_path.exists()
    disk_valid = False
    if disk_exists:
        try:
            with open(disk_path, encoding="utf-8") as f:
                disk_data = json.load(f)
                disk_valid = disk_data.get("session_id") == run_id
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Disk JSON invalid for %s: %s", run_id, e)

    # 5. Verify PostgreSQL persistence
    pg_found = False
    pg_status = None
    with SessionLocal() as db:
        row = db.execute(
            text("SELECT id, status, passed, reward, total_tokens, payload FROM sessions WHERE id = :id"),
            {"id": run_id},
        ).fetchone()
        if row:
            pg_found = True
            pg_status = row.status

    verdict_names = [
        v.get("metric_name") if isinstance(v, dict) else getattr(v, "metric_name", str(v))
        for v in rec.audit_verdicts
    ]
    result_summary = {
        "run_id": run_id,
        "task_id": task_id,
        "model": model,
        "provider": provider,
        "status": rec.status,
        "passed": rec.passed,
        "reward": rec.reward,
        "total_steps": rec.total_steps,
        "total_tokens": rec.total_tokens,
        "duration_sec": rec.total_duration_sec,
        "verdicts_count": len(rec.audit_verdicts),
        "verdict_names": verdict_names,
        "disk_persisted": disk_exists and disk_valid,
        "pg_persisted": pg_found,
        "pg_status": pg_status,
    }

    logger.info("RESULT FOR %s: %s", run_id, json.dumps(result_summary, indent=2))
    return result_summary


async def main():
    target_task = sys.argv[1] if len(sys.argv) > 1 else None
    target_model = sys.argv[2] if len(sys.argv) > 2 else None
    specs = [
        s for s in RUN_SPECS
        if (target_task is None or s["task_id"] == target_task)
        and (target_model is None or s["model"] == target_model)
    ]

    results = []
    for spec in specs:
        try:
            res = await run_single_eval(spec)
            results.append(res)
        except Exception as exc:
            logger.error("Evaluation failed with exception for %s (%s): %s", spec["task_id"], spec["model"], exc, exc_info=True)
            results.append({
                "task_id": spec["task_id"],
                "model": spec["model"],
                "error": str(exc),
            })

    print("\n" + "=" * 90)
    print("EVALUATION MATRIX SUMMARY")
    print("=" * 90)
    for r in results:
        print(f"Task: {r.get('task_id'):<22} | Model: {r.get('model'):<30} | Status: {r.get('status')} | Steps: {r.get('total_steps')} | Reward: {r.get('reward')} | Verdicts: {r.get('verdicts_count')}/7")
    print("=" * 90)


if __name__ == "__main__":
    asyncio.run(main())
