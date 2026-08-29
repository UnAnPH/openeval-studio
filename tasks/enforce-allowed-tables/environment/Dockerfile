# The container the agent runs in: a real Apache Airflow monorepo checkout seeded
# at /app/airflow, with the common.ai provider installed *editable* from the uv
# workspace. Editable install means every import of
# airflow.providers.common.ai.* resolves to the on-disk /app/airflow source — so
# the agent's edits (and the oracle's copied files) are the code that actually runs.
#
# Only agent-facing runtime deps belong here. Test-only deps (pytest) live in
# tests/test.sh.

FROM python:3.12-slim-bookworm

# Pinned uv, straight from the official image. Must be >= 0.11.8 — the seed's
# pyproject.toml/uv.lock use newer schema features (relative exclude-newer,
# per-package exclude-newer). Matches the toolkit's own uv version.
COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /uvx /bin/

# git/curl for the agent; ca-certificates so uv can fetch over TLS.
RUN apt-get update && apt-get install -y \
        git \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# The full monorepo checkout (airflow-core, providers, task-sdk, shared, uv.lock).
COPY airflow /app/airflow

ENV AIRFLOW_HOME=/app/airflow_home \
    UV_PROJECT_ENVIRONMENT=/app/airflow/.venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_PREFERENCE=system

# Install the common.ai provider with its `sql` extra (→ common-sql + sqlglot) plus
# its workspace dependency chain (airflow-core, common-compat, standard, task-sdk,
# shared libs), editable, resolved from the committed uv.lock for determinism.
RUN cd /app/airflow \
    && uv sync --project providers/common/ai --extra sql --frozen --python 3.12
