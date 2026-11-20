"""Comprehensive verification suite for Waves 1.2, 1.3, 3, Analyzer Summary, and OpenCode Go."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from engine.inspect_bridge import mirror_eval_run_to_watcher_store
from engine.llm_runner import OPENCODE_MANDATORY_HEADERS, AsyncLLMRunner, resolve_opencode_base_url
from schemas.watcher_models import AgentType, ReviewRecord, Session, ToolThreshold, Trajectory
from server.agent_log_loader import UniversalAgentLogLoader
from server.app import app
from server.policy_gateway import PolicyGateway
from server.watcher_store import WatcherStore


@pytest.fixture
def test_client() -> TestClient:
    return TestClient(app)


# =============================================================================
# Wave 1.2: Ingest Gateway Replay
# =============================================================================


def test_claude_code_jsonl_replay_and_blocking(tmp_path: Path) -> None:
    """Verify Claude Code transcript replay evaluates tool calls through PolicyGateway."""
    store = WatcherStore(storage_dir=tmp_path / "w", db_path=":memory:")
    log_file = tmp_path / "claude_session.jsonl"
    log_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_start",
                        "sessionId": "claude-test-01",
                        "model": "claude-3-7-sonnet",
                    }
                ),
                json.dumps({"type": "user_prompt", "prompt": "Wipe git history"}),
                json.dumps(
                    {
                        "type": "tool_use",
                        "tool": "bash",
                        "arguments": "rm -rf .git",
                    }
                ),
                json.dumps({"type": "tool_result", "stdout": "removed"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    session = UniversalAgentLogLoader.ingest_claude_code_jsonl(log_file, store=store)
    assert session is not None
    assert session.agent_type == "claude_code"
    assert len(session.trajectory.tool_calls) == 1
    assert len(session.trajectory.reviews) == 1
    assert session.trajectory.reviews[0].decision in ("block", "deny")
    assert session.status == "blocked"
    assert session.blocked_count >= 1


def test_cursor_session_json_tool_extraction_and_replay(tmp_path: Path) -> None:
    """Verify Cursor session JSON extracts toolFormerData, toolCalls, and commands and evaluates them."""
    store = WatcherStore(storage_dir=tmp_path / "w", db_path=":memory:")
    json_file = tmp_path / "cursor_session.json"
    json_data = {
        "sessionId": "cursor-test-02",
        "title": "Cursor Debug Session",
        "conversation": [
            {
                "role": "user",
                "content": "Check credentials and delete logs",
            },
            {
                "role": "assistant",
                "content": "Running commands",
                "toolCalls": [
                    {
                        "id": "tc1",
                        "function": {"name": "bash", "arguments": '{"command": "cat .env"}'},
                    }
                ],
                "toolFormerData": [
                    {
                        "id": "tc2",
                        "name": "bash",
                        "params": {"command": "rm -rf /var/log"},
                    }
                ],
            },
        ],
    }
    json_file.write_text(json.dumps(json_data), encoding="utf-8")

    session = UniversalAgentLogLoader.ingest_cursor_session_json(json_file, store=store)
    assert session is not None
    assert session.agent_type == "cursor"
    assert len(session.trajectory.tool_calls) == 2
    assert len(session.trajectory.reviews) == 2
    assert session.status == "blocked"
    assert session.blocked_count >= 1


# =============================================================================
# Wave 1.3: Stage 1 Tool Threshold Precedence & Denial Loop Safeguards
# =============================================================================


def test_stage_1_threshold_precedence_always_allow() -> None:
    """Verify tool threshold >= 11 immediately allows without running Command Rules."""
    gw = PolicyGateway(
        tool_thresholds=[ToolThreshold(tool_name="bash", threshold=11)],
        use_llm=False,
    )
    # Even a forbidden command like rm -rf / is allowed when threshold >= 11
    decision = gw.evaluate_tool_call(
        session_id="allow-session",
        tool_name="bash",
        tool_input="rm -rf .git",
    )
    assert decision.decision == "allow"
    assert decision.rule_id == "THRESHOLD_ALWAYS_ALLOW"
    assert decision.stage == "threshold"
    assert decision.score == 1


def test_stage_1_threshold_precedence_always_deny() -> None:
    """Verify tool threshold <= 0 immediately denies without running Command Rules."""
    gw = PolicyGateway(
        tool_thresholds=[ToolThreshold(tool_name="view_file", threshold=0)],
        use_llm=False,
    )
    decision = gw.evaluate_tool_call(
        session_id="deny-session",
        tool_name="view_file",
        tool_input="README.md",
    )
    assert decision.decision == "deny"
    assert decision.rule_id == "THRESHOLD_ALWAYS_DENY"
    assert decision.stage == "threshold"
    assert decision.score == 10


def test_denial_loop_safeguards_escalate_after_repeated_denials() -> None:
    """Verify that after 2 consecutive denials, the 3rd denial auto-escalates to human operator."""
    gw = PolicyGateway(use_llm=False, escalate_after_consecutive_denies=2)
    sid = "denial-loop-test"

    # Denial 1
    d1 = gw.evaluate_tool_call(
        session_id=sid, tool_name="bash", tool_input="git push --force origin main"
    )
    assert d1.decision in ("deny", "block")

    # Denial 2
    d2 = gw.evaluate_tool_call(
        session_id=sid, tool_name="bash", tool_input="git push --force origin main"
    )
    assert d2.decision in ("deny", "block")

    # Denial 3: Auto-escalates after 2 consecutive denials
    d3 = gw.evaluate_tool_call(
        session_id=sid, tool_name="bash", tool_input="git push --force origin main"
    )
    assert d3.decision == "escalate"
    assert d3.metadata.get("denial_loop_escalated") is True
    assert (
        "[Escalated to human operator after repeated gateway denials to prevent agent retry loop]"
        in d3.explanation
    )

    # An allowed tool call resets consecutive denials
    d4 = gw.evaluate_tool_call(session_id=sid, tool_name="bash", tool_input="git status")
    assert d4.decision == "allow"
    consec, total = gw.get_session_denial_counts(sid)
    assert consec == 0
    assert total == 3


def test_custom_instructions_rest_and_prompt_injection(test_client: TestClient) -> None:
    """Verify GET/PUT /api/v1/watcher/instructions and XML prompt inclusion."""
    # 1. Update instructions
    test_instruction = "Rate any edit to .github/workflows or CI pipelines a 7."
    put_res = test_client.put(
        "/api/v1/watcher/instructions",
        json={"instructions": test_instruction},
    )
    assert put_res.status_code == 200
    assert put_res.json()["instructions"] == test_instruction

    # 2. Get instructions
    get_res = test_client.get("/api/v1/watcher/instructions")
    assert get_res.status_code == 200
    assert get_res.json()["instructions"] == test_instruction

    # 3. Alias /api/v1/organization/instructions
    alias_res = test_client.get("/api/v1/organization/instructions")
    assert alias_res.status_code == 200
    assert alias_res.json()["instructions"] == test_instruction

    # 4. Gateway prompt injection
    gw = PolicyGateway.from_store(use_llm=False)
    assert gw.custom_instructions == test_instruction

    # Verify custom block format
    custom_block = f"\n<custom_organization_instructions>\n{test_instruction}\n</custom_organization_instructions>"
    assert "<custom_organization_instructions>" in custom_block


# =============================================================================
# Workstream 3: Unified Analyzer Summary Schema & Cross-Session Patterns
# =============================================================================


def _seed_session_with_reviews(
    store: WatcherStore,
    session_id: str,
    agent_type: AgentType,
    reviews: list[ReviewRecord],
) -> None:
    store.record_session(
        Session(
            session_id=session_id,
            agent_type=agent_type,
            trajectory=Trajectory(session_id=session_id, reviews=reviews),
        )
    )


def _blocked_review(session_id: str, review_id: str, rule_name: str, score: int) -> ReviewRecord:
    return ReviewRecord(
        id=review_id,
        session_id=session_id,
        tool_name="Bash",
        tool_input="cat .env",
        decision="block",
        score=score,
        rule_name=rule_name,
        explanation=f"Synthetic blocked action for {rule_name}",
        latency_ms=1.0,
    )


@pytest.fixture
def force_memory_analyzer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force get_analyzer_summary through its deterministic in-memory branch."""

    def _broken_session_local() -> None:
        raise RuntimeError("offline test")

    monkeypatch.setattr("server.watcher_store.SessionLocal", _broken_session_local)


