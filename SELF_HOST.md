# Self-Hosting Guide for OpenEval Studio

OpenEval Studio is designed to run self-contained on your local machine, inside Docker, or on lightweight cloud infrastructure without external SaaS dependencies.

---

## 1. Quickstart: Local Development

### Prerequisites
- **Python 3.12+** (with `uv` recommended: `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **Docker** (for local PostgreSQL 16)
- **Node.js 20+** & `npm`

### Setup Steps
```bash
# 1. Clone repository
git clone https://github.com/UnAnPH/openeval-studio.git
cd openeval-studio

# 2. Start local PostgreSQL 16 database
docker compose up -d postgres

# 3. Install dependencies & run database migrations
uv sync --extra dev
uv run alembic upgrade head

# 4. Install UI dependencies & build
cd ui && npm install && npm run build && cd ..

# 5. Launch services
# Terminal 1: Backend API (FastAPI on http://localhost:8000)
uv run uvicorn server.app:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2: Vite UI Dev Server (http://localhost:5173)
cd ui && npm run dev
```

Open `http://localhost:5173` in your browser.

---

## 2. Multi-Tenant User Registration & Hook Setup

1. **Sign Up:** Navigate to `http://localhost:5173/` (or `https://openeval.studio/`). Click **Create Account** and register with your preferred username and password.
   *(Note: The username `demo` is reserved for public curated fixtures).*
2. **Retrieve API Key:** Upon successful registration, the studio routes directly to the **Hook Setup** view and generates a high-entropy API key (`oe_live_...`). This key is displayed **once**.
3. **Configure Local IDE Hooks:** Copy the pre-filled export commands for your agent:
   ```bash
   export OPENEVAL_WATCHER_URL="http://127.0.0.1:8000/api/watcher/evaluate"
   export OPENEVAL_API_KEY="<user_api_key>"
   ```
4. **Run Hook Installers:**
   - **Cursor:** `./scripts/install_cursor_watcher_hook.sh`
   - **Claude Code:** Configure `~/.claude/settings.json` with `python3 scripts/claude_code_watcher_hook.py`
   - **Google Antigravity:** `./scripts/install_antigravity_watcher_hook.sh`

---

## 3. Production Docker Compose Stack

The production Docker Compose setup runs three services on an isolated internal bridge network (`openeval-net`):
- **`postgres` (PostgreSQL 16)**: Internal port 5432 only. Persistent volume mounted at `/var/lib/openeval/pg`.
- **`app` (OpenEval Studio)**: FastAPI backend and precompiled React 19 UI running on port 8000 internally. Executes `alembic upgrade head` before serving traffic.
- **`caddy` (Caddy 2)**: Reverse proxies ports 80/443 to `app:8000` with automated Let's Encrypt TLS certificates.

```bash
# Pull release images and run
docker compose --env-file /etc/openeval.env -f docker-compose.prod.yml pull
docker compose --env-file /etc/openeval.env -f docker-compose.prod.yml up -d
```

### Inspect Container Status
```bash
docker compose -f docker-compose.prod.yml ps
```

### View Application Logs
```bash
docker compose -f docker-compose.prod.yml logs -f app
```

---

## 4. Cloud Deployment (AWS London `eu-west-2`)

For public reviewer evaluation or deploying a remote safety runtime, use the integrated **Cloud Power Switch**:

```bash
# Spin up London EC2 t3.micro instance with prebuilt GHCR container (~2-3 min)
make cloud-on

# Inspect live resources and estimated hourly burn rate ($/hr)
make cloud-status

# Tear down all resources when finished ($0.00/hr clean slate)
make cloud-off
```

*See [docs/AWS_DEPLOYMENT.md](docs/AWS_DEPLOYMENT.md) for full architecture details and manual Terraform instructions.*

---

## 5. Environment Variables Reference

| Variable | Default | Description |
| :--- | :--- | :--- |
| `DATABASE_URL` | `postgresql+psycopg://openeval:openeval@127.0.0.1:5432/openeval` | PostgreSQL 16 database connection string. |
| `OPENEVAL_DEMO_SEED` | `0` (`1` in demo) | When `1`, seeds demo evaluation fixtures and sessions under the `demo` tenant. |
| `SESSION_SECRET` | *(random secret)* | HMAC-SHA256 signing secret for session cookies. |
| `OPENEVAL_API_KEY` | `""` | Optional system API key (authenticates as `owner` tenant). |
| `WATCHER_ENFORCE_MODE` | `enforce` | Gate policy mode: `enforce` (blocking), `observe` (audit), `paused` (bypass). |
| `FAIL_OPEN` | `true` | When `true`, allows actions if the gateway times out (>1.5s) to avoid freezing developers. |
| `OPENEVAL_WATCHER_USE_LLM` | `0` | When `1`, enables Layer 3 deep LLM trajectory review (requires `GEMINI_API_KEY`). |
| `GEMINI_API_KEY` | `""` | API key for frontier LLM calls (Gemini 2.5/Flash-Lite). |

---

## 6. Verifying Your Installation

```bash
# 1. Run full test suite
uv run pytest tests/ -m "not slow" -q

# 2. Check health endpoint
curl -fsS http://127.0.0.1:8000/api/health

# 3. Test evaluation gate with API key
curl -s -X POST http://127.0.0.1:8000/api/watcher/evaluate \
  -H "Authorization: Bearer <user_api_key>" \
  -H "Content-Type: application/json" \
  -d '{"tool_name": "bash", "tool_input": "echo hello", "agent_id": "test"}'
```
