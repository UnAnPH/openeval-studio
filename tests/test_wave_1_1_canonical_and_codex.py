"""Wave 1.1: canonical tool normalization, CLI rules test, and Codex CLI hook."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from schemas.watcher_models import ToolThreshold
from server.policy_gateway import PolicyGateway
from server.tool_normalizer import (
    build_decision_context,
    get_tool_lookup_candidates,
    is_command_tool,
    is_file_tool,
    normalize_tool_name,
)

REPO_ROOT = Path(__file__).parent.parent
CLI_PATH = REPO_ROOT / "cli.py"
CODEX_HOOK = REPO_ROOT / "scripts" / "codex_watcher_hook.py"


# =============================================================================
# Task 1: Canonical Tool Normalization
# =============================================================================


@pytest.mark.parametrize(
    ("raw_name", "expected"),
    [
        # Claude Code
        ("Bash", "Bash"),
        ("Edit", "Edit"),
        ("MultiEdit", "Edit"),
        # OpenAI Codex CLI
        ("exec_command", "Bash"),
        ("apply_patch", "Edit"),
        # Google Antigravity
        ("run_command", "Bash"),
        ("replace_file_content", "Edit"),
        # Cursor
        ("run_terminal_cmd", "Bash"),
        ("search_replace", "Edit"),
        # MCP / misc
        ("container.exec", "Bash"),
        ("view_file", "Read"),
        ("list_dir", "Glob"),
        ("websearch", "WebFetch"),
        ("send_message", "Agent"),
    ],
)
def test_normalize_tool_name_across_agents(raw_name: str, expected: str) -> None:
    assert normalize_tool_name(raw_name) == expected


def test_unknown_tool_name_preserves_raw_name() -> None:
    assert normalize_tool_name("custom_mcp_tool") == "custom_mcp_tool"


def test_empty_tool_name_is_unknown() -> None:
    assert normalize_tool_name("   ") == "unknown"


def test_lookup_candidates_cover_alias_keys() -> None:
    candidates = get_tool_lookup_candidates("exec_command")
    assert candidates[0] == "exec_command"
    assert "Bash" in candidates
    assert "execute_bash" in candidates


def test_command_and_file_tool_classification() -> None:
    assert is_command_tool("exec_command") is True
    assert is_command_tool("run_terminal_cmd") is True
    assert is_command_tool("Edit") is False
    assert is_file_tool("apply_patch") is True
    assert is_file_tool("view_file") is True
    assert is_file_tool("Bash") is False


def test_build_decision_context() -> None:
    context = build_decision_context("exec_command", "Bash")
    assert context == {
        "original_tool": "exec_command",
        "evaluated_tool": "Bash",
        "matched_threshold_key": "Bash",
        "normalized": True,
    }


# =============================================================================
# Task 2: PolicyGateway canonical threshold resolution
# =============================================================================


@pytest.mark.parametrize("raw_tool_name", ["exec_command", "run_command"])
def test_threshold_configured_on_bash_resolves_for_codex_and_antigravity(
    raw_tool_name: str,
) -> None:
    gw = PolicyGateway(
        use_llm=False,
        tool_thresholds=[ToolThreshold(tool_name="Bash", threshold=11)],
    )
    record = gw.evaluate_tool_call(
        session_id=f"wave-1-1-{uuid.uuid4().hex[:8]}",
        tool_name=raw_tool_name,
        tool_input="echo hello",
    )

    assert record.decision == "allow"
    assert record.score == 1
    assert record.metadata["canonical_tool"] == "Bash"
    context = record.metadata["decision_context"]
    assert context["original_tool"] == raw_tool_name
    assert context["evaluated_tool"] == "Bash"
    assert context["normalized"] is True


def test_rule_path_metadata_includes_canonical_tool() -> None:
    gw = PolicyGateway(use_llm=False)
    record = gw.evaluate_tool_call(
        session_id=f"wave-1-1-{uuid.uuid4().hex[:8]}",
        tool_name="exec_command",
        tool_input="git push --force origin main",
    )

    assert record.decision == "block"
    assert record.metadata["canonical_tool"] == "Bash"
    assert record.metadata["decision_context"]["original_tool"] == "exec_command"


# =============================================================================
# Task 3: `cli.py rules test`
# =============================================================================


def _run_cli_rules_test(*args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(CLI_PATH), "rules", "test", *args, "--json"],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        env={**os.environ, "OPENEVAL_WATCHER_USE_LLM": "0"},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_cli_rules_test_denies_git_force_push() -> None:
    payload = _run_cli_rules_test("Bash", "git push --force origin main")
    assert payload["decision"] in ("deny", "escalate")
    assert payload["rule_id"] == "git_force_push"
    assert payload["evaluated_tool"] == "Bash"


def test_cli_rules_test_denies_json_wrapped_sudo_rm() -> None:
    payload = _run_cli_rules_test('{"command": "sudo rm -rf /"}')
    assert payload["decision"] == "deny"
    assert payload["is_command_tool"] is True


def test_cli_rules_test_normalizes_obfuscated_command() -> None:
    payload = _run_cli_rules_test("run_command", "rm -r -f /")
    assert payload["evaluated_tool"] == "Bash"
    assert "rm -rf" in payload["normalized_command"]
    assert payload["decision"] == "deny"


# =============================================================================
# Task 4: OpenAI Codex CLI hook
# =============================================================================


def test_codex_hook_denies_rm_rf_root_locally(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "OPENEVAL_WATCHER_URL": "http://127.0.0.1:59999",
        "OPENEVAL_WATCHER_TIMEOUT": "0.2",
        "OPENEVAL_CIRCUIT_STATE_FILE": str(tmp_path / "circuit.json"),
        "OPENEVAL_MAX_GATEWAY_FAILURES": "999",
    }
    proc = subprocess.run(
        [sys.executable, str(CODEX_HOOK)],
        input='{"name": "exec_command", "arguments": {"cmd": "rm -rf /"}}',
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode == 2
    verdict = json.loads(proc.stdout.strip())
    assert verdict["continue"] is False
    assert verdict["decision"] == "block"
    assert "force-delete" in verdict["reason"] or "rm -rf" in verdict["reason"]
    assert "force-delete" in proc.stderr or "rm -rf" in proc.stderr


def test_codex_hook_allows_benign_command_without_server(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "OPENEVAL_WATCHER_URL": "http://127.0.0.1:59999",
        "OPENEVAL_WATCHER_TIMEOUT": "0.2",
        "OPENEVAL_CIRCUIT_STATE_FILE": str(tmp_path / "circuit.json"),
        "OPENEVAL_MAX_GATEWAY_FAILURES": "999",
    }
    proc = subprocess.run(
        [sys.executable, str(CODEX_HOOK)],
        input='{"name": "exec_command", "arguments": {"cmd": "ls -la"}}',
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    assert proc.returncode == 0
    verdict = json.loads(proc.stdout.strip())
    assert verdict["continue"] is True
    assert verdict["decision"] == "approve"


def test_codex_hook_permission_request_denies_rm_rf_root(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "OPENEVAL_WATCHER_URL": "http://127.0.0.1:59999",
        "OPENEVAL_WATCHER_TIMEOUT": "0.2",
        "OPENEVAL_CIRCUIT_STATE_FILE": str(tmp_path / "circuit.json"),
        "OPENEVAL_MAX_GATEWAY_FAILURES": "999",
    }
    proc = subprocess.run(
        [sys.executable, str(CODEX_HOOK)],
        input=json.dumps(
            {
                "hook_event_name": "PermissionRequest",
                "name": "exec_command",
                "arguments": {"cmd": "rm -rf /"},
            }
        ),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode == 2
    verdict = json.loads(proc.stdout.strip())
    assert verdict["continue"] is False
    assert verdict["hookSpecificOutput"]["hookEventName"] == "PermissionRequest"
    assert verdict["hookSpecificOutput"]["decision"]["behavior"] == "deny"
    assert "force-delete" in verdict["hookSpecificOutput"]["decision"]["message"]
