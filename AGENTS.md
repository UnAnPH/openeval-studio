# AGENTS.md — Operating manual for coding agents working on OpenEval Studio

> **Audience:** Autonomous coding agents and humans.  
> **Stack:** Python 3.12+ (FastAPI, DuckDB, Pydantic v2), React 19.3 + TypeScript + Tailwind, Docker.  
> **Tone:** Portfolio / research lab — prefer honest claims over marketing language.

> **Model routing & delegation (`delegate-worker` — Mode-Aware):** Route between inline execution and headless OpenCode (`opencode-go/deepseek-v4.1-flash` default; `opencode-go/glm-5.3` `$200/mo` escalation) via `.agents/skills/delegate-worker/scripts/run-subagent.sh` (see [`.agents/skills/delegate-worker/SKILL.md`](./.agents/skills/delegate-worker/SKILL.md)):
> - **Standalone `Gemini 4 Hydrogen`:** Execute inline by default (`1–4` files, `< ~80–100` lines). Offload only for heavy batch/boilerplate (`3+` files or `>= 80–100` lines), full new test suites, or broad `5+` file sweeps (`discover`).
> - **`Orchestrator (Argon)`:** Small/medium edits (`1–2` files, `< ~50` lines) run inline via Hydrogen. Heavy work (`3+` files, `>= 50` lines, test suites, or `5+` file `discover`) uses a `delegate-worker` work order executed by Hydrogen via `run-subagent.sh`, followed by local verification.
> - **Standalone `Gemini 4 Argon` / `Claude`:** Offload `>= 15`-line implementations and `3+` file discovery to `opencode-go/deepseek-v4.1-flash` first. If `deepseek-v4.1-flash` fails verification or hits quota/throttling, escalate automatically to `opencode-go/glm-5.3` (`$200/mo` pool). Never escalate to `opencode-go/deepseek-v4-pro` (`$15/mo`) without asking the user first.

---

## Mission

OpenEval Studio is:

1. An **eval IDE** (tasks, sandboxes, Inspect bridge, compare, search).
2. A **runtime safety gate** for Antigravity / Claude Code / Cursor / Codex / OpenCode hooks → `/api/v1/watcher/evaluate`.

Do not describe it as enterprise SaaS, multi-tenant MDM, or “Deep LLM always-on” unless LLM env flags are enabled and wired.

> **Naming & Architecture Note:** All visitor-facing copy, UI labels, and user documentation use **OpenEval Studio** and **runtime gate**. Internal code artifacts (`server/watcher_store.py`, `/api/v1/watcher/*` routes, and hook script filenames such as `antigravity_watcher_gate.py`) provide the canonical safety engine and telemetry layer.

---

## Inviolable rules

1. **No fake production data** — never hardcode mock incidents/turns in UI paths (empty state instead). Demo endpoints must be labeled DEMO-ONLY. In Sessions, 0-message sessions must not be displayed.
2. **Interactive Lockout (`force_ask`)** — When an action is denied in `enforce` mode, client hooks (Antigravity, Cursor, Claude Code) must return an interactive lockout (`"decision": "force_ask"`, `terminal_reason="lockout"`) to halt agent execution and require operator confirmation.
3. **No command or result truncation** — full command strings and executed tool results must be preserved unclipped in stores, SSE payloads, and UI detail cards.
4. **Filter layout contract** — The `Live` status filter tab belongs exclusively on **Safety → Sessions** (`All` | `Live` | `Blocked` | `Closed`). **Safety → Control** strictly maintains 3 decision tabs (`All` | `Blocked` | `Closed`).
5. **Design system** — `lucide-react` only in new/edited views; cards `bg-white rounded-2xl border border-border-subtle shadow-sm`.
6. **Command rules only on command tools** — when applying shell regexes in `evaluate_action_watcher_gateway`, do not treat file-write payloads as shell commands.
7. **Quality before done:** `python3 scripts/build_hooks.py --check`, `uv run python scripts/sync_canonical_graders_ui.py --check`, `uv run ruff check .`, `uv run pyright`, `uv run pytest tests/ -m "not slow" -q`, `cd ui && npm test && npm run build` (0 errors). **Scoped Work Order Exception (`delegate-worker`):** When executing a scoped OpenCode work order, run **ONLY** the work order's `## 4. Verification Command` (do NOT run the full 658-test `pytest tests/` suite or repo-wide `pyright` inside the worker, and never leave side-effect files like `artifacts/hermetic-eval.json` modified); the outer orchestrator runs broader repo gates after reviewing the diff.
8. **Circuit Breaker Statefulness** — Client hooks must never fail-open indefinitely on repeated gateway outages. Hook state must use atomic replace (`tmp` -> `os.replace`) and trip to `force_ask` after 3 consecutive failures in 60s. Unit tests involving hooks must isolate `OPENEVAL_CIRCUIT_STATE_FILE` per test.

---

## Engineering Principles (Apollo × Grug House Rules)

