# OpenEval Studio 🧪
> High-performance evaluation studio, execution sandboxing engine, and trajectory visualizer for frontier AI coding agents.

---

## 🚀 Quickstart with `uv`

### 1. Installation
```bash
# Install dependencies into virtual environment using uv
uv sync
```

### 2. Configure API Key
```bash
cp .env.example .env
# Edit .env and set GEMINI_API_KEY
```

### 3. Test LLM Connection
```bash
uv run python cli.py test-llm
```

### 4. Run Benchmark Evals
```bash
uv run python cli.py run tasks/feed-sync-platform
```

### 5. Run Test Suite
```bash
uv run pytest tests/ -v
```
