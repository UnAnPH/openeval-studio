"""Canonical Tool Normalization for OpenEval Watcher (Wave 1.1).

Maps agent-specific tool names across Claude Code, Cursor, OpenAI Codex CLI,
Google Antigravity, OpenCode, and MCP into canonical Watcher tool names and
provides lookup candidates for threshold resolution and decision_context
provenance.
"""

from __future__ import annotations

from typing import Any

CANONICAL_BASH = "Bash"
CANONICAL_READ = "Read"
CANONICAL_WRITE = "Write"
CANONICAL_EDIT = "Edit"
CANONICAL_GLOB = "Glob"
CANONICAL_GREP = "Grep"
CANONICAL_WEBFETCH = "WebFetch"
CANONICAL_ASK_USER = "AskUserQuestion"
CANONICAL_AGENT = "Agent"

# Legacy alias map so thresholds configured on either canonical ("Bash")
# or legacy ("execute_bash") names resolve cleanly.
_CANONICAL_ALIASES: dict[str, tuple[str, ...]] = {
    CANONICAL_BASH: (
        "Bash",
        "execute_bash",
        "run_command",
        "Shell",
        "PowerShell",
        "run_terminal_cmd",
    ),
    CANONICAL_READ: ("Read", "view_file", "read_file"),
    CANONICAL_WRITE: ("Write", "write_file", "write_to_file", "create_file"),
    CANONICAL_EDIT: (
        "Edit",
        "replace_file_content",
        "search_replace",
        "apply_patch",
        "patch",
        "edit_file",
        "NotebookEdit",
    ),
    CANONICAL_GLOB: ("Glob", "list_dir"),
    CANONICAL_GREP: ("Grep", "grep_search"),
    CANONICAL_WEBFETCH: ("WebFetch", "WebSearch"),
    CANONICAL_ASK_USER: ("AskUserQuestion", "ask_question", "question"),
    CANONICAL_AGENT: ("Agent", "SendMessage", "invoke_subagent", "task"),
}

_TOOL_TO_CANONICAL: dict[str, str] = {
    # Shell / Command Execution (Claude Code, Cursor, Codex, Antigravity)
    "bash": CANONICAL_BASH,
    "shell": CANONICAL_BASH,
    "powershell": CANONICAL_BASH,
    "terminal": CANONICAL_BASH,
    "run_command": CANONICAL_BASH,
    "execute_command": CANONICAL_BASH,
    "cmd": CANONICAL_BASH,
    "exec": CANONICAL_BASH,
    "sh": CANONICAL_BASH,
    "zsh": CANONICAL_BASH,
    "execute_bash": CANONICAL_BASH,
    "exec_command": CANONICAL_BASH,
    "shell_command": CANONICAL_BASH,
    "container.exec": CANONICAL_BASH,
    "local_shell": CANONICAL_BASH,
    "run_terminal_cmd": CANONICAL_BASH,
    # File Read
    "read": CANONICAL_READ,
    "read_file": CANONICAL_READ,
    "view_file": CANONICAL_READ,
    "cat_file": CANONICAL_READ,
    # File Write
    "write": CANONICAL_WRITE,
    "write_file": CANONICAL_WRITE,
    "write_to_file": CANONICAL_WRITE,
    "create_file": CANONICAL_WRITE,
    # File Edit / Patch
    "edit": CANONICAL_EDIT,
    "multiedit": CANONICAL_EDIT,
    "notebookedit": CANONICAL_EDIT,
    "edit_file": CANONICAL_EDIT,
    "replace_file_content": CANONICAL_EDIT,
    "multi_replace_file_content": CANONICAL_EDIT,
    "search_replace": CANONICAL_EDIT,
    "apply_patch": CANONICAL_EDIT,
    # OpenCode
    "patch": CANONICAL_EDIT,
    # Directory / Glob
    "glob": CANONICAL_GLOB,
    "list_dir": CANONICAL_GLOB,
    "find_by_name": CANONICAL_GLOB,
    # Search / Grep
    "grep": CANONICAL_GREP,
    "grep_search": CANONICAL_GREP,
    "search_files": CANONICAL_GREP,
    # Web
    "webfetch": CANONICAL_WEBFETCH,
    "websearch": CANONICAL_WEBFETCH,
    "search_web": CANONICAL_WEBFETCH,
    "read_url_content": CANONICAL_WEBFETCH,
    # User Interaction
    "askuserquestion": CANONICAL_ASK_USER,
    "ask_question": CANONICAL_ASK_USER,
    "ask_permission": CANONICAL_ASK_USER,
    "question": CANONICAL_ASK_USER,
    # Subagent Delegation
    "agent": CANONICAL_AGENT,
    "sendmessage": CANONICAL_AGENT,
    "send_message": CANONICAL_AGENT,
    "invoke_subagent": CANONICAL_AGENT,
    "subagent": CANONICAL_AGENT,
    "task": CANONICAL_AGENT,
}


def normalize_tool_name(raw_name: str) -> str:
    """Return the canonical Watcher tool name (e.g. 'Bash', 'Read', 'Write', 'Edit')."""
    cleaned = (raw_name or "").strip()
    if not cleaned:
        return "unknown"
    return _TOOL_TO_CANONICAL.get(cleaned.lower(), cleaned)


def get_tool_lookup_candidates(raw_name: str) -> list[str]:
    """Return ordered threshold lookup keys: exact name, canonical name, and aliases."""
    cleaned = (raw_name or "").strip()
    canonical = normalize_tool_name(cleaned)
    candidates: list[str] = []
    for item in (cleaned, canonical, *_CANONICAL_ALIASES.get(canonical, ())):
        if item and item not in candidates:
            candidates.append(item)
    return candidates


def is_command_tool(tool_name: str) -> bool:
    """Return True if the tool executes shell/terminal commands."""
    return normalize_tool_name(tool_name) == CANONICAL_BASH


def is_file_tool(tool_name: str) -> bool:
    """Return True if the tool reads, writes, or edits files."""
    return normalize_tool_name(tool_name) in {
        CANONICAL_READ,
        CANONICAL_WRITE,
        CANONICAL_EDIT,
        CANONICAL_GLOB,
        CANONICAL_GREP,
    }


def build_decision_context(
    raw_tool_name: str, matched_threshold_tool: str | None = None
) -> dict[str, Any]:
    """Build Apollo Watcher decision_context provenance dictionary."""
    canonical = normalize_tool_name(raw_tool_name)
    return {
        "original_tool": raw_tool_name,
        "evaluated_tool": canonical,
        "matched_threshold_key": matched_threshold_tool or canonical,
        "normalized": raw_tool_name != canonical,
    }