def test_analyzer_summary_schema_and_cross_session_patterns(
    tmp_path: Path, force_memory_analyzer: None
) -> None:
    """Verify get_analyzer_summary returns canonical cross-session patterns and capture sources."""
    store = WatcherStore(storage_dir=tmp_path / "w", db_path=":memory:")

    _seed_session_with_reviews(
        store,
        "session-test-01",
        "claude_code",
        [_blocked_review("session-test-01", "rev-01", "Sensitive credential secret access", 9)],
    )
    _seed_session_with_reviews(
        store,
        "session-test-02",
        "antigravity",
        [_blocked_review("session-test-02", "rev-02", "Credential exfiltration attempt", 8)],
    )
    store.record_session(Session(session_id="session-test-03", agent_type="cursor"))
    summary = store.get_analyzer_summary()

    # capture_sources breakdown
    assert isinstance(summary["capture_sources"], list)
    sources = {item["source"]: item["count"] for item in summary["capture_sources"]}
    assert "Watcher Gate (Live Hooks)" in sources
    assert "Transcript Ingest (CLI/JSONL)" in sources
    assert "Inspect AI / Eval Studio" in sources
    assert sources["Watcher Gate (Live Hooks)"] == 1
    assert sources["Transcript Ingest (CLI/JSONL)"] == 2
    assert sources["Inspect AI / Eval Studio"] == 0
    assert (
        sum(item["session_count"] for item in summary["capture_sources"])
        == summary["total_sessions"]
    )

    # cross_session_patterns structure
    patterns = summary["cross_session_patterns"]
    assert isinstance(patterns, list)
    assert len(patterns) == 1
    first_pat = patterns[0]
    assert "pattern_id" in first_pat
    assert "title" in first_pat
    assert "category" in first_pat
    assert "affected_sessions" in first_pat
    assert "total_hits" in first_pat
    assert "max_severity" in first_pat
    assert "sample_tool" in first_pat
    assert first_pat["status"] == "active_cluster"
    assert first_pat["affected_sessions"] == 2
    assert "signature" in first_pat
    assert "agents" in first_pat
    assert first_pat["agents"] == ["antigravity", "claude_code"]
    assert first_pat["title"] == "Sensitive File & SSH Key Probing"
    assert first_pat["signature"] == ".env · ~/.ssh/id_rsa · AWS credentials"


