# Self-hosting OpenEval Studio

Runs as a local dev stack, a five-service Docker Compose deployment, or a single
EC2/Azure VM via Terraform. PostgreSQL 16 is required; Redis is optional.

## 1. Local development

Prerequisites: Python 3.12+, [`uv`](https://docs.astral.sh/uv/), Docker, Node 20+.

```bash
git clone https://github.com/UnAnPH/openeval-studio.git
cd openeval-studio

docker compose up -d postgres redis     # PostgreSQL 16 + Redis 7
uv sync --extra dev
uv run alembic upgrade head

cd ui && npm install && npm run build && cd ..
uv run uvicorn server.app:app --host 127.0.0.1 --port 8000 --reload
```

For UI hot-reload, run `cd ui && npm run dev` (Vite `:5173` proxies `/api` to `:8000`).
`/api/health` returns 503 when PostgreSQL is unreachable.

## 2. Accounts, API keys, and hooks

1. Open `http://localhost:8000/` and **Create Account**. The username `demo` is
   reserved for the curated public tenant.
2. The **Hook Setup** view shows a one-time `oe_live_...` API key. Hooks send it as
   `Authorization: Bearer <key>` (or `X-OpenEval-Key`); browsers use a signed session
   cookie instead.
3. Export the gate endpoint and key in the shell that launches your agent:

```bash
export OPENEVAL_WATCHER_URL="http://127.0.0.1:8000/api/v1/watcher/evaluate"
export OPENEVAL_API_KEY="oe_live_..."
```

4. Install the hook for your agent:

| Agent | Setup |
| --- | --- |
| **Antigravity** | `./scripts/install_antigravity_watcher_hook.sh` → `~/.gemini/config/hooks.json`, matcher `.*` |
| **Cursor** | `./scripts/install_cursor_watcher_hook.sh` → `.cursor/hooks.json` or `~/.cursor/hooks.json` |
| **Claude Code** | Add a `PreToolUse` entry in `~/.claude/settings.json` running `python3 scripts/claude_code_watcher_hook.py` |
| **Codex** | Wire `scripts/codex_watcher_hook.py` as a PreToolUse wrapper; it reads JSON stdin and writes `decision` + `permission` JSON stdout |
| **OpenCode** | `./scripts/install_opencode_watcher_hook.sh` → plugin at `~/.config/opencode/plugins/openeval-watcher/index.ts` |

Antigravity uses a 2.2 s timeout; the other four use 1.5 s. All five share the same
atomic circuit breaker (`~/.openeval/circuit_breaker.json`): transient errors fail
open, but 3 failures in 60 s trip to `force_ask` / `ask`.

## 3. Production Compose (five services)

`docker-compose.prod.yml` runs on one internal bridge network:

| Service | Image | Role |
| --- | --- | --- |
| `postgres` | `postgres:16-alpine` | Internal `:5432`, data at `/var/lib/openeval/pg`, password from Docker secret `pg_password` |
| `redis` | `redis:7-alpine` | Internal `:6379`; SSE pub/sub bus + Lua rate-limit token bucket |
| `docker-proxy` | `tecnativa/docker-socket-proxy` | Only service that mounts `/var/run/docker.sock:ro`; exposes `CONTAINERS`, `IMAGES`, `EXEC`, `POST` |
| `app` | `ghcr.io/unanph/openeval-studio:${IMAGE_TAG}` | FastAPI + built React UI; runs `alembic upgrade head` then uvicorn on `:8000`; `DOCKER_HOST=tcp://docker-proxy:2375` |
| `caddy` | `caddy:2-alpine` | Publishes `:80`/`:443`, TLS, reverse proxy to `app:8000` |

```bash
docker compose --env-file /etc/openeval.env -f docker-compose.prod.yml pull
docker compose --env-file /etc/openeval.env -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f app
```

The `app` container runs as non-root UID 1000 (`Dockerfile`) and never mounts the
Docker socket itself; eval sandboxes reach the daemon through the proxy. If you do not
need sandboxes, delete `docker-proxy`, drop `DOCKER_HOST`, and mount the socket nowhere.
The runtime gate and PostgreSQL do not need the Docker API.

## 4. Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://openeval:openeval@127.0.0.1:5432/openeval` | PostgreSQL 16 connection string |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | SSE/event bus + rate limiter; falls back in-process if absent |
| `SESSION_SECRET` | dev-only default | HMAC secret for session cookies; `OPENEVAL_ENV=production` rejects a missing/insecure value |
| `OPENEVAL_API_KEY` | empty | Optional system key authenticated as the `owner` tenant |
| `OPENEVAL_DEMO_SEED` | `0` | `1` seeds the isolated `demo` tenant with curated fixtures plus IP-rate-limited live eval launches |
| `OPENEVAL_LOCK_DIR` | `~/.openeval/locks` | Private per-hook lockout flag directory used by the circuit breaker |
| `OPENEVAL_WATCHER_USE_LLM` | `0` | `1` enables LLM review (requires a provider key) |
| `OPENEVAL_WATCHER_USE_DECISION_API` | unset | `1` enables Tier 1.5 `gemini-decision-1`; unset piggybacks on the LLM flag |
| `OPENEVAL_WATCHER_LLM_TIMEOUT` | `0.8` | Server LLM budget, clamped to max 0.8 s |
| `OPENEVAL_WATCHER_ON_FAILURE` | `fail_open` | Server verdict on LLM timeout: `fail_open`, `escalate`, or `fail_closed` |
| `OPENEVAL_GATE_FAIL_MODE` | `open` | `open` or `closed`; hook behavior on gateway failure before the breaker trips |
| `OPENEVAL_MAX_GATEWAY_FAILURES` | `3` | Failures inside the window before lockout |
| `OPENEVAL_FAILURE_WINDOW_SEC` | `60.0` | Sliding failure window |
| `OPENEVAL_CIRCUIT_STATE_FILE` | `~/.openeval/circuit_breaker.json` | Atomic breaker state |
| `OPENEVAL_SIEM_ENDPOINT` | unset | If set, deny/escalate events are POSTed synchronously (Splunk/Datadog/syslog) |
| `OPENEVAL_SIEM_TYPE` / `OPENEVAL_SIEM_TOKEN` | `splunk` / empty | Collector format (`splunk`, `datadog`, `syslog_rfc5424`) and token |
| `OPENEVAL_MCP_WORKSPACE_ROOT` | current directory | Root that the MCP `stdio` server confines `sandbox_bash` and scaffolding to |
| `OPENEVAL_MCP_ALLOW_HOST_BASH` | `0` | `1` allows `sandbox_bash` on the host when no Docker daemon is available |
| `GEMINI_API_KEY` | empty | Provider key for LLM/Tier-1.5 stages |

`OPENEVAL_GATE_USE_LLM` is obsolete: setting it makes startup raise `RuntimeError`.
The old `FAIL_OPEN` variable is no longer read by any hook.

## 5. Verification

```bash
uv run pytest tests/ -m "not slow" -q
curl -fsS http://127.0.0.1:8000/api/health
curl -s -X POST http://127.0.0.1:8000/api/v1/watcher/evaluate \
  -H "Authorization: Bearer $OPENEVAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"bash","tool_input":"echo hello","agent_id":"test"}'
```

## 6. Cloud power switch

```bash
make cloud-on       # AWS eu-west-2, t3.micro, prebuilt GHCR image, ~2-3 min
make cloud-status   # resources + hourly burn
make cloud-off      # destroy everything; $0.00/hr
```

Always-on cost is roughly **$13–15/month** (t3.micro + Elastic IP + 10 GB gp3, with
PostgreSQL 16 in Docker and S3 dumps); destroyed, it is $0.00/hr. See
`docs/AWS_DEPLOYMENT.md` and `terraform/README.md`.
