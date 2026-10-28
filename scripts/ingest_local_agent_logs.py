#!/usr/bin/env python3
"""CLI utility to scan and ingest local coding agent transcripts into PostgreSQL.

Discovers transcripts from:
  - Antigravity (~/.gemini/antigravity/brain/*/.system_generated/logs/transcript.jsonl)
  - Cursor (~/.cursor/projects/*/agent-transcripts/*/*.jsonl)
  - Claude Code (~/.claude/projects/*/*.jsonl and ~/.claude/sessions/*.jsonl)

Associates all ingested sessions with the designated OpenEval Studio user
so they immediately appear in the Sessions, Control, and Analyzer surfaces.

Usage:
  uv run python scripts/ingest_local_agent_logs.py --user jayson
  uv run python scripts/ingest_local_agent_logs.py --user jayson --agent antigravity --limit 20
  uv run python scripts/ingest_local_agent_logs.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

load_dotenv()

from server.agent_log_loader import UniversalAgentLogLoader, get_antigravity_titles  # noqa: E402
from server.db import (  # noqa: E402
    SessionLocal,
    current_user_id,
    current_user_slug,
    get_user_id_by_slug,
)
from server.watcher_store import get_watcher_store  # noqa: E402


def resolve_target_user(username: str | None) -> tuple[int, str]:
    """Resolve user ID and username from database or defaults."""
    with SessionLocal() as db:
        if username:
            slug = username.strip().lower()
            uid = get_user_id_by_slug(db, slug)
            return uid, slug

        # Look for most recently active non-system user (excluding 'owner', 'demo')
        from sqlalchemy import text

        row = db.execute(
            text(
                "SELECT id, slug FROM users WHERE slug NOT IN ('demo', 'owner') ORDER BY id DESC LIMIT 1"
            )
        ).fetchone()
        if row:
            return int(row[0]), str(row[1])

        # Fallback to 'owner'
        owner_id = get_user_id_by_slug(db, "owner")
        return owner_id, "owner"


def discover_antigravity_transcripts() -> list[Path]:
    """Find all Antigravity session transcript files."""
    brain_dir = Path.home() / ".gemini" / "antigravity" / "brain"
    if not brain_dir.exists():
        return []

    transcripts: list[Path] = []
    for d in brain_dir.iterdir():
        if not d.is_dir():
            continue
        t_path = d / ".system_generated" / "logs" / "transcript.jsonl"
        if t_path.exists() and t_path.stat().st_size > 50:
            transcripts.append(t_path)

    # Sort newest first
    transcripts.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return transcripts


def discover_cursor_transcripts() -> list[Path]:
    """Find all Cursor agent transcript files."""
    cursor_root = Path.home() / ".cursor" / "projects"
    if not cursor_root.exists():
        return []

    transcripts: list[Path] = []
    for p in cursor_root.glob("*/agent-transcripts/*/*.jsonl"):
        if "subagents" in p.parts:
            continue
        if p.exists() and p.stat().st_size > 50:
            transcripts.append(p)

    transcripts.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return transcripts


def discover_claude_transcripts() -> list[Path]:
    """Find all Claude Code transcript files."""
    claude_root = Path.home() / ".claude"
    if not claude_root.exists():
        return []

    transcripts: list[Path] = []
    projects = claude_root / "projects"
    if projects.exists():
        for p in projects.glob("*/*.jsonl"):
            if p.stat().st_size > 50:
                transcripts.append(p)

    sessions = claude_root / "sessions"
    if sessions.exists():
        for p in sessions.glob("*.jsonl"):
            if p.stat().st_size > 50:
                transcripts.append(p)

    # Deduplicate and sort newest first
    unique = {p.resolve(): p for p in transcripts}.values()
    return sorted(unique, key=lambda p: p.stat().st_mtime, reverse=True)


def ingest_via_remote_api(
    server_url: str,
    api_key: str | None,
    agent: str,
    limit: int,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Upload discovered transcripts to remote OpenEval Studio via HTTP API."""
    base_url = server_url.rstrip("/")
    for suffix in ("/api/v1/watcher/evaluate", "/api/v1/watcher", "/api/v1"):
        if base_url.endswith(suffix):
            base_url = base_url[:-len(suffix)]
            break

    ingest_endpoint = f"{base_url}/api/v1/agent-logs/ingest"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-OpenEval-Key"] = api_key

    print("=" * 64)
    print(" 🚀  OpenEval Studio — Remote Agent Log Ingest Utility")
    print("=" * 64)
    print(f" Target Studio:   {base_url}")
    print(f" Ingest Endpoint: {ingest_endpoint}")
    print(f" Auth Mode:       {'X-OpenEval-Key configured' if api_key else 'Session / Public'}")
    print(f" Mode:            {'DRY-RUN (No writes)' if dry_run else 'REMOTE HTTP UPLOAD'}")
    print("=" * 64)

    # Check connectivity
    try:
        health_req = urllib.request.Request(f"{base_url}/api/health", headers=headers)
        with urllib.request.urlopen(health_req, timeout=8) as resp:
            pass
    except Exception as e:
        print(f"⚠️ Warning: Connection test to {base_url}/api/health returned: {e}")

    total_ingested = 0
    total_failed = 0

    files_to_ingest: list[tuple[str, Path]] = []
    if agent in ("all", "antigravity"):
        ag_files = discover_antigravity_transcripts()
        count = len(ag_files) if limit <= 0 else min(len(ag_files), limit)
        for p in ag_files[:count]:
            files_to_ingest.append(("antigravity", p))
    if agent in ("all", "cursor"):
        cur_files = discover_cursor_transcripts()
        count = len(cur_files) if limit <= 0 else min(len(cur_files), limit)
        for p in cur_files[:count]:
            files_to_ingest.append(("cursor", p))
    if agent in ("all", "claude_code"):
        claude_files = discover_claude_transcripts()
        count = len(claude_files) if limit <= 0 else min(len(claude_files), limit)
        for p in claude_files[:count]:
            files_to_ingest.append(("claude_code", p))

    print(f"\nDiscovered {len(files_to_ingest)} local transcript(s) to upload.\n")

    for idx, (agent_type, p) in enumerate(files_to_ingest, 1):
        if dry_run:
            print(f"  [{idx}/{len(files_to_ingest)}] [DRY-RUN] [{agent_type}] {p.name}")
            continue

        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            payload = json.dumps({
                "filename": p.name,
                "file_content": content,
                "agent_type": agent_type,
            }).encode("utf-8")

            req = urllib.request.Request(ingest_endpoint, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
                sess = data.get("session", {})
                sid = sess.get("session_id", p.name[:16])
                title = sess.get("title") or sid
                total_ingested += 1
                if verbose or idx <= 5 or idx == len(files_to_ingest) or idx % 10 == 0:
                    print(f"  [{idx}/{len(files_to_ingest)}] ✅ Ingested [{agent_type}] {sid} | {title[:45]}")
        except Exception as e:
            total_failed += 1
            print(f"  [{idx}/{len(files_to_ingest)}] ❌ Failed [{agent_type}] {p.name}: {e}")

    print("\n" + "=" * 64)
    print(" 🎉  Remote Ingestion Complete!")
    print("=" * 64)
    print(f" Total Sessions Uploaded: {total_ingested}")
    print(f" Total Upload Errors:     {total_failed}")
    print(f" Target Studio:           {base_url}")
    print("=" * 64)
    print(f" Open {base_url}/#sessions to inspect your uploaded sessions!\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest local Antigravity, Cursor, and Claude Code sessions into OpenEval Studio."
    )
    parser.add_argument(
        "--server",
        "-s",
        help="Remote OpenEval Studio URL (e.g. 'https://studio.example.com'). If set, uploads via HTTP API instead of direct DB. Can also use OPENEVAL_SERVER_URL or OPENEVAL_WATCHER_URL.",
    )
    parser.add_argument(
        "--api-key",
        "-k",
        help="OpenEval API Key for remote authentication. Can also use OPENEVAL_API_KEY.",
    )
    parser.add_argument(
        "--user",
        "-u",
        help="Target OpenEval Studio username (e.g. 'jayson'). Used for local DB connection mode.",
    )
    parser.add_argument(
        "--agent",
        "-a",
        choices=["all", "antigravity", "cursor", "claude_code"],
        default="all",
        help="Filter agent type to ingest (default: all).",
    )
    parser.add_argument(
        "--limit",
        "-n",
        type=int,
        default=50,
        help="Max number of sessions to ingest per agent type (default: 50). Use 0 for unlimited.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and report transcripts without writing to the database or remote server.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print verbose details for every ingested session.",
    )

    args = parser.parse_args()

    # If remote server specified or in env, use HTTP API ingest mode
    server_url = args.server or os.getenv("OPENEVAL_SERVER_URL") or os.getenv("OPENEVAL_WATCHER_URL")
    api_key = args.api_key or os.getenv("OPENEVAL_API_KEY")

    if server_url and not server_url.startswith("postgres"):
        ingest_via_remote_api(
            server_url=server_url,
            api_key=api_key,
            agent=args.agent,
            limit=args.limit,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
        return

    # 1. Resolve Target User (Local DB Mode)
    user_id, username = resolve_target_user(args.user)
    print("=" * 64)
    print(" 🚀  OpenEval Studio — Local Agent Log Ingest Utility")
    print("=" * 64)
    print(f" Target User:     {username} (user_id: {user_id})")
    print(f" Target Database: {os.getenv('DATABASE_URL', 'default')}")
    print(
        f" Mode:            {'DRY-RUN (No writes)' if args.dry_run else 'LIVE INGEST (Writing to PostgreSQL)'}"
    )
    print("=" * 64)

    # Bind tenant ContextVars for WatcherStore
    current_user_slug.set(username)
    current_user_id.set(user_id)
    store = get_watcher_store()

    # Warm up title mappings for Antigravity
    UniversalAgentLogLoader._ag_titles = get_antigravity_titles()

    total_ingested = 0
    total_tools = 0
    total_reviews = 0
    now = time.time()

    # 2. Ingest Antigravity
    if args.agent in ("all", "antigravity"):
        ag_files = discover_antigravity_transcripts()
        count = len(ag_files) if args.limit <= 0 else min(len(ag_files), args.limit)
        print(f"\n[Antigravity] Found {len(ag_files)} transcript(s). Ingesting up to {count}...")

        for idx, t_path in enumerate(ag_files[:count], 1):
            age_sec = now - t_path.stat().st_mtime
            is_active = (age_sec < 45 * 60) or (idx == 1 and age_sec < 90 * 60)
            status = "working" if is_active else "completed"

            if args.dry_run:
                title = UniversalAgentLogLoader._ag_titles.get(
                    t_path.parent.parent.name, t_path.parent.parent.name[:12]
                )
                print(
                    f"  [{idx}/{count}] [DRY-RUN] {t_path.parent.parent.name[:12]} - {title[:40]}"
                )
                continue

            session = UniversalAgentLogLoader.ingest_antigravity_transcript(
                t_path, status=status, store=store
            )
            if session:
                total_ingested += 1
                tc_count = len(session.trajectory.tool_calls)
                rev_count = len(session.trajectory.reviews)
                total_tools += tc_count
                total_reviews += rev_count
                if args.verbose or idx <= 5 or idx == count:
                    print(
                        f"  [{idx}/{count}] ✅ {session.session_id} | {session.title[:45]} | {tc_count} tools, {rev_count} reviews"
                    )

    # 3. Ingest Cursor
    if args.agent in ("all", "cursor"):
        cursor_files = discover_cursor_transcripts()
        count = len(cursor_files) if args.limit <= 0 else min(len(cursor_files), args.limit)
        print(f"\n[Cursor] Found {len(cursor_files)} transcript(s). Ingesting up to {count}...")

        for idx, c_path in enumerate(cursor_files[:count], 1):
            age_sec = now - c_path.stat().st_mtime
            is_active = (age_sec < 45 * 60) or (idx == 1 and age_sec < 90 * 60)
            status = "working" if is_active else "completed"

            if args.dry_run:
                print(f"  [{idx}/{count}] [DRY-RUN] {c_path.name[:20]}")
                continue

            session = UniversalAgentLogLoader.ingest_cursor_agent_transcript_jsonl(
                c_path, status=status, store=store
            )
            if session:
                total_ingested += 1
                tc_count = len(session.trajectory.tool_calls)
                rev_count = len(session.trajectory.reviews)
                total_tools += tc_count
                total_reviews += rev_count
                if args.verbose or idx <= 5 or idx == count:
                    print(
                        f"  [{idx}/{count}] ✅ {session.session_id} | {session.title[:45]} | {tc_count} tools, {rev_count} reviews"
                    )

    # 4. Ingest Claude Code
    if args.agent in ("all", "claude_code"):
        claude_files = discover_claude_transcripts()
        count = len(claude_files) if args.limit <= 0 else min(len(claude_files), args.limit)
        print(
            f"\n[Claude Code] Found {len(claude_files)} transcript(s). Ingesting up to {count}..."
        )

        for idx, cl_path in enumerate(claude_files[:count], 1):
            if args.dry_run:
                print(f"  [{idx}/{count}] [DRY-RUN] {cl_path.name[:20]}")
                continue

            session = UniversalAgentLogLoader.ingest_claude_code_jsonl(cl_path, store=store)
            if session:
                total_ingested += 1
                tc_count = len(session.trajectory.tool_calls)
                rev_count = len(session.trajectory.reviews)
                total_tools += tc_count
                total_reviews += rev_count
                if args.verbose or idx <= 5 or idx == count:
                    print(
                        f"  [{idx}/{count}] ✅ {session.session_id} | {session.title[:45]} | {tc_count} tools, {rev_count} reviews"
                    )

    print("\n" + "=" * 64)
    print(" 🎉  Ingestion Complete!")
    print("=" * 64)
    print(f" Total Sessions Ingested: {total_ingested}")
    print(f" Total Tool Calls Loaded: {total_tools}")
    print(f" Policy Reviews Audited:  {total_reviews}")
    print(f" Target User:             {username}")
    print("=" * 64)
    print(" Open http://localhost:5173/#sessions to explore and search all transcripts!\n")


if __name__ == "__main__":
    main()
