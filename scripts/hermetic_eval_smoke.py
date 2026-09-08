#!/usr/bin/env python3
"""Hermetic held-out verifier smoke (no API key, no Docker required).

Runs nop vs oracle against tasks/regex-log tests with /app remapped to a tempdir.
Honest FINDINGS evidence for benchmark integrity — not model Pass@k.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "tasks" / "regex-log"
ARTIFACT_DIR = ROOT / "artifacts"
OUT = ARTIFACT_DIR / "hermetic-eval.json"


def _oracle_regex() -> str:
    solve = (TASK / "solution" / "solve.sh").read_text(encoding="utf-8")
    m = re.search(r"cat << 'EOF' > /app/regex\.txt\n(.*)\nEOF", solve, re.S)
    if not m:
        raise RuntimeError("Could not extract oracle regex from solve.sh")
    return m.group(1).strip() + "\n"


def _patched_test(app_regex: Path) -> str:
    src = (TASK / "tests" / "test_outputs.py").read_text(encoding="utf-8")
    return src.replace('Path("/app/regex.txt")', f"Path({str(app_regex)!r})")


def _run_case(name: str, write_oracle: bool) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"openeval-{name}-") as td:
        td_path = Path(td)
        app = td_path / "app"
        tests = td_path / "tests"
        app.mkdir()
        tests.mkdir()
        regex_path = app / "regex.txt"
        (tests / "test_outputs.py").write_text(_patched_test(regex_path), encoding="utf-8")
        if write_oracle:
            regex_path.write_text(_oracle_regex(), encoding="utf-8")

        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=line", str(tests / "test_outputs.py")],
            cwd=td,
            capture_output=True,
            text=True,
        )
        return {
            "case": name,
            "oracle": write_oracle,
            "exit_code": proc.returncode,
            "passed": proc.returncode == 0,
            "stdout_tail": (proc.stdout or "")[-500:],
            "stderr_tail": (proc.stderr or "")[-500:],
        }


def main() -> int:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    nop = _run_case("nop", write_oracle=False)
    oracle = _run_case("oracle", write_oracle=True)

    ok = (not nop["passed"]) and oracle["passed"]
    payload = {
        "task_id": "regex-log",
        "kind": "hermetic_held_out_verifier",
        "model": "oracle(solve.sh) — no frontier model",
        "n_runs": 2,
        "pass_rate": {
            "nop": "0/1" if not nop["passed"] else "UNEXPECTED_PASS",
            "oracle": "1/1" if oracle["passed"] else "UNEXPECTED_FAIL",
        },
        "confounders": [
            "no API key",
            "no Docker (local pytest with /app remapped)",
            "not model Pass@k",
        ],
        "timestamp": datetime.now(UTC).isoformat(),
        "cases": [nop, oracle],
        "integrity_ok": ok,
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("task_id", "pass_rate", "integrity_ok")}, indent=2))
    print(f"Wrote {OUT}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