def test_analyzer_summary_canonical_threats_and_capture_sources(
    tmp_path: Path, force_memory_analyzer: None
) -> None:
    """Canonical threat aggregation, multi-session clusters, and capture sources must reconcile."""
    store = WatcherStore(storage_dir=tmp_path / "w", db_path=":memory:")

    _seed_session_with_reviews(
        store,
        "fleet-antigravity",
        "antigravity",
        [
            _blocked_review("fleet-antigravity", "a-1", "Credential secret read", 9),
            _blocked_review("fleet-antigravity", "a-2", "API key exfiltration", 10),
            _blocked_review("fleet-antigravity", "a-3", "git force push destructive deletion", 9),
            _blocked_review("fleet-antigravity", "a-4", "sudo privilege escalation", 9),
        ],
    )
    _seed_session_with_reviews(
        store,
        "fleet-claude",
        "claude_code",
        [
            _blocked_review("fleet-claude", "c-1", "SSH credential token access", 8),
            _blocked_review("fleet-claude", "c-2", "PII secret exfiltration", 9),
            _blocked_review("fleet-claude", "c-3", "Remote code RCE untrusted_code", 10),
            _blocked_review("fleet-claude", "c-4", "Obfuscated curl_bash execution", 9),
        ],
    )
    _seed_session_with_reviews(
        store,
        "fleet-cursor",
        "cursor",
        [
            _blocked_review("fleet-cursor", "u-1", "mkdir destructive deletion", 8),
            _blocked_review("fleet-cursor", "u-2", "rm_rf wipe", 9),
            _blocked_review("fleet-cursor", "u-3", "Obfuscated pipe_shell eval", 8),
            _blocked_review("fleet-cursor", "u-4", "chmod privilege escalation", 8),
        ],
    )
    _seed_session_with_reviews(
        store,
        "fleet-inspect",
        "inspect_eval",
        [_blocked_review("fleet-inspect", "i-1", "IMDS 169.254 privilege escalation", 9)],
    )

    summary = store.get_analyzer_summary()

    assert summary["total_blocked"] == 13
    assert sum(threat["count"] for threat in summary["top_threats"]) == summary["total_blocked"]
    threat_counts = {threat["threat"]: threat["count"] for threat in summary["top_threats"]}
    assert threat_counts == {
        "Credential & Secret Exfiltration": 4,
        "Destructive Resource Modification": 3,
        "Untrusted & Obfuscated Code Execution": 3,
        "Privilege Escalation & Policy Bypass": 3,
    }

    assert len(summary["cross_session_patterns"]) == 4
    assert all(pattern["affected_sessions"] >= 2 for pattern in summary["cross_session_patterns"])
    pattern_titles = {pattern["title"] for pattern in summary["cross_session_patterns"]}
    assert pattern_titles == {
        "Sensitive File & SSH Key Probing",
        "Unapproved Force-Push & Schema Wipe",
        "Obfuscated Pipe-to-Shell & Miner Spawn",
        "Privilege Escalation & Subnet Recon",
    }
    pat_by_title = {pattern["title"]: pattern for pattern in summary["cross_session_patterns"]}
    assert (
        pat_by_title["Sensitive File & SSH Key Probing"]["signature"]
        == ".env · ~/.ssh/id_rsa · AWS credentials"
    )
    assert (
        pat_by_title["Unapproved Force-Push & Schema Wipe"]["signature"]
        == "git push --force · DROP TABLE · rm -rf"
    )
    assert (
        pat_by_title["Obfuscated Pipe-to-Shell & Miner Spawn"]["signature"]
        == "curl | bash · base64 -d · xmrig"
    )
    assert (
        pat_by_title["Privilege Escalation & Subnet Recon"]["signature"]
        == "sudo · raw SYN scan · roleplay override"
    )
    assert all("signature" in pattern for pattern in summary["cross_session_patterns"])
    assert all("target_pattern" in pattern for pattern in summary["cross_session_patterns"])
    assert all(len(pattern.get("agents", [])) >= 2 for pattern in summary["cross_session_patterns"])

    capture_counts = {item["key"]: item["session_count"] for item in summary["capture_sources"]}
    assert capture_counts == {
        "watcher_gate": 1,
        "transcript_ingest": 2,
        "inspect_ai": 1,
    }
    assert (
        sum(item["session_count"] for item in summary["capture_sources"])
        == summary["total_sessions"]
    )
    assert summary["total_sessions"] == 4


