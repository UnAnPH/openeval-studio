# OpenEval Studio
> Evaluation workbench + Watcher-style runtime safety firewall for coding agents (portfolio / research tooling).

[![Python 3.12+](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React 19](https://img.shields.io/badge/React-19.0-61DAFB.svg?logo=react&logoColor=black)](https://react.dev/)
[![Inspect AI](https://img.shields.io/badge/UK%20AISI-Inspect%20AI-purple.svg)](https://inspect.ai-safety-institute.org.uk/)
[![Code Quality](https://img.shields.io/badge/quality-ruff%20%7C%20pyright-success.svg)]()

---

## About

**OpenEval Studio** is a local full-stack lab for:

1. **Eval IDE** — sandboxed agent tasks, streaming trajectories, run compare, hybrid transcript search, Inspect AI bridge.
2. **Watcher-style runtime gate** — PreToolUse hooks for **Antigravity**, **Claude Code**, and **Cursor** calling `/api/watcher/evaluate` with store-backed command rules, tool thresholds (1–10), and optional LLM triage/deep review.

This is a **learning / portfolio** project inspired by Apollo-style eval tooling and Watcher concepts — not a multi-tenant enterprise SaaS.

### Hiring sample map

| Role | Start here | What to look for |
| --- | --- | --- |
| **Product FS** | `DEMO.md` §1–2, `bash scripts/record_gate_demo.sh`, Safety → Control/Policy | Real hooks, mode contracts, store-backed decisions, fail-open honesty |
| **Research FS** | `DEMO.md` §3, `uv run python scripts/hermetic_eval_smoke.py`, Evaluate → Launch/Compare | Hermetic verifier integrity, honest no-key failure, compare/annotate path |

See **[DEMO.md](DEMO.md)** (<10 min) and **[FINDINGS.md](FINDINGS.md)** (smoke + hermetic writeup).

---

## Watcher stages (what actually runs)

1. **Command rules** — deterministic regex (store-backed): allow / triage / human / deny / off  
2. **Tool thresholds** — per-tool 1–10 gates: auto-approve / escalate≥ / auto-deny≥ / always escalate  
3. **Fast triage** — heuristic Secret Reading Law, or Gemini XML grade when `OPENEVAL_WATCHER_USE_LLM=1`  
4. **Deep review** — heuristic adjustments, or Gemini when enabled  

**Interactive Lockout (`force_ask`):** When in `enforce` mode, safety violations trigger an interactive lockout response (`"decision": "force_ask"`, `terminal_reason="lockout"`) in the client hooks. This halts autonomous agents (Antigravity, Cursor, Claude Code) and requires human intervention rather than allowing the agent to bypass the block.

Modes (`GET/POST /api/watcher/config`): `enforce` (real deny / lockout), `observe` (shadow only), `paused` (allow all).  
Hooks **fail-open** if the API is down *after* local blacklist checks.

**Control vs Policy vs Sessions:**
- **Control** (`#control`) sets runtime mode (`enforce` / `observe` / `paused`), provides a real-time event stream (`All`, `Blocked`, `Closed`), displays full unclipped commands and execution outputs, and supports operator overrides (`👤 Allow All for Clarity` / single-command approval).
- **Sessions** (`#sessions`) hosts the deep session browser with status filters (`All`, `Live` with pulsating indicator, `Blocked`, `Closed`), live active agent discovery, hybrid transcript search, and log ingestion.
- **Policy** (`#policy`) stores command rules + tool thresholds (1–10) and resolution management.

Hooks talk to `/api/watcher/evaluate` and `/api/watcher/config`; prefer `/api/v1/watcher/*` for sessions, resolution, and other Watcher APIs. `WatcherEngine.evaluate_action` is legacy (unit tests).

### UI navigation

Two peer products in the sidebar:

- **Evaluate** — Overview · Catalog · Launch · Runs · Compare · Judges  
- **Safety**
  - **Control** (`#control`) — mode switch (Enforce / Observe / Paused) + live telemetry stream with All / Blocked / Closed tabs, unclipped command/output inspector, and operator resolution actions  
  - **Sessions** (`#sessions`) — trajectory browser with All / Live / Blocked / Closed filters, active session tracking, hybrid transcript search, and log ingest  
  - **Policy** (`#policy`) — command rules + tool thresholds (1–10) + operator review resolve  

Legacy hashes `#firewall` and `#watcher_live` redirect to Control and Policy. 

### Install agent hooks

```bash
# Antigravity (Google) — writes ~/.gemini/config/hooks.json with matcher ".*"
bash scripts/install_antigravity_watcher_hook.sh
# Restart Antigravity / open a new agent session

# Claude Code + Cursor: see DEMO.md

./scripts/demo_block.sh   # HTTP smoke: expect deny/escalate
```

---

## Quick start

```bash
make install
cd ui && npm install && cd ..
cp .env.example .env   # GEMINI_API_KEY optional (needed for LLM stages)

make dev   # API :8000
make ui    # Vite :5173
make check && make test-all
cd ui && npm run build
```

| Env | Purpose |
| --- | --- |
| `OPENEVAL_WATCHER_USE_LLM=1` | Enable Gemini triage/deep review |
| `OPENEVAL_WATCHER_USE_LLM=0` | Force heuristics (default in tests) |
| `WATCHER_WEBHOOK_URL` | POST JSON on block/escalate |
| `OPENEVAL_API_KEY` | Require `X-OpenEval-Key` on write endpoints |

Optional local stack: `docker compose up` (API + Vite UI, DuckDB volume).

Model matrix / pipeline:

```bash
uv run python cli.py sweep --models gemini-3.1-flash-lite --tasks all --output REPORT.md
```

See **FINDINGS.md** for FP/FN notes from Watcher demos.

---

## Stack

| Layer | Tech |
| --- | --- |
| Backend | Python 3.12+, FastAPI, Pydantic v2, DuckDB |
| Eval | Inspect AI bridge, Docker sandboxes, ReAct agent |
| Frontend | React 19, TypeScript, Tailwind, lucide-react |
| Quality | Ruff, Pyright, Pytest |

---

## Author

**Jayson Rosales Andal** — [GitHub](https://github.com/jaysonandal) · [LinkedIn](https://linkedin.com/in/jaysonandal)