1. **Degrade explicitly for decisions; fail loudly for records.** Verdict paths may fall back predictably, but records and telemetry must never silently swallow DB errors or emit fake zero-value analytics. Surface the failure.
2. **Single source of truth, single-file deployment.** Hook logic lives in `scripts/_hook_core.py`; regenerate shims with `python3 scripts/build_hooks.py` and verify with `--check`. Canonical graders sync via `uv run python scripts/sync_canonical_graders_ui.py` (verify with `--check`).
3. **Typed internal seams, thin boundary adapters.** Normalize untrusted shapes once at the edge (`RawToolCall`, `review_from_row`, `reset_all_runtime_state`) and pass typed models inward.
4. **Small-steps cut-point modularity.** Grow by extracting focused modules (`engine/graders/`, `server/policy_helpers.py`, `server/store_mixins/`, `ui/src/lib/verdict.ts`, `ui/src/lib/api.ts`) instead of rewriting monoliths.

---

## Runtime gate truth table

| Piece | Status |
| --- | --- |
| Antigravity hook | `scripts/antigravity_watcher_gate.py` + `install_antigravity_watcher_hook.sh` (matcher `.*`, `force_ask` lockout, stateful circuit breaker) |
| Claude Code hook | `scripts/claude_code_watcher_hook.py` (settings.json PreToolUse, interactive prompt on deny) |
| Cursor hook | `scripts/cursor_watcher_gate.py` + `install_cursor_watcher_hook.sh` (`.cursor/hooks.json`, `ask` prompt lockout, stateful circuit breaker) |
| Adversarial Evasion | Traps base64/hex/openssl pipelines, interpreter reflection (`python/node -c`), split flags (`rm -r -f`), and secret upload exfil |
| Operator Override | Single-action approval (`/api/v1/watcher/gate/resolve`) and bulk resolution (`/api/v1/watcher/reviews/resolve-all`) |
| Live Session Ingest | Automatic discovery of active sessions (`working`, `active`, `running`) into Sessions `Live` tab |
| Thresholds UI | Safety → Policy → Tool Thresholds (wired to store + evaluate) |
| LLM Stage 3/4 | Optional via `OPENEVAL_WATCHER_USE_LLM=1`; else heuristics |
| Codex | `scripts/codex_watcher_hook.py` (JSON stdin, local blacklist, `force_ask` lockout, stateful circuit breaker) |
| OpenCode hook | `scripts/opencode_watcher_hook.py` + `scripts/install_opencode_watcher_hook.sh` (OpenCode plugin at `~/.config/opencode/plugins/openeval-watcher/index.ts` hooking `permission.evaluate` + `tool.execute.before`, `force_ask` lockout via `effect=ask`, stateful circuit breaker) |

---

## Layout (short)

```
cli.py, inspect_tasks.py
engine/          # agents, judges, scanners, sweep
schemas/         # pydantic models
server/          # FastAPI, stores, policy_gateway, review resolution
scripts/         # agent hooks + demo_block.sh + install scripts
tasks/           # 5-file benchmarks
ui/              # React app
tests/           # 658 pytest tests + UI suite (`cd ui && npm test`)
DEMO.md FINDINGS.md
```

---

## Extension recipes

- New task: `tasks/<id>/` 5-file standard + register in `inspect_tasks.py` / defaults.  
- New command rule / threshold: Safety → Policy UI or `server/command_rules.py` + tests.  
- Operator review: Use UI resolution buttons or `POST /api/v1/watcher/gate/resolve` / `POST /api/v1/watcher/reviews/resolve-all`.
- Hook changes: update matching script under `scripts/` and re-run install for Antigravity/Cursor.

<!-- BEGIN AWS Agent Toolkit rules -->
# AWS Guidance

- Where these AWS rules conflict with the project's own instructions, the
  project's instructions take precedence.
- Prefer the AWS MCP Server for AWS interactions — it provides sandboxed
  execution, observability, and audit logging. If unavailable, use the
  AWS CLI directly.
- Before starting a task, check whether a relevant AWS skill is available.
  Load the skill with `retrieve_skill` and prefer its guidance over
  general knowledge.
- When uncertain about specific AWS details (API parameters, permissions,
  limits, error codes), verify against documentation rather than guessing.
  State uncertainty explicitly if you cannot confirm.
- When creating infrastructure, prefer infrastructure-as-code (AWS CDK or
  CloudFormation) over direct CLI commands.
- When working with infrastructure, follow AWS Well-Architected Framework
  principles.
- Do not use em dashes in AWS resource names or descriptions. Use
  hyphens instead.

## Secret Safety

- MUST load the `aws-secrets-manager` skill first for any secret,
  credential, API key, token, or password task. MUST NOT call
  `secretsmanager get-secret-value` or `batch-get-secret-value`, and MUST
  NOT hit the Secrets Manager Agent daemon directly. MUST use
  `{{resolve:secretsmanager:secret-id:SecretString:json-key}}` with
  `asm-exec` so the secret resolves at runtime without entering context.
<!-- END AWS Agent Toolkit rules -->
