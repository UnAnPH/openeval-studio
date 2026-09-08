# AGENTS.md — Operating manual for coding agents working on OpenEval Studio

> **Audience:** Autonomous coding agents and humans.  
> **Stack:** Python 3.12+ (FastAPI, DuckDB, Pydantic v2), React 19 + TypeScript + Tailwind, Docker.  
> **Tone:** Portfolio / research lab — prefer honest claims over marketing language.

---

## Mission

OpenEval Studio is:

1. An **eval IDE** (tasks, sandboxes, Inspect bridge, compare, search).  
2. A **Watcher-style runtime gate** for Antigravity / Claude Code / Cursor hooks → `/api/watcher/evaluate`.

Do not describe it as enterprise SaaS, multi-tenant MDM, or “Deep LLM always-on” unless LLM env flags are enabled and wired.

---

## Inviolable rules

1. **No fake production data** — never hardcode mock incidents/turns in UI paths (empty state instead). Demo endpoints must be labeled DEMO-ONLY.
2. **Design system** — `lucide-react` only in new/edited views; cards `bg-white rounded-2xl border border-border-subtle shadow-sm`.
3. **Command rules only on command tools** — when applying shell regexes in `evaluate_action_watcher_gateway`, do not treat file-write payloads as shell commands.
4. **Quality before done:** `make check`, `uv run pytest tests/`, `cd ui && npm run build`.

---

## Watcher truth table

| Piece | Status |
| --- | --- |
| Antigravity hook | `scripts/antigravity_watcher_gate.py` + `install_antigravity_watcher_hook.sh` (matcher `.*`) |
| Claude Code hook | `scripts/claude_code_watcher_hook.py` (settings.json PreToolUse) |
| Cursor hook | `scripts/cursor_watcher_gate.py` (`.cursor/hooks.json` beforeShellExecution) |
| Thresholds UI | Watcher Live → Tool Thresholds (wired to store + evaluate) |
| LLM Stage 3/4 | Optional via `OPENEVAL_WATCHER_USE_LLM=1`; else heuristics |
| Codex | Threshold names only — no full client hook yet |

---

## Layout (short)

```
cli.py, inspect_tasks.py
engine/          # agents, judges, scanners, sweep
schemas/         # pydantic models
server/          # FastAPI, stores, policy_gateway
scripts/         # agent hooks + demo_block.sh
tasks/           # 5-file benchmarks
ui/              # React app
tests/
DEMO.md FINDINGS.md
```

---

## Extension recipes

- New task: `tasks/<id>/` 5-file standard + register in `inspect_tasks.py` / defaults.  
- New command rule / threshold: Watcher Live UI or `server/command_rules.py` + tests.  
- Hook changes: update matching script under `scripts/` and re-run install for Antigravity.
