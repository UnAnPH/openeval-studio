# Multi-stage: build React UI, then ship a single FastAPI container.
FROM node:20-alpine AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock* README.md ./
COPY schemas schemas
COPY engine engine
COPY server server
COPY sandbox sandbox
COPY cli.py inspect_tasks.py ./
COPY --from=ui /ui/dist ui/dist
RUN uv sync --no-dev || uv pip install --system -e .
ENV WATCHER_STORAGE_DIR=/tmp/watcher
EXPOSE 8000
CMD ["sh", "-c", "uv run uvicorn server.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
