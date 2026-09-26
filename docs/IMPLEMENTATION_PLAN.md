# OpenEval Studio: PostgreSQL 16 Multi-Tenant Architecture & Production Deployment Plan

## How to run this

Give an Antigravity agent one task at a time. Each task ends in a commit that passes the verify commands. Do not start the next task until those commands pass.

Paste this block at the top of every task:

```text
You are implementing one task in /workspace (OpenEval Studio).
Stack: Python 3.12, FastAPI, Postgres 16, Alembic, SQLAlchemy 2, React/TypeScript.
Decisions already made. Do not revisit them.
- Postgres runs in Docker on the EC2 disk. No RDS. No ALB. No Aurora. No DynamoDB.
- One database, multi-tenant users (including "demo" and registered users).
- https://openeval.studio/demo reads demo only, no password.
- https://openeval.studio/ requires login with username and password (session cookie).
- Hooks with OPENEVAL_API_KEY write as that key's owner.
- Daily pg_dump goes to a private S3 bucket.
- DuckDB is no longer the source of truth.
Do not wipe other users' rows when seeding demo fixtures.
Do not commit passwords, dump files, or .env.
Do not add a laptop log shipper in these tasks.
Preserve AGENTS.md rules: force_ask lockout, full command text, Sessions tabs All|Live|Blocked|Closed, Control tabs All|Blocked|Closed, shell rules only on shell tools.
```

---

## Target layout

```text
EC2 t3.micro (London eu-west-2)
  Caddy :80/:443  ->  app :8000
  app             ->  postgres :5432   (openeval-net Docker bridge only, port 5432 not public)
  volume              /var/lib/openeval/pg
  timer (03:15)       pg_dump | gzip | aws s3 cp  ->  s3://<bucket>/postgres/YYYY-MM-DD.sql.gz
```

Estimated run rate: ~$13–15 per month for the `t3.micro` instance, 10 GB gp3 SSD, and public IPv4. S3 dumps cost pennies.

---

## Relational Schema

```text
users(id, slug unique, password_hash nullable, created_at)
sessions(id, user_id fk, agent_type, status, title, working_dir, current_activity, passed, reward, total_tokens, total_duration_sec, estimated_cost_usd, payload jsonb, created_at, updated_at)
reviews(id, user_id fk, session_id fk, tool_name, tool_input, decision, score, stage, rule_name, explanation, diff, latency_ms, created_at)
trajectory_events(id, user_id fk, session_id fk, kind, payload jsonb, created_at)
api_keys(id, user_id fk, token_hash, created_at)
```

Indexes:
- `ix_sessions_user_created (user_id, created_at)`
- `ix_reviews_user_created (user_id, created_at)`
- `ix_trajectory_events_session (user_id, session_id, created_at)`

All session, review, trajectory, Control, and Analyzer queries filter by `WHERE user_id = :current_user_id`. The active user is resolved by middleware from the session cookie, the `Authorization: Bearer <key>` header, or `X-OpenEval-Surface: demo` (reads only) and injected into a `ContextVar[int]`.

---

## Task Sequence

### Task 1 — Local Postgres, Dependencies, and Alembic Migrations

- **Read these files first:**
  - `pyproject.toml`
  - `server/watcher_store.py` (table columns and data structures currently written)
  - `docker-compose.yml`

- **Do this:**
  1. Add dependencies to `pyproject.toml`: `sqlalchemy>=2.0.0`, `psycopg[binary]>=3.1.0`, `alembic>=1.13.0`.
  2. Add `postgres` service to `docker-compose.yml`:
     - Image `postgres:16-alpine`, container name `openeval-postgres`.
     - Volume `pgdata:/var/lib/postgresql/data`.
     - Port mapped to localhost only: `${POSTGRES_PORT:-127.0.0.1:5432}:5432`.
     - Environment: `POSTGRES_USER=openeval`, `POSTGRES_PASSWORD=openeval`, `POSTGRES_DB=openeval`.
  3. Initialize Alembic with `alembic.ini` and `alembic/env.py` reading `DATABASE_URL` from environment.
  4. Create migration `alembic/versions/0001_init.py` creating the 5 schema tables: `users`, `sessions`, `reviews`, `trajectory_events`, `api_keys`, plus the composite indexes on `(user_id, created_at)`.
  5. Add `make db-up` (`docker compose up -d postgres`) and `make db-migrate` (`uv run alembic upgrade head`) to `Makefile`.

- **Do not do this:**
  - Do not expose port 5432 to `0.0.0.0`.
  - Do not use cloud database services (RDS, Aurora).
  - Do not remove DuckDB code yet in this task.

