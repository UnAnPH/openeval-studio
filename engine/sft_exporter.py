"""SFT / DPO Fine-Tuning Dataset Exporter for OpenEval Studio.

Extracts passing and failing agent evaluation trajectories and converts them into
standard OpenAI JSONL, ShareGPT, and DPO Preference Pair formats for LoRA / full fine-tuning.
"""

from pathlib import Path
from typing import Any, Literal

from server.inspect_loader import list_inspect_run_records
from server.store import RunRecord, global_run_store

ExportFormat = Literal["openai_chat", "sharegpt", "dpo_pairs"]


class SFTDatasetExporter:
    """Exports benchmark trajectories into LLM fine-tuning datasets."""

    @classmethod
    def export_runs(
        cls,
        logs_dir: Path,
        format_type: ExportFormat = "openai_chat",
        only_passed: bool = True,
        task_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Collect runs and serialize into the requested fine-tuning format."""
        in_memory = global_run_store.list_runs()
        inspect_runs = list_inspect_run_records(logs_dir)

        seen = set()
        all_runs: list[RunRecord] = []
        for r in in_memory:
            if r.run_id not in seen:
                seen.add(r.run_id)
                all_runs.append(r)
        for r in inspect_runs:
            if r.run_id not in seen:
                seen.add(r.run_id)
                all_runs.append(r)

        # Filter runs
        target_runs = all_runs
        if only_passed and format_type != "dpo_pairs":
            target_runs = [r for r in target_runs if r.passed is True]
        if task_id:
            target_runs = [r for r in target_runs if r.task_id == task_id]

        if format_type == "openai_chat":
            return cls._to_openai_chat(target_runs)
        elif format_type == "sharegpt":
            return cls._to_sharegpt(target_runs)
        elif format_type == "dpo_pairs":
            return cls._to_dpo_pairs(all_runs, task_id)
        return []

    @staticmethod
    def _to_openai_chat(runs: list[RunRecord]) -> list[dict[str, Any]]:
        """Convert runs to OpenAI ChatCompletion JSONL format."""
        dataset = []
        for run in runs:
            sys_msg = (
                "You are an autonomous expert software engineering agent in an "
                "isolated Linux sandbox. Investigate, plan, execute commands, "
                "modify files, and verify your solution."
            )
            messages: list[dict[str, str]] = [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": f"Task: {run.task_id}"},
            ]

            for step in run.steps:
                action_text = f"Thought: {step.thought}\nAction: {step.action.tool}"
                if step.action.command:
                    action_text += f" (command: {step.action.command})"
                if step.action.path:
                    action_text += f" (path: {step.action.path})"

                messages.append({"role": "assistant", "content": action_text})
                if step.observation:
                    messages.append({"role": "tool", "content": step.observation})

            if run.final_summary:
                messages.append({"role": "assistant", "content": f"Summary: {run.final_summary}"})

            dataset.append(
                {"messages": messages, "metadata": {"task_id": run.task_id, "run_id": run.run_id}}
            )
        return dataset

    @staticmethod
    def _to_sharegpt(runs: list[RunRecord]) -> list[dict[str, Any]]:
        """Convert runs to ShareGPT conversations format."""
        dataset = []
        for run in runs:
            convos = [
                {"from": "system", "value": "You are an autonomous AI evaluation agent."},
                {"from": "human", "value": f"Please solve benchmark task {run.task_id}."},
            ]

            for step in run.steps:
                text = f"Thought: {step.thought}\nTool: {step.action.tool}"
                if step.action.command:
                    text += f"\nCommand: {step.action.command}"
                convos.append({"from": "gpt", "value": text})
                if step.observation:
                    convos.append({"from": "observation", "value": step.observation})

            dataset.append({"id": run.run_id, "conversations": convos})
        return dataset

    @staticmethod
    def _to_dpo_pairs(runs: list[RunRecord], task_id: str | None = None) -> list[dict[str, Any]]:
        """Group passing and failing runs on the same task into DPO preference pairs."""
        by_task: dict[str, list[RunRecord]] = {}
        for r in runs:
            if task_id and r.task_id != task_id:
                continue
            by_task.setdefault(r.task_id, []).append(r)

        pairs = []
        for tid, task_runs in by_task.items():
            passed_runs = [r for r in task_runs if r.passed is True]
            failed_runs = [r for r in task_runs if r.passed is False]

            for p_run in passed_runs:
                for f_run in failed_runs:
                    chosen_summary = p_run.final_summary or "Task successfully resolved."
                    rejected_summary = f_run.failure_reason or "Verification test failure."
                    pairs.append(
                        {
                            "prompt": f"Solve benchmark task: {tid}",
                            "chosen": f"Steps: {p_run.total_steps} | Summary: {chosen_summary}",
                            "rejected": f"Failed with: {rejected_summary}",
                            "metadata": {
                                "task_id": tid,
                                "chosen_model": p_run.model,
                                "rejected_model": f_run.model,
                            },
                        }
                    )
        return pairs
