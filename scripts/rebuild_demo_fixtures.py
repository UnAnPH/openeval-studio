"""Rebuild the UI demo fixture payload from fixtures/demo/{sessions,evals}/*.json.

Writes ``ui/src/data/demoFixturesData.json``. ``ui/src/data/demoFixtures.ts``
must stay a thin wrapper that imports that JSON (no inline fixture literals),
so rebuilds never reintroduce a ~28k-line TypeScript file.
"""

import json
from pathlib import Path

DATA_PATH = Path("ui/src/data/demoFixturesData.json")
WRAPPER_PATH = Path("ui/src/data/demoFixtures.ts")
MAX_WRAPPER_LINES = 80


def rebuild_demo_fixtures() -> tuple[int, int]:
    session_files = sorted(Path("fixtures/demo/sessions").glob("*.json"))
    eval_files = sorted(Path("fixtures/demo/evals").glob("*.json"))
    if not session_files or not eval_files:
        raise ValueError(
            "Missing demo fixtures: expected JSON files under "
            "fixtures/demo/sessions/ and fixtures/demo/evals/"
        )

    sessions_data = [json.loads(f.read_text(encoding="utf-8")) for f in session_files]
    eval_texts = [(f, f.read_text(encoding="utf-8")) for f in eval_files]
    eval_texts.sort(
        key=lambda item: (
            0
            if item[0].name == "eval-build-pov-ray-20261119-214347-rskns8.json"
            else 1
            if "SAFETY FIREWALL BLOCKED" in item[1]
            else 2,
            item[0].name,
        )
    )
    eval_data = [json.loads(text) for _, text in eval_texts]
    payload = {"sessions": sessions_data, "evals": eval_data}

    DATA_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    wrapper = WRAPPER_PATH.read_text(encoding="utf-8")
    wrapper_lines = wrapper.count("\n") + 1
    if wrapper_lines >= MAX_WRAPPER_LINES or "demoFixturesData.json" not in wrapper:
        raise RuntimeError(
            f"{WRAPPER_PATH} is no longer a thin JSON wrapper "
            f"({wrapper_lines} lines, limit {MAX_WRAPPER_LINES - 1}). "
            "Keep the raw payload in demoFixturesData.json."
        )

    return len(sessions_data), len(eval_data)


if __name__ == "__main__":
    sessions, evals = rebuild_demo_fixtures()
    print(
        f"Rebuilt {DATA_PATH} with {sessions} sessions and {evals} evals "
        f"({WRAPPER_PATH} stays a thin wrapper)."
    )
