# OpenEval Studio

> Full-stack evaluation workbench + runtime safety gate for AI coding agents (portfolio / research tooling).

[![Python 3.12+](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React 19](https://img.shields.io/badge/React-19.0-61DAFB.svg?logo=react&logoColor=black)](https://react.dev/)
[![Inspect AI](https://img.shields.io/badge/UK%20AISI-Inspect%20AI-purple.svg)](https://inspect.ai-safety-institute.org.uk/)
[![Code Quality](https://img.shields.io/badge/quality-ruff%20%7C%20pyright-success.svg)]()

---

## 1. What It Is

**OpenEval Studio** is a portfolio and research evaluation lab built to explore frontier AI safety evaluation and autonomous agent governance:

1. **Eval IDE** — sandboxed agent evaluation harness, streaming trajectories, synchronized run compare, hybrid transcript search, and Inspect AI bridge.
2. **Runtime Gate** — real-time PreToolUse governance hooks for **Antigravity**, **Claude Code**, and **Cursor** invoking `/api/gate/evaluate` (alias `/api/watcher/evaluate`) with deterministic command rules, tool thresholds (1–10), and optional LLM triage/deep review.

*Note: This is an independent portfolio and learning lab — not an enterprise multi-tenant SaaS product.*

---

## 2. Hosted Demo

- **Live URL:** [https://openeval-studio.onrender.com](https://openeval-studio.onrender.com) *(or your deployed Render instance)*
- **Data Scope:**
  - **Safety → Sessions & Control:** Display **DEMO-ONLY** seeded session fixtures to demonstrate real-time interception, unclipped commands, and hybrid search without exposing private agent logs.
  - **Evaluate → Launch, Judges & Red Team:** Execute **LIVE** frontier LLM calls when `GEMINI_API_KEY` is provided. When keys are unset, endpoints fail honestly with explicit error messages.

---

## 3. Visual Tour

| Safety Control (Interception Deny) | Sessions (Hybrid Transcript Search) |
|:---:|:---:|
| ![Safety Control Deny](docs/screenshots/control-deny.png) | ![Sessions Search](docs/screenshots/sessions-search.png) |

| Evaluate Compare (Hermetic Diff) | Runs Registry (Red Team Badge & Filter) |
|:---:|:---:|
| ![Compare Diff](docs/screenshots/compare-diff.png) | ![Runs Red Team](docs/screenshots/runs-redteam.png) |

---

## 4. Local Quickstart

### Run with Hosted DEMO Seed:
```bash
# Clone and setup environment
git clone https://github.com/jaysonandal/openeval-studio.git
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

## 5. Runtime Gate Architecture (What Actually Runs)

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

## 6. Installing Agent Hooks

```bash
# Google Antigravity — writes ~/.gemini/config/hooks.json with matcher ".*"
bash scripts/install_antigravity_watcher_hook.sh

# Cursor & Claude Code
# See DEMO.md for setup instructions and hook configuration.

# Run local HTTP smoke test
./scripts/demo_block.sh
```

---

## 7. Development & Quality Bar

```bash
make check      # Ruff format & lint + Pyright typechecking
make test       # Pytest unit tests
cd ui && npm run build # Production UI build
```

---

## 8. Free Cloud Deployment Guide

OpenEval Studio can be hosted **100% for free** without paying for compute or managed databases. When deployed with `OPENEVAL_DEMO_SEED=1`, the service operates hermetically: it serves the pre-seeded developer sessions and real Inspect AI evaluation runs without requiring a live Docker daemon or local coding agent on the host.

### Option A: Render (Recommended — Zero Config)
Render offers a **Free Web Service** tier (512 MB RAM, free SSL, custom domain):
1. Push your repository to GitHub.
2. Sign in to [Render.com](https://render.com) and click **New + $\rightarrow$ Web Service**.
3. Connect your repository. Render will automatically detect `Dockerfile`:
   - **Environment:** `Docker` (uses the multi-stage `Dockerfile` to build Vite UI + FastAPI into one container).
   - **Plan:** `Free`
   - **Environment Variables:**
     - `OPENEVAL_DEMO_SEED`: `1`
     - `WATCHER_ENFORCE_MODE`: `enforce`
     - `FAIL_OPEN`: `true`
4. Click **Deploy Web Service**. Your app will be live at `https://<your-app>.onrender.com` in ~2–3 minutes.
*Note: Render free services spin down after 15 minutes of inactivity and wake up automatically on the first incoming request.*

### Option B: Koyeb (100% Free — Fast & Continuous)
[Koyeb](https://www.koyeb.com/) offers a free Nano instance tier (512 MB RAM, 0.1 vCPU):
1. Sign in to [Koyeb.com](https://www.koyeb.com) and click **Create Service** $\rightarrow$ **GitHub**.
2. Select your `openeval-studio` repository.
3. Select **Dockerfile** as the build method.
4. Set Environment Variables:
   - `OPENEVAL_DEMO_SEED`: `1`
5. Set the internal port to `8000` (or leave default auto-detection).
6. Click **Deploy**. Your app will be live at `https://<your-app>.koyeb.app`.

### Option C: Google Cloud Run (Free Tier: 2 Million Requests / Month)
If you have a Google Cloud account, Cloud Run provides 2 million free container invocations per month:
```bash
gcloud run deploy openeval-studio \
  --source . \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars OPENEVAL_DEMO_SEED=1
```

### Option D: AWS (London eu-west-2) with Application Load Balancer & RDS PostgreSQL
Deploy with an **AWS Application Load Balancer (ALB)**, **Amazon RDS for PostgreSQL**, and an **EC2 instance** with automated 3GB swap memory (Free Tier eligible):
```bash
cd terraform/aws
terraform init
terraform apply
```
SSH into the EC2 instance and launch the containers:
```bash
docker compose -f docker-compose.prod.yml up -d --build
```
*Read the full [AWS Deployment Guide](docs/AWS_DEPLOYMENT.md) for details.*

### Option E: Azure Virtual Machine with Terraform
Deploy on an Azure Linux VM (`Standard_B2s`) with automated credit-saving shutdown schedules:
```bash
cd terraform/azure
terraform init
terraform apply -var="prefix=openeval" -var="location=italynorth"
```

---

## Author

**Jayson Rosales Andal** — [GitHub](https://github.com/jaysonandal) · [LinkedIn](https://linkedin.com/in/jaysonandal)
