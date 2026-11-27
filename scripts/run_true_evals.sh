#!/usr/bin/env bash
# Run a small matrix of difficult true evals (local + Inspect) with Gemini Flash Lite.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export HOME="${HOME_OVERRIDE:-/Users/jaysonandal}"
export DOCKER_HOST="${DOCKER_HOST:-unix:///Users/jaysonandal/.orbstack/run/docker.sock}"
set -a
# shellcheck disable=SC1091
source "$ROOT/.env"
set +a
export GOOGLE_API_KEY="${GEMINI_API_KEY:?GEMINI_API_KEY missing}"
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy || true

MODEL="${MODEL:-gemini-3.1-flash-lite}"
INSPECT_MODEL="${INSPECT_MODEL:-google/gemini-3.1-flash-lite}"
OUT="$ROOT/artifacts/true-evals"
mkdir -p "$OUT"/{local,aime2025,gpqa,agentic_misalignment,traces,hf-cache,summary}
export HF_HOME="$OUT/hf-cache"

EVAL_VENV="$ROOT/artifacts/eval-venv"
PY="$ROOT/.venv/bin/python"
INSPECT="$EVAL_VENV/bin/inspect"

echo "== Docker check =="
docker info --format 'OK {{.ServerVersion}}'
docker ps >/dev/null

echo "== Local hard tasks ($MODEL) =="
LOCAL_TASKS=(
  tasks/alignment-faking
  tasks/constraint-promise-conflict
  tasks/oversight-subversion
  tasks/cancel-async-tasks
)

SUMMARY="$OUT/summary/results.jsonl"
: > "$SUMMARY"

for task in "${LOCAL_TASKS[@]}"; do
  name="$(basename "$task")"
  log="$OUT/local/${name}.log"
  echo "---- RUN $name ----" | tee "$log"
  set +e
  "$PY" cli.py run "$task" --model "$MODEL" 2>&1 | tee -a "$log"
  code=${PIPESTATUS[0]}
  set -e
  # Best-effort parse of reward / pass from log
  reward="$(rg -o 'Reward[: ]+[0-9.]+|reward[=:][ ]*[0-9.]+|Pass[: ]+(True|False|yes|no)|passed|failed' "$log" | tail -5 | tr '\n' ';' || true)"
  printf '{"suite":"local","task":"%s","model":"%s","exit":%s,"signals":"%s"}\n' \
    "$name" "$MODEL" "$code" "${reward//\"/\'}" >> "$SUMMARY"
done

echo "== Inspect hard evals ($INSPECT_MODEL) =="
if [[ ! -x "$INSPECT" ]]; then
  echo "Missing $INSPECT — create artifacts/eval-venv first" >&2
  exit 1
fi

run_inspect () {
  local task="$1"
  local limit="$2"
  local dir="$OUT/$3"
  mkdir -p "$dir"
  export INSPECT_TRACE_FILE="$OUT/traces/${3}.trace.log"
  # Redirect inspect app-support writes into workspace via nested HOME only for inspect
  local old_home="$HOME"
  export HOME="$OUT/home"
  mkdir -p "$HOME"
  set +e
  "$INSPECT" eval "$task" \
    --model "$INSPECT_MODEL" \
    --max-connections 2 \
    --log-dir "$dir" \
    --display plain \
    --limit "$limit" \
    2>&1 | tee "$OUT/${3}-run.log"
  local code=${PIPESTATUS[0]}
  set -e
  export HOME="$old_home"
  printf '{"suite":"inspect","task":"%s","model":"%s","limit":%s,"exit":%s}\n' \
    "$task" "$INSPECT_MODEL" "$limit" "$code" >> "$SUMMARY"
}

run_inspect aime2025 10 aime2025
run_inspect gpqa_diamond 8 gpqa
run_inspect agentic_misalignment 6 agentic_misalignment

echo "== Done =="
echo "Summary: $SUMMARY"
cat "$SUMMARY"
