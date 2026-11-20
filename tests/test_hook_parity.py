"""Parity tests for the shared OpenEval runtime-gate hook core.

Every hook must stay standalone (single-file, curl-installable), so
``scripts/_hook_core.py`` is embedded between marker comments rather than
imported at runtime. These tests guarantee:

1. ``scripts/build_hooks.py --check`` passes (no embedded block drifted).
2. All five hooks expose the same canonical local blacklist, with each hook's
   client-specific tuple shape and rule-id flavour preserved.
3. All five hooks share identical circuit-breaker state semantics.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
BUILD_HOOKS = SCRIPTS_DIR / "build_hooks.py"
CORE_PATH = SCRIPTS_DIR / "_hook_core.py"

BEGIN_MARKER = "# === BEGIN OPENEVAL_SHARED_HOOK_CORE ==="
END_MARKER = "# === END OPENEVAL_SHARED_HOOK_CORE ==="

HOOKS: dict[str, Path] = {
    "antigravity": SCRIPTS_DIR / "antigravity_watcher_gate.py",
    "cursor": SCRIPTS_DIR / "cursor_watcher_gate.py",
    "claude": SCRIPTS_DIR / "claude_code_watcher_hook.py",
    "codex": SCRIPTS_DIR / "codex_watcher_hook.py",
    "opencode": SCRIPTS_DIR / "opencode_watcher_hook.py",
}


def _embedded_block(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    assert text.count(BEGIN_MARKER) == 1, f"{path} must contain exactly one begin marker"
    assert text.count(END_MARKER) == 1, f"{path} must contain exactly one end marker"
    start = text.index(BEGIN_MARKER)
    end = text.index(END_MARKER) + len(END_MARKER)
    return text[start:end]


def _load_core() -> ModuleType:
    spec = importlib.util.spec_from_file_location("hook_parity_core", CORE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_hook(name: str, path: Path) -> ModuleType:
    module_name = f"hook_parity_{name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CORE = _load_core()


@pytest.fixture(scope="module")
def hook_modules() -> dict[str, ModuleType]:
    return {name: _load_hook(name, path) for name, path in HOOKS.items()}


def test_build_hooks_check_passes() -> None:
    proc = subprocess.run(
        [sys.executable, str(BUILD_HOOKS), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "up-to-date shared core" in proc.stdout


@pytest.mark.parametrize("hook_name", sorted(HOOKS))
def test_every_hook_embeds_exact_canonical_block(hook_name: str) -> None:
    hook_block = _embedded_block(HOOKS[hook_name])
    core_block = _embedded_block(CORE_PATH)
    assert hook_block == core_block, (
        f"{hook_name} hook drifted from scripts/_hook_core.py; run: python3 scripts/build_hooks.py"
    )


@pytest.mark.parametrize(
    ("hook_name", "expected_shape"),
    [
        ("antigravity", "watcher"),
        ("cursor", "reasons"),
        ("claude", "reasons"),
        ("codex", "local"),
        ("opencode", "local"),
    ],
)
def test_all_hooks_share_canonical_blacklist(
    hook_name: str, expected_shape: str, hook_modules: dict[str, ModuleType]
) -> None:
    module = hook_modules[hook_name]
    canonical = CORE.CANONICAL_BLACKLIST_PATTERNS
    assert len(canonical) == len(module.LOCAL_BLACKLIST)

    if expected_shape == "local":
        expected = CORE.LOCAL_BLACKLIST_RULES
        assert expected == module.LOCAL_BLACKLIST
        for pattern, reason, rule_id in module.LOCAL_BLACKLIST:
            assert pattern and reason and rule_id.startswith("LOCAL_")
    elif expected_shape == "watcher":
        expected = CORE.LOCAL_BLACKLIST_WATCHER_RULES
        assert expected == module.LOCAL_BLACKLIST
        for pattern, reason, rule_id in module.LOCAL_BLACKLIST:
            assert pattern and reason and rule_id.startswith("RULE_")
    else:
        expected = CORE.LOCAL_BLACKLIST_REASONS
        assert expected == module.LOCAL_BLACKLIST
        for pattern, reason in module.LOCAL_BLACKLIST:
            assert pattern and reason

    # Every hook sees the same regexes and human reasons in the same order.
    assert [(entry[0], entry[1]) for entry in module.LOCAL_BLACKLIST] == [
        (pattern, reason) for pattern, reason, _local, _watcher in canonical
    ]


def test_canonical_blacklist_preserves_expected_rules() -> None:
    """Regression guard for the rule ids and patterns existing tests depend on."""
    local_rules = {rule_id: pattern for pattern, _reason, rule_id in CORE.LOCAL_BLACKLIST_RULES}
    expected_patterns = {
        "LOCAL_RM_RF_ROOT": r"\brm\s+",
        "LOCAL_MKFS": r"\bmkfs",
        "LOCAL_SUDO": r"\bsudo\b",
        "LOCAL_GIT_FORCE_PUSH": r"git\s+push",
        "LOCAL_CURL_BASH": r"curl\s+.*\|\s*(ba)?sh",
        "LOCAL_ENCODED_PIPE_SHELL": "base64",
        "LOCAL_IMDS": r"169\.254\.169\.254",
        "LOCAL_SECRET_EXFIL": r"\.env",
        "LOCAL_SECRET_READ": r"\.env\b",
        "LOCAL_CHMOD": r"chmod",
    }
    for rule_id, fragment in expected_patterns.items():
        assert rule_id in local_rules
        assert fragment in local_rules[rule_id]


@pytest.mark.parametrize("hook_name", sorted(HOOKS))
def test_hooks_share_circuit_breaker_constants(
    hook_name: str, hook_modules: dict[str, ModuleType]
) -> None:
    module = hook_modules[hook_name]
    assert module.CIRCUIT_STATE_FILE == CORE.CIRCUIT_STATE_FILE
    assert module.MAX_CONSECUTIVE_FAILURES == CORE.MAX_CONSECUTIVE_FAILURES
    assert module.FAILURE_WINDOW_SEC == CORE.FAILURE_WINDOW_SEC
    assert module.MAX_LOG_BYTES == CORE.MAX_LOG_BYTES == 10 * 1024 * 1024


@pytest.mark.parametrize("hook_name", sorted(HOOKS))
def test_hooks_share_circuit_breaker_state_semantics(
    hook_name: str,
    hook_modules: dict[str, ModuleType],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = hook_modules[hook_name]
    state_file = tmp_path / f"{hook_name}-circuit.json"
    monkeypatch.setattr(module, "CIRCUIT_STATE_FILE", state_file)
    monkeypatch.setattr(module, "MAX_CONSECUTIVE_FAILURES", 3)
    monkeypatch.setattr(module, "FAILURE_WINDOW_SEC", 60.0)

    module._reset_gateway_failures()
    assert not state_file.exists()

    assert module._record_gateway_failure() == 1
    assert module._record_gateway_failure() == 2
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert len(data["failures"]) == 2

    module._reset_gateway_failures()
    assert not state_file.exists()

    # Failures older than the sliding window are pruned, not carried forward.
    old = time.time() - 120.0
    state_file.write_text(json.dumps({"failures": [old]}), encoding="utf-8")
    assert module._record_gateway_failure() == 1


@pytest.mark.parametrize("hook_name", sorted(HOOKS))
def test_hooks_share_safe_coercion_helpers(
    hook_name: str, hook_modules: dict[str, ModuleType]
) -> None:
    module = hook_modules[hook_name]
    assert module._safe_int(None, 7) == 7
    assert module._safe_int("2.0") == 2
    assert module._safe_int(object(), 5) == 5
    assert module._safe_float(None, 1.5) == 1.5
    assert module._safe_float("2.2") == 2.2
    assert module._safe_float("not-a-number", 3.0) == 3.0


def test_claude_hook_detects_cursor_and_delegates_without_misattribution(
    hook_modules: dict[str, ModuleType],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Cursor invoking ~/.claude/settings.json must delegate cleanly rather than log as claude_code."""
    import io

    claude_mod = hook_modules["claude"]
    fake_home = tmp_path / "home"
    (fake_home / ".cursor").mkdir(parents=True)
    (fake_home / ".cursor" / "hooks.json").write_text(
        json.dumps(
            {"hooks": {"beforeShellExecution": [{"command": "python3 cursor_watcher_gate.py"}]}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)

    assert claude_mod._is_invoked_by_cursor({"conversation_id": "401f6106"}, "Grep") is True
    assert claude_mod._is_invoked_by_cursor({}, "Shell") is True

    monkeypatch.setenv("CLAUDECODE", "1")
    assert claude_mod._is_invoked_by_cursor({"conversation_id": "401f6106"}, "Bash") is False
    monkeypatch.delenv("CLAUDECODE", raising=False)

    # Ensure urlopen is never called when Cursor's native hook already covers the session.
    def _fail_urlopen(*_a: object, **_kw: object) -> None:
        raise AssertionError("Should not call Watcher gateway twice from Claude compat hook")

    monkeypatch.setattr(claude_mod.urllib.request, "urlopen", _fail_urlopen)
    monkeypatch.setattr(
        claude_mod.sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Write",
                    "tool_input": {"file_path": "/app/navigator.js"},
                    "conversation_id": "401f6106-3c73-41e3-90cd-7e3bf033fc76",
                }
            )
        ),
    )
    claude_mod.main()
    out = json.loads(capsys.readouterr().out.strip())
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"

    # Even after ~/.cursor/hooks.json is removed, Claude hook must still skip Cursor calls.
    (fake_home / ".cursor" / "hooks.json").unlink()
    monkeypatch.setattr(
        claude_mod.sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Shell",
                    "tool_input": {"command": "git status"},
                    "conversation_id": "401f6106-3c73-41e3-90cd-7e3bf033fc76",
                }
            )
        ),
    )
    claude_mod.main()
    out2 = json.loads(capsys.readouterr().out.strip())
    assert out2["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_cursor_gate_extracts_transcript_context_not_raw_uuid(
    hook_modules: dict[str, ModuleType],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor_mod = hook_modules["cursor"]
    cid = "401f6106-3c73-41e3-90cd-7e3bf033fc76"

    # Bare UUID without transcript returns empty string rather than polluting message context.
    fake_home = tmp_path / "home"
    (fake_home / ".cursor" / "projects").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    assert cursor_mod._extract_cursor_thought_context({"conversation_id": cid}, cid) == ""

    # With Cursor JSONL transcript, extracts latest user and assistant text.
    transcript = (
        fake_home / ".cursor" / "projects" / "demo" / "agent-transcripts" / cid / f"{cid}.jsonl"
    )
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "role": "user",
                        "message": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": "<user_query>\nFix navigator\n</user_query>",
                                }
                            ]
                        },
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "message": {
                            "content": [{"type": "text", "text": "Inspecting navigator routes."}]
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    ctx = cursor_mod._extract_cursor_thought_context({"conversation_id": cid}, cid)
    assert "[user]: Fix navigator" in ctx
    assert "[assistant]: Inspecting navigator routes." in ctx


def test_live_gate_evaluate_records_messages_alongside_tool_calls() -> None:
    """Live /api/v1/watcher/evaluate must persist messages so Sessions never show 0 msgs."""
    from fastapi.testclient import TestClient

    from server.app import app
    from server.watcher_store import get_watcher_store

    sid = "cursor-live-msgs-check-001"
    client = TestClient(app)
    resp = client.post(
        "/api/v1/watcher/evaluate",
        json={
            "agent_id": "cursor",
            "tool_name": "Write",
            "arguments": {"path": "/workspace/navigator.js"},
            "session_id": sid,
            "thought_context": "[user]: Fix navigator\n[assistant]: Updating navigator.js",
        },
    )
    assert resp.status_code == 200
    session = get_watcher_store().get_session(sid)
    assert session is not None
    assert len(session.trajectory.tool_calls) >= 1
    assert len(session.trajectory.messages) >= 2
    assert session.trajectory.messages[0].role == "user"
    assert session.trajectory.messages[0].content == "Fix navigator"
    assert session.trajectory.messages[1].role == "assistant"
    assert session.trajectory.messages[1].content == "Updating navigator.js"


def test_cursor_gate_reports_after_shell_and_post_tool_use_result(
    hook_modules: dict[str, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Cursor afterShellExecution / postToolUse must report execution output to /watcher/result."""
    import io

    cursor_mod = hook_modules["cursor"]
    captured: list[tuple[str, dict]] = []

    class _DummyResp:
        def __enter__(self) -> _DummyResp:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

    def _fake_urlopen(req: object, timeout: float = 1.5) -> _DummyResp:
        url = getattr(req, "full_url", "")
        data = json.loads(getattr(req, "data", b"{}").decode("utf-8"))
        captured.append((url, data))
        return _DummyResp()

    monkeypatch.setattr(cursor_mod.urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(
        cursor_mod,
        "WATCHER_URL",
        "https://openeval.studio/api/v1/watcher/evaluate",
    )
    monkeypatch.setattr(
        cursor_mod.sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "hook_event_name": "afterShellExecution",
                    "command": "git status",
                    "output": "On branch main\nnothing to commit",
                    "conversation_id": "401f6106-3c73-41e3-90cd-7e3bf033fc76",
                }
            )
        ),
    )
    cursor_mod.main()
    assert capsys.readouterr().out.strip() == "{}"
    assert len(captured) == 1
    url, body = captured[0]
    assert url.endswith("/api/v1/watcher/result")
    assert body["agent_id"] == "cursor"
    assert "nothing to commit" in body["stdout"]
