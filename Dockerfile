FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock* README.md ./
COPY schemas schemas
COPY engine engine
COPY server server
COPY sandbox sandbox
COPY cli.py inspect_tasks.py ./
RUN uv sync --no-dev || uv pip install --system -e .
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