- **Verify with this command:**
  ```bash
  make db-up && uv run alembic upgrade head && uv run alembic upgrade head
  docker exec openeval-postgres psql -U openeval -d openeval -c '\dt'
  ```
  *(Second migration run is a silent no-op. `\dt` lists 5 tables.)*

---

### Task 2 — Database Engine & Postgres WatcherStore

- **Read these files first:**
  - `server/watcher_store.py`
  - `server/db_pool.py`
  - `schemas/watcher_models.py`

- **Do this:**
  1. Create `server/db.py` defining:
     - SQLAlchemy engine with connection pool (`pool_pre_ping=True`, `pool_size=10`).
     - `SessionLocal = sessionmaker(...)`.
     - `current_user_slug: ContextVar[str] = ContextVar("current_user_slug", default="owner")`.
     - `current_user_id: ContextVar[int | None] = ContextVar("current_user_id", default=None)`.
     - Fast cached lookup function to resolve `user_id` from slug or ID.
  2. Rewrite `server/watcher_store.py` to persist sessions, reviews, and trajectory events to PostgreSQL via SQLAlchemy.
  3. Keep existing public method signatures unchanged:
     - `ensure_live_session`, `create_session`, `record_session`, `get_session`, `list_sessions`
     - `update_session`, `delete_session`, `record_decision`, `get_session_decisions`, `resolve_decision`
     - `append_trajectory_event`, `get_trajectory`, `get_analytics_overview`, `get_analyzer_summary`
     - Policy methods: `get_default_policy`, `reset_policy_to_defaults`, `update_command_rule`, `update_tool_threshold`
  4. In `get_analyzer_summary`, compute latency percentiles directly in SQL:
     ```sql
     SELECT
         percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms) AS p50,
         percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95,
         AVG(latency_ms) AS avg_lat
     FROM reviews
     WHERE user_id = :uid AND latency_ms > 0;
     ```
     When there are no rows, all latency fields must return `0.0` (zero placeholder constants).
  5. Keep SSE subscribe/unsubscribe/broadcast in memory for real-time streaming.
  6. Add pytest fixture in `tests/conftest.py` setting `DATABASE_URL` and running migrations for tests.

- **Do not do this:**
  - Do not change `WatcherStore` method signatures or callers.
  - Do not write to DuckDB files as the source of truth.
  - Do not use hardcoded latency numbers when review tables are empty.

- **Verify with this command:**
  ```bash
  uv run pytest tests/ -m "not slow" -q
  ```

---

### Task 3 — Startup Lifespan, Database Health Probe, and Idempotent Demo Seeding

- **Read these files first:**
  - `server/demo_seed.py`
  - `server/app.py`
  - `fixtures/demo/sessions/`

- **Do this:**
  1. In `server/demo_seed.py`:
     - On startup, idempotently upsert user `demo` and user `owner`:
       ```sql
       INSERT INTO users (slug) VALUES ('demo'), ('owner') ON CONFLICT (slug) DO NOTHING;
       ```
     - Load `fixtures/demo/sessions/*.json` with `INSERT ... ON CONFLICT (id) DO UPDATE` assigned exclusively to the `demo` user ID.
     - Remove any calls that clear all sessions or touch rows where `user_id != demo_user_id`.
  2. In `server/app.py`:
     - Wire database check and demo fixture seeding into the FastAPI lifespan startup handler.
     - Update `GET /api/health` to execute `SELECT 1` on PostgreSQL. Return `200 OK` with database status when connected; return `503 Service Unavailable` if PostgreSQL connection fails.

- **Do not do this:**
  - Do not delete or wipe rows belonging to other users when seeding demo data.
  - Do not return 200 from `/api/health` if the database query fails.

- **Verify with this command:**
  ```bash
  # Seed twice and assert demo sessions count equals fixture count and owner has 0 rows
  uv run python -c "
  from server.demo_seed import seed_demo_data
  seed_demo_data()
  seed_demo_data()
  "
  curl -fsS http://127.0.0.1:8000/api/health
  ```

---

### Task 4 — Tag Release, Lowercased GHCR Publishing, and Deployment Workflow

- **Read these files first:**
  - `.github/workflows/ci.yml`
  - `.github/workflows/deploy-aws.yml`
  - `docker-compose.prod.yml`

