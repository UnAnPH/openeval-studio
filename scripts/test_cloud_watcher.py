#!/usr/bin/env python3
"""OpenEval Cloud Watcher Connector & Connectivity Probe.

Validates connectivity between your local developer workstation (Cursor, Claude Code,
Antigravity) and your deployed cloud backend (e.g. https://demo.openeval.studio).

Usage:
  python scripts/test_cloud_watcher.py [URL] [API_KEY]
  
Example:
  python scripts/test_cloud_watcher.py https://demo.openeval.studio my_secret_key
  python scripts/test_cloud_watcher.py http://127.0.0.1:8000
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request


def probe_cloud(base_url: str, api_key: str = "") -> bool:
    clean_url = base_url.rstrip("/")
    if clean_url.endswith("/api/watcher/evaluate"):
        clean_url = clean_url[: -len("/api/watcher/evaluate")]
    elif clean_url.endswith("/api/gate/evaluate"):
        clean_url = clean_url[: -len("/api/gate/evaluate")]

    health_url = f"{clean_url}/api/health"
    evaluate_url = f"{clean_url}/api/watcher/evaluate"

    print("=" * 60)
    print(" 🛡️  OPENEVAL STUDIO CLOUD WATCHER CONNECTOR PROBE")
    print("=" * 60)
    print(f" Target Host:     {clean_url}")
    print(f" Auth Protected:  {'Yes (Key provided)' if api_key else 'No key (public mode)'}")
    print("-" * 60)

    # 1. Health Probe
    print("[1/3] Probing /api/health ...", end=" ", flush=True)
    try:
        t0 = time.perf_counter()
        req = urllib.request.Request(health_url)
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode())
            rtt_ms = (time.perf_counter() - t0) * 1000
            print(f"✅ OK ({rtt_ms:.1f}ms)")
            print(f"      Backend Version: {data.get('version', 'unknown')}, Demo Seed: {data.get('demo_seed')}")
    except Exception as exc:
        print(f"❌ FAILED\n      Error: {exc}")
        return False

    # 2. Watcher Evaluation Probe (Safe Tool Call)
    print("[2/3] Probing /api/watcher/evaluate (Safe command) ...", end=" ", flush=True)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-OpenEval-Key"] = api_key
        headers["Authorization"] = f"Bearer {api_key}"

    session_id = f"probe-{int(time.time())}"
    safe_payload = {
        "agent_id": "cloud-probe",
        "tool_name": "bash",
        "arguments": {"command": "git status"},
        "session_id": session_id,
        "thought_context": "Connectivity validation probe",
    }

    try:
        t0 = time.perf_counter()
        req = urllib.request.Request(
            evaluate_url,
            data=json.dumps(safe_payload).encode(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            verdict = json.loads(resp.read().decode())
            rtt_ms = (time.perf_counter() - t0) * 1000
            dec = verdict.get("decision")
            print(f"✅ OK ({rtt_ms:.1f}ms) -> decision: '{dec}'")
    except Exception as exc:
        print(f"❌ FAILED\n      Error: {exc}")
        return False

    # 3. Watcher Evaluation Probe (Blocked Command)
    print("[3/3] Probing /api/watcher/evaluate (Security rule test) ...", end=" ", flush=True)
    bad_payload = {
        "agent_id": "cloud-probe",
        "tool_name": "bash",
        "arguments": {"command": "sudo rm -rf /"},
        "session_id": session_id,
        "thought_context": "Security rule validation probe",
    }

    try:
        req = urllib.request.Request(
            evaluate_url,
            data=json.dumps(bad_payload).encode(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            verdict = json.loads(resp.read().decode())
            dec = verdict.get("decision")
            if dec in ("deny", "escalate"):
                print(f"✅ INTERCEPTED -> decision: '{dec}' (rule enforced correctly)")
            else:
                print(f"⚠️ Warning: Expected 'deny' or 'escalate', got: '{dec}'")
    except Exception as exc:
        print(f"❌ FAILED\n      Error: {exc}")
        return False

    print("=" * 60)
    print(" 🎉 CLOUD CONNECTOR VERIFIED SUCCESSFULLY!")
    print("=" * 60)
    print("To stream live IDE telemetry from your terminal to your cloud dashboard,")
    print("add these lines to your ~/.zshrc or terminal session:\n")
    print(f'export OPENEVAL_WATCHER_URL="{clean_url}/api/watcher/evaluate"')
    if api_key:
        print(f'export OPENEVAL_API_KEY="{api_key}"')
    print("\nThen restart Cursor or your agent: your actions will stream live to the cloud!")
    return True


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("OPENEVAL_WATCHER_URL", "http://127.0.0.1:8000")
    key = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("OPENEVAL_API_KEY", "")
    success = probe_cloud(target, key)
    sys.exit(0 if success else 1)
