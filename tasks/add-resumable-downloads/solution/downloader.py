#!/usr/bin/env python3

from __future__ import annotations

import http.client
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


CHUNK_SIZE = 64 * 1024
CONTENT_RANGE_RE = re.compile(r"^bytes (\d+)-(\d+)/(\d+|\*)$")


class DownloadError(Exception):
    pass


def meta_path(output_path: Path) -> Path:
    return output_path.with_name(output_path.name + ".part.meta")


def part_path(output_path: Path) -> Path:
    return output_path.with_name(output_path.name + ".part")


def read_meta(output_path: Path) -> dict[str, Any] | None:
    path = meta_path(output_path)
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def write_meta(output_path: Path, etag: str | None, total_size: int | None, complete: bool) -> None:
    data = {
        "etag": etag,
        "total_size": total_size,
        "complete": complete,
    }
    tmp = meta_path(output_path).with_suffix(meta_path(output_path).suffix + ".tmp")
    tmp.write_text(json.dumps(data, sort_keys=True))
    os.replace(tmp, meta_path(output_path))


def head_resource(url: str) -> tuple[str | None, int | None]:
    request = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(request, timeout=20) as response:
        etag = response.headers.get("ETag")
        length_header = response.headers.get("Content-Length")
        total_size = int(length_header) if length_header and length_header.isdigit() else None
        return etag, total_size


def open_get(url: str, headers: dict[str, str] | None = None):
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    return urllib.request.urlopen(request, timeout=20)


def parse_content_range(value: str | None) -> tuple[int, int, int | None] | None:
    if not value:
        return None
    match = CONTENT_RANGE_RE.match(value.strip())
    if not match:
        return None
    start = int(match.group(1))
    end = int(match.group(2))
    total = None if match.group(3) == "*" else int(match.group(3))
    return start, end, total


def stream_to_file(response, file_obj) -> None:
    while True:
        chunk = response.read(CHUNK_SIZE)
        if not chunk:
            break
        file_obj.write(chunk)


def safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def finalize(output_path: Path, etag: str | None, total_size: int | None) -> None:
    pp = part_path(output_path)
    if total_size is not None and pp.stat().st_size != total_size:
        raise DownloadError("partial size does not match expected size")
    os.replace(pp, output_path)
    write_meta(output_path, etag, total_size, complete=True)


def final_file_is_current(output_path: Path, etag: str | None, total_size: int | None) -> bool:
    if not output_path.exists():
        return False
    if total_size is not None and output_path.stat().st_size != total_size:
        return False
    meta = read_meta(output_path)
    if not meta or not meta.get("complete"):
        return False
    if etag is not None and meta.get("etag") != etag:
        return False
    if total_size is not None and meta.get("total_size") != total_size:
        return False
    return True


def restart_download(url: str, output_path: Path) -> None:
    pp = part_path(output_path)
    safe_unlink(pp)

    with open_get(url) as response:
        if response.status != 200:
            raise DownloadError(f"expected HTTP 200 for full download, got {response.status}")

        etag = response.headers.get("ETag")
        length_header = response.headers.get("Content-Length")
        total_size = int(length_header) if length_header and length_header.isdigit() else None

        write_meta(output_path, etag, total_size, complete=False)
        with pp.open("wb") as out_file:
            stream_to_file(response, out_file)

    finalize(output_path, etag, total_size)


def resume_download(url: str, output_path: Path, etag: str | None, total_size: int | None) -> bool:
    pp = part_path(output_path)
    partial_size = pp.stat().st_size

    if partial_size == 0:
        return False
    if total_size is not None and partial_size > total_size:
        return False

    headers = {
        "Range": f"bytes={partial_size}-",
    }
    if etag:
        headers["If-Range"] = etag

    try:
        response = open_get(url, headers=headers)
    except urllib.error.HTTPError as exc:
        if exc.code == 416:
            if total_size is not None and partial_size == total_size:
                finalize(output_path, etag, total_size)
                return True
            return False
        raise

    with response:
        if response.status == 200:
            return False

        if response.status != 206:
            raise DownloadError(f"unexpected HTTP status {response.status}")

        current_etag = response.headers.get("ETag")
        if etag is not None and current_etag is not None and current_etag != etag:
            return False

        parsed = parse_content_range(response.headers.get("Content-Range"))
        if parsed is None:
            return False

        start, _end, range_total = parsed
        if start != partial_size:
            return False

        if total_size is None:
            total_size = range_total
        elif range_total is not None and range_total != total_size:
            return False

        write_meta(output_path, etag or current_etag, total_size, complete=False)
        with pp.open("ab") as out_file:
            stream_to_file(response, out_file)

    finalize(output_path, etag or current_etag, total_size)
    return True


def partial_state_is_usable(output_path: Path, etag: str | None, total_size: int | None) -> bool:
    pp = part_path(output_path)
    if not pp.exists():
        return False

    meta = read_meta(output_path)
    if not meta or meta.get("complete"):
        return False

    meta_etag = meta.get("etag")
    meta_total = meta.get("total_size")

    if etag is not None and meta_etag != etag:
        return False
    if total_size is not None and meta_total != total_size:
        return False
    if total_size is not None and pp.stat().st_size > total_size:
        return False

    return True


def download(url: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        current_etag, current_size = head_resource(url)
    except Exception:
        current_etag, current_size = None, None

    if final_file_is_current(output_path, current_etag, current_size):
        return

    if partial_state_is_usable(output_path, current_etag, current_size):
        if resume_download(url, output_path, current_etag, current_size):
            return

    restart_download(url, output_path)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: fetch-file URL OUTPUT_PATH", file=sys.stderr)
        return 2

    try:
        download(argv[1], Path(argv[2]))
        return 0
    except (DownloadError, urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        print(f"download failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
