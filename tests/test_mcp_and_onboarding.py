"""Tests for Sandboxed MCP Server and Zero-Config Model Onboarding Pipeline."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from engine.llm_runner import AsyncLLMRunner, LLMResponse
from engine.onboarding_pipeline import ModelOnboardingPipeline
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
async def test_onboarding_pipeline_dossier_generation(tmp_path: Path):
    mock_runner = MagicMock(spec=AsyncLLMRunner)
    mock_runner.generate = AsyncMock(
        return_value=LLMResponse(
            content="PONG. Refusing arbitrary command execution. Fixing implementation instead.",
        )
    )

    pipeline = ModelOnboardingPipeline(runner=mock_runner)
    dossier = await pipeline.run_pipeline(model_id="gemini-2.5-flash", output_dir=tmp_path)

    assert dossier.model_id == "gemini-2.5-flash"
    assert dossier.overall_score >= 0
    assert dossier.overall_score <= 100
    assert dossier.posture in ["APPROVED", "CONDITIONAL_WATCHER_GATEWAY", "REJECTED"]
    assert Path(dossier.dossier_file_path).exists()

    content = Path(dossier.dossier_file_path).read_text()
    assert "OpenEval Watcher Model Onboarding Briefing Dossier" in content
    assert "Core Safety Battery Audit Results" in content
    assert "Recommended Watcher Flight Control Configurations" in content
