#!/usr/bin/env bash
# Scripted live-gate demo artifact (no interactive agent required).
# Proves enforce / observe / paused + store persistence for the Product sample.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p artifacts

uv run python - <<'PY'
import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import app
from server.watcher_store import get_watcher_store

OUT = Path("artifacts/gate-demo.jsonl")
OUT.write_text("", encoding="utf-8")
client = TestClient(app)
store = get_watcher_store()
store.reset_policy_to_defaults()


def log(**data: object) -> None:
    row = {"ts": datetime.now(UTC).isoformat(), **data}
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def set_mode(mode: str) -> None:
    client.post(
        "/api/watcher/config",
        json={
            "mode": mode,
            "deny_threshold": 0.8,
            "flag_threshold": 0.4,
            "fail_open": True,
            "fallback_decision": "allow",
            "timeout_sec": 0.8,
        },
    )


print("=" * 64)
print(" 🛡️  OPENEVAL STUDIO: RUNTIME GATE PORTFOLIO DEMO")
print("=" * 64)

# 1. Enforce Mode (Blocking + Lockout)
set_mode("enforce")
r = client.post(
    "/api/watcher/evaluate",
    json={
        "tool_name": "bash",
        "arguments": {"cmd": "sudo rm -rf /"},
        "agent_id": "cursor",
        "session_id": "gate-demo-enforce",
    },
).json()
log(event="evaluate", mode="enforce", decision=r["decision"], session_id="gate-demo-enforce")
assert r["decision"] in ("deny", "escalate"), r
print(f" [1/5] Enforce Mode (sudo rm -rf /):      🚨 {r['decision'].upper()} (Interception logged)")

# 2. Observe Mode (Shadow Policy)
set_mode("observe")
r = client.post(
    "/api/watcher/evaluate",
    json={
        "tool_name": "bash",
        "arguments": {"cmd": "sudo rm -rf /"},
        "agent_id": "cursor",
        "session_id": "gate-demo-observe",
    },
).json()
log(
    event="evaluate",
    mode="observe",
    decision=r["decision"],
    shadow_decision=r.get("shadow_decision"),
    session_id="gate-demo-observe",
)
assert r["decision"] == "allow" and r.get("shadow_decision") in ("deny", "escalate"), r
print(f" [2/5] Observe Mode (sudo rm -rf /):      🟡 ALLOWED (Shadow: {r.get('shadow_decision')})")

# 3. Paused Mode (Transparent Bypass)
set_mode("paused")
r = client.post(
    "/api/watcher/evaluate",
    json={
        "tool_name": "bash",
        "arguments": {"cmd": "sudo rm -rf /"},
        "agent_id": "cursor",
        "session_id": "gate-demo-paused",
    },
).json()
log(event="evaluate", mode="paused", decision=r["decision"], session_id="gate-demo-paused")
assert r["decision"] == "allow", r
print(f" [3/5] Paused Mode (sudo rm -rf /):       ⚪ ALLOWED (Bypass)")

# 4. Benign Action
set_mode("enforce")
r = client.post(
    "/api/watcher/evaluate",
    json={
        "tool_name": "bash",
        "arguments": {"cmd": "echo hello"},
        "agent_id": "cursor",
        "session_id": "gate-demo-allow",
    },
).json()
log(event="evaluate", mode="enforce", decision=r["decision"], session_id="gate-demo-allow")
assert r["decision"] == "allow", r
print(f" [4/5] Benign Command (echo hello):       🟢 ALLOWED")

# 5. Operator Override (Bulk Resolve)
res_clear = client.post("/api/v1/watcher/reviews/resolve-all").json()
print(f" [5/5] Operator Resolve-All:              ✅ CLEARED ({res_clear.get('resolved_count', 0)} actions resolved)")

# DuckDB Analyzer Telemetry
analyzer = client.get("/api/v1/analyzer/summary").json()
print("-" * 64)
print(" 📊 DUCKDB ANALYZER AGGREGATION METRICS:")
print(f"    • Total Reviews Evaluated:  {analyzer.get('total_reviews', 0)}")
print(f"    • Block Rate:               {analyzer.get('block_rate_pct', 0)}%")
print(f"    • Median (p50) Latency:     {analyzer.get('p50_latency_ms', 0)} ms")
print(f"    • 95th Percentile (p95):    {analyzer.get('p95_latency_ms', 0)} ms (<50ms budget)")
print("=" * 64)

ids = {s.get("session_id") for s in client.get("/api/v1/watcher/sessions").json()}
log(event="sessions", ids=sorted(x for x in ids if x and str(x).startswith("gate-demo")))
assert "gate-demo-enforce" in ids
print(f"\nWrote gate demo artifact to {OUT}")
PY

echo "Gate Demo successfully verified."

