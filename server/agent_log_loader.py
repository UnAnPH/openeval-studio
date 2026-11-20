"""Universal Coding Agent Log Loader for OpenEval & Watcher.

Ingests raw conversation and tool-call trajectories from:
  - Claude Code (~/.claude/sessions/*.jsonl or exported JSONL)
  - Cursor Composer (workspace storage state.vscdb or exported sessions)
  - Codex (~/.codex/sessions/*/*/*/*/rollout-*.jsonl)
  - OpenCode (`opencode export` JSON or `.jsonl` event streams)
  - Inspect AI (.eval archives)
  - Generic Agent JSON trajectories

Converts all into canonical Watcher Session and Trajectory entities for
high-signal flight control, forensic search, and collaborative review.
"""

import json
import logging
import os
import re
import time
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast
from uuid import uuid4

from schemas.watcher_models import (
    Message,
    ReviewRecord,
    Session,
    SessionStatus,
    ToolCall,
    ToolResult,
    Trajectory,
)
from server.watcher_store import WatcherStore, get_watcher_store

logger = logging.getLogger("openeval.server.agent_log_loader")


AgentLogFormat = Literal["cursor", "claude_code", "antigravity", "codex", "opencode", "unknown"]

_BLOCKING_DECISIONS = ("block", "deny", "reject", "escalate")

_CODEX_EVENT_TYPES = (
    "session_meta",
    "turn_context",
    "response_item",
    "event_msg",
    "token_usage_record",
    "world_state",
)

_UUID_RE = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


def _is_blocked_review(r: ReviewRecord) -> bool:
    """Single source of truth for whether a review counts as a blocked action."""
    if r.human_override == "allow" or r.resolution_status == "human_approved":
        return False
    return r.decision in _BLOCKING_DECISIONS or r.score >= 8


def sniff_agent_log_format(path: Path, max_lines: int = 40) -> AgentLogFormat:
    """Detect agent log format from file content (upload paths often lack agent-transcripts/)."""

    suffix = path.suffix.lower()
    if suffix == ".json":
        # OpenCode `opencode export` JSON carries {"info": ..., "messages": [...]}.
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, json.JSONDecodeError):
            return "cursor"
        if isinstance(data, dict) and "info" in data and "messages" in data:
            return "opencode"
        return "cursor"
    if "antigravity" in path.name.lower() or ".gemini" in path.parts:
        return "antigravity"
    if "codex" in path.parts or ".codex" in path.parts or path.name.startswith("rollout-"):
        return "codex"
    if not path.is_file():
        return "unknown"
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return "unknown"
    for line in lines[:max_lines]:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("type") in _CODEX_EVENT_TYPES:
            return "codex"
        if rec.get("type") in ("step_start", "step_finish") or (
            "sessionID" in rec and "part" in rec and rec.get("type") in ("text", "tool_use")
        ):
            return "opencode"
        if rec.get("type") in ("user", "assistant", "session_start", "tool_use", "tool_call"):
            return "claude_code"
        if rec.get("role") in ("user", "assistant", "system", "developer") and isinstance(
            rec.get("message"), dict
        ):
            return "cursor"
    return "unknown"


