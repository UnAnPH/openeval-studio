"""Hybrid Dense Vector & Lexical Transcript Fragment Search Engine for OpenEval & Watcher.

Combines:
  1. Dense vector semantic search (Google Gemini text-embedding-004 + deterministic fallback)
  2. Coarse lexical term-overlap matching (normalized keyword / term frequency — not true BM25)
  3. Hybrid score fusion: CombinedScore = 0.6 * CosineSimilarity + 0.4 * LexicalNorm
"""

import logging
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from server.inspect_loader import list_inspect_run_records
from server.store import global_run_store
from server.transcript_embeddings import (
    TranscriptEmbeddingService,
    cosine_similarity,
)
from server.watcher_store import get_watcher_store

logger = logging.getLogger("openeval.server.transcript_search")


class FragmentMatch(BaseModel):
    """A high-signal fragment extracted from an evaluation or live agent transcript."""

    model_config = ConfigDict(extra="ignore")

    session_id: str = Field(..., description="ID of the matched session or run")
    project_or_task: str = Field(..., description="Task ID or project name")
    model: str = Field(..., description="Agent model identifier")
    turn_start: int = Field(default=1, description="1-indexed starting turn number")
    turn_end: int = Field(default=1, description="1-indexed ending turn number")
    matched_role: str = Field(
        default="assistant", description="Message role (user, assistant, tool)"
    )
    matched_span: str = Field(..., description="Exact textual excerpt or reasoning snippet")
    explanation: str = Field(
        ...,
        description="Executive 1-sentence summary of why this fragment matches the query",
    )
    relevance_score: float = Field(default=1.0, ge=0.0, le=1.0)
    match_type: Literal["dense", "lexical", "hybrid"] = "hybrid"
    dense_score: float = Field(default=0.0, description="Cosine similarity score (0.0 - 1.0)")
    lexical_score: float = Field(
        default=0.0, description="Normalized lexical term-overlap score (not true BM25)"
    )
    combined_score: float = Field(default=0.0, description="0.6 * dense + 0.4 * lexical")