- **Do this:**
  1. In `.github/workflows/ci.yml`:
     - Add `tags: ["prod-*"]` to `on.push`.
     - Lowercase repository name before pushing to GHCR: `ghcr.io/unanph/openeval-studio:${TAG}` and `ghcr.io/unanph/openeval-studio:latest`. (GHCR strictly rejects mixed-case repository names).
     - Keep `feat/**` pushes and pull requests test-only (no docker push, no deploy).
     - Add `deploy` job in `.github/workflows/ci.yml` running only for `refs/tags/prod-*` after image push:
       - Connects via SSH using secrets `AWS_EC2_HOST` and `AWS_SSH_PRIVATE_KEY`.
       - Fetches git tags, checks out the tag:
         ```bash
         git fetch --tags
         git checkout $TAG
         echo "IMAGE_TAG=$TAG" > /home/ubuntu/openeval-studio/.release-env
         docker compose --env-file /etc/openeval.env --env-file .release-env -f docker-compose.prod.yml pull
         docker compose --env-file /etc/openeval.env --env-file .release-env -f docker-compose.prod.yml up -d
         ```
  2. In `docker-compose.prod.yml`:
     - Set image to `ghcr.io/unanph/openeval-studio:${IMAGE_TAG:-latest}`.
  3. Document the one-time GHCR visibility requirement:
     - On first push to GHCR, GitHub defaults the package to Private. The user must either set package visibility to Public in GitHub Packages UI or configure a `GHCR_PULL_TOKEN` with `read:packages`.

- **Do not do this:**
  - Do not deploy from branch pushes or pull requests (only `prod-*` tags).
  - Do not use mixed-case image names in Docker commands or workflows.
  - Do not execute `docker compose ... --build` on the EC2 server.

- **Verify with this command:**
  ```bash
  # Validate workflow file syntax
  uv run python -c "
  import yaml
  yaml.safe_load(open('.github/workflows/ci.yml'))
  print('CI workflow YAML syntax OK')
  "
  ```

---

### Task 5 — Production Docker Compose with Caddy TLS & Internal Postgres

- **Read these files first:**
  - `docker-compose.prod.yml`
  - `Dockerfile`

- **Do this:**
  1. Rewrite `docker-compose.prod.yml` with three services connected via a dedicated internal bridge network `openeval-net`:
     - `postgres`:
       - Image: `postgres:16-alpine`
       - Container name: `openeval-postgres`
       - Restart: `unless-stopped`
       - Environment: `POSTGRES_USER=openeval`, `POSTGRES_PASSWORD_FILE=/run/secrets/pg_password`, `POSTGRES_DB=openeval`
       - Volume: `/var/lib/openeval/pg:/var/lib/postgresql/data`
       - Network: `openeval-net` (no host port published).
     - `app`:
       - Image: `ghcr.io/unanph/openeval-studio:${IMAGE_TAG:-latest}`
       - Container name: `openeval-studio`
       - Restart: `unless-stopped`
       - Depends on: `postgres`
       - Environment from `/etc/openeval.env` and `.release-env`
       - Command: `sh -c "uv run alembic upgrade head && uv run uvicorn server.app:app --host 0.0.0.0 --port 8000"`
       - Network: `openeval-net` (port 8000 internal only).
     - `caddy`:
       - Image: `caddy:2-alpine`
       - Container name: `openeval-caddy`
       - Restart: `unless-stopped`
       - Ports: `"80:80"`, `"443:443"`
       - Volumes:
         - `/var/lib/openeval/Caddyfile:/etc/caddy/Caddyfile:ro`
         - `/var/lib/openeval/caddy_data:/data`
         - `/var/lib/openeval/caddy_config:/config`
       - Network: `openeval-net`
  2. Create standard `Caddyfile` template:
     ```caddy
     openeval.studio {
         reverse_proxy app:8000
     }
     ```

- **Do not do this:**
  - Do not publish port 5432 to the host or internet.
  - Do not commit `.release-env`, `/etc/openeval.env`, or password files to git.
  - Do not build images on the EC2 instance.

- **Verify with this command:**
  ```bash
  docker compose -f docker-compose.prod.yml config
  ```
  *(Validates compose config and confirms no port 5432 bindings on host).*

---

### Task 6 — Terraform Infrastructure, S3 Backups, and Restore Runbook

- **Read these files first:**
  - `terraform/aws/main.tf`
  - `terraform/aws/variables.tf`
  - `terraform/aws/outputs.tf`
  - `terraform/aws/cloud-init.yaml`
  - `scripts/cloud_switch.py`

