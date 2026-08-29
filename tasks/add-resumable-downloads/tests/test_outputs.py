from __future__ import annotations

import os
import socket
import subprocess
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory


APP_CMD = ["/app/bin/fetch-file"]


@dataclass
class ServerState:
    body: bytes
    etag: str
    interrupt_once_at: int | None = None
    ignore_range: bool = False
    force_416: bool = False
    range_416_once: bool = False
    range_416_sent: bool = False
    requests: list[dict[str, str | None]] = field(default_factory=list)
    body_bytes_sent: int = 0
    interrupted: bool = False


class TestHTTPServer:
    def __init__(self, state: ServerState):
        self.state = state
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def __enter__(self):
        state = self.state

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format, *args):
                return

            def do_HEAD(self):
                self.send_response(200)
                self.send_header("ETag", state.etag)
                self.send_header("Content-Length", str(len(state.body)))
                self.end_headers()

            def do_GET(self):
                range_header = self.headers.get("Range")
                if_range = self.headers.get("If-Range")
                state.requests.append(
                    {
                        "range": range_header,
                        "if_range": if_range,
                    }
                )

                if state.interrupt_once_at is not None and not state.interrupted and range_header is None:
                    cutoff = state.interrupt_once_at
                    state.interrupted = True
                    self.send_response(200)
                    self.send_header("ETag", state.etag)
                    self.send_header("Content-Length", str(len(state.body)))
                    self.end_headers()
                    chunk = state.body[:cutoff]
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    state.body_bytes_sent += len(chunk)
                    self.connection.close()
                    return

                if range_header and state.range_416_once and not state.range_416_sent:
                    state.range_416_sent = True
                    self.send_response(416)
                    self.send_header("ETag", state.etag)
                    self.send_header("Content-Range", f"bytes */{len(state.body)}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                if state.force_416:
                    self.send_response(416)
                    self.send_header("ETag", state.etag)
                    self.send_header("Content-Range", f"bytes */{len(state.body)}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                if range_header and not state.ignore_range:
                    try:
                        unit, value = range_header.split("=", 1)
                        start_text, end_text = value.split("-", 1)
                        start = int(start_text)
                    except Exception:
                        self.send_response(400)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return

                    if unit != "bytes":
                        self.send_response(400)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return

                    if start >= len(state.body):
                        self.send_response(416)
                        self.send_header("ETag", state.etag)
                        self.send_header("Content-Range", f"bytes */{len(state.body)}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return

                    payload = state.body[start:]
                    self.send_response(206)
                    self.send_header("ETag", state.etag)
                    self.send_header("Content-Range", f"bytes {start}-{len(state.body) - 1}/{len(state.body)}")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    state.body_bytes_sent += len(payload)
                    return

                self.send_response(200)
                self.send_header("ETag", state.etag)
                self.send_header("Content-Length", str(len(state.body)))
                self.end_headers()
                self.wfile.write(state.body)
                state.body_bytes_sent += len(state.body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        assert self.httpd is not None
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5)

    @property
    def url(self) -> str:
        assert self.httpd is not None
        host, port = self.httpd.server_address
        return f"http://{host}:{port}/artifact.bin"


def run_fetch(url: str, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        APP_CMD + [url, str(output)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
    )


def body(seed: int, size: int = 180_000) -> bytes:
    return bytes((seed + i * 31) % 256 for i in range(size))


def test_full_download_writes_exact_resource_bytes():
    """A normal successful run writes exactly the served resource to OUTPUT_PATH."""
    with TemporaryDirectory() as td:
        output = Path(td) / "artifact.bin"
        expected = body(7)
        state = ServerState(body=expected, etag='"full-v1"')

        with TestHTTPServer(state) as server:
            result = run_fetch(server.url, output)

        assert result.returncode == 0, result.stderr
        assert output.read_bytes() == expected
        assert not output.with_name(output.name + ".part").exists()


def test_interrupted_download_resumes_with_range_and_if_range():
    """After an interrupted transfer, the next run resumes from the partial length using Range and If-Range."""
    with TemporaryDirectory() as td:
        output = Path(td) / "artifact.bin"
        expected = body(11)
        cutoff = 55_123
        state = ServerState(body=expected, etag='"resume-v1"', interrupt_once_at=cutoff)

        with TestHTTPServer(state) as server:
            first = run_fetch(server.url, output)
            assert first.returncode != 0
            partial = output.with_name(output.name + ".part")
            assert partial.exists()
            assert partial.stat().st_size == cutoff

            second = run_fetch(server.url, output)

        assert second.returncode == 0, second.stderr
        assert output.read_bytes() == expected
        assert any(req["range"] == f"bytes={cutoff}-" for req in state.requests)
        assert any(req["if_range"] == '"resume-v1"' for req in state.requests)


def test_server_ignoring_range_restarts_instead_of_appending():
    """If a server answers a resume request with 200, the downloader restarts instead of appending the full body."""
    with TemporaryDirectory() as td:
        output = Path(td) / "artifact.bin"
        expected = body(23)
        cutoff = 44_000
        state = ServerState(body=expected, etag='"ignore-range"', interrupt_once_at=cutoff)

        with TestHTTPServer(state) as server:
            first = run_fetch(server.url, output)
            assert first.returncode != 0
            state.ignore_range = True
            second = run_fetch(server.url, output)

        assert second.returncode == 0, second.stderr
        assert output.read_bytes() == expected
        assert output.stat().st_size == len(expected)


def test_changed_etag_restarts_from_current_resource():
    """If the resource ETag changes after a partial download, the final file is the new resource, not old+new bytes."""
    with TemporaryDirectory() as td:
        output = Path(td) / "artifact.bin"
        old_body = body(31)
        new_body = body(83)
        state = ServerState(body=old_body, etag='"old-etag"', interrupt_once_at=60_000)

        with TestHTTPServer(state) as server:
            first = run_fetch(server.url, output)
            assert first.returncode != 0
            state.body = new_body
            state.etag = '"new-etag"'
            state.interrupt_once_at = None
            second = run_fetch(server.url, output)

        assert second.returncode == 0, second.stderr
        assert output.read_bytes() == new_body
        assert output.read_bytes() != old_body


def test_416_with_complete_partial_finalizes_without_corruption():
    """A 416 response for a local partial that is already complete finalizes that partial as OUTPUT_PATH."""
    with TemporaryDirectory() as td:
        output = Path(td) / "artifact.bin"
        expected = body(41)
        cutoff = 70_000
        state = ServerState(body=expected, etag='"range-complete"', interrupt_once_at=cutoff)

        with TestHTTPServer(state) as server:
            first = run_fetch(server.url, output)
            assert first.returncode != 0
            partial = output.with_name(output.name + ".part")
            partial.write_bytes(expected)
            state.force_416 = True
            second = run_fetch(server.url, output)

        assert second.returncode == 0, second.stderr
        assert output.read_bytes() == expected
        assert not output.with_name(output.name + ".part").exists()




def test_416_after_saved_bytes_downloads_current_file():
    """A 416 response after saved bytes is handled safely and produces the current resource."""
    with TemporaryDirectory() as td:
        output = Path(td) / "artifact.bin"
        expected = body(47)
        cutoff = 33_000
        state = ServerState(body=expected, etag='"range-incomplete"', interrupt_once_at=cutoff)

        with TestHTTPServer(state) as server:
            first = run_fetch(server.url, output)
            assert first.returncode != 0
            assert output.with_name(output.name + ".part").stat().st_size == cutoff

            state.range_416_once = True
            second = run_fetch(server.url, output)

        assert second.returncode == 0, second.stderr
        assert output.read_bytes() == expected
        assert state.range_416_sent
        assert any(req["range"] == f"bytes={cutoff}-" for req in state.requests)

def test_missing_or_corrupt_partial_metadata_restarts_from_zero():
    """A partial file without usable metadata is discarded and replaced by a clean full download."""
    with TemporaryDirectory() as td:
        output = Path(td) / "artifact.bin"
        expected = body(53)
        partial = output.with_name(output.name + ".part")
        partial.write_bytes(expected[:30_000])
        output.with_name(output.name + ".part.meta").write_text("not-json")

        state = ServerState(body=expected, etag='"corrupt-meta"')

        with TestHTTPServer(state) as server:
            result = run_fetch(server.url, output)

        assert result.returncode == 0, result.stderr
        assert output.read_bytes() == expected
        assert output.stat().st_size == len(expected)
        assert state.body_bytes_sent == len(expected)

def test_existing_complete_file_does_not_download_body_again():
    """A second run for an already complete current file succeeds without downloading the response body again."""
    with TemporaryDirectory() as td:
        output = Path(td) / "artifact.bin"
        expected = body(59)
        state = ServerState(body=expected, etag='"already-complete"')

        with TestHTTPServer(state) as server:
            first = run_fetch(server.url, output)
            assert first.returncode == 0, first.stderr
            assert output.read_bytes() == expected

            state.body_bytes_sent = 0
            state.requests.clear()

            second = run_fetch(server.url, output)

        assert second.returncode == 0, second.stderr
        assert output.read_bytes() == expected
        assert state.body_bytes_sent == 0