class TranscriptFragmentSearchEngine:
    """Two-stage hybrid search engine indexing and extracting relevant transcript fragments."""

    @classmethod
    def _extract_text_chunks(cls, run: Any) -> list[dict[str, Any]]:
        """Extract searchable text turns from a session or RunRecord."""
        chunks: list[dict[str, Any]] = []

        # Extract from Trajectory messages if available
        if hasattr(run, "trajectory") and run.trajectory and run.trajectory.messages:
            for idx, msg in enumerate(run.trajectory.messages, start=1):
                full_text = f"{msg.content or ''}\n{msg.thinking or ''}".strip()
                if full_text:
                    chunks.append(
                        {
                            "turn": idx,
                            "role": msg.role,
                            "text": full_text,
                        }
                    )

        # Extract from AgentSteps if legacy/benchmark format
        if not chunks and hasattr(run, "steps") and run.steps:
            for s in run.steps:
                turn = getattr(s, "step_number", None) or 1
                thought = getattr(s, "thought", "") or ""
                action = getattr(s, "action", "") or ""
                obs = getattr(s, "observation", "") or ""
                combined = f"Thought: {thought}\nAction: {action}\nObservation: {obs}".strip()
                if combined:
                    chunks.append(
                        {
                            "turn": turn,
                            "role": "assistant",
                            "text": combined,
                        }
                    )
        return chunks

    @classmethod
    def search_fragments(
        cls,
        query: str,
        logs_dir: Path | None = None,
        max_fragments: int = 15,
        llm_model: str = "google/gemini-2.5-flash",
        force_fallback_embeddings: bool = False,
    ) -> list[FragmentMatch]:
        """Execute hybrid dense vector and lexical fragment retrieval."""
        query_clean = query.strip()
        if not query_clean:
            return []

        query_terms = [t.lower() for t in re.findall(r"\w+", query_clean) if len(t) > 2]
        if not query_terms:
            query_terms = [query_clean.lower()]

        # Collect candidate sessions from WatcherStore, Global RunStore, and Inspect logs
        from server.demo_seed import is_demo_seed_enabled

        watcher_store = get_watcher_store()
        all_sessions = watcher_store.list_sessions()
        if is_demo_seed_enabled():
            candidate_records = [
                s
                for s in all_sessions
                if "demo" in s.session_id or s.session_id.startswith("demo-")
            ]
        else:
            runs_in_memory = global_run_store.list_runs()
            candidate_records = list(all_sessions) + list(runs_in_memory)
            if logs_dir and logs_dir.exists():
                inspect_records = list_inspect_run_records(logs_dir)
                candidate_records.extend(inspect_records)

        embed_svc = TranscriptEmbeddingService.get_instance(
            force_fallback=force_fallback_embeddings
        )
        query_emb = embed_svc.embed_text(query_clean)

        scored_candidates: list[dict[str, Any]] = []
        for record in candidate_records:
            chunks = cls._extract_text_chunks(record)
            for chunk in chunks:
                text_lower = chunk["text"].lower()

                # Lexical scoring (term overlap & frequency)
                matches = sum(1 for term in query_terms if term in text_lower)
                lexical_score = min(1.0, matches / max(1, len(query_terms)))

                # Dense vector cosine similarity
                chunk_emb = embed_svc.embed_text(chunk["text"])
                dense_score = cosine_similarity(query_emb, chunk_emb)

                # Hybrid fusion: 60% Dense Semantic + 40% Lexical term-overlap
                combined = round(0.6 * dense_score + 0.4 * lexical_score, 3)

                if combined >= 0.12 or matches > 0:
                    # Classify match type
                    if dense_score >= 0.45 and lexical_score >= 0.3:
                        m_type: Literal["dense", "lexical", "hybrid"] = "hybrid"
                    elif dense_score > lexical_score:
                        m_type = "dense"
                    else:
                        m_type = "lexical"

                    scored_candidates.append(
                        {
                            "record": record,
                            "turn": chunk["turn"],
                            "role": chunk["role"],
                            "text": chunk["text"],
                            "dense_score": round(dense_score, 3),
                            "lexical_score": round(lexical_score, 3),
                            "combined_score": combined,
                            "match_type": m_type,
                        }
                    )

        scored_candidates.sort(key=lambda c: c["combined_score"], reverse=True)
        top_candidates = scored_candidates[:max_fragments]

        if not top_candidates:
            return []

        # Stage 2: Fragment Span Extraction & Explanation
        results: list[FragmentMatch] = []
        for cand in top_candidates:
            rec = cand["record"]
            session_id = getattr(rec, "session_id", None) or getattr(rec, "run_id", "session")
            project = getattr(rec, "project_name", None) or getattr(rec, "task_id", "unnamed")
            model_name = getattr(rec, "model", "unknown")
            text = cand["text"]
            turn = cand["turn"]

            # Deterministic span extraction
            start_idx = 0
            for term in query_terms:
                idx = text.lower().find(term)
                if idx != -1:
                    start_idx = max(0, idx - 40)
                    break
            span = text[start_idx : start_idx + 240].strip()
            if len(text) > start_idx + 240:
                span += "..."

            match_label = (
                "Semantic similarity match"
                if cand["match_type"] == "dense"
                else (
                    "Hybrid semantic & lexical match"
                    if cand["match_type"] == "hybrid"
                    else f"Keyword match on '{query_clean}'"
                )
            )
            explanation = (
                f"{match_label} ({int(cand['combined_score'] * 100)}% relevance) in turn #{turn}."
            )

            results.append(
                FragmentMatch(
                    session_id=session_id,
                    project_or_task=project,
                    model=model_name,
                    turn_start=turn,
                    turn_end=turn,
                    matched_role=cand["role"],
                    matched_span=span,
                    explanation=explanation,
                    relevance_score=cand["combined_score"],
                    match_type=cand["match_type"],
                    dense_score=cand["dense_score"],
                    lexical_score=cand["lexical_score"],
                    combined_score=cand["combined_score"],
                )
            )

        return results
