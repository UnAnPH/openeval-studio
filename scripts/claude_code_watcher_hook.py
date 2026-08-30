#!/usr/bin/env python3
"""
Claude Code PreToolUse Safety Hook (DISABLED).
Always exits 0 with zero delay to allow long-running multi-hour prompts.
"""
import sys

def main() -> None:
    # Always allow execution immediately
    sys.exit(0)

if __name__ == "__main__":
    main()
