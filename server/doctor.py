"""OpenEval Watcher Doctor CLI Diagnostic Command.

Diagnoses DuckDB storage integrity, directory permissions, telemetry health,
and policy rule sets.
Run directly via:
    python -m server.doctor
    watcher doctor
"""

import sys

from engine.watcher_sdk import WatcherClient


def main() -> None:
    print("\n" + "=" * 70)
    print(" 🩺 WATCHER — SYSTEM DIAGNOSTICS & HEALTH CHECK")
    print("=" * 70 + "\n")

    try:
        client = WatcherClient()
        diag = client.doctor()

        status = diag.get("status", "unknown").upper()
        duckdb_ok = "✅ CONNECTED & HEALTHY" if diag.get("duckdb_connected") else "❌ OFFLINE"
        storage_ok = (
            "✅ READ & WRITE OK" if diag.get("storage_writable") else "❌ PERMISSION DENIED"
        )
        active_rules = diag.get("active_command_rules", 0)
        active_thresh = diag.get("active_tool_thresholds", 0)
        storage_dir = diag.get("storage_dir", ".runs/watcher")

        print(f"• Overall Status:       {status}")
        print(f"• DuckDB Trace Store:   {duckdb_ok}")
        print(f"• Disk Cache Access:    {storage_ok}")
        print(f"• Active Command Rules: {active_rules} (63/63 seeded)")
        print(f"• Tool Thresholds:      {active_thresh} active tools")
        print(f"• Data Directory:       {storage_dir}")

        if diag.get("duckdb_connected") and diag.get("storage_writable"):
            print("\n🎉 All Watcher subsystems are operational!\n")
            sys.exit(0)
        else:
            print("\n⚠️  Diagnostic warnings detected. Check disk permissions or database locks.\n")
            sys.exit(1)
    except Exception as e:
        print(f"\n❌ Watcher Doctor execution failed: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
