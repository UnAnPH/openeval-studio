#!/usr/bin/env bash
# Lock-8 automated verify: CI smoke + hermetic + gate demos + artifact checks.
# Does NOT rotate API keys. Does NOT require a live Antigravity UI session.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export HOME="${HOME:-/Users/jaysonandal}"

PY="${ROOT}/.venv/bin/python"
PYTEST="${ROOT}/.venv/bin/pytest"
if [[ ! -x "$PY" ]]; then
  echo "missing $PY — run: make install" >&2
  exit 1
fi

pass=0
fail=0
skip=0

ok() { echo "  PASS  $*"; pass=$((pass + 1)); }
bad() { echo "  FAIL  $*"; fail=$((fail + 1)); }
note() { echo "  SKIP  $*"; skip=$((skip + 1)); }

echo "== OpenEval lock-8 verify =="
echo "cwd: $ROOT"
echo "python: $PY"
echo

echo "-- 0) Secret hygiene (litellm) --"
if rg -n 'api_key:\s*os\.environ/' litellm_config.yaml >/dev/null; then
  ok "litellm_config.yaml uses os.environ/… (no hardcoded key pattern required here)"
else
  bad "litellm_config.yaml should use api_key: os.environ/GEMINI_API_KEY"
fi
if rg -n 'api_key:\s*AQ\.|api_key:\s*AIza|api_key:\s*sk-' litellm_config.yaml >/dev/null; then
  bad "litellm_config.yaml still looks like it embeds a raw API key — rotate + scrub"
else
  ok "no obvious raw API key literal in litellm_config.yaml"
fi
echo "  NOTE  Rotate any previously leaked key manually (Step 1) — this script cannot do that."
echo

echo "-- 1) pytest smoke (ci-smoke without uv sync) --"
if "$PYTEST" tests/ -m "not slow" -q --tb=short; then
  ok "pytest tests/ -m 'not slow'"
else
  bad "pytest tests/ -m 'not slow'"
fi
echo

echo "-- 2) hermetic eval smoke --"
if "$PY" scripts/hermetic_eval_smoke.py; then
  ok "hermetic_eval_smoke.py"
else
  bad "hermetic_eval_smoke.py"
fi
if [[ -f artifacts/hermetic-eval.json ]]; then
  ok "artifacts/hermetic-eval.json present"
else
  bad "artifacts/hermetic-eval.json missing"
fi
echo

echo "-- 3) product gate demo --"
# Inline gate demo using project venv (avoid uv run sync on locked .venv/bin/openeval)
if "$PY" - <<'PY'
import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import app
from server.watcher_store import get_watcher_store

OUT = Path("artifacts/gate-demo.jsonl")
OUT.parent.mkdir(parents=True, exist_ok=True)
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


set_mode("enforce")
r = client.post(
    "/api/watcher/evaluate",
    json={
        "tool_name": "bash",
        "arguments": {"cmd": "sudo rm -rf /"},
        "agent_id": "demo",
        "session_id": "gate-demo-enforce",
    },
).json()
log(event="evaluate", mode="enforce", decision=r["decision"], session_id="gate-demo-enforce")
assert r["decision"] in ("deny", "escalate"), r

set_mode("observe")
r = client.post(
    "/api/watcher/evaluate",
    json={
        "tool_name": "bash",
        "arguments": {"cmd": "sudo rm -rf /"},
        "agent_id": "demo",
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

set_mode("paused")
r = client.post(
    "/api/watcher/evaluate",
    json={
        "tool_name": "bash",
        "arguments": {"cmd": "sudo rm -rf /"},
        "agent_id": "demo",
        "session_id": "gate-demo-paused",
    },
).json()
log(event="evaluate", mode="paused", decision=r["decision"], session_id="gate-demo-paused")
assert r["decision"] == "allow", r

set_mode("enforce")
r = client.post(
    "/api/watcher/evaluate",
    json={
        "tool_name": "bash",
        "arguments": {"cmd": "echo hello"},
        "agent_id": "demo",
        "session_id": "gate-demo-allow",
    },
).json()
log(event="evaluate", mode="enforce", decision=r["decision"], session_id="gate-demo-allow")
assert r["decision"] == "allow", r

ids = {s.session_id for s in store.list_sessions()}
log(event="sessions", ids=sorted(x for x in ids if x and str(x).startswith("gate-demo")))
assert "gate-demo-enforce" in ids
print(f"gate demo OK → {OUT}")
PY
then
  ok "gate-demo (venv python)"
else
  bad "gate-demo (venv python)"
fi
if [[ -f artifacts/gate-demo.jsonl ]]; then
  ok "artifacts/gate-demo.jsonl present"
else
  bad "artifacts/gate-demo.jsonl missing"
fi
echo

echo "-- 4) Antigravity hook demo (TestClient + real gate script) --"
if bash scripts/record_antigravity_gate_demo.sh; then
  ok "record_antigravity_gate_demo.sh"
else
  bad "record_antigravity_gate_demo.sh"
fi
if [[ -f artifacts/antigravity-gate-demo.jsonl ]]; then
  ok "artifacts/antigravity-gate-demo.jsonl present"
else
  bad "artifacts/antigravity-gate-demo.jsonl missing"
fi
echo

echo "-- 5) FINDINGS / true-eval artifacts --"
for f in FINDINGS.md DEMO.md README.md \
  artifacts/true-evals/summary/true-eval-matrix.json \
  artifacts/true-evals/aime2025 \
  artifacts/true-evals/gpqa \
  artifacts/true-evals/local; do
  if [[ -e "$f" ]]; then
    ok "$f"
  else
    bad "missing $f"
  fi
done
if rg -n 'true model Pass@k|Eval writeup \(true model' FINDINGS.md >/dev/null; then
  ok "FINDINGS.md has true Pass@k writeup"
else
  bad "FINDINGS.md missing true Pass@k section"
fi
if rg -n 'gpqa|GPQA' FINDINGS.md >/dev/null; then
  ok "FINDINGS.md mentions GPQA (confounder honesty)"
else
  note "FINDINGS.md has no GPQA note"
fi
echo

echo "-- 6) Docker (optional for true-eval re-runs) --"
if docker info >/dev/null 2>&1; then
  ok "Docker daemon reachable"
else
  note "Docker not reachable — true-eval re-runs / Launch sandbox need OrbStack up"
fi
echo

echo "== Summary: $pass pass, $fail fail, $skip skip =="
if [[ "$fail" -gt 0 ]]; then
  echo "Lock-8 verify FAILED"
  exit 1
fi
echo "Lock-8 verify OK (manual left: key rotation + live Antigravity clip + PR CI check)"
exit 0
