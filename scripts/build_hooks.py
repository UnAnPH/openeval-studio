#!/usr/bin/env python3
"""Synchronise the shared hook core into every standalone runtime gate hook.

The hooks must stay 100% standalone so they can be installed from a single
downloaded file (``curl .../scripts/<hook>.py | python3``). Rather than import
``scripts/_hook_core.py`` at runtime, each hook embeds an identical copy of the
shared block between these markers::

    # === BEGIN OPENEVAL_SHARED_HOOK_CORE ===
    ...
    # === END OPENEVAL_SHARED_HOOK_CORE ===

Usage::

    python3 scripts/build_hooks.py            # inject / refresh the block
    python3 scripts/build_hooks.py --check    # exit 1 if any hook drifted
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
CORE_PATH = SCRIPTS_DIR / "_hook_core.py"

BEGIN_MARKER = "# === BEGIN OPENEVAL_SHARED_HOOK_CORE ==="
END_MARKER = "# === END OPENEVAL_SHARED_HOOK_CORE ==="

HOOK_FILES: tuple[Path, ...] = (
    SCRIPTS_DIR / "antigravity_watcher_gate.py",
    SCRIPTS_DIR / "cursor_watcher_gate.py",
    SCRIPTS_DIR / "claude_code_watcher_hook.py",
    SCRIPTS_DIR / "codex_watcher_hook.py",
    SCRIPTS_DIR / "opencode_watcher_hook.py",
)


def _read_core_block() -> str:
    """Return the canonical block, including both marker lines."""
    text = CORE_PATH.read_text(encoding="utf-8")
    if text.count(BEGIN_MARKER) != 1 or text.count(END_MARKER) != 1:
        raise SystemExit(
            f"{CORE_PATH}: expected exactly one BEGIN/END marker pair "
            f"(found begin={text.count(BEGIN_MARKER)}, end={text.count(END_MARKER)})"
        )
    start = text.index(BEGIN_MARKER)
    end = text.index(END_MARKER) + len(END_MARKER)
    return text[start:end]


def _current_block(text: str, path: Path, core_block: str) -> str:
    """Return the block currently embedded in ``path`` (or raise a clear error)."""
    begin_count = text.count(BEGIN_MARKER)
    end_count = text.count(END_MARKER)
    if begin_count != 1 or end_count != 1:
        expected = core_block.splitlines()[0]
        raise SystemExit(
            f"{path}: expected exactly one shared-core marker pair "
            f"({expected!r}); found begin={begin_count}, end={end_count}. "
            "Add the BEGIN/END markers around the shared block."
        )
    start = text.index(BEGIN_MARKER)
    end = text.index(END_MARKER) + len(END_MARKER)
    return text[start:end]


def _check_hook(path: Path, core_block: str) -> bool:
    text = path.read_text(encoding="utf-8")
    current = _current_block(text, path, core_block)
    if current == core_block:
        print(f"ok      {path.relative_to(REPO_ROOT)}")
        return True
    print(f"drifted {path.relative_to(REPO_ROOT)}", file=sys.stderr)
    diff = difflib.unified_diff(
        current.splitlines(),
        core_block.splitlines(),
        fromfile=f"{path.relative_to(REPO_ROOT)} (embedded)",
        tofile=f"{CORE_PATH.relative_to(REPO_ROOT)} (canonical)",
        lineterm="",
    )
    for line in diff:
        print(line, file=sys.stderr)
    return False


def _sync_hook(path: Path, core_block: str) -> bool:
    text = path.read_text(encoding="utf-8")
    current = _current_block(text, path, core_block)
    if current == core_block:
        print(f"ok      {path.relative_to(REPO_ROOT)}")
        return False
    start = text.index(BEGIN_MARKER)
    end = text.index(END_MARKER) + len(END_MARKER)
    updated = text[:start] + core_block + text[end:]
    path.write_text(updated, encoding="utf-8")
    print(f"updated {path.relative_to(REPO_ROOT)}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify every hook has the exact up-to-date shared core block",
    )
    args = parser.parse_args(argv)

    if not CORE_PATH.is_file():
        print(f"missing canonical core: {CORE_PATH}", file=sys.stderr)
        return 1

    try:
        core_block = _read_core_block()
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1

    missing = [path for path in HOOK_FILES if not path.is_file()]
    if missing:
        for path in missing:
            print(f"missing hook: {path}", file=sys.stderr)
        return 1

    if args.check:
        all_synced = True
        for path in HOOK_FILES:
            try:
                all_synced = _check_hook(path, core_block) and all_synced
            except SystemExit as exc:
                print(str(exc), file=sys.stderr)
                all_synced = False
        if all_synced:
            print("All hook scripts embed the up-to-date shared core.")
            return 0
        print(
            "Shared core drift detected. Run: python3 scripts/build_hooks.py",
            file=sys.stderr,
        )
        return 1

    changed = False
    for path in HOOK_FILES:
        try:
            changed = _sync_hook(path, core_block) or changed
        except SystemExit as exc:
            print(str(exc), file=sys.stderr)
            return 1
    if not changed:
        print("All hook scripts already embed the up-to-date shared core.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
