.PHONY: install
install:
	uv sync

.PHONY: dev
dev:
	uv run uvicorn server.app:app --host 0.0.0.0 --port 8000 --reload

.PHONY: serve
serve:
	uv run uvicorn server.app:app --host 0.0.0.0 --port 8000

.PHONY: ui
ui:
	cd ui && npm run dev

.PHONY: install-dev
install-dev:
	uv sync --extra dev
	uv run pre-commit install

.PHONY: type
type:
	uv run pyright

.PHONY: format
format:
	uv run ruff check --fix
	uv run ruff format

.PHONY: check
check:
	uv run ruff check --fix
	uv run ruff format
	uv run pyright

.PHONY: test
test:
	uv run pytest tests/ -m "not slow"

.PHONY: test-all
test-all:
	uv run pytest tests/ -v

.PHONY: ci-smoke
ci-smoke:
	uv run pytest tests/ -m "not slow" -q --tb=short
	uv run python scripts/hermetic_eval_smoke.py
	bash scripts/record_gate_demo.sh

.PHONY: portfolio-demo
portfolio-demo:
	bash scripts/record_gate_demo.sh

.PHONY: benchmark
benchmark:
	uv run python scripts/benchmark_gate_latency.py

