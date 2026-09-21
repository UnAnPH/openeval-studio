# AGENTS.md — Operating manual for coding agents working on OpenEval Studio

> **Audience:** Autonomous coding agents and humans.  
> **Stack:** Python 3.12+ (FastAPI, DuckDB, Pydantic v2), React 18.3 + TypeScript + Tailwind, Docker.  
> **Tone:** Portfolio / research lab — prefer honest claims over marketing language.

---

## Mission

OpenEval Studio is:

1. An **eval IDE** (tasks, sandboxes, Inspect bridge, compare, search).  
2. A **runtime safety gate** for Antigravity / Claude Code / Cursor hooks → `/api/gate/evaluate` (alias `/api/watcher/evaluate`).

Do not describe it as enterprise SaaS, multi-tenant MDM, or “Deep LLM always-on” unless LLM env flags are enabled and wired.

> **Naming & Architecture Note:** All visitor-facing copy, UI labels, and user documentation use **OpenEval Studio** and **runtime gate**. Internal code artifacts (`server/watcher_store.py`, `/api/watcher/*` aliases, and hook script filenames such as `antigravity_watcher_gate.py`) are deliberately maintained for backward compatibility with existing agent integrations and tests.

---

## Inviolable rules

1. **No fake production data** — never hardcode mock incidents/turns in UI paths (empty state instead). Demo endpoints must be labeled DEMO-ONLY. In Sessions, 0-message sessions must not be displayed.
2. **Interactive Lockout (`force_ask`)** — When an action is denied in `enforce` mode, client hooks (Antigravity, Cursor, Claude Code) must return an interactive lockout (`"decision": "force_ask"`, `terminal_reason="lockout"`) to halt agent execution and require operator confirmation.
3. **No command or result truncation** — full command strings and executed tool results must be preserved unclipped in stores, SSE payloads, and UI detail cards.
4. **Filter layout contract** — The `Live` status filter tab belongs exclusively on **Safety → Sessions** (`All` | `Live` | `Blocked` | `Closed`). **Safety → Control** strictly maintains 3 decision tabs (`All` | `Blocked` | `Closed`).
5. **Design system** — `lucide-react` only in new/edited views; cards `bg-white rounded-2xl border border-border-subtle shadow-sm`.
6. **Command rules only on command tools** — when applying shell regexes in `evaluate_action_watcher_gateway`, do not treat file-write payloads as shell commands.
7. **Quality before done:** `make check`, `uv run pytest tests/`, `cd ui && npm run build` (0 TypeScript/Vite errors).

---

## Runtime gate truth table

| Piece | Status |
| --- | --- |
| Antigravity hook | `scripts/antigravity_watcher_gate.py` + `install_antigravity_watcher_hook.sh` (matcher `.*`, `force_ask` lockout) |
| Claude Code hook | `scripts/claude_code_watcher_hook.py` (settings.json PreToolUse, interactive prompt on deny) |
| Cursor hook | `scripts/cursor_watcher_gate.py` + `install_cursor_watcher_hook.sh` (`.cursor/hooks.json`, `force_ask` lockout) |
| Operator Override | Single-action approval (`/api/v1/watcher/gate/resolve`) and bulk resolution (`/api/v1/watcher/reviews/resolve-all`) |
| Live Session Ingest | Automatic discovery of active sessions (`working`, `active`, `running`) into Sessions `Live` tab |
| Thresholds UI | Safety → Policy → Tool Thresholds (wired to store + evaluate) |
| LLM Stage 3/4 | Optional via `OPENEVAL_WATCHER_USE_LLM=1`; else heuristics |
| Codex | Threshold names only — no full client hook yet |

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
tests/           # 136 pytest unit/integration tests
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
