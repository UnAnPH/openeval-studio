# OpenEval Studio

> Full-stack evaluation workbench + runtime safety gate for AI coding agents (portfolio / research tooling).

[![Python 3.12+](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React 19.3](https://img.shields.io/badge/React-19.3-61DAFB.svg?logo=react&logoColor=black)](https://react.dev/)
[![Inspect AI](https://img.shields.io/badge/UK%20AISI-Inspect%20AI-purple.svg)](https://inspect.ai-safety-institute.org.uk/)
[![Code Quality](https://img.shields.io/badge/quality-ruff%20%7C%20pyright-success.svg)]()

---

## 1. What It Is

**OpenEval Studio** is a portfolio evaluation workbench and autonomous coding agent safety runtime:

1. **Eval IDE** — sandboxed agent evaluation harness, streaming trajectories, synchronized run compare, hybrid transcript search, and Inspect AI bridge.
2. **Runtime Gate** — real-time PreToolUse governance hooks for **Antigravity**, **Claude Code**, and **Cursor** invoking `/api/gate/evaluate` (alias `/api/watcher/evaluate`) with deterministic command rules, tool thresholds (1–10), and optional LLM triage/deep review.
3. **Org-Wide Analyzer** — DuckDB-backed analytical telemetry tracking cross-session block rates, latency percentiles, and top blocked threat categories.

*Engineering notes: See [PORTFOLIO.md](PORTFOLIO.md) for what this work sample proves, and [SELF_HOST.md](SELF_HOST.md) for self-hosting instructions.*

---

## 2. Capabilities Tour

| Surface | Core Capability | Under the Hood |
| :--- | :--- | :--- |
| **Safety → Control** | Interactive intervention (`deny` / `force_ask`) | PreToolUse hooks intercept unsafe commands (`sudo rm -rf`, credential dumps) before dispatch. |
| **Safety → Sessions** | Full agent trajectory audit & hybrid search | Dense semantic embeddings + lexical keyword search over unclipped agent transcripts. |
| **Safety → Analyzer** | Organization-wide threat & latency metrics | In-process DuckDB SQL queries over session reviews computing $p50/p95$ gate latencies & threat distributions. |
| **Evaluate → Runs** | Inspect AI benchmark execution & compare | Synchronized step-by-step diffs across agent trajectories with 4-pillar safety audit verdicts. |

---

## 3. Local Quickstart

### Run with DEMO Seed (Instant Sandbox):
```bash
# Clone and setup environment
git clone https://github.com/UnAnPH/openeval-studio.git
cd openeval-studio

make install
cd ui && npm install && cd ..
cp .env.example .env

# Enable DEMO seed for Sessions and Control
export OPENEVAL_DEMO_SEED=1

# Start the full stack
make dev   # API on :8000
make ui    # Vite SPA on :5173
```


### Try Sample Hybrid Search Queries:
In **Safety → Sessions**, use hybrid dense vector + lexical search:
- `force push` — matches git force push attempts with destructive modification warnings.
- `sudo` — matches superuser privilege escalation commands.
- `curl | bash` — matches untrusted code execution and piping.
- `build` — matches benign compilation and testing steps.

---

## 4. Runtime Gate Architecture (What Actually Runs)

1. **Deterministic Command Rules** — regex matching backed by DuckDB: `allow`, `triage`, `human`, `deny`, `off`.
2. **Tool Thresholds** — per-tool 1–10 risk limits: auto-approve, escalate, auto-deny.
3. **Fast Triage** — heuristic Secret Reading analysis, or Gemini XML grading when `OPENEVAL_GATE_USE_LLM=1`.
4. **Deep Review** — comprehensive context inspection when risk is elevated.

**Interactive Lockout (`force_ask`):** In `enforce` mode, policy violations trigger an interactive lockout response (`"decision": "force_ask"`, `terminal_reason="lockout"`) in client hooks. This halts autonomous agents and requires human operator sign-off before proceeding.

**Modes (`GET/POST /api/watcher/config`):**
- `enforce`: Active blocking and lockout.
- `observe`: Shadow mode (logs and displays interceptions without blocking execution).
- `paused`: Transparent bypass (allows all actions).

Hooks **fail-open** if the backend API is unreachable *after* evaluating local instant-deny rules.

---

## 5. Installing Agent Hooks

```bash
# Google Antigravity — writes ~/.gemini/config/hooks.json with matcher ".*"
bash scripts/install_antigravity_watcher_hook.sh

# Cursor & Claude Code
# See DEMO.md for setup instructions and hook configuration.

# Run local HTTP smoke test
./scripts/demo_block.sh
```

---

## 6. Development & Quality Bar

```bash
make check      # Ruff format & lint + Pyright typechecking
make test       # Pytest unit tests
cd ui && npm run build # Production UI build
```

---

## 7. Self-Hosting & Deployment

OpenEval Studio runs self-contained locally or in containers without external SaaS dependencies:

### Production Docker Compose Stack
Launch the dual-surface environment (port `8000` live evaluation + port `8001` seeded demo):
```bash
docker compose -f docker-compose.prod.yml up -d --build
```

### Reference Documentation
- **[PORTFOLIO.md](PORTFOLIO.md)** — Architectural design memo, latency benchmarks, and mapping to Apollo Watcher challenges.
- **[SELF_HOST.md](SELF_HOST.md)** — Complete self-hosting manual, environment variables, and authentication configuration.
- **[AWS Deployment Guide](docs/AWS_DEPLOYMENT.md)** — London (`eu-west-2`) Terraform deployment with the Cloud Power Switch (`make cloud-on` / `make cloud-off` / `make cloud-status`).


---

## Author

**Jayson Rosales Andal** — [GitHub](https://github.com/jaysonandal) · [LinkedIn](https://linkedin.com/in/jaysonandal)
