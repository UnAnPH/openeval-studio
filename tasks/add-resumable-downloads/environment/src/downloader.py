#!/usr/bin/env python3

from __future__ import annotations

import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


CHUNK_SIZE = 64 * 1024


def download(url: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        prefix=output_path.name + ".",
        suffix=".tmp",
        dir=str(output_path.parent),
    )

    try:
        with os.fdopen(fd, "wb") as out_file:
            with urllib.request.urlopen(url, timeout=20) as response:
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    out_file.write(chunk)

        os.replace(temp_name, output_path)

    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: fetch-file URL OUTPUT_PATH", file=sys.stderr)
        return 2

    url = argv[1]
    output_path = Path(argv[2])

    try:
        download(url, output_path)
    except urllib.error.HTTPError as exc:
        print(f"download failed: HTTP {exc.code}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"download failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
