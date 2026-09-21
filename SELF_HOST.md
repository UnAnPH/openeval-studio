# Self-Hosting Guide for OpenEval Studio

OpenEval Studio is designed to run self-contained on your local machine, inside Docker, or on lightweight cloud infrastructure without external SaaS dependencies.

---

## 1. Quickstart: Local Development (60 Seconds)

### Prerequisites
- **Python 3.12+** (with `uv` recommended: `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **Node.js 20+** & `npm`

### Setup Steps
```bash
# 1. Clone repository
git clone https://github.com/UnAnPH/openeval-studio.git
cd openeval-studio

# 2. Install dependencies
make install
cd ui && npm install && cd ..

# 3. Configure environment
cp .env.example .env

# 4. Launch services (runs with pre-seeded demo fixtures)
export OPENEVAL_DEMO_SEED=1
make dev   # FastAPI backend on http://localhost:8000
make ui    # Vite React frontend on http://localhost:5173
```

Open `http://localhost:5173` in your browser.

---

## 2. Production Docker Compose (Dual Surface)

The production Docker Compose configuration runs two isolated instances:
- **Port 8000 (`openeval-live`)**: Real evaluation workbench and active agent runtime gate.
- **Port 8001 (`openeval-demo`)**: Hermetic sandbox seeded with demo trajectories for safe demonstration without exposing private logs.

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

### Inspect Container Status
```bash
docker compose -f docker-compose.prod.yml ps
```

### View Application Logs
```bash
docker compose -f docker-compose.prod.yml logs -f openeval-live
```

---

## 3. Cloud Deployment (AWS London `eu-west-2`)

For evaluating remote agent fleets or providing a shared reviewer link, use the integrated **Cloud Power Switch**:

```bash
# Spin up AWS Application Load Balancer, EC2 t3.micro, and RDS PostgreSQL (~3-4 min)
make cloud-on

# Inspect live resources and estimated hourly burn rate ($/hr)
make cloud-status

# Tear down all resources when finished ($0.00/hr clean slate)
make cloud-off
```

*See [docs/AWS_DEPLOYMENT.md](docs/AWS_DEPLOYMENT.md) for full architecture and manual Terraform steps.*

---

## 4. Environment Variables Reference

| Variable | Default | Description |
| :--- | :--- | :--- |
| `OPENEVAL_DEMO_SEED` | `0` (`1` in demo) | When `1`, serves pre-seeded demo fixtures and bypasses personal machine logs. |
| `WATCHER_ENFORCE_MODE` | `enforce` | Gate mode: `enforce` (active block), `observe` (shadow audit), `paused` (bypass). |
| `FAIL_OPEN` | `true` | When `true`, allows actions if the gateway times out (>1.5s) to avoid freezing developers. |
| `OPENEVAL_WATCHER_USE_LLM` | `0` | When `1`, enables Layer 3 deep LLM trajectory review (requires `GEMINI_API_KEY`). |
| `GEMINI_API_KEY` | `""` | API key for frontier LLM calls (Gemini 2.5/Flash-Lite). |
| `OPENEVAL_API_KEY` | `""` | Optional shared bearer token for authenticating remote client hooks. |
| `ADMIN_USER` | `jayson` | Basic auth username for private workbench endpoints. |
| `ADMIN_PASSWORD` | `""` | Basic auth password. If empty, authentication is disabled. |
| `DATABASE_URL` | `""` | PostgreSQL connection string. If empty, uses SQLite/DuckDB on local disk. |
| `WATCHER_STORAGE_DIR` | `artifacts/watcher` | Directory path where session transcripts and reviews are persisted. |

---

## 5. Verifying Your Installation

```bash
# Run backend smoke test & unit tests
uv run pytest tests/ -m "not slow" -q

# Run gate policy smoke test
python3 scripts/test_cloud_watcher.py http://localhost:8000

# Execute full terminal demo artifact
make portfolio-demo
```
