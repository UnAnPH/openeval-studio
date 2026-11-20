"""Sandboxed Research Model Context Protocol (MCP) Server for OpenEval Studio & Watcher.

Implements standard MCP (Model Context Protocol) over JSON-RPC 2.0 stdio:
Exposes 3 core tools:
1. `sandbox_bash`: Execute shell commands inside ephemeral Docker / local sandboxes.
2. `transcript_search`: Two-stage LLM-powered fragment search over agent trajectories.
3. `task_scaffold`: Inspect benchmark tasks, specifications, and test harnesses.

Run directly via:
    python -m server.mcp_server
    python cli.py mcp
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from schemas.task_spec import load_task_spec
from server.transcript_search import TranscriptFragmentSearchEngine

logger = logging.getLogger("openeval.server.mcp_server")

MCP_TOOLS_SPEC = [
    {
        "name": "sandbox_bash",
        "description": "Execute a bash command in a sandboxed execution container or workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command line to execute",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory path",
                    "default": ".",
                },
                "timeout_secs": {
                    "type": "integer",
                    "description": "Maximum execution time in seconds",
                    "default": 30,
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "transcript_search",
        "description": "Two-stage LLM-powered search finding interesting fragments in agent evaluation transcripts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language search query or concept",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of fragments to return",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "task_scaffold",
        "description": "Inspect task requirements, prompt instructions, and test harness specifications.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task identifier, e.g. cancel-async-tasks",
                },
            },
            "required": ["task_id"],
        },
    },
]


class SandboxedMCPServer:
    """JSON-RPC 2.0 MCP Protocol Server."""

    def __init__(self) -> None:
        pass

    async def execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Route tool invocation to the appropriate backend service."""
        if name == "sandbox_bash":
            command = arguments.get("command", "")
            raw_cwd = str(arguments.get("cwd") or ".")
            timeout = int(arguments.get("timeout_secs", 30))

            expanded_cwd = os.path.expanduser(raw_cwd)
            cwd_path = Path(expanded_cwd).resolve()
            workspace_root = Path(
                os.path.expanduser(os.getenv("OPENEVAL_MCP_WORKSPACE_ROOT") or str(Path.cwd()))
            ).resolve()
            allow_host_paths = os.getenv("OPENEVAL_MCP_ALLOW_HOST_PATHS") == "1"

            # Route both cwd and command through the Watcher gateway before any execution:
            # MCP tools are agent-callable, so they get the same pre-execution gate and
            # sensitive host directories (e.g. ~/.ssh, /etc) cannot be bind-mounted.
            from server.policy_gateway import PolicyGateway

            gateway = PolicyGateway.get_shared()
            cwd_check_input = f"{raw_cwd} {cwd_path}" if str(cwd_path) != raw_cwd else str(cwd_path)
            cwd_rec = await gateway.evaluate_tool_call_async("mcp-sandbox", "Read", cwd_check_input)
            if cwd_rec.decision != "allow":
                return {
                    "exit_code": 126,
                    "stdout": "",
                    "stderr": (
                        f"Blocked cwd by Watcher PolicyGateway ({cwd_rec.stage}, "
                        f"score {cwd_rec.score}/10): {cwd_rec.explanation}"
                    ),
                    "status": "blocked",
                    "decision": cwd_rec.decision,
                }

            if not allow_host_paths and not cwd_path.is_relative_to(workspace_root):
                return {
                    "exit_code": 126,
                    "stdout": "",
                    "stderr": (
                        f"Blocked cwd outside workspace root ({workspace_root}): {cwd_path}. "
                        "Set OPENEVAL_MCP_ALLOW_HOST_PATHS=1 to override."
                    ),
                    "status": "blocked",
                    "decision": "deny",
                }

            if not cwd_path.is_dir():
                return {
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": f"Invalid cwd (not an existing directory): {cwd_path}",
                    "status": "error",
                }

            rec = await gateway.evaluate_tool_call_async("mcp-sandbox", "sandbox_bash", command)
            if rec.decision != "allow":
                return {
                    "exit_code": 126,
                    "stdout": "",
                    "stderr": (
                        f"Blocked by Watcher PolicyGateway ({rec.stage}, "
                        f"score {rec.score}/10): {rec.explanation}"
                    ),
                    "status": "blocked",
                    "decision": rec.decision,
                }

            allow_host_bash = os.getenv("OPENEVAL_MCP_ALLOW_HOST_BASH") == "1"

            try:
                # Default: ephemeral, network-less container with a read-only
                # workspace mount. Host execution is an explicit opt-in.
                if allow_host_bash:
                    proc = await asyncio.create_subprocess_shell(
                        command,
                        cwd=str(cwd_path),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                else:
                    proc = await asyncio.create_subprocess_exec(
                        "docker",
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "-v",
                        f"{cwd_path}:/workspace:ro",
                        "-w",
                        "/workspace",
                        os.getenv("OPENEVAL_MCP_SANDBOX_IMAGE", "alpine:3.20"),
                        "sh",
                        "-lc",
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                exit_code = proc.returncode or 0
                return {
                    "exit_code": exit_code,
                    "stdout": stdout.decode("utf-8", errors="replace"),
                    "stderr": stderr.decode("utf-8", errors="replace"),
                    "status": "success" if exit_code == 0 else "error",
                }
            except TimeoutError:
                return {
                    "exit_code": 124,
                    "stdout": "",
                    "stderr": f"Command timed out after {timeout} seconds",
                    "status": "timeout",
                }
            except OSError as e:
                # `TimeoutError` is a subclass of OSError, so it is handled above.
                if not allow_host_bash:
                    return {
                        "exit_code": 126,
                        "stdout": "",
                        "stderr": "sandbox_bash requires Docker daemon or OPENEVAL_MCP_ALLOW_HOST_BASH=1",
                        "status": "error",
                    }
                return {
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": str(e),
                    "status": "exception",
                }
            except Exception as e:
                return {
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": str(e),
                    "status": "exception",
                }

        elif name == "transcript_search":
            query = arguments.get("query", "")
            limit = int(arguments.get("limit", 5))

            try:
                matches = TranscriptFragmentSearchEngine.search_fragments(
                    query, max_fragments=limit
                )
                return {
                    "query": query,
                    "count": len(matches),
                    "matches": [m.model_dump() for m in matches],
                }
            except Exception as e:
                return {"error": str(e), "matches": []}

        elif name == "task_scaffold":
            task_id = arguments.get("task_id", "")
            try:
                spec = load_task_spec(task_id)
                return {
                    "task_id": spec.task_id,
                    "category": spec.metadata.category,
                    "instructions": spec.instruction_text or "",
                    "tags": spec.metadata.tags,
                }
            except Exception as e:
                return {"error": f"Failed loading task {task_id}: {e}"}

        raise ValueError(f"Unknown MCP tool: {name}")

    async def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Handle incoming JSON-RPC 2.0 requests."""
        method = request.get("method")
        msg_id = request.get("id")

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "openeval-watcher-mcp",
                        "version": "1.0.0",
                    },
                },
            }

        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": MCP_TOOLS_SPEC},
            }

        elif method == "tools/call":
            params = request.get("params", {})
            name = params.get("name", "")
            args = params.get("arguments", {})

            try:
                content = await self.execute_tool(name, args)
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(content, indent=2),
                            }
                        ],
                        "isError": False,
                    },
                }
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": str(e)}],
                        "isError": True,
                    },
                }

        elif method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

        return None


async def run_mcp_stdio_server() -> None:
    """Run MCP server listening on stdin and writing to stdout."""
    server = SandboxedMCPServer()
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        line = await reader.readline()
        if not line:
            break
        raw = line.decode("utf-8").strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
            resp = await server.handle_request(req)
            if resp:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
        except Exception as e:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"},
            }
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()


def main() -> None:
    """Entrypoint for python -m server.mcp_server."""
    asyncio.run(run_mcp_stdio_server())


if __name__ == "__main__":
    main()
