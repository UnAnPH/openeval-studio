"""Tests for Sandboxed MCP Server."""

import pytest

from server.mcp_server import SandboxedMCPServer


@pytest.mark.asyncio
async def test_mcp_server_initialize():
    server = SandboxedMCPServer()
    resp = await server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert resp is not None
    assert resp["id"] == 1
    assert "tools" in resp["result"]["capabilities"]
    assert resp["result"]["serverInfo"]["name"] == "openeval-watcher-mcp"


@pytest.mark.asyncio
async def test_mcp_server_tools_list():
    server = SandboxedMCPServer()
    resp = await server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert resp is not None
    tools = resp["result"]["tools"]
    tool_names = {t["name"] for t in tools}
    assert tool_names == {"sandbox_bash", "transcript_search", "task_scaffold"}


@pytest.mark.asyncio
async def test_mcp_server_execute_sandbox_bash():
    server = SandboxedMCPServer()
    resp = await server.execute_tool("sandbox_bash", {"command": "echo 'Hello from MCP'"})
    assert resp["exit_code"] == 0
    assert "Hello from MCP" in resp["stdout"]
    assert resp["status"] == "success"