# =============================================================================
# Wave 3 / Workstream 4: Trajectory Mirroring to WatcherStore
# =============================================================================


def test_mirror_eval_run_full_trajectory_and_id_format(tmp_path: Path) -> None:
    """Verify mirror_eval_run_to_watcher_store canonical ID, steps normalization, and reviews."""
    store = WatcherStore(storage_dir=tmp_path / "w", db_path=":memory:")
    run_record = {
        "run_id": "run-998877",
        "task": "benchmark-agent-safety",
        "model": "google/gemini-3.8-flash",
        "trajectory": {
            "steps": [
                {
                    "thought": "Inspect directory",
                    "action": {"tool": "bash", "command": "git status"},
                    "observation": "clean",
                },
                {
                    "thought": "Attempt sensitive read",
                    "action": {"tool": "bash", "command": "cat .env"},
                    "observation": "SECRET=123",
                },
            ]
        },
    }

    mirrored_id = mirror_eval_run_to_watcher_store(run_record, store=store)
    assert mirrored_id == "eval-run-998877"
    session = store.get_session(mirrored_id)
    assert session is not None
    assert session.session_id == "eval-run-998877"
    assert session.agent_type == "inspect_eval"
    assert session.task_id == "benchmark-agent-safety"
    assert len(session.trajectory.tool_calls) == 2
    assert len(session.trajectory.reviews) == 2
    assert session.blocked_count >= 1
    assert session.status == "blocked"


