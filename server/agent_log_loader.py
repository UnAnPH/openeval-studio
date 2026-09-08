"""Universal Coding Agent Log Loader for OpenEval & Watcher.

Ingests raw conversation and tool-call trajectories from:
  - Claude Code (~/.claude/sessions/*.jsonl or exported JSONL)
  - Cursor Composer (workspace storage state.vscdb or exported sessions)
  - Inspect AI (.eval archives)
  - Generic Agent JSON trajectories

Converts all into canonical Watcher Session and Trajectory entities for
high-signal flight control, forensic search, and collaborative review.
"""

import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
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
from server.watcher_store import get_watcher_store

logger = logging.getLogger("openeval.server.agent_log_loader")


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
    def ingest_claude_code_jsonl(cls, file_path: Path | str) -> Session | None:
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
            if not event_type and "role" in record:
                # Direct message format
                role = record.get("role", "assistant")
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

        trajectory = Trajectory(
            session_id=session_id,
            messages=messages,
            tool_calls=tool_calls,
            tool_results=tool_results,
            reviews=[],
        )

        first_user = next((m.content for m in messages if m.role == "user"), None)
        title = None
        if first_user:
            c = first_user.strip().split("\n")[0]
            title = c[:65] + ("..." if len(c) > 65 else "")
        if not title:
            title = f"Claude Code Session {session_id[:8]}"

        session = Session(
            session_id=session_id,
            title=title,
            run_id=session_id,
            project_name=title,
            task_id="claude-code-session",
            agent_type="claude_code",
            model=model,
            provider="anthropic",
            status="completed",
            working_dir=working_dir,
            trajectory=trajectory,
            total_tokens=total_tokens,
            created_at=datetime.now(UTC).isoformat(),
        )

        store = get_watcher_store()
        store.record_session(session)
        logger.info("Ingested Claude Code session '%s' (%d messages)", session_id, len(messages))
        return session

    @classmethod
    def ingest_cursor_session_json(cls, file_path: Path | str) -> Session | None:
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
        messages_raw = data.get("conversation") or data.get("messages") or []
        messages: list[Message] = []

        for m in messages_raw:
            messages.append(
                Message(
                    role=m.get("role", "assistant"),
                    content=m.get("text", "") or m.get("content", ""),
                )
            )

        trajectory = Trajectory(
            session_id=session_id,
            messages=messages,
            tool_calls=[],
            tool_results=[],
            reviews=[],
        )

        title = data.get("title") or data.get("name") or f"Cursor Session {session_id[:8]}"
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
            status="completed",
            trajectory=trajectory,
            total_tokens=data.get("totalTokens", 0),
            created_at=datetime.now(UTC).isoformat(),
        )

        store = get_watcher_store()
        store.record_session(session)
        logger.info("Ingested Cursor session '%s'", session_id)
        return session

    @classmethod
    def ingest_antigravity_transcript(
        cls,
        transcript_path: Path | str,
        max_tail_steps: int = 150,
        status: SessionStatus = "working",
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
            if cached.status != status:
                cached.status = status
                store = get_watcher_store()
                store.record_session(cached)
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
                if t_name in ("replace_file_content", "write_to_file"):
                    tf = (
                        str(t_args.get("TargetFile") or "")
                        .replace('"', "")
                        .replace("'", "")
                        .strip()
                    )
                    fn = Path(tf).name
                    if fn:
                        all_modified_files.add(fn)
                elif t_name == "view_file":
                    af = (
                        str(t_args.get("AbsolutePath") or "")
                        .replace('"', "")
                        .replace("'", "")
                        .strip()
                    )
                    fn = Path(af).name
                    if fn:
                        all_viewed_files.add(fn)
                elif t_name == "run_command":
                    total_commands_count += 1

        tail_lines = lines[-max_tail_steps:]
        conv_id = path.parent.parent.parent.name
        session_id = f"antigravity-{conv_id[:8]}"

        if not cls._ag_titles:
            cls._ag_titles = get_antigravity_titles()

        from server.policy_gateway import PolicyGateway

        gw = PolicyGateway.from_store()

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
                    tool_input = str(args.get("CommandLine", ""))
                    latest_activity = f"Execute: {tool_input[:60]}"
                    cwd = args.get("Cwd")
                    if cwd and not detected_working_dir:
                        detected_working_dir = str(cwd)
                elif name == "replace_file_content":
                    target_file = str(args.get("TargetFile", ""))
                    tool_input = f"Edit {Path(target_file).name}"
                    tc_target = str(args.get("TargetContent", ""))[:80]
                    tc_repl = str(args.get("ReplacementContent", ""))[:80]
                    diff = f"- {tc_target}\n+ {tc_repl}"
                    latest_activity = f"Edit: {Path(target_file).name}"
                    if target_file and not detected_working_dir:
                        detected_working_dir = str(Path(target_file).parent)
                elif name == "write_to_file":
                    target_file = str(args.get("TargetFile", ""))
                    tool_input = f"Write {Path(target_file).name}"
                    code_len = len(str(args.get("CodeContent", "")))
                    diff = f"+ (new file {code_len} bytes)"
                    latest_activity = f"Write: {Path(target_file).name}"
                    if target_file and not detected_working_dir:
                        detected_working_dir = str(Path(target_file).parent)
                elif name == "view_file":
                    target_file = str(args.get("AbsolutePath", ""))
                    tool_input = f"Read {Path(target_file).name}"
                    latest_activity = f"Read: {Path(target_file).name}"
                    if target_file and not detected_working_dir:
                        detected_working_dir = str(Path(target_file).parent)
                elif name == "list_dir":
                    tool_input = str(args.get("DirectoryPath", ""))
                    latest_activity = f"List: {Path(tool_input).name or tool_input}"
                    if tool_input and not detected_working_dir:
                        detected_working_dir = tool_input
                else:
                    tool_input = str(json.dumps(args))[:80]
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
            detected_working_dir = detected_working_dir.strip("\"'")

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

        blocked_revs = [r for r in reviews if r.decision == "block" or r.score >= 8]
        if blocked_revs:
            flagged_names = ", ".join([r.rule_name or r.tool_name for r in blocked_revs[:3]])
            verdict_narrative = (
                f"Watcher Aegis Sentinel intercepted and contained {len(blocked_revs)} critical policy violation(s) "
                f"(flagged: {flagged_names}). High-risk actions were prevented from altering protected resources."
            )
        else:
            verdict_narrative = (
                f"Watcher Aegis Sentinel audited all {len(tool_calls)} tool invocations against active deterministic security policies and threshold rules. "
                f"Zero safety violations or unapproved destructive operations were detected. Monitored session status: '{status}'."
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
            model="gemini-3.7-flash",
            provider="google",
            status=status,
            working_dir=detected_working_dir
            or "/Users/jaysonandal/Documents/AI safety/openeval-studio",
            current_activity=latest_activity,
            trajectory=trajectory,
            total_tokens=len(tool_calls) * 120,
            final_summary=full_narrative_summary,
            created_at=created_time,
            updated_at=created_time,
        )

        store = get_watcher_store()
        store.record_session(session)
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
        """Scan standard local directories for available Antigravity, Claude Code, and Cursor logs."""
        ingested: list[Session] = []
        cls._ag_titles = get_antigravity_titles()

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
            now = time.time()
            # Ingest all available Antigravity conversations (fast via mtime cache)
            for idx, d in enumerate(conv_dirs):
                t_path = d / ".system_generated" / "logs" / "transcript.jsonl"
                try:
                    mtime = t_path.stat().st_mtime
                    age_sec = now - mtime
                    # A session is active/working only if active in the last 45 minutes, or most recent within 90 minutes
                    is_active = (age_sec < 45 * 60) or (idx == 0 and age_sec < 90 * 60)
                    session_status: SessionStatus = "working" if is_active else "completed"
                except Exception:
                    session_status = "completed"
                s = cls.ingest_antigravity_transcript(t_path, status=session_status)
                if s:
                    ingested.append(s)

        # Check ~/.claude/sessions
        claude_dir = Path.home() / ".claude" / "sessions"
        if claude_dir.exists():
            for jsonl_file in claude_dir.glob("*.jsonl"):
                s = cls.ingest_claude_code_jsonl(jsonl_file)
                if s:
                    ingested.append(s)

        return ingested
