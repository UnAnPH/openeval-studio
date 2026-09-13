# Stage 1: Build Vite React UI
FROM node:20-slim AS ui-builder
WORKDIR /app/ui
COPY ui/package*.json ./
RUN npm ci || npm install
COPY ui ./
RUN npm run build

# Stage 2: Python FastAPI Backend
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock* README.md ./
COPY schemas schemas
COPY engine engine
COPY server server
COPY sandbox sandbox
COPY fixtures fixtures
COPY cli.py inspect_tasks.py ./
COPY --from=ui-builder /app/ui/dist /app/ui/dist
RUN uv sync --no-dev || uv pip install --system -e .

ENV OPENEVAL_DEMO_SEED=1
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uv run uvicorn server.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