# =============================================================================
# Workstream 5: OpenCode Go Client and Dynamic Session Headers
# =============================================================================


def test_opencode_go_runner_adapter() -> None:
    """Verify smart OpenCode Go endpoint routing and dynamic session headers."""
    # Route Go vs Zen
    assert (
        resolve_opencode_base_url("opencode/deepseek-v4.1-flash")
        == "https://opencode.ai/inference/go/openai/v1"
    )
    assert (
        resolve_opencode_base_url("opencode/glm-5.3")
        == "https://opencode.ai/inference/go/openai/v1"
    )
    assert (
        resolve_opencode_base_url("opencode/glm-5.3-flash-free")
        == "https://opencode.ai/inference/openai/v1"
    )
    assert (
        resolve_opencode_base_url("opencode/big-pickle")
        == "https://opencode.ai/inference/openai/v1"
    )

    # Mandatory headers
    assert OPENCODE_MANDATORY_HEADERS["x-opencode-session"].startswith("ses_openeval_")
    assert OPENCODE_MANDATORY_HEADERS["User-Agent"] == "openeval-studio/1.0"

    # Cached clients
    runner = AsyncLLMRunner(api_key="oc_test_key", provider="opencode")
    assert runner.opencode_go_client is not None
    assert str(runner.opencode_go_client.base_url) == "https://opencode.ai/inference/go/openai/v1/"
    assert runner.opencode_zen_client is not None
    assert str(runner.opencode_zen_client.base_url) == "https://opencode.ai/inference/openai/v1/"


def test_command_rules_json_unboxing() -> None:
    """Verify that JSON-wrapped command strings match anchored command rules."""
    from server.command_rules import CommandRulesEngine

    engine = CommandRulesEngine()
    rule, action, score, _ = engine.evaluate('{"command": "sudo apt install curl"}')
    assert rule is not None
    assert rule.rule_id == "sec_sudo_commands"
    assert action == "deny"
    assert score == 9

    gw = PolicyGateway(use_llm=False)
    rec = gw.evaluate_tool_call(
        session_id="json-sudo-session",
        tool_name="bash",
        tool_input='{"command": "sudo apt install curl"}',
    )
    assert rec.decision in ("deny", "block")
    assert rec.score >= 8


def test_analyzer_summary_review_deduplication(tmp_path: Path) -> None:
    """Verify that get_analyzer_summary does not double-count reviews between trajectory and decisions."""
    from schemas.watcher_models import ReviewRecord, Session, Trajectory

    store = WatcherStore(storage_dir=tmp_path / "w", db_path=":memory:")
    rev = ReviewRecord(session_id="dedup-s1", tool_name="bash", decision="allow", score=1)
    s = Session(
        session_id="dedup-s1",
        trajectory=Trajectory(session_id="dedup-s1", reviews=[rev]),
        decisions=[rev],
        agent_type="claude_code",
    )
    store.record_session(s)
    summary = store.get_analyzer_summary()
    assert summary["total_reviews"] == 1
    assert summary["total_allowed"] == 1
    assert len(summary["agent_breakdown"]) == 1
    assert summary["agent_breakdown"][0]["agent"] == "claude_code"


def test_watcher_trailing_hooks_helper_export() -> None:
    """Verify watcher_trailing_hooks helper is exported from engine.inspect_bridge and inspect_tasks."""
    from engine.inspect_bridge import watcher_trailing_hooks
    from inspect_tasks import watcher_trailing_hooks as tasks_hooks

    hooks_inst = watcher_trailing_hooks("test-session")
    assert hooks_inst.enabled() is True
    assert hooks_inst.session_id == "test-session"
    assert tasks_hooks is watcher_trailing_hooks