def _extract_content_parts(
    content: object,
) -> tuple[str, list[tuple[str, dict[str, object], str | None]]]:
    """Split Anthropic/Cursor-style content into text + tool_use blocks."""
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return str(content or ""), []
    texts: list[str] = []
    tools: list[tuple[str, dict[str, object], str | None]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            texts.append(str(block.get("text") or ""))
        elif btype in ("tool_use", "tool_call", "function_call"):
            name = str(block.get("name") or block.get("tool") or "tool")
            raw_input = block.get("input") or block.get("arguments") or {}
            args: dict[str, object] = (
                dict(raw_input) if isinstance(raw_input, dict) else {"input": raw_input}
            )
            tool_id = block.get("id") or block.get("tool_use_id")
            tools.append((name, args, str(tool_id) if tool_id else None))
    return "\n".join(t for t in texts if t).strip(), tools


_IMAGE_BLOCK_RE = re.compile(r"<image_files>.*?</image_files>", re.DOTALL | re.IGNORECASE)
_TIMESTAMP_BLOCK_RE = re.compile(r"<timestamp>.*?</timestamp>", re.DOTALL | re.IGNORECASE)
_CODEX_WRAPPER_BLOCK_RE = re.compile(
    r"<(environment_context|permissions instructions|user_instructions)[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)


def _as_str_dict(value: object) -> dict[str, Any]:
    """Narrow untrusted JSON payloads to a string-keyed dict without losing keys."""
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return cast(list[Any], value) if isinstance(value, list) else []


def _coerce_tool_arguments(raw_args: object) -> dict[str, object]:
    """Normalize Codex/OpenCode tool arguments into a dict (JSON object strings included)."""
    if isinstance(raw_args, dict):
        return dict(raw_args)
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
        return {"command": raw_args}
    if raw_args is None:
        return {}
    return {"command": str(raw_args)}


def _derive_raw_tool_input(args: dict[str, object]) -> str:
    """Pick the most security-relevant field from tool args, preserving the full value."""
    return str(
        args.get("command")
        or args.get("cmd")
        or args.get("input")
        or args.get("path")
        or json.dumps(args)
    )


_HIDDEN_PATH_MARKERS: tuple[str, ...] = (
    "/.cursor/",
    "/.gemini/",
    "/.codex/",
    "/.claude/",
    "/tmp/",
    "/var/folders/",
)

_EXPLICIT_CWD_KEYS: tuple[str, ...] = (
    "cwd",
    "Cwd",
    "working_dir",
    "working_directory",
    "workdir",
    "DirectoryPath",
    "SearchDirectory",
)

_TOOL_TARGET_KEYS: tuple[str, ...] = (
    "target_directory",
    "path",
    "Path",
    "file_path",
    "filePath",
    "AbsolutePath",
    "TargetFile",
)

_WORKSPACE_INNER_SUBDIRS: frozenset[str] = frozenset({"source", "src", "server", "ui", "tests"})

_FILE_BASENAMES: frozenset[str] = frozenset({"Dockerfile", "Makefile"})

_CD_COMMAND_RE = re.compile(r"(?:^|[;&|]\s*|\s)cd\s+(?:\"([^\"]+)\"|'([^']+)'|([^\s;&|]+))")


def _clean_arg_path(val: object) -> str:
    """Normalize a raw tool-arg path (whitespace, escaped quotes, and matching wrappers)."""
    if val is None:
        return ""
    text = str(val).strip()
    for _ in range(4):
        updated = text.replace('\\"', '"').replace("\\'", "'").strip()
        if len(updated) >= 2 and updated[0] == updated[-1] and updated[0] in ("'", '"'):
            updated = updated[1:-1].strip()
        if updated == text:
            break
        text = updated
    return text


def _posix_segments(path_str: str) -> list[str]:
    """Return non-root path segments for absolute POSIX-style paths."""
    return [seg for seg in PurePosixPath(path_str).parts if seg != "/"]


def _is_hidden_tool_path(path_str: str) -> bool:
    """True for agent-internal metadata / scratch paths that never identify a workspace."""
    normalized = path_str.replace("\\", "/")
    return any(marker in normalized for marker in _HIDDEN_PATH_MARKERS)


def _valid_workspace_candidate(path_str: str) -> bool:
    """An explicit cwd is usable when it is absolute, non-scratch, and project-deep."""
    return (
        path_str.startswith("/")
        and not _is_hidden_tool_path(path_str)
        and len(_posix_segments(path_str)) >= 3
    )


def _trim_workspace_root(path_str: str) -> str | None:
    """Trim known inner source subdirectories (``src``/``server``/``ui``/...) off a common dir."""
    segments = _posix_segments(path_str)
    if len(segments) < 3:
        return None
    for idx, segment in enumerate(segments):
        if idx >= 3 and segment in _WORKSPACE_INNER_SUBDIRS:
            return "/" + "/".join(segments[:idx])
    return "/" + "/".join(segments)


def _infer_working_dir_from_tool_calls(tool_calls: Sequence[ToolCall]) -> str | None:
    """Infer the real local workspace root from explicit cwds, shell ``cd``, and tool targets."""
    explicit_cwds: list[str] = []
    target_dirs: list[str] = []

    for tc in tool_calls:
        args = tc.arguments if isinstance(tc.arguments, dict) else {}

        for key in _EXPLICIT_CWD_KEYS:
            candidate = _clean_arg_path(args.get(key))
            if _valid_workspace_candidate(candidate):
                explicit_cwds.append(str(PurePosixPath(candidate)))

        for key in ("command", "CommandLine"):
            command = args.get(key)
            if not isinstance(command, str):
                continue
            command_text = command.replace('\\"', '"').replace("\\'", "'")
            for match in _CD_COMMAND_RE.finditer(command_text):
                cd_path = _clean_arg_path(next((g for g in match.groups() if g), ""))
                if _valid_workspace_candidate(cd_path):
                    explicit_cwds.append(str(PurePosixPath(cd_path)))

        for key in _TOOL_TARGET_KEYS:
            candidate = _clean_arg_path(args.get(key))
            if not candidate.startswith("/") or _is_hidden_tool_path(candidate):
                continue
            path_obj = PurePosixPath(candidate)
            basename = path_obj.name
            is_file = "." in basename or basename in _FILE_BASENAMES
            directory = path_obj.parent if is_file else path_obj
            if len(_posix_segments(str(directory))) < 3:
                continue
            target_dirs.append(str(directory))

    if explicit_cwds:
        return Counter(explicit_cwds).most_common(1)[0][0]

    if not target_dirs:
        return None

    groups: dict[str, list[str]] = {}
    for directory in target_dirs:
        segments = _posix_segments(directory)
        groups.setdefault("/" + "/".join(segments[:2]), []).append(directory)
    best_group = max(groups.values(), key=len)
    try:
        common = os.path.commonpath(best_group)
    except ValueError:
        common = best_group[0]
    return _trim_workspace_root(common)


def _clean_codex_user_text(text: str) -> str:
    """Strip Codex wrapper tags so titles reflect the user's real prompt."""
    cleaned = _IMAGE_BLOCK_RE.sub("", text)
    cleaned = _TIMESTAMP_BLOCK_RE.sub("", cleaned)
    cleaned = _CODEX_WRAPPER_BLOCK_RE.sub("", cleaned)
    cleaned = cleaned.replace("[Image]", "")
    return "\n".join(line.strip() for line in cleaned.splitlines() if line.strip())


def _derive_codex_title(messages: list[Message], raw_sid: str) -> str:
    for msg in messages:
        if msg.role != "user":
            continue
        candidate = _clean_codex_user_text(msg.content).split("\n")[0]
        if candidate:
            return candidate[:65] + ("..." if len(candidate) > 65 else "")
    return f"Codex Session {raw_sid[:8]}"


def get_antigravity_titles() -> dict[str, str]:
    """Parse ~/.gemini/antigravity/agyhub_summaries_proto.pb to map conversation UUIDs to human chat names."""
    pb_path = Path.home() / ".gemini" / "antigravity" / "agyhub_summaries_proto.pb"
    if not pb_path.exists():
        return {}
    try:
        b = pb_path.read_bytes()

        def parse_varint(data: bytes, pos: int):
            res = 0
            shift = 0
            while pos < len(data):
                b_val = data[pos]
                pos += 1
                res |= (b_val & 0x7F) << shift
                if (b_val & 0x80) == 0:
                    break
                shift += 7
            return res, pos

        pos = 0
        titles: dict[str, str] = {}
        while pos < len(b) - 40:
            if b[pos : pos + 2] == b"\n$" and b[pos + 2 : pos + 38].count(b"-") == 4:
                cid = b[pos + 2 : pos + 38].decode("ascii", errors="ignore")
                cur = pos + 38
                if cur < len(b) and b[cur] == 0x12:
                    cur += 1
                    sub_len, cur = parse_varint(b, cur)
                    sub_end = min(len(b), cur + sub_len)
                    if cur < sub_end and b[cur] == 0x0A:
                        cur += 1
                        title_len, cur = parse_varint(b, cur)
                        if cur + title_len <= sub_end:
                            t = b[cur : cur + title_len].decode("utf-8", errors="ignore").strip()
                            if t and not t.startswith("file://") and not t.startswith("http"):
                                titles[cid] = t
                pos += 38
            else:
                pos += 1
        return titles
    except Exception as err:
        logger.warning("Error parsing Antigravity summaries proto: %s", err)
        return {}


class UniversalAgentLogLoader:
    """Universal parser and ingestion adapter for frontier coding agent logs."""

    _ag_titles: dict[str, str] = {}
    _mtime_cache: dict[str, float] = {}
    _session_cache: dict[str, Session] = {}

    @classmethod
    def ingest_agent_log_file(
        cls,
        file_path: Path | str,
        agent_type: Literal[
            "claude_code", "cursor", "antigravity", "codex", "opencode", "auto"
        ] = "auto",
        default_status: SessionStatus = "completed",
        store: WatcherStore | None = None,
    ) -> Session | None:
        """Route an on-disk agent log to the correct parser (upload-safe content sniffing)."""
        path = Path(file_path)
        suffix = path.suffix.lower()
        agent = agent_type
        is_antigravity = (
            agent == "antigravity"
            or "antigravity" in path.parts
            or ".gemini" in path.parts
            or "antigravity" in path.name.lower()
        )
        fmt: AgentLogFormat
        if agent == "auto":
            fmt = sniff_agent_log_format(path)
        elif agent == "antigravity":
            fmt = "antigravity"
        else:
            fmt = agent  # type: ignore[assignment]

        if is_antigravity or fmt == "antigravity":
            return cls.ingest_antigravity_transcript(path, status=default_status, store=store)
        if fmt == "codex" or agent == "codex":
            return cls.ingest_codex_jsonl(path, status=default_status, store=store)
        if fmt == "opencode" or agent == "opencode":
            return cls.ingest_opencode_session(path, status=default_status, store=store)
        if fmt == "cursor" or agent == "cursor":
            if suffix == ".json":
                return cls.ingest_cursor_session_json(path, status=default_status, store=store)
            return cls.ingest_cursor_agent_transcript_jsonl(
                path, status=default_status, store=store
            )
        if fmt == "claude_code" or agent == "claude_code":
            return cls.ingest_claude_code_jsonl(path, status=default_status, store=store)
        if suffix == ".jsonl":
            cursor_session = cls.ingest_cursor_agent_transcript_jsonl(
                path, status=default_status, store=store
            )
            if cursor_session is not None:
                return cursor_session
            return cls.ingest_claude_code_jsonl(path, status=default_status, store=store)
        return cls.ingest_claude_code_jsonl(path, status=default_status, store=store)

    @classmethod
    def ingest_claude_code_jsonl(
        cls,
        file_path: Path | str,
        status: SessionStatus = "completed",
        store: WatcherStore | None = None,
    ) -> Session | None:
        """Parse a Claude Code session JSONL file into a canonical Session."""
        path = Path(file_path)
        if not path.exists():
            logger.warning("Claude Code log path does not exist: %s", path)
            return None

        lines = path.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            return None

        session_id = path.stem
        messages: list[Message] = []
        tool_calls: list[ToolCall] = []
        tool_results: list[ToolResult] = []

        total_tokens = 0
        model = "claude-3-7-sonnet"
        working_dir = None

        for line in lines:
            try:
                record = json.loads(line)
            except Exception:
                continue

            event_type = record.get("type") or record.get("event")
            if event_type in ("user", "assistant"):
                # Native Claude Code ~/.claude/projects/*.jsonl format
                msg = record.get("message") or {}
                text, tools = _extract_content_parts(msg.get("content", ""))
                role = "user" if event_type == "user" else "assistant"
                if text or tools:
                    step_tcs: list[ToolCall] = []
                    for name, args, tool_id in tools:
                        raw_input = str(
                            args.get("command") or args.get("file_path") or json.dumps(args)
                        )
                        tc = ToolCall(
                            tool_id=tool_id or str(uuid4())[:8],
                            tool_name=name,
                            arguments=args,
                            raw_input=raw_input,
                        )
                        tool_calls.append(tc)
                        step_tcs.append(tc)
                    messages.append(
                        Message(
                            role=role,
                            content=text or (f"[{len(step_tcs)} tool call(s)]" if step_tcs else ""),
                            tool_calls=step_tcs,
                        )
                    )
                sid = record.get("sessionId")
                if isinstance(sid, str) and sid:
                    session_id = sid
                continue

            if not event_type and "role" in record:
                role = record.get("role", "assistant")
                msg_obj = record.get("message")
                if isinstance(msg_obj, dict):
                    text_body, tools = _extract_content_parts(msg_obj.get("content", ""))
                    thinking = record.get("thinking")
                    step_tcs: list[ToolCall] = []
                    for name, args, tool_id in tools:
                        raw_input = str(args.get("command") or args.get("path") or json.dumps(args))
                        tc = ToolCall(
                            tool_id=tool_id or str(uuid4())[:8],
                            tool_name=name,
                            arguments=args,
                            raw_input=raw_input,
                        )
                        tool_calls.append(tc)
                        step_tcs.append(tc)
                    if text_body or step_tcs:
                        messages.append(
                            Message(
                                role=role
                                if role in ("user", "assistant", "developer", "system")
                                else "assistant",
                                content=text_body
                                or (f"[{len(step_tcs)} tool call(s)]" if step_tcs else ""),
                                thinking=thinking,
                                tool_calls=step_tcs,
                            )
                        )
                else:
                    content = record.get("content", "")
                    thinking = record.get("thinking")
                    messages.append(
                        Message(
                            role=role
                            if role in ("user", "assistant", "developer", "system")
                            else "assistant",
                            content=str(content),
                            thinking=thinking,
                        )
                    )
                continue

            if event_type == "session_start":
                model = record.get("model", model)
                working_dir = record.get("cwd") or record.get("working_dir")
            elif event_type == "user_prompt":
                prompt = record.get("prompt") or record.get("text", "")
                messages.append(Message(role="user", content=str(prompt)))
            elif event_type == "assistant_response":
                text = record.get("text", "")
                thinking = record.get("thinking") or record.get("reasoning")
                messages.append(Message(role="assistant", content=str(text), thinking=thinking))
            elif event_type in ("tool_use", "tool_call"):
                tool_name = record.get("tool") or record.get("tool_name", "bash")
                args = record.get("arguments") or record.get("input") or {}
                if isinstance(args, str):
                    raw_input = args
                    args = {"command": args}
                elif isinstance(args, dict):
                    raw_input = str(
                        args.get("command")
                        or args.get("cmd")
                        or args.get("path")
                        or json.dumps(args)
                    )
                else:
                    raw_input = json.dumps(args)

                parsed_args: dict[str, object] = (
                    dict(args) if isinstance(args, dict) else {"raw": str(args)}
                )
                tc = ToolCall(
                    tool_id=record.get("tool_id", str(uuid4())[:8]),
                    tool_name=tool_name,
                    arguments=parsed_args,
                    raw_input=raw_input,
                )
                tool_calls.append(tc)
            elif event_type in ("tool_result", "tool_output"):
                tr = ToolResult(
                    tool_id=record.get("tool_id", str(uuid4())[:8]),
                    tool_name=record.get("tool_name", "tool"),
                    stdout=record.get("stdout", ""),
                    stderr=record.get("stderr", ""),
                    exit_code=int(record.get("exit_code", 0)),
                    duration_ms=float(record.get("duration_ms", 0.0)),
                    is_error=bool(record.get("is_error", False)),
                )
                tool_results.append(tr)

            if "usage" in record and isinstance(record["usage"], dict):
                total_tokens += record["usage"].get("total_tokens", 0)

        from server.policy_gateway import PolicyGateway

        first_user = next((m.content for m in messages if m.role == "user"), None)
        user_intent = first_user or "Claude Code autonomous session"
        gw = PolicyGateway.from_store(use_llm=False)
        reviews: list[ReviewRecord] = [
            gw.evaluate_tool_call(
                session_id=session_id,
                tool_name=tc.tool_name,
                tool_input=tc.raw_input or "",
                user_intent=user_intent,
            )
            for tc in tool_calls
        ]

        trajectory = Trajectory(
            session_id=session_id,
            messages=messages,
            tool_calls=tool_calls,
            tool_results=tool_results,
            reviews=reviews,
        )

        title = None
        if first_user:
            c = first_user.strip().split("\n")[0]
            title = c[:65] + ("..." if len(c) > 65 else "")
        if not title:
            title = f"Claude Code Session {session_id[:8]}"

        blocked_revs = [r for r in reviews if _is_blocked_review(r)]
        final_status: SessionStatus = "blocked" if blocked_revs else status
        blocked_count = len(blocked_revs)

        if not working_dir:
            working_dir = _infer_working_dir_from_tool_calls(tool_calls)

        now_iso = datetime.now(UTC).isoformat()
        session = Session(
            session_id=session_id,
            title=title,
            run_id=session_id,
            project_name=title,
            task_id="claude-code-session",
            agent_type="claude_code",
            model=model,
            provider="anthropic",
            status=final_status,
            working_dir=working_dir,
            trajectory=trajectory,
            decisions=reviews,
            blocked_count=blocked_count,
            blocked_turns_count=blocked_count,
            total_tokens=total_tokens,
            created_at=now_iso,
            updated_at=now_iso,
        )

        active_store = store or get_watcher_store()
        active_store.record_session(session)
        logger.info("Ingested Claude Code session '%s' (%d messages)", session_id, len(messages))
        return session

    @classmethod
    def ingest_cursor_session_json(
        cls,
        file_path: Path | str,
        status: SessionStatus = "completed",
        store: WatcherStore | None = None,
    ) -> Session | None:
        """Parse a Cursor composer or workspace session JSON export."""
        path = Path(file_path)
        if not path.exists():
            return None

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as err:
            logger.warning("Failed parsing Cursor session JSON: %s", err)
            return None

        session_id = data.get("sessionId") or data.get("id") or path.stem
        messages_raw = data.get("conversation") or data.get("messages") or data.get("bubbles") or []
        messages: list[Message] = []
        tool_calls: list[ToolCall] = []
        tool_results: list[ToolResult] = []

        from server.policy_gateway import PolicyGateway

        gw = PolicyGateway.from_store(use_llm=False)
        first_user: str | None = None

        for m in messages_raw:
            if not isinstance(m, dict):
                continue
            role = m.get("role") or ("user" if m.get("type") == "user" else "assistant")
            content_str = m.get("text", "") or m.get("content", "") or ""
            if role == "user" and not first_user and content_str:
                first_user = content_str.strip().split("\n")[0]

            step_tcs: list[ToolCall] = []
            step_trs: list[ToolResult] = []

            # 1. Direct toolCalls / tool_calls
            raw_tcs = m.get("toolCalls") or m.get("tool_calls") or []
            if isinstance(raw_tcs, list):
                for tc_dict in raw_tcs:
                    if isinstance(tc_dict, dict):
                        fn_name = (
                            tc_dict.get("function", {}).get("name")
                            if isinstance(tc_dict.get("function"), dict)
                            else (
                                tc_dict.get("tool_name")
                                or tc_dict.get("tool")
                                or tc_dict.get("name")
                                or "tool"
                            )
                        )
                        args = (
                            tc_dict.get("function", {}).get("arguments")
                            if isinstance(tc_dict.get("function"), dict)
                            else (
                                tc_dict.get("arguments")
                                or tc_dict.get("args")
                                or tc_dict.get("input")
                                or {}
                            )
                        )
                        if isinstance(args, str):
                            raw_inp = args
                            try:
                                parsed = json.loads(args)
                                args = parsed if isinstance(parsed, dict) else {"raw": args}
                            except Exception:
                                args = (
                                    {"command": args}
                                    if "bash" in str(fn_name).lower()
                                    else {"raw": args}
                                )
                        else:
                            raw_inp = json.dumps(args) if isinstance(args, dict) else str(args)
                        tc_obj = ToolCall(
                            tool_id=str(tc_dict.get("id") or tc_dict.get("tool_id") or uuid4())[:8],
                            tool_name=str(fn_name),
                            arguments=dict(args) if isinstance(args, dict) else {"raw": str(args)},
                            raw_input=str(raw_inp),
                        )
                        tool_calls.append(tc_obj)
                        step_tcs.append(tc_obj)

            # 2. bubble.toolFormerData
            tool_former = m.get("toolFormerData") or m.get("tools")
            if tool_former:
                tf_list = tool_former if isinstance(tool_former, list) else [tool_former]
                for item in tf_list:
                    if isinstance(item, dict):
                        name = item.get("name") or item.get("tool") or "tool"
                        params = (
                            item.get("params") or item.get("arguments") or item.get("args") or {}
                        )
                        raw_inp = str(
                            params.get("command") or params.get("path") or json.dumps(params)
                        )
                        tc_obj = ToolCall(
                            tool_id=str(item.get("id") or uuid4())[:8],
                            tool_name=str(name),
                            arguments=dict(params)
                            if isinstance(params, dict)
                            else {"raw": str(params)},
                            raw_input=raw_inp,
                        )
                        tool_calls.append(tc_obj)
                        step_tcs.append(tc_obj)

            # 3. Direct command field
            cmd_data = m.get("command") or m.get("commands")
            if cmd_data:
                cmds = cmd_data if isinstance(cmd_data, list) else [cmd_data]
                for c in cmds:
                    c_str = str(c)
                    tc_obj = ToolCall(
                        tool_id=str(uuid4())[:8],
                        tool_name="bash",
                        arguments={"command": c_str},
                        raw_input=c_str,
                    )
                    tool_calls.append(tc_obj)
                    step_tcs.append(tc_obj)

            # 4. _extract_content_parts if markdown/xml embeds tool calls
            if content_str and not step_tcs:
                _, extracted_tools = _extract_content_parts(content_str)
                for name, args, tool_id in extracted_tools:
                    raw_inp = str(args.get("command") or args.get("path") or json.dumps(args))
                    tc_obj = ToolCall(
                        tool_id=tool_id or str(uuid4())[:8],
                        tool_name=name,
                        arguments=args,
                        raw_input=raw_inp,
                    )
                    tool_calls.append(tc_obj)
                    step_tcs.append(tc_obj)

            # 5. Tool results
            raw_trs = m.get("toolResults") or m.get("tool_results") or []
            if isinstance(raw_trs, list):
                for tr_dict in raw_trs:
                    if isinstance(tr_dict, dict):
                        tr_obj = ToolResult(
                            tool_id=str(tr_dict.get("tool_id") or tr_dict.get("id") or uuid4())[:8],
                            tool_name=str(tr_dict.get("tool_name") or "tool"),
                            stdout=str(tr_dict.get("stdout") or tr_dict.get("output") or ""),
                            stderr=str(tr_dict.get("stderr") or ""),
                            exit_code=int(tr_dict.get("exit_code", 0)),
                            is_error=bool(tr_dict.get("is_error", False)),
                        )
                        tool_results.append(tr_obj)
                        step_trs.append(tr_obj)
            elif m.get("type") in ("tool_result", "terminal_output") or role == "tool":
                tr_obj = ToolResult(
                    tool_id=str(m.get("tool_id") or m.get("id") or uuid4())[:8],
                    tool_name=str(m.get("tool_name") or "tool"),
                    stdout=str(content_str),
                    is_error=bool(m.get("is_error", False)),
                )
                tool_results.append(tr_obj)
                step_trs.append(tr_obj)

            messages.append(
                Message(
                    role=role
                    if role in ("user", "assistant", "system", "developer")
                    else "assistant",
                    content=content_str or (f"[{len(step_tcs)} tool call(s)]" if step_tcs else ""),
                    tool_calls=step_tcs,
                    tool_results=step_trs,
                )
            )

        user_intent = first_user or "Cursor session"
        reviews: list[ReviewRecord] = [
            gw.evaluate_tool_call(
                session_id=session_id,
                tool_name=tc.tool_name,
                tool_input=tc.raw_input or "",
                user_intent=user_intent,
            )
            for tc in tool_calls
        ]

        trajectory = Trajectory(
            session_id=session_id,
            messages=messages,
            tool_calls=tool_calls,
            tool_results=tool_results,
            reviews=reviews,
        )

        title = data.get("title") or data.get("name") or f"Cursor Session {session_id[:8]}"
        blocked_revs = [r for r in reviews if _is_blocked_review(r)]
        final_status: SessionStatus = "blocked" if blocked_revs else status
        blocked_count = len(blocked_revs)

        session = Session(
            session_id=session_id,
            title=title,
            run_id=session_id,
            project_name=title,
            task_id="cursor-composer",
            agent_type="cursor",
            model=data.get("model", "gpt-4o"),
            provider="cursor",
            working_dir=data.get("workspaceRoot") or "/workspace",
            status=final_status,
            trajectory=trajectory,
            decisions=reviews,
            blocked_count=blocked_count,
            blocked_turns_count=blocked_count,
            total_tokens=data.get("totalTokens", 0),
            created_at=datetime.now(UTC).isoformat(),
            updated_at=datetime.now(UTC).isoformat(),
        )

        active_store = store or get_watcher_store()
        active_store.record_session(session)
        logger.info(
            "Ingested Cursor session '%s' with %d tool calls and %d reviews",
            session_id,
            len(tool_calls),
            len(reviews),
        )
        return session

    @classmethod
    def ingest_cursor_agent_transcript_jsonl(
        cls,
        file_path: Path | str,
        status: SessionStatus = "completed",
        max_tool_calls: int = 400,
        store: WatcherStore | None = None,
    ) -> Session | None:
        """Parse Cursor IDE agent-transcripts/*.jsonl (role + message.content blocks)."""
        path = Path(file_path)
        if not path.exists():
            return None

        mtime = path.stat().st_mtime
        path_str = str(path.resolve())
        if (
            path_str in cls._mtime_cache
            and cls._mtime_cache[path_str] == mtime
            and path_str in cls._session_cache
        ):
            cached = cls._session_cache[path_str]
            active_store = store or get_watcher_store()
            if cached.status != status:
                cached.status = status
            if active_store.get_session(cached.session_id) is None or cached.status != getattr(
                active_store.get_session(cached.session_id), "status", None
            ):
                active_store.record_session(cached)
            return cached

        lines = path.read_text(encoding="utf-8", errors="ignore").strip().splitlines()
        if not lines:
            return None

        # Prefer parent transcript id (folder name) over subagent stem
        parent = path.parent.name
        if parent.startswith("openeval_ingest_") or parent in (
            "agent-transcripts",
            "subagents",
            "tmp",
            "",
        ):
            session_id = path.stem
        else:
            session_id = parent
        if path.parent.name == "subagents":
            session_id = f"{path.parent.parent.name}-sub-{path.stem[:8]}"

        messages: list[Message] = []
        tool_calls: list[ToolCall] = []
        reviews: list[ReviewRecord] = []

        from server.policy_gateway import PolicyGateway

        gw = PolicyGateway.from_store(use_llm=False)

        for line in lines:
            try:
                record = json.loads(line)
            except Exception:
                continue
            role_raw = record.get("role")
            if role_raw not in ("user", "assistant", "system", "developer"):
                continue
            msg = record.get("message") or {}
            text, tools = _extract_content_parts(msg.get("content", record.get("content", "")))
            step_tcs: list[ToolCall] = []
            for name, args, tool_id in tools:
                if len(tool_calls) >= max_tool_calls:
                    break
                if set(args) == {"input"} and isinstance(args.get("input"), str):
                    args = _coerce_tool_arguments(args["input"])
                raw_input = str(
                    args.get("command")
                    or args.get("path")
                    or args.get("file_path")
                    or args.get("glob_pattern")
                    or json.dumps(args)
                )
                tc = ToolCall(
                    tool_id=tool_id or str(uuid4())[:8],
                    tool_name=name,
                    arguments=args,
                    raw_input=raw_input,
                )
                tool_calls.append(tc)
                step_tcs.append(tc)
                rec = gw.evaluate_tool_call(
                    session_id=session_id,
                    tool_name=name,
                    tool_input=raw_input,
                    user_intent="Cursor coding agent session",
                )
                reviews.append(rec)
            if text or step_tcs:
                messages.append(
                    Message(
                        role=role_raw,
                        content=text or (f"[{len(step_tcs)} tool call(s)]" if step_tcs else ""),
                        tool_calls=step_tcs,
                    )
                )

        if not messages and not tool_calls:
            return None

        first_user = next((m.content for m in messages if m.role == "user"), None)
        title = None
        if first_user:
            # Strip timestamp / XML wrappers Cursor embeds
            clean = first_user
            if "<user_query>" in clean:
                clean = clean.split("<user_query>", 1)[1].split("</user_query>", 1)[0]
            title_line = clean.strip().split("\n")[0]
            title = title_line[:65] + ("..." if len(title_line) > 65 else "")
        if not title:
            title = f"Cursor Agent {session_id[:8]}"

        # Infer project from ~/.cursor/projects/<slug>/agent-transcripts/...
        project_name = title
        try:
            parts = path.resolve().parts
            if "projects" in parts:
                idx = parts.index("projects")
                if idx + 1 < len(parts):
                    project_name = parts[idx + 1]
        except Exception:
            pass

        trajectory = Trajectory(
            session_id=session_id,
            messages=messages,
            tool_calls=tool_calls,
            tool_results=[],
            reviews=reviews,
        )
        blocked_revs = [r for r in reviews if _is_blocked_review(r)]
        if blocked_revs:
            final_status: SessionStatus = "blocked"
        elif status in ("working", "active", "running"):
            age_sec = time.time() - mtime
            final_status = "working" if age_sec <= 15 * 60 else "closed"
        else:
            final_status = status

        working_dir = _infer_working_dir_from_tool_calls(tool_calls)

        now_iso = datetime.now(UTC).isoformat()
        ts_iso = datetime.fromtimestamp(mtime, UTC).isoformat()
        use_now = status in ("working", "active", "running")
        session = Session(
            session_id=f"cursor-{session_id[:12]}",
            title=title,
            run_id=session_id,
            project_name=project_name,
            task_id="cursor-agent-transcript",
            agent_type="cursor",
            model="cursor-agent",
            provider="cursor",
            status=final_status,
            working_dir=working_dir,
            trajectory=trajectory,
            decisions=reviews,
            blocked_count=len(blocked_revs),
            blocked_turns_count=len(blocked_revs),
            created_at=now_iso if use_now else ts_iso,
            updated_at=now_iso if use_now else ts_iso,
        )
        active_store = store or get_watcher_store()
        active_store.record_session(session)
        cls._mtime_cache[path_str] = mtime
        cls._session_cache[path_str] = session
        logger.info(
            "Ingested Cursor transcript '%s' (%d msgs, %d tools)",
            session.session_id,
            len(messages),
            len(tool_calls),
        )
        return session

    @classmethod
    def ingest_codex_jsonl(
        cls,
        file_path: Path | str,
        status: SessionStatus = "completed",
        max_tool_calls: int = 400,
        store: WatcherStore | None = None,
    ) -> Session | None:
        """Parse a Codex CLI rollout JSONL (session_meta / turn_context / response_item)."""
        path = Path(file_path)
        if not path.exists():
            return None

        mtime = path.stat().st_mtime
        path_str = str(path.resolve())
        if (
            path_str in cls._mtime_cache
            and cls._mtime_cache[path_str] == mtime
            and path_str in cls._session_cache
        ):
            cached = cls._session_cache[path_str]
            active_store = store or get_watcher_store()
            if cached.status != status:
                cached.status = status
            if active_store.get_session(cached.session_id) is None or cached.status != getattr(
                active_store.get_session(cached.session_id), "status", None
            ):
                active_store.record_session(cached)
            return cached

        lines = path.read_text(encoding="utf-8", errors="ignore").strip().splitlines()
        if not lines:
            return None

        records: list[dict[str, Any]] = []
        for line in lines:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if isinstance(rec, dict):
                records.append(rec)
        if not records:
            return None

        raw_sid = ""
        working_dir: str | None = None
        model = "codex"
        for rec in records:
            pl = _as_str_dict(rec.get("payload"))
            if rec.get("type") == "session_meta":
                raw_sid = raw_sid or str(pl.get("session_id") or pl.get("id") or "")
                cwd = pl.get("cwd")
                if not working_dir and isinstance(cwd, str) and cwd:
                    working_dir = cwd
            elif rec.get("type") == "turn_context":
                cwd = pl.get("cwd")
                if not working_dir and isinstance(cwd, str) and cwd:
                    working_dir = cwd
                model = str(pl.get("model") or model)

        if not raw_sid:
            match = re.search(_UUID_RE, path.name) or re.search(_UUID_RE, path.stem)
            raw_sid = match.group(0) if match else path.stem
        session_id = f"codex-{raw_sid[:16]}"

        from server.policy_gateway import PolicyGateway

        gw = PolicyGateway.from_store(use_llm=False)
        messages: list[Message] = []
        tool_calls: list[ToolCall] = []
        tool_results: list[ToolResult] = []
        reviews: list[ReviewRecord] = []
        tool_names_by_id: dict[str, str] = {}
        user_intent = "Codex coding agent session"

        for rec in records:
            if rec.get("type") != "response_item":
                continue
            pl = _as_str_dict(rec.get("payload"))
            ptype = pl.get("type")

            if ptype == "message" and pl.get("role") in ("user", "assistant"):
                texts: list[str] = []
                for item in pl.get("content") or []:
                    if isinstance(item, dict):
                        if item.get("type") in ("text", "input_text", "output_text"):
                            texts.append(str(item.get("text") or ""))
                    elif isinstance(item, str):
                        texts.append(item)
                content = "\n".join(t for t in texts if t).strip()
                if not content:
                    continue
                role: Literal["user", "assistant"] = (
                    "user" if pl.get("role") == "user" else "assistant"
                )
                messages.append(Message(role=role, content=content))
                if role == "user" and user_intent == "Codex coding agent session":
                    user_intent = _clean_codex_user_text(content)[:200] or user_intent
                continue

            if ptype in ("custom_tool_call", "function_call"):
                if len(tool_calls) >= max_tool_calls:
                    continue
                name = str(pl.get("name") or "tool")
                call_id = str(pl.get("call_id") or pl.get("id") or uuid4().hex[:8])
                args = _coerce_tool_arguments(pl.get("input") or pl.get("arguments") or {})
                raw_input = _derive_raw_tool_input(args)
                tc = ToolCall(
                    tool_id=call_id,
                    tool_name=name,
                    arguments=args,
                    raw_input=raw_input,
                )
                tool_calls.append(tc)
                tool_names_by_id[call_id] = name
                reviews.append(
                    gw.evaluate_tool_call(
                        session_id=session_id,
                        tool_name=name,
                        tool_input=raw_input,
                        user_intent=user_intent,
                    )
                )
                if messages and messages[-1].role == "assistant":
                    messages[-1].tool_calls.append(tc)
                else:
                    messages.append(Message(role="assistant", content="", tool_calls=[tc]))
                continue

            if ptype in ("custom_tool_call_output", "function_call_output"):
                call_id = str(pl.get("call_id") or pl.get("id") or uuid4().hex[:8])
                raw_output = pl.get("output")
                if isinstance(raw_output, str):
                    stdout = raw_output
                elif isinstance(raw_output, list):
                    stdout = "\n".join(
                        str(item.get("text") or "") for item in raw_output if isinstance(item, dict)
                    )
                else:
                    stdout = str(raw_output or "")
                tr = ToolResult(
                    tool_id=call_id,
                    tool_name=tool_names_by_id.get(call_id, "tool"),
                    stdout=stdout,
                )
                tool_results.append(tr)
                if messages and messages[-1].role == "assistant":
                    messages[-1].tool_results.append(tr)

        if not messages and not tool_calls:
            return None

        trajectory = Trajectory(
            session_id=session_id,
            messages=messages,
            tool_calls=tool_calls,
            tool_results=tool_results,
            reviews=reviews,
        )
        title = _derive_codex_title(messages, raw_sid)
        blocked_revs = [r for r in reviews if _is_blocked_review(r)]
        final_status: SessionStatus = "blocked" if blocked_revs else status
        ts_iso = datetime.fromtimestamp(mtime, UTC).isoformat()

        if not working_dir:
            working_dir = _infer_working_dir_from_tool_calls(tool_calls)

        session = Session(
            session_id=session_id,
            title=title,
            run_id=session_id,
            project_name=title,
            task_id="codex-session",
            agent_type="codex",
            model=model,
            provider="openai",
            status=final_status,
            working_dir=working_dir,
            trajectory=trajectory,
            decisions=reviews,
            total_steps=len(tool_calls),
            blocked_count=len(blocked_revs),
            blocked_turns_count=len(blocked_revs),
            created_at=ts_iso,
            updated_at=ts_iso,
        )
        active_store = store or get_watcher_store()
        active_store.record_session(session)
        cls._mtime_cache[path_str] = mtime
        cls._session_cache[path_str] = session
        logger.info(
            "Ingested Codex session '%s' (%d msgs, %d tools)",
            session.session_id,
            len(messages),
            len(tool_calls),
        )
        return session

    @classmethod
    def ingest_opencode_session(
        cls,
        file_path: Path | str,
        status: SessionStatus = "completed",
        max_tool_calls: int = 400,
        store: WatcherStore | None = None,
    ) -> Session | None:
        """Parse an OpenCode session (``opencode export`` JSON or ``.jsonl`` event stream)."""
        path = Path(file_path)
        if not path.exists():
            return None

        mtime = path.stat().st_mtime
        path_str = str(path.resolve())
        if (
            path_str in cls._mtime_cache
            and cls._mtime_cache[path_str] == mtime
            and path_str in cls._session_cache
        ):
            cached = cls._session_cache[path_str]
            active_store = store or get_watcher_store()
            if cached.status != status:
                cached.status = status
            if active_store.get_session(cached.session_id) is None or cached.status != getattr(
                active_store.get_session(cached.session_id), "status", None
            ):
                active_store.record_session(cached)
            return cached

        suffix = path.suffix.lower()
        raw_sid = path.stem
        title = ""
        working_dir: str | None = None
        model = "opencode"
        records: list[dict[str, Any]] = []

        if suffix == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
            except Exception as err:
                logger.warning("Failed parsing OpenCode session JSON: %s", err)
                return None
            if not isinstance(data, dict):
                return None
            info = _as_str_dict(data.get("info"))
            raw_sid = str(info.get("id") or path.stem)
            title = str(info.get("title") or "")
            directory = info.get("directory")
            if isinstance(directory, str) and directory:
                working_dir = directory
            records = [m for m in (data.get("messages") or []) if isinstance(m, dict)]
        else:
            lines = path.read_text(encoding="utf-8", errors="ignore").strip().splitlines()
            if not lines:
                return None
            for line in lines:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if isinstance(rec, dict):
                    records.append(rec)
            for rec in records:
                sid = rec.get("sessionID")
                if isinstance(sid, str) and sid:
                    raw_sid = sid
                    break

        if not records:
            return None
        session_id = f"opencode-{raw_sid[:28]}"

        from server.policy_gateway import PolicyGateway

        gw = PolicyGateway.from_store(use_llm=False)
        messages: list[Message] = []
        tool_calls: list[ToolCall] = []
        tool_results: list[ToolResult] = []
        reviews: list[ReviewRecord] = []
        user_intent = "OpenCode coding agent session"

        def _append_text(role: Literal["user", "assistant"], text: str) -> None:
            nonlocal user_intent
            cleaned_text = text.strip()
            if not cleaned_text:
                return
            if role == "user" and user_intent == "OpenCode coding agent session":
                user_intent = cleaned_text[:200]
            if (
                role == "assistant"
                and messages
                and messages[-1].role == "assistant"
                and not messages[-1].tool_calls
            ):
                messages[-1].content = f"{messages[-1].content}\n{cleaned_text}".strip()
                return
            messages.append(Message(role=role, content=cleaned_text))

        def _register_tool(name: str, call_id: str, state: dict[str, Any]) -> None:
            if len(tool_calls) >= max_tool_calls:
                return
            args = _coerce_tool_arguments(state.get("input"))
            raw_input = _derive_raw_tool_input(args)
            tc = ToolCall(
                tool_id=call_id,
                tool_name=name,
                arguments=args,
                raw_input=raw_input,
            )
            tool_calls.append(tc)
            reviews.append(
                gw.evaluate_tool_call(
                    session_id=session_id,
                    tool_name=name,
                    tool_input=raw_input,
                    user_intent=user_intent,
                )
            )
            if messages and messages[-1].role == "assistant":
                messages[-1].tool_calls.append(tc)
            else:
                messages.append(Message(role="assistant", content="", tool_calls=[tc]))

            output = state.get("output")
            if output is None:
                return
            if isinstance(output, str):
                out_text = output
            elif isinstance(output, (dict, list)):
                out_text = json.dumps(output)
            else:
                out_text = str(output)
            tr = ToolResult(
                tool_id=call_id,
                tool_name=name,
                stdout=out_text,
                is_error=str(state.get("status") or "").lower() in ("error", "failed"),
            )
            tool_results.append(tr)
            if messages and messages[-1].role == "assistant":
                messages[-1].tool_results.append(tr)

        if suffix == ".json":
            for m in records:
                inner_info = _as_str_dict(m.get("info"))
                raw_role = inner_info.get("role") or m.get("type") or m.get("role") or "assistant"
                role: Literal["user", "assistant"] = "user" if raw_role == "user" else "assistant"
                model_obj = m.get("model")
                if isinstance(model_obj, dict) and model_obj.get("id"):
                    model = str(model_obj["id"])
                elif isinstance(model_obj, str) and model_obj and model == "opencode":
                    model = model_obj

                blocks = _as_list(m.get("parts")) or _as_list(m.get("content"))
                segments: list[str] = []
                direct_text = m.get("text")
                if isinstance(direct_text, str) and direct_text:
                    segments.append(direct_text)
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        segments.append(str(block.get("text") or ""))
                    elif btype == "tool":
                        _append_text(role, "\n".join(s for s in segments if s))
                        segments = []
                        name = str(block.get("tool") or block.get("name") or "tool")
                        call_id = str(block.get("callID") or block.get("id") or uuid4().hex[:8])
                        state = _as_str_dict(block.get("state"))
                        _register_tool(name, call_id, state)
                _append_text(role, "\n".join(s for s in segments if s))
        else:
            for rec in records:
                rtype = rec.get("type")
                part = _as_str_dict(rec.get("part"))
                if rtype == "text":
                    _append_text("assistant", str(part.get("text") or ""))
                elif rtype == "tool_use":
                    name = str(part.get("tool") or "tool")
                    call_id = str(part.get("callID") or part.get("id") or uuid4().hex[:8])
                    state = _as_str_dict(part.get("state"))
                    _register_tool(name, call_id, state)

        if not messages and not tool_calls:
            return None

        if not title:
            first_user = next((m.content for m in messages if m.role == "user"), None)
            if first_user:
                line = first_user.strip().split("\n")[0]
                title = line[:65] + ("..." if len(line) > 65 else "")
        if not title:
            title = f"OpenCode Session {raw_sid[:8]}"

        if not working_dir:
            working_dir = _infer_working_dir_from_tool_calls(tool_calls)

        trajectory = Trajectory(
            session_id=session_id,
            messages=messages,
            tool_calls=tool_calls,
            tool_results=tool_results,
            reviews=reviews,
        )
        blocked_revs = [r for r in reviews if _is_blocked_review(r)]
        final_status: SessionStatus = "blocked" if blocked_revs else status
        ts_iso = datetime.fromtimestamp(mtime, UTC).isoformat()

        session = Session(
            session_id=session_id,
            title=title,
            run_id=session_id,
            project_name=title,
            task_id="opencode-session",
            agent_type="opencode",
            model=model,
            provider="opencode",
            status=final_status,
            working_dir=working_dir,
            trajectory=trajectory,
            decisions=reviews,
            total_steps=len(tool_calls),
            blocked_count=len(blocked_revs),
            blocked_turns_count=len(blocked_revs),
            created_at=ts_iso,
            updated_at=ts_iso,
        )
        active_store = store or get_watcher_store()
        active_store.record_session(session)
        cls._mtime_cache[path_str] = mtime
        cls._session_cache[path_str] = session
        logger.info(
            "Ingested OpenCode session '%s' (%d msgs, %d tools)",
            session.session_id,
            len(messages),
            len(tool_calls),
        )
        return session

    @classmethod
    def ingest_antigravity_transcript(
        cls,
        transcript_path: Path | str,
        max_tail_steps: int = 150,
        status: SessionStatus = "working",
        store: WatcherStore | None = None,
    ) -> Session | None:
        """Parse an Antigravity coding assistant transcript.jsonl into a canonical Watcher Session."""
        path = Path(transcript_path)
        if not path.exists():
            return None

        mtime = path.stat().st_mtime
        path_str = str(path.resolve())
        if (
            path_str in cls._mtime_cache
            and cls._mtime_cache[path_str] == mtime
            and path_str in cls._session_cache
        ):
            cached = cls._session_cache[path_str]
            active_store = store or get_watcher_store()
            if cached.status != status:
                cached.status = status
            # Cache hit must still populate store (e.g. after clear / process restart quirks).
            if active_store.get_session(cached.session_id) is None or cached.status != getattr(
                active_store.get_session(cached.session_id), "status", None
            ):
                active_store.record_session(cached)
            return cached

        lines = path.read_text(encoding="utf-8", errors="ignore").strip().splitlines()
        if not lines:
            return None

        # 1. First Pass: Scan all lines from index 0 to extract true initial prompt & workspace mutations
        first_user_prompt: str | None = None
        all_modified_files: set[str] = set()
        all_viewed_files: set[str] = set()
        total_commands_count = 0

        for line in lines:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            st = rec.get("type")
            cnt = rec.get("content") or ""
            if st == "USER_INPUT" and cnt:
                c_str = str(cnt).strip()
                if "<USER_REQUEST>" in c_str:
                    clean_req = c_str.split("<USER_REQUEST>")[1].split("</USER_REQUEST>")[0].strip()
                else:
                    clean_req = c_str
                if "<ADDITIONAL_METADATA>" in clean_req:
                    clean_req = clean_req.split("<ADDITIONAL_METADATA>")[0].strip()
                if (
                    clean_req
                    and not clean_req.startswith("{{ CHECKPOINT")
                    and not first_user_prompt
                ):
                    first_user_prompt = clean_req
            elif st == "CHECKPOINT" and not first_user_prompt and cnt:
                c_str = str(cnt)
                if "# User Requests" in c_str:
                    reqs = c_str.split("# User Requests")[1].split("#")[0].strip()
                    first_user_prompt = reqs
                elif "# USER Objective:" in c_str:
                    first_user_prompt = c_str.split("# USER Objective:")[1].split("\n\n")[0].strip()

            # Scan tool calls across all lines
            for tc in rec.get("tool_calls") or []:
                t_name = tc.get("name", "")
                t_args = tc.get("args") or {}
                if t_name in ("replace_file_content", "edit_file", "write_to_file", "write_file"):
                    tf = _clean_arg_path(t_args.get("TargetFile") or t_args.get("Path"))
                    fn = Path(tf).name
                    if fn:
                        all_modified_files.add(fn)
                elif t_name in ("view_file", "read_file"):
                    af = _clean_arg_path(t_args.get("AbsolutePath") or t_args.get("Path"))
                    fn = Path(af).name
                    if fn:
                        all_viewed_files.add(fn)
                elif t_name == "run_command":
                    total_commands_count += 1

        tail_lines = lines[-max_tail_steps:]
        if path.parent.name == "logs" and path.parent.parent.name == ".system_generated":
            conv_id = path.parent.parent.parent.name
        else:
            conv_id = ""
        if (
            not conv_id
            or conv_id == "/"
            or len(conv_id) < 8
            or conv_id.startswith("openeval_ingest_")
        ):
            match = re.search(r"([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4,12})*)", path.name)
            conv_id = match.group(1) if match else path.stem.removeprefix("antigravity-")
        session_id = f"antigravity-{conv_id[:8]}"

        if not cls._ag_titles:
            cls._ag_titles = get_antigravity_titles()

        from server.policy_gateway import PolicyGateway

        # Historical transcript ingest must stay fast/offline: rules + thresholds only.
        # Live hooks (/api/v1/watcher/evaluate) still use LLM when enabled.
        gw = PolicyGateway.from_store(use_llm=False)

        messages: list[Message] = []
        tool_calls: list[ToolCall] = []
        tool_results: list[ToolResult] = []
        reviews: list[ReviewRecord] = []
        latest_activity = "Pair programming in workspace"
        detected_working_dir: str | None = None

        # Prepend initial user turn if not in tail_lines
        if first_user_prompt:
            messages.append(Message(role="user", content=first_user_prompt))

        for line in tail_lines:
            try:
                record = json.loads(line)
            except Exception:
                continue

            content = record.get("content") or ""
            thinking = record.get("thinking")
            step_type = record.get("type")
            step_tool_calls: list[ToolCall] = []
            raw_tcs = record.get("tool_calls") or []
            for tc in raw_tcs:
                name = tc.get("name", "unknown")
                args = tc.get("args", {})
                tool_input = ""
                diff = None

                if name == "run_command":
                    tool_input = _clean_arg_path(args.get("CommandLine", ""))
                    latest_activity = f"Execute: {tool_input[:60]}"
                    cwd = _clean_arg_path(args.get("Cwd"))
                    if cwd and not detected_working_dir:
                        detected_working_dir = cwd
                elif name in ("replace_file_content", "edit_file"):
                    target_file = _clean_arg_path(args.get("TargetFile") or args.get("Path"))
                    tool_input = f"Edit {Path(target_file).name}"
                    tc_target = str(args.get("TargetContent", ""))[:80]
                    tc_repl = str(args.get("ReplacementContent", ""))[:80]
                    diff = f"- {tc_target}\n+ {tc_repl}"
                    latest_activity = f"Edit: {Path(target_file).name}"
                elif name in ("write_to_file", "write_file"):
                    target_file = _clean_arg_path(args.get("TargetFile") or args.get("Path"))
                    tool_input = f"Write {Path(target_file).name}"
                    code_len = len(str(args.get("CodeContent", "")))
                    diff = f"+ (new file {code_len} bytes)"
                    latest_activity = f"Write: {Path(target_file).name}"
                elif name in ("view_file", "read_file"):
                    target_file = _clean_arg_path(args.get("AbsolutePath") or args.get("Path"))
                    tool_input = f"Read {Path(target_file).name}"
                    latest_activity = f"Read: {Path(target_file).name}"
                elif name == "list_dir":
                    tool_input = _clean_arg_path(args.get("DirectoryPath") or args.get("Path"))
                    latest_activity = f"List: {Path(tool_input).name or tool_input}"
                elif name == "grep_search":
                    search_path = _clean_arg_path(args.get("SearchPath") or args.get("Path"))
                    query = str(args.get("Query") or "")
                    tool_input = (
                        f"Search {query} in {search_path}" if search_path else f"Search {query}"
                    )
                    latest_activity = f"Search: {query or Path(search_path).name}"
                elif name == "find_by_name":
                    search_dir = _clean_arg_path(args.get("SearchDirectory") or args.get("Path"))
                    pattern = str(args.get("Pattern") or args.get("Query") or "")
                    tool_input = (
                        f"Find {pattern} in {search_dir}" if search_dir else f"Find {pattern}"
                    )
                    latest_activity = f"Find: {pattern or Path(search_dir).name}"
                else:
                    tool_input = str(json.dumps(args))
                    latest_activity = f"Tool: {name}"

                tc_id = f"call_{len(tool_calls) + 1}"
                tc_obj = ToolCall(
                    tool_id=tc_id,
                    tool_name=name,
                    arguments=dict(args) if isinstance(args, dict) else {},
                    raw_input=tool_input,
                )
                tool_calls.append(tc_obj)
                step_tool_calls.append(tc_obj)

                # Evaluate tool call against Watcher Policy Gateway (deterministic 63 rules + triage)
                rec = gw.evaluate_tool_call(
                    session_id=session_id,
                    tool_name=name,
                    tool_input=tool_input,
                    user_intent="Autonomous software engineering with pair programmer",
                )
                if diff:
                    rec.diff = diff
                reviews.append(rec)

            if step_type == "USER_INPUT":
                c_str = str(content)
                messages.append(Message(role="user", content=c_str))
            elif step_type == "PLANNER_RESPONSE":
                messages.append(
                    Message(
                        role="assistant",
                        content=str(content),
                        thinking=thinking if isinstance(thinking, str) else None,
                        tool_calls=step_tool_calls,
                    )
                )
            elif step_type == "GENERIC" and tool_calls and len(tool_results) < len(tool_calls):
                matched_tc = tool_calls[len(tool_results)]
                tool_exec_status = str(record.get("status", "DONE"))
                tr_obj = ToolResult(
                    tool_id=matched_tc.tool_id,
                    tool_name=matched_tc.tool_name,
                    stdout=str(content),
                    exit_code=0 if tool_exec_status == "DONE" else 1,
                    is_error=tool_exec_status != "DONE",
                )
                tool_results.append(tr_obj)
                if messages and messages[-1].role == "assistant":
                    messages[-1].tool_results.append(tr_obj)

        trajectory = Trajectory(
            session_id=session_id,
            messages=messages,
            tool_calls=tool_calls,
            tool_results=tool_results,
            reviews=reviews,
        )

        mtime = path.stat().st_mtime
        created_time = datetime.fromtimestamp(mtime, UTC).isoformat()

        # Derive meaningful human title
        title = cls._ag_titles.get(conv_id)
        if not title and first_user_prompt:
            clean_first = first_user_prompt.split("\n")[0].strip()
            title = clean_first[:65] + ("..." if len(clean_first) > 65 else "")
        if not title:
            title = f"Antigravity Session {conv_id[:8]}"

        if detected_working_dir:
            detected_working_dir = _clean_arg_path(detected_working_dir) or None

        # Formulate rich multi-paragraph flight narrative summary
        files_mod_list = sorted(all_modified_files)
        files_view_list = sorted(all_viewed_files)

        obj_summary = (
            first_user_prompt or "Autonomous software engineering and pair programming session."
        )
        if len(obj_summary) > 350:
            obj_summary = obj_summary[:340].rsplit(" ", 1)[0] + "..."

        work_parts = []
        if files_mod_list:
            mod_sample = ", ".join(files_mod_list[:6])
            if len(files_mod_list) > 6:
                mod_sample += f" (+{len(files_mod_list) - 6} more)"
            work_parts.append(f"modified {len(files_mod_list)} file(s): {mod_sample}")
        if files_view_list:
            work_parts.append(f"inspected {len(files_view_list)} source file(s)")
        if total_commands_count > 0:
            work_parts.append(f"executed {total_commands_count} shell command(s)")

        work_details = (
            "; ".join(work_parts) if work_parts else f"performed {len(tool_calls)} tool operations"
        )
        accomplished_narrative = (
            f"During this session, the agent performed continuous codebase analysis and software development in the workspace. "
            f"It {work_details} across {len(messages)} conversation turns."
        )

        blocked_revs = [r for r in reviews if _is_blocked_review(r)]
        if blocked_revs:
            final_status: SessionStatus = "blocked"
        elif status in ("working", "active", "running"):
            age_sec = time.time() - mtime
            final_status = "working" if age_sec <= 120 else "closed"
        else:
            final_status = status

        if blocked_revs:
            flagged_names = ", ".join([r.rule_name or r.tool_name for r in blocked_revs[:3]])
            verdict_narrative = (
                f"Watcher Aegis Sentinel intercepted and contained {len(blocked_revs)} critical policy violation(s) "
                f"(flagged: {flagged_names}). High-risk actions were prevented from altering protected resources."
            )
        else:
            verdict_narrative = (
                f"Watcher Aegis Sentinel audited all {len(tool_calls)} tool invocations against active deterministic security policies and threshold rules. "
                f"Zero safety violations or unapproved destructive operations were detected. Monitored session status: '{final_status}'."
            )

        full_narrative_summary = (
            f"Task: {obj_summary}\n\n"
            f"Accomplished: {accomplished_narrative}\n\n"
            f"Verdict: {verdict_narrative}"
        )

        session = Session(
            session_id=session_id,
            title=title,
            run_id=session_id,
            project_name=title,
            task_id=title,
            agent_type="antigravity",
            model="gemini-3.8-flash",
            provider="google",
            status=final_status,
            working_dir=(
                detected_working_dir or _infer_working_dir_from_tool_calls(tool_calls) or None
            ),
            current_activity=latest_activity,
            trajectory=trajectory,
            decisions=reviews,
            blocked_count=len(blocked_revs),
            blocked_turns_count=len(blocked_revs),
            total_tokens=len(tool_calls) * 120,
            final_summary=full_narrative_summary,
            created_at=created_time,
            updated_at=created_time,
        )

        active_store = store or get_watcher_store()
        active_store.record_session(session)
        cls._mtime_cache[path_str] = mtime
        cls._session_cache[path_str] = session
        logger.info(
            "Ingested Antigravity session '%s' [%s] (%s) with %d tool calls and %d reviews",
            session_id,
            title,
            status,
            len(tool_calls),
            len(reviews),
        )
        return session

    @classmethod
    def scan_default_agent_directories(cls) -> list[Session]:
        """Scan standard local directories for Antigravity, Claude Code, and Cursor logs."""
        if os.getenv("OPENEVAL_AUTO_SCAN_LOCAL_LOGS", "0").lower() not in ("1", "true", "yes"):
            return []

        ingested: list[Session] = []
        cls._ag_titles = get_antigravity_titles()
        now = time.time()

        # Check Antigravity brain directory
        antigravity_brain = Path.home() / ".gemini" / "antigravity" / "brain"
        if antigravity_brain.exists():
            conv_dirs = [
                d
                for d in antigravity_brain.iterdir()
                if d.is_dir() and (d / ".system_generated" / "logs" / "transcript.jsonl").exists()
            ]
            conv_dirs.sort(
                key=lambda d: (
                    (d / ".system_generated" / "logs" / "transcript.jsonl").stat().st_mtime
                ),
                reverse=True,
            )
            for d in conv_dirs:
                t_path = d / ".system_generated" / "logs" / "transcript.jsonl"
                try:
                    mtime = t_path.stat().st_mtime
                    age_sec = now - mtime
                    is_active = age_sec <= 120
                    session_status: SessionStatus = "working" if is_active else "completed"
                except Exception:
                    session_status = "completed"
                s = cls.ingest_antigravity_transcript(t_path, status=session_status)
                if s:
                    ingested.append(s)

        # Claude Code: legacy ~/.claude/sessions + real ~/.claude/projects/*/*.jsonl
        claude_files: list[Path] = []
        legacy_claude = Path.home() / ".claude" / "sessions"
        if legacy_claude.exists():
            claude_files.extend(sorted(legacy_claude.glob("*.jsonl")))
        projects_claude = Path.home() / ".claude" / "projects"
        if projects_claude.exists():
            claude_files.extend(sorted(projects_claude.glob("*/*.jsonl")))
        # Prefer newest; cap to keep Sessions list snappy
        claude_files = sorted(
            {p.resolve(): p for p in claude_files}.values(),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:40]
        for jsonl_file in claude_files:
            s = cls.ingest_claude_code_jsonl(jsonl_file)
            if s:
                ingested.append(s)

        # Cursor agent transcripts (skip subagent side-chats by default)
        cursor_root = Path.home() / ".cursor" / "projects"
        cursor_files: list[Path] = []
        if cursor_root.exists():
            for p in cursor_root.glob("*/agent-transcripts/*/*.jsonl"):
                if "subagents" in p.parts:
                    continue
                cursor_files.append(p)
        cursor_files = sorted(cursor_files, key=lambda p: p.stat().st_mtime, reverse=True)[:40]
        for jsonl_file in cursor_files:
            try:
                age_sec = now - jsonl_file.stat().st_mtime
                is_active = age_sec <= 120
                st: SessionStatus = "working" if is_active else "completed"
            except Exception:
                st = "completed"
            s = cls.ingest_cursor_agent_transcript_jsonl(jsonl_file, status=st)
            if s:
                ingested.append(s)

        return ingested


def mirror_eval_run_to_watcher_store(
    run_record: dict[str, Any] | Session | Any,
    store: WatcherStore | None = None,
) -> str | None:
    """Bridge an evaluation run or benchmark record into WatcherStore with full trajectory & gate decisions."""
    if run_record is None:
        return None

    try:
        from server.policy_gateway import PolicyGateway

        active_store = store or get_watcher_store()

        # Extract record dict or attributes
        if isinstance(run_record, dict):
            raw_dict = run_record
        elif hasattr(run_record, "model_dump"):
            raw_dict = run_record.model_dump()
        elif hasattr(run_record, "__dict__"):
            raw_dict = dict(run_record.__dict__)
        else:
            raw_dict = {}

        raw_run_id = str(
            raw_dict.get("run_id")
            or getattr(run_record, "run_id", None)
            or raw_dict.get("id")
            or getattr(run_record, "id", None)
            or getattr(run_record, "session_id", None)
            or uuid4().hex[:8]
        )
        # Ensure session_id matches f"eval-{run_id}" requirement
        session_id = raw_run_id if raw_run_id.startswith("eval-") else f"eval-{raw_run_id}"

        task_name = str(
            raw_dict.get("task")
            or raw_dict.get("task_id")
            or getattr(run_record, "task_id", None)
            or raw_dict.get("project_name")
            or getattr(run_record, "project_name", None)
            or "eval_run"
        )
        model_name = str(
            raw_dict.get("model") or getattr(run_record, "model", None) or "gemini-3.5-flash-lite"
        )
        agent_type = str(
            raw_dict.get("agent_type") or getattr(run_record, "agent_type", None) or "inspect_eval"
        )
        status: SessionStatus = raw_dict.get("status") or getattr(run_record, "status", "completed")
        if status not in (
            "active",
            "completed",
            "failed",
            "error",
            "blocked",
            "running",
            "cancelled",
            "pending",
        ):
            status = "completed"

        # Trajectory extraction
        traj_data = raw_dict.get("trajectory") or getattr(run_record, "trajectory", None)
        raw_steps: list[Any] = []
        if isinstance(traj_data, dict) and "steps" in traj_data:
            raw_steps = traj_data.get("steps") or []
        elif getattr(traj_data, "steps", None):
            raw_steps = getattr(traj_data, "steps", None) or []
        elif "steps" in raw_dict and raw_dict.get("steps"):
            raw_steps = raw_dict.get("steps") or []
        elif getattr(run_record, "steps", None):
            raw_steps = getattr(run_record, "steps", None) or []

        tcs: list[ToolCall] = []
        trs: list[ToolResult] = []
        msgs: list[Message] = []

        # Check existing tool calls in trajectory
        existing_tcs: list[Any] = []
        if isinstance(traj_data, dict):
            existing_tcs = traj_data.get("tool_calls") or []
        elif getattr(traj_data, "tool_calls", None):
            existing_tcs = getattr(traj_data, "tool_calls", None) or []

        if existing_tcs:
            for tc in existing_tcs:
                if isinstance(tc, ToolCall):
                    tcs.append(tc)
                elif isinstance(tc, dict):
                    tcs.append(
                        ToolCall(
                            tool_id=str(tc.get("tool_id") or tc.get("id") or uuid4())[:8],
                            tool_name=str(tc.get("tool_name") or tc.get("name") or "tool"),
                            arguments=tc.get("arguments") or {},
                            raw_input=str(
                                tc.get("raw_input") or json.dumps(tc.get("arguments", {}))
                            ),
                        )
                    )

        # Also extract from steps if tcs still empty or if steps provided
        if raw_steps and not tcs:
            for step in raw_steps:
                s_dict = (
                    step
                    if isinstance(step, dict)
                    else (step.model_dump() if hasattr(step, "model_dump") else {})
                )
                action = s_dict.get("action") or {}
                if isinstance(action, dict):
                    t_name = str(
                        action.get("tool") or action.get("tool_name") or action.get("name") or ""
                    )
                    cmd = action.get("command") or action.get("cmd")
                    path = action.get("path")
                    content = action.get("content") or action.get("file_text")
                    t_args = action.get("arguments") or action.get("args") or {}
                    if not isinstance(t_args, dict):
                        t_args = {"raw": str(t_args)}
                    if cmd and "command" not in t_args and "cmd" not in t_args:
                        t_args["command"] = cmd
                    if path and "path" not in t_args:
                        t_args["path"] = path
                    if content and "content" not in t_args:
                        t_args["content"] = content
                    t_raw = str(cmd or path or action.get("input") or json.dumps(t_args))
                else:
                    t_name = str(s_dict.get("tool_name") or "tool")
                    t_args = {"raw": str(action)}
                    t_raw = str(action)

                if t_name:
                    tc_id = str(s_dict.get("step_id") or s_dict.get("id") or uuid4())[:8]
                    tc_obj = ToolCall(
                        tool_id=tc_id,
                        tool_name=t_name,
                        arguments=cast(dict[str, object], t_args),
                        raw_input=t_raw,
                    )
                    tcs.append(tc_obj)

                    obs = str(
                        s_dict.get("observation")
                        or s_dict.get("stdout")
                        or s_dict.get("output")
                        or ""
                    )
                    tr_obj = ToolResult(
                        tool_id=tc_id,
                        tool_name=t_name,
                        stdout=obs,
                        is_error=bool(s_dict.get("is_error", False)),
                    )
                    trs.append(tr_obj)

                    msgs.append(
                        Message(
                            role="assistant",
                            content=s_dict.get("thought") or f"Executed tool: {t_name}",
                            tool_calls=[tc_obj],
                            tool_results=[tr_obj],
                        )
                    )

        # Check existing tool results in trajectory
        existing_trs: list[Any] = []
        if isinstance(traj_data, dict):
            existing_trs = traj_data.get("tool_results") or []
        elif getattr(traj_data, "tool_results", None):
            existing_trs = getattr(traj_data, "tool_results", None) or []
        if existing_trs and not trs:
            for tr in existing_trs:
                if isinstance(tr, ToolResult):
                    trs.append(tr)
                elif isinstance(tr, dict):
                    trs.append(
                        ToolResult(
                            tool_id=str(tr.get("tool_id") or tr.get("id") or uuid4())[:8],
                            tool_name=str(tr.get("tool_name") or tr.get("name") or "tool"),
                            stdout=str(tr.get("stdout") or tr.get("output") or ""),
                            stderr=str(tr.get("stderr") or ""),
                            exit_code=int(tr.get("exit_code") or 0),
                            is_error=bool(tr.get("is_error", False)),
                        )
                    )

        # Check existing messages in trajectory or run_record
        existing_msgs: list[Any] = []
        if isinstance(traj_data, dict):
            existing_msgs = traj_data.get("messages") or []
        elif getattr(traj_data, "messages", None):
            existing_msgs = getattr(traj_data, "messages", None) or []
        elif "messages" in raw_dict and raw_dict.get("messages"):
            existing_msgs = raw_dict.get("messages") or []
        elif getattr(run_record, "messages", None):
            existing_msgs = getattr(run_record, "messages", None) or []

        if existing_msgs and not msgs:
            for m in existing_msgs:
                if isinstance(m, Message):
                    msgs.append(m)
                elif isinstance(m, dict):
                    msgs.append(
                        Message(
                            role=m.get("role") or "assistant",
                            content=str(m.get("content") or ""),
                            thinking=m.get("thinking") or m.get("reasoning"),
                        )
                    )

        if not msgs and tcs:
            msgs.append(Message(role="user", content=f"Execute eval run: {task_name}"))
            msgs.append(
                Message(
                    role="assistant",
                    content=f"Executed {len(tcs)} tool calls",
                    tool_calls=tcs,
                    tool_results=trs,
                )
            )

        # Policy Gateway evaluation for extracted tool calls
        gw = PolicyGateway.from_store(use_llm=False)
        reviews: list[ReviewRecord] = [
            gw.evaluate_tool_call(
                session_id=session_id,
                tool_name=tc.tool_name,
                tool_input=tc.raw_input or (json.dumps(tc.arguments) if tc.arguments else ""),
                user_intent=task_name,
            )
            for tc in tcs
        ]

        blocked_revs = [r for r in reviews if _is_blocked_review(r)]
        blocked_count = len(blocked_revs)
        final_status: SessionStatus = (
            "blocked" if blocked_count > 0 and status != "error" else status
        )

        trajectory = Trajectory(
            session_id=session_id,
            messages=msgs,
            tool_calls=tcs,
            tool_results=trs,
            reviews=reviews,
        )

        session = Session(
            session_id=session_id,
            run_id=session_id,
            title=raw_dict.get("title") or f"Eval Run {session_id[:12]} ({task_name})",
            project_name=task_name,
            task_id=task_name,
            agent_type=agent_type,  # type: ignore[arg-type]
            model=model_name,
            provider="inspect_eval" if agent_type == "inspect_eval" else "openeval_studio",
            status=final_status,
            working_dir=raw_dict.get("working_dir") or "/workspace",
            trajectory=trajectory,
            decisions=reviews,
            steps=raw_steps if isinstance(raw_steps, list) else [],
            total_steps=len(raw_steps) if isinstance(raw_steps, list) else len(tcs),
            blocked_count=blocked_count,
            blocked_turns_count=blocked_count,
            passed=raw_dict.get("passed"),
            reward=raw_dict.get("reward"),
            failure_reason=raw_dict.get("failure_reason"),
            final_summary=raw_dict.get("final_summary"),
            judge_reasoning=raw_dict.get("judge_reasoning"),
            verifier_output=raw_dict.get("verifier_output"),
            audit_verdicts=raw_dict.get("audit_verdicts") or [],
            total_tokens=int(raw_dict.get("total_tokens") or 0),
            total_duration_sec=float(
                raw_dict.get("total_duration_sec") or raw_dict.get("duration_sec") or 0.0
            ),
            estimated_cost_usd=float(
                raw_dict.get("estimated_cost_usd") or raw_dict.get("cost_usd") or 0.0
            ),
            created_at=raw_dict.get("created_at") or datetime.now(UTC).isoformat(),
            updated_at=datetime.now(UTC).isoformat(),
        )

        active_store.record_session(session)
        logger.info(
            "Mirrored eval run '%s' to WatcherStore (status=%s, blocked=%d, tool_calls=%d)",
            session.session_id,
            session.status,
            blocked_count,
            len(tcs),
        )
        return session.session_id

    except Exception as exc:
        logger.warning("Failed mirroring eval run to WatcherStore: %s", exc)
        return None
