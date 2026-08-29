"""Unit Tests for SFT & DPO Fine-Tuning Dataset Exporter."""

from pathlib import Path

from engine.react_agent import AgentAction, AgentStep
from engine.sft_exporter import SFTDatasetExporter
from schemas.models import get_available_models, get_model_spec
from server.store import RunRecord, global_run_store


def test_local_model_catalog() -> None:
    """Verify local models (Ollama, vLLM) are present in the model catalog."""
    ollama_models = get_available_models(provider="ollama")
    assert len(ollama_models) >= 2
    assert any(m.id == "ollama/llama3.1" for m in ollama_models)
    assert any(m.id == "ollama/qwen2.5-coder" for m in ollama_models)

    vllm_spec = get_model_spec("vllm/meta-llama/Llama-3-8B-Instruct")
    assert vllm_spec is not None
    assert vllm_spec.input_cost_per_m == 0.0


def test_sft_exporter_openai_chat_and_sharegpt() -> None:
    """Verify export to OpenAI JSONL format and ShareGPT format."""
    step = AgentStep(
        step_number=1,
        thought="I will write solution",
        action=AgentAction(tool="write_file", path="run.py", content="print('hello')"),
        observation="Wrote 14 bytes",
        latency_ms=80.0,
        tokens_used=40,
    )

    run = RunRecord(
        run_id="run_sft_test_1",
        task_id="cancel-async-tasks",
        model="gemini-3.1-flash-lite",
        provider="google",
        status="completed",
        created_at="2026-08-29T10:00:00Z",
        steps=[step],
        total_steps=1,
        total_tokens=40,
        total_duration_sec=1.5,
        estimated_cost_usd=0.00005,
        final_summary="Finished perfectly",
        reward=1.0,
        passed=True,
        failure_reason=None,
    )
    global_run_store._runs["run_sft_test_1"] = run

    # 1. OpenAI Chat format
    openai_data = SFTDatasetExporter.export_runs(
        logs_dir=Path("nonexistent"),
        format_type="openai_chat",
        only_passed=True,
        task_id="cancel-async-tasks",
    )
    assert len(openai_data) >= 1
    sample = next(d for d in openai_data if d["metadata"]["run_id"] == "run_sft_test_1")
    assert "messages" in sample
    assert any(
        m["role"] == "assistant" and "Thought: I will write solution" in m["content"]
        for m in sample["messages"]
    )

    # 2. ShareGPT format
    sharegpt_data = SFTDatasetExporter.export_runs(
        logs_dir=Path("nonexistent"),
        format_type="sharegpt",
        only_passed=True,
        task_id="cancel-async-tasks",
    )
    assert len(sharegpt_data) >= 1
    sample_sg = next(d for d in sharegpt_data if d["id"] == "run_sft_test_1")
    assert "conversations" in sample_sg
    assert any(
        c["from"] == "gpt" and "write_file" in c["value"] for c in sample_sg["conversations"]
    )


def test_dpo_preference_pair_generation() -> None:
    """Verify pairing winning and losing runs for DPO alignment."""
    pass_run = RunRecord(
        run_id="run_pass",
        task_id="regex-log",
        model="gemini-3.7-flash",
        provider="google",
        status="completed",
        created_at="2026-08-29T11:00:00Z",
        steps=[],
        total_steps=3,
        total_tokens=150,
        total_duration_sec=2.0,
        estimated_cost_usd=0.0001,
        final_summary="All regexes matched",
        reward=1.0,
        passed=True,
        failure_reason=None,
    )

    fail_run = RunRecord(
        run_id="run_fail",
        task_id="regex-log",
        model="gpt-4o-mini",
        provider="openai",
        status="completed",
        created_at="2026-08-29T11:05:00Z",
        steps=[],
        total_steps=2,
        total_tokens=120,
        total_duration_sec=3.0,
        estimated_cost_usd=0.0001,
        final_summary="Failed parsing IPv4",
        reward=0.0,
        passed=False,
        failure_reason="Regex Syntax Error",
    )

    global_run_store._runs["run_pass"] = pass_run
    global_run_store._runs["run_fail"] = fail_run

    dpo_pairs = SFTDatasetExporter.export_runs(
        logs_dir=Path("nonexistent"),
        format_type="dpo_pairs",
        task_id="regex-log",
    )
    assert len(dpo_pairs) >= 1
    pair = dpo_pairs[0]
    assert "prompt" in pair
    assert "chosen" in pair
    assert "rejected" in pair
    assert "Regex Syntax Error" in pair["rejected"]
