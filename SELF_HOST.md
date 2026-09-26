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

## 2. Production Docker Compose (Single Service)

The production Docker Compose configuration runs a single prebuilt container pulling directly from GHCR:
- **Port 80:8000**: Unified FastAPI backend serving the precompiled React 19 UI with DuckDB session persistence mounted at `/var/lib/openeval`.
- Zero local compilation on host (avoids OOM on 1GB memory instances).

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

### Inspect Container Status
```bash
docker compose -f docker-compose.prod.yml ps
```

### View Application Logs
```bash
docker compose -f docker-compose.prod.yml logs -f openeval
```

---

## 3. Cloud Deployment (AWS London `eu-west-2`)

For evaluating remote agent fleets or providing a public reviewer demo link, use the integrated **Cloud Power Switch**:

```bash
# Spin up London EC2 t3.micro instance with prebuilt GHCR container (~2-3 min)
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
| `WATCHER_STORAGE_DIR` | `.runs/watcher` | Directory path where session transcripts, DuckDB, and reviews are persisted (`/var/lib/openeval` in cloud). |

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