- **Do this:**
  1. In `terraform/aws/main.tf`:
     - Retain 1 VPC, 1 public subnet in London (`eu-west-2a`), 1 `t3.micro` EC2 instance, 10 GB gp3 root disk.
     - Security group: Ingress ports 80 and 443 open to `0.0.0.0/0`. Ingress port 22 open strictly to `var.admin_cidr` (default empty `[]`).
     - Create private S3 bucket (`aws_s3_bucket.backups`) with:
       - `force_destroy = false`
       - All public access blocked (`aws_s3_bucket_public_access_block`).
       - Versioning enabled (`aws_s3_bucket_versioning`).
       - Lifecycle expiration rule deleting objects older than 14 days.
     - IAM instance profile attached to EC2 with least-privilege policy allowing `s3:PutObject` and `s3:GetObject` on `arn:aws:s3:::<bucket_name>/postgres/*`.
  2. Create `scripts/pg_dump_to_s3.sh`:
     - Validates `BACKUP_BUCKET` is non-empty; exits with error code 1 if missing.
     - Runs:
       ```bash
       docker exec openeval-postgres pg_dump -U openeval openeval | gzip | aws s3 cp - "s3://${BACKUP_BUCKET}/postgres/$(date -u +%F).sql.gz"
       ```
  3. In `terraform/aws/cloud-init.yaml`:
     - Write systemd service and timer executing `pg_dump_to_s3.sh` daily at 03:15 UTC.
  4. Create `scripts/pg_restore_from_s3.sh`:
     - Requires argument `s3://<bucket>/postgres/YYYY-MM-DD.sql.gz`.
     - Confirms action with operator, stops `app` container, restores database dump into `openeval-postgres`, and restarts `app`.
  5. In `scripts/cloud_switch.py`:
     - Update hourly cost constants for London: `t3.micro` ($0.0118/hr), Public IPv4 ($0.005/hr), 10GB gp3 ($0.096/GB-mo).
     - Calculate burn rate (~$13–15/month).

- **Do not do this:**
  - Do not create AWS access keys on the instance (use IAM instance profile).
  - Do not make the S3 backup bucket public.
  - Do not re-introduce ALB or RDS resources.

- **Verify with this command:**
  ```bash
  terraform -chdir=terraform/aws validate
  # Test backup script validation
  bash scripts/pg_dump_to_s3.sh || test $? -eq 1
  ```

---

### Task 7 — Multi-Tenant Authentication, Registration, Session Cookies, and API Key Storage

- **Read these files first:**
  - `server/app.py`
  - `server/db.py`
  - `schemas/watcher_models.py`

- **Do this:**
  1. Add password hashing utility (`bcrypt` or `argon2-cffi`) and secure token hashing (`hashlib.sha256`).
  2. Add authentication routes in `server/app.py` (or `server/auth.py` router):
     - `POST /api/auth/register`:
       - Rejects username "demo" (400 Bad Request).
       - Hashes password; creates user in `users` table.
       - Generates high-entropy raw API key (`oe_live_...`), stores SHA-256 hash in `api_keys`.
       - Sets signed server-side session cookie.
       - Returns JSON with raw API key (shown once to user).
     - `POST /api/auth/login`:
       - Verifies username and password hash.
       - Sets signed session cookie.
     - `POST /api/auth/logout`:
       - Clears session cookie.
     - `POST /api/auth/rotate-key`:
       - Authenticated via session cookie.
       - Invalidates existing token hash in `api_keys`, generates new raw key, stores hash, returns new raw key once.
  3. Update middleware in `server/app.py`:
     - If session cookie present: resolve user and set `current_user_slug` / `current_user_id`.
     - If `Authorization: Bearer <key>` present: compute SHA-256 hash, query `api_keys` to resolve user, and set context.
     - If `X-OpenEval-Surface: demo` present:
       - For read requests: set context to `"demo"` and bypass authentication.
       - For `POST /api/watcher/evaluate` or mutating requests: ignore demo header, enforce API key or session cookie, return 401 if unauthenticated.
     - `GET /demo` and `GET /demo/*`: serves SPA with no authentication.
     - `GET /api/health`: stays open.
     - All other app routes require authentication. Basic Auth is removed.
  4. Ensure `users.password_hash` is excluded from all Pydantic response models.

- **Do not do this:**
  - Do not allow registration with username "demo".
  - Do not store raw API keys in the database.
  - Do not return password hashes in API responses.
  - Do not allow the demo header to write or select real user accounts.

- **Verify with this command:**
  ```bash
  uv run pytest tests/test_auth.py -q
  ```
  *(Tests: register user1 and user2; verify separate API keys; verify "demo" username rejected; verify demo header write returns 401).*

