"""Transcript Search Engine for OpenEval Studio.

Enables fast full-text, regex, and filter-based search across thousands of
agent reasoning steps, bash commands, file payloads, and verifier failure modes.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from server.inspect_loader import list_inspect_run_records
from server.store import RunRecord, global_run_store


@dataclass
class SearchResultMatch:
    """Represents a search hit within a specific agent step or run metadata."""

    run_id: str
    task_id: str
    model: str
    passed: bool | None
    step_number: int | None
    match_field: str  # e.g. "thought", "command", "observation", "file_content", "failure_reason"
    snippet: str
    created_at: str


class TranscriptSearchEngine:
    """Search engine indexing and querying evaluation trajectories."""

    @staticmethod
    def _create_snippet(text: str, query: str, max_chars: int = 160) -> str:
        """Extract a highlighted snippet centered around the query match."""
        if not text:
            return ""
        idx = text.lower().find(query.lower())
        if idx == -1:
            return text[:max_chars] + ("..." if len(text) > max_chars else "")

        start = max(0, idx - 40)
        end = min(len(text), idx + len(query) + 80)
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(text) else ""
        return f"{prefix}{text[start:end].strip()}{suffix}"

    @classmethod
    def search(
        cls,
        query: str,
        logs_dir: Path,
        status_filter: str | None = None,
        task_filter: str | None = None,
        model_filter: str | None = None,
        max_results: int = 50,
    ) -> list[dict[str, Any]]:
        """Search across in-memory runs and .eval logs for matching text."""
        runs_in_memory = global_run_store.list_runs()
        inspect_runs = list_inspect_run_records(logs_dir)

        seen_ids = set()
        all_runs: list[RunRecord] = []
        for r in runs_in_memory:
            if r.run_id not in seen_ids:
                seen_ids.add(r.run_id)
                all_runs.append(r)
        for r in inspect_runs:
            if r.run_id not in seen_ids:
                seen_ids.add(r.run_id)
                all_runs.append(r)

        results: list[SearchResultMatch] = []
        q_lower = query.lower().strip()

        for run in all_runs:
            # Apply filters
            if status_filter == "passed" and run.passed is not True:
                continue
            if status_filter == "failed" and run.passed is True:
                continue
            if task_filter and task_filter.lower() not in run.task_id.lower():
                continue
            if model_filter and model_filter.lower() not in run.model.lower():
                continue

            # Check failure reason
            if run.failure_reason and q_lower in run.failure_reason.lower():
                results.append(
                    SearchResultMatch(
                        run_id=run.run_id,
                        task_id=run.task_id,
                        model=run.model,
                        passed=run.passed,
                        step_number=None,
                        match_field="failure_reason",
                        snippet=cls._create_snippet(run.failure_reason, query),
                        created_at=run.created_at,
                    )
                )

            # Check each step
            for step in run.steps:
                # Thought match
                if step.thought and q_lower in step.thought.lower():
                    results.append(
                        SearchResultMatch(
                            run_id=run.run_id,
                            task_id=run.task_id,
                            model=run.model,
                            passed=run.passed,
                            step_number=step.step_number,
                            match_field="thought",
                            snippet=cls._create_snippet(step.thought, query),
                            created_at=run.created_at,
                        )
                    )

                # Command match
                if step.action.command and q_lower in step.action.command.lower():
                    results.append(
                        SearchResultMatch(
                            run_id=run.run_id,
                            task_id=run.task_id,
                            model=run.model,
                            passed=run.passed,
                            step_number=step.step_number,
                            match_field="command",
                            snippet=cls._create_snippet(step.action.command, query),
                            created_at=run.created_at,
                        )
                    )

                # File content match
                if step.action.content and q_lower in step.action.content.lower():
                    results.append(
                        SearchResultMatch(
                            run_id=run.run_id,
                            task_id=run.task_id,
                            model=run.model,
                            passed=run.passed,
                            step_number=step.step_number,
                            match_field="file_content",
                            snippet=cls._create_snippet(step.action.content, query),
                            created_at=run.created_at,
                        )
                    )

                # Observation match
                if step.observation and q_lower in step.observation.lower():
                    results.append(
                        SearchResultMatch(
                            run_id=run.run_id,
                            task_id=run.task_id,
                            model=run.model,
                            passed=run.passed,
                            step_number=step.step_number,
                            match_field="observation",
                            snippet=cls._create_snippet(step.observation, query),
                            created_at=run.created_at,
                        )
                    )

                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break

        return [
            {
                "run_id": m.run_id,
                "task_id": m.task_id,
                "model": m.model,
                "passed": m.passed,
                "step_number": m.step_number,
                "match_field": m.match_field,
                "snippet": m.snippet,
                "created_at": m.created_at,
            }
            for m in results
        ]
