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
import subprocess
import sys
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
            cwd = arguments.get("cwd", ".")
            timeout = int(arguments.get("timeout_secs", 30))

            try:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    cwd=cwd,
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
                    "instructions": spec.instruction_text[:500] if spec.instruction_text else "",
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