---

### Task 8 — Frontend Hook Setup Screen, Key Rotation, and Per-User Session Isolation

- **Read these files first:**
  - `ui/src/App.tsx`
  - `ui/src/components/`
  - `scripts/install_cursor_watcher_hook.sh`
  - `scripts/install_antigravity_watcher_hook.sh`
  - `scripts/claude_code_watcher_hook.py`

- **Do this:**
  1. In `ui/src/App.tsx`:
     - On successful registration, route to the **Hook Setup** view:
       - Displays the raw API key once with a copy button.
       - Renders copyable setup commands for **Cursor**, **Claude Code**, and **Antigravity**, populated with:
         ```bash
         export OPENEVAL_WATCHER_URL="https://openeval.studio/api/watcher/evaluate"
         export OPENEVAL_API_KEY="<user_api_key>"
         ```
     - Add a "Hooks" tab/view in navigation allowing authenticated users to review setup instructions and rotate their API key.
     - If on `/demo` or `/demo/*`:
       - Show prominent top **DEMO-ONLY** banner:
         *"Demo Mode: Viewing sanitized agent evaluation fixtures. Switch to Live Studio (/)"*
       - Include a "Live" link that navigates to `/`.
       - Central fetch wrapper automatically attaches `X-OpenEval-Surface: demo`.
  2. In database queries for sessions, reviews, Control, and Analyzer:
     - Ensure all queries filter strictly by `WHERE user_id = :current_user_id`.
     - Logged-in users can only see and manage their own sessions.

- **Do not do this:**
  - Do not build a laptop log shipper. Transcripts appear when hooks POST them.
  - Do not allow users to view other users' session transcripts or reviews.

- **Verify with this command:**
  ```bash
  cd ui && npm run build
  ```
  *(Smoke test: Register two separate test users, submit evaluate calls with their respective keys, and verify user1's UI shows only user1's sessions, user2 shows only user2's sessions, and `/demo` shows only fixtures).*

---

### Task 9 — CI Postgres Service Integration, Documentation, and End-to-End Verification

- **Read these files first:**
  - `.github/workflows/ci.yml`
  - `docs/AWS_DEPLOYMENT.md`
  - `SELF_HOST.md`
  - `README.md`

- **Do this:**
  1. In `.github/workflows/ci.yml`:
     - Add PostgreSQL 16 service container to the `test` job.
     - Set `DATABASE_URL: postgresql+psycopg://openeval:openeval@localhost:5432/openeval_test`.
     - Run `uv run alembic upgrade head` prior to pytest execution.
  2. Update documentation:
     - `docs/AWS_DEPLOYMENT.md`: Document single EC2 instance, Caddy HTTPS reverse proxy, internal Postgres container, daily S3 backup systemd timer, restore runbook, and ~$13–15/mo cost.
     - `SELF_HOST.md`: Document registration, Hook Setup instructions, environment variables, and Docker Compose stack.
     - `README.md`: Update architecture overview and quickstart links.

- **Do not do this:**
  - Do not leave references to RDS, ALB, or legacy Basic Auth in documentation.
  - Do not skip Alembic migrations in CI test setup.

- **Verify with this command:**
  ```bash
  make check
  uv run pytest tests/ -m "not slow" -q
  ```

---

## Done When

- [ ] **Tag Release:** Pushing a `prod-*` tag (e.g. `prod-1.0.1`) triggers CI tests, builds and pushes lowercased `ghcr.io/unanph/openeval-studio:prod-1.0.1` and `:latest`, and the server automatically pulls and restarts the container running that tag without compiling.
- [ ] **User Registration:** A visitor can register with username and password, password hash is securely stored, the username "demo" is rejected, and the raw API key is presented once on the Hook Setup screen.
- [ ] **Hook Instructions:** The Hook Setup screen renders pre-filled install commands for Cursor, Claude Code, and Antigravity containing the user's specific API key and URL.
- [ ] **Per-User Sessions:** Tool calls posted to `/api/watcher/evaluate` with a user's API key insert records for that user; the website Sessions, Control, and Analyzer views display only that user's data; two registered users cannot inspect or modify each other's sessions.
- [ ] **Public `/demo`:** Navigating to `https://openeval.studio/demo` requires no login, displays the DEMO-ONLY banner, serves only curated fixtures, and rejects all write attempts.
- [ ] **S3 Backup & Restore:** `scripts/pg_dump_to_s3.sh` uploads a gzipped database dump to S3 on schedule; `scripts/pg_restore_from_s3.sh` successfully restores the database from an S3 archive.
