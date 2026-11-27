#!/usr/bin/env python3
"""Sanitize session JSON fixtures and verify zero denylist tokens exist.

Denylist tokens:
- Radiosa, Simplecast
- Apollo, Watcher (as external branding)
- Emails (e.g. user@example.com)
- Home paths (/Users/*, /home/*)
- Author name (Jayson Andal, Jayson, jaysonandal)
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

DENYLIST_TERMS = [
    r"\bradiosa\b",
    r"\bsimplecast\b",
    r"\bapollo\b",
    r"\bwatcher\b",
    r"\bjayson\b",
    r"\bandal\b",
]

EMAIL_REGEX = r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"
HOME_PATH_REGEX = r"(?:/Users/[a-zA-Z0-9_.-]+|/home/[a-zA-Z0-9_.-]+)"


def contains_denylist_token(text: str) -> list[str]:
    """Return list of denylist matches found in text."""
    found: list[str] = []
    text_lower = text.lower()
    for pattern in DENYLIST_TERMS:
        if re.search(pattern, text_lower):
            found.append(pattern)
    if re.search(EMAIL_REGEX, text):
        found.append("email_address")
    if re.search(HOME_PATH_REGEX, text):
        found.append("home_path")
    return found


def sanitize_text(text: str) -> str:
    """Scrub denylisted tokens and PII paths from text."""
    # Replace home paths
    scrubbed = re.sub(HOME_PATH_REGEX, "/workspace", text)
    # Replace author emails
    scrubbed = re.sub(EMAIL_REGEX, "developer@example.com", scrubbed)
    # Replace author names
    scrubbed = re.sub(r"Jayson\s+Andal", "Operator", scrubbed, flags=re.IGNORECASE)
    scrubbed = re.sub(r"jaysonandal", "operator", scrubbed, flags=re.IGNORECASE)
    scrubbed = re.sub(r"\bJayson\b", "Operator", scrubbed)
    # Replace forbidden project names
    scrubbed = re.sub(r"\bradiosa\b", "open-source-ci", scrubbed, flags=re.IGNORECASE)
    scrubbed = re.sub(r"\bsimplecast\b", "local-lab", scrubbed, flags=re.IGNORECASE)
    # Replace Watcher / Apollo brand references in user copy
    scrubbed = re.sub(
        r"Apollo\s+Watcher\s+Live", "OpenEval Runtime Gate", scrubbed, flags=re.IGNORECASE
    )
    scrubbed = re.sub(r"Apollo", "OpenEval", scrubbed)
    scrubbed = re.sub(r"Watcher\s+Live", "Safety Control", scrubbed, flags=re.IGNORECASE)
    scrubbed = re.sub(r"Watcher\s+Firewall", "OpenEval Runtime Gate", scrubbed, flags=re.IGNORECASE)
    scrubbed = re.sub(r"Watcher\s+Gate", "OpenEval Runtime Gate", scrubbed, flags=re.IGNORECASE)
    scrubbed = re.sub(r"\bWatcher\b", "OpenEval", scrubbed)
    scrubbed = re.sub(r"\bwatcher\b", "monitor", scrubbed)
    return scrubbed


def sanitize_session_data(data: dict[str, Any]) -> dict[str, Any]:
    """Deeply sanitize a Session JSON structure."""
    raw_json = json.dumps(data)
    cleaned_json = sanitize_text(raw_json)
    cleaned_data: dict[str, Any] = json.loads(cleaned_json)

    # Force demo org and project
    cleaned_data["org_id"] = "demo"
    if not cleaned_data.get("project_name") or cleaned_data.get("project_name") in (
        "default_project",
        "hello",
    ):
        cleaned_data["project_name"] = "open-source-ci"
    if not cleaned_data.get("task_id") or cleaned_data.get("task_id") in (
        "default_project",
        "claude-code-session",
    ):
        cleaned_data["task_id"] = "open-source-ci"
    if cleaned_data.get("working_dir"):
        cleaned_data["working_dir"] = "/workspace"

    return cleaned_data


def check_fixtures(fixtures_dir: Path) -> bool:
    """Validate all JSON fixtures under fixtures_dir against denylist."""
    passed = True
    for json_file in sorted(fixtures_dir.glob("**/*.json")):
        content = json_file.read_text(encoding="utf-8")
        matches = contains_denylist_token(content)
        if matches:
            print(f"FAILED: {json_file} contains denylist tokens: {matches}")
            passed = False
        else:
            print(f"PASSED: {json_file.name} clean")
    return passed


def write_sanitized_fixtures(fixtures_dir: Path) -> int:
    """Sanitize all JSON fixtures under fixtures_dir in place."""
    modified_count = 0
    for json_file in sorted(fixtures_dir.glob("**/*.json")):
        content = json_file.read_text(encoding="utf-8")
        scrubbed = sanitize_text(content)
        scrubbed = re.sub(r"<think>\s*</think>", "", scrubbed)
        if scrubbed != content:
            json_file.write_text(scrubbed, encoding="utf-8")
            modified_count += 1
            print(f"SANITIZED: {json_file.name}")
        else:
            print(f"UNCHANGED: {json_file.name}")
    return modified_count


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Sanitize demo session fixtures.")
    parser.add_argument(
        "--check", action="store_true", help="Check fixtures for denylist violations"
    )
    parser.add_argument("--write", action="store_true", help="Sanitize fixtures in-place")
    parser.add_argument(
        "--fixtures-dir", default="fixtures/demo", help="Directory of fixtures to check"
    )
    args = parser.parse_args()

    target_dir = Path(args.fixtures_dir)
    if args.write:
        if not target_dir.exists():
            print(f"Fixtures dir '{target_dir}' does not exist.")
            return 1
        write_sanitized_fixtures(target_dir)
        return 0

    if args.check:
        if not target_dir.exists():
            print(f"Fixtures dir '{target_dir}' does not exist.")
            return 1
        ok = check_fixtures(target_dir)
        return 0 if ok else 1

    print("Use --check to validate fixtures against denylist or --write to sanitize in-place.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
