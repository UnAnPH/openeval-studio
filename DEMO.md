# OpenEval Studio — reviewer demo (<10 minutes)

Two peer products: **Evaluate** (eval IDE) and **Safety** (runtime gate). This doc is the
fastest path to prove both without theater.

## 0. Start (2 min)

```bash
cd openeval-studio
make install
# one-shot lock-8 automated checks (CI smoke + hermetic + gate demos):
bash scripts/lock8_verify.sh
make serve          # http://127.0.0.1:8000
# optional UI
cd ui && npm install && npm run dev
```

UI nav: **Evaluate** · Catalog / Launch / Runs / Compare · **Safety** · Control / Sessions / Policy

## 1. Safety smoke — no live agent (1 min)

```bash
./scripts/demo_block.sh
# or scripted mode contracts + artifact:
bash scripts/record_gate_demo.sh   # → artifacts/gate-demo.jsonl
bash scripts/record_antigravity_gate_demo.sh  # → artifacts/antigravity-gate-demo.jsonl
```

Expect `PASS` / deny-or-escalate for force-push, sudo, curl|bash, IMDS. Gate demo also asserts observe shadow + paused allow.

## 2. Safety live — Antigravity (optional, ~2 min)

```bash
bash scripts/install_antigravity_watcher_hook.sh
# Restart Antigravity / new agent chat (hooks reload)
```

1. UI → **Safety → Policy** → set Bash / `run_command` Auto-deny ≥ 5 → Save  
2. UI → **Safety → Control** → mode **Enforce**  
3. In Antigravity, ask the agent to run: `git push --force origin main`  
4. Expect hard block with `[WATCHER …]`; check **Sessions** and Control live stream  
5. Control → **Observe**: same command should **allow** with `shadow_decision` (hooks obey `decision`)

Safe control: `echo hello` must still allow.

Logs: `~/.openeval/watcher-gate.log`

## 3. Evaluate path (honest) (~3 min)

### Hermetic verifier (no API key)

```bash
uv run python scripts/hermetic_eval_smoke.py
# → artifacts/hermetic-eval.json  (nop fail / oracle pass on regex-log)
```

### Model Launch (needs key)

1. Set `GEMINI_API_KEY` or `OPENAI_API_KEY` in `.env`  
2. UI → **Evaluate → Launch** → pick a small local task (e.g. `cancel-async-tasks`)  
3. If the key is missing, the run **fails** (no fake green pass)  
4. On success: **Runs** → trajectory → **Compare** / annotate in **Sessions**

## 4. Claude Code / Cursor hooks

```bash
bash scripts/install_antigravity_watcher_hook.sh
bash scripts/install_cursor_watcher_hook.sh   # writes .cursor/hooks.json + ~/.cursor/hooks.json
```

Matcher notes:

- Antigravity PreToolUse matcher must be `.*` (not `*`)  
- Cursor: `beforeShellExecution` + `preToolUse` → `scripts/cursor_watcher_gate.py`  
- Fail-open: server down → local blacklist still denies high-severity; else allow  
- Local blacklist denies now also **report** to `/api/watcher/evaluate` so Control Live Stream / Sessions show the block  

Verify:

1. API + UI running; Safety → Control → **Enforce**  
2. `curl` force-push via `./scripts/demo_block.sh` → expect deny; Live Stream shows it  
3. In Cursor/Antigravity, ask agent to `git push --force origin main` → hard deny + Live Stream row  
4. Safety → Sessions → refresh → antigravity / cursor rows from disk ingest  

**Restart API after pulling these fixes** (ingest + interceptions seed are server-side). Reload UI.  

## What not to claim

- Not multi-tenant Analyzer SaaS / production SIEM  
- Transcript search lexical channel is **term-overlap**, not true BM25  
- `WatcherEngine.evaluate_action` is **legacy unit-test path**; production gate is PolicyGateway + mode  

## Related

- `FINDINGS.md` — FP/FN notes and eval writeup template  
- `README.md` — architecture honesty  
