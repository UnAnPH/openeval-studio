"""Tests for Sandboxed MCP Server."""

from types import SimpleNamespace

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


@pytest.mark.asyncio
async def test_mcp_server_blocks_sensitive_and_outside_cwd():
    server = SandboxedMCPServer()
    for dangerous_cwd in ("~/.ssh", "/etc", ".."):
        resp = await server.execute_tool(
            "sandbox_bash",
            {"command": "ls -la", "cwd": dangerous_cwd},
        )
        assert resp["status"] == "blocked", (
            f"Expected blocked for cwd={dangerous_cwd!r}, got {resp}"
        )
        assert resp["exit_code"] == 126
        assert resp["decision"] == "deny"


@pytest.mark.asyncio
async def test_mcp_server_task_scaffold_preserves_full_instructions(monkeypatch):
    long_instructions = "Detailed task instruction sentence. " * 25
    assert len(long_instructions) > 650

    fake_spec = SimpleNamespace(
        task_id="long-instruction-task",
        metadata=SimpleNamespace(category="swe-bench", tags=["long", "instructions"]),
        instruction_text=long_instructions,
    )

    monkeypatch.setattr("server.mcp_server.load_task_spec", lambda task_id: fake_spec)

    server = SandboxedMCPServer()
    resp = await server.execute_tool("task_scaffold", {"task_id": "long-instruction-task"})

    assert "error" not in resp
    assert resp["task_id"] == "long-instruction-task"
    assert resp["instructions"] == long_instructions
    assert len(resp["instructions"]) == len(long_instructions)
    assert resp["instructions"].endswith("Detailed task instruction sentence. ")
