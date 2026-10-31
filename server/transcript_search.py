"""Hybrid Dense Vector & Lexical Transcript Fragment Search Engine for OpenEval & Watcher.

Combines:
  1. Dense vector semantic search (Google Gemini text-embedding-004 + deterministic fallback)
  2. Coarse lexical term-overlap matching (normalized keyword / term frequency — not true BM25)
  3. Hybrid score fusion: CombinedScore = 0.6 * CosineSimilarity + 0.4 * LexicalNorm
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Literal

from google import genai
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
        """Extract searchable text turns from a session or RunRecord, including tool calls and reviews."""
        chunks: list[dict[str, Any]] = []

        # Extract from Trajectory messages if available
        if hasattr(run, "trajectory") and run.trajectory and run.trajectory.messages:
            for idx, msg in enumerate(run.trajectory.messages, start=1):
                text_parts: list[str] = []
                content = getattr(msg, "content", None)
                if content:
                    text_parts.append(str(content))
                thinking = getattr(msg, "thinking", None)
                if thinking:
                    text_parts.append(f"Thinking: {thinking}")

                # Index tool calls (tool name, command lines, arguments, and paths)
                tool_calls = getattr(msg, "tool_calls", None) or []
                for tc in tool_calls:
                    tc_name = getattr(tc, "tool_name", "") or ""
                    args = getattr(tc, "arguments", None) or {}
                    raw_in = getattr(tc, "raw_input", None) or ""
                    args_str = " ".join(f"{k}: {v}" for k, v in args.items()) if isinstance(args, dict) else str(args)
                    text_parts.append(f"Tool {tc_name}: {args_str} {raw_in}".strip())

                # Index tool results (especially security firewall blocks & stderr)
                tool_results = getattr(msg, "tool_results", None) or []
                for tr in tool_results:
                    stderr = getattr(tr, "stderr", "") or ""
                    stdout = getattr(tr, "stdout", "") or ""
                    if stderr:
                        text_parts.append(f"Result Stderr: {stderr}")
                    if stdout:
                        text_parts.append(f"Result Output: {stdout[:400]}")

                # Index message-level or associated security reviews
                msg_reviews = getattr(msg, "reviews", None) or []
                for rev in msg_reviews:
                    decision = getattr(rev, "decision", "") or ""
                    rule_violation = (
                        getattr(rev, "rule_violation", "")
                        or getattr(rev, "rule_id", "")
                        or ""
                    )
                    explanation = getattr(rev, "explanation", "") or ""
                    text_parts.append(f"Security Policy [{decision}]: {rule_violation} - {explanation}")

                full_text = "\n".join(text_parts).strip()
                if full_text:
                    chunks.append(
                        {
                            "turn": idx,
                            "role": getattr(msg, "role", "assistant"),
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
    def _llm_search_session_turns(
        cls,
        record: Any,
        query: str,
        max_fragments: int = 5,
        model_name: str = "gemini-3.5-flash-lite",
    ) -> list[FragmentMatch] | None:
        """Use Gemini LLM to semantically analyze session turns and identify exact answers to natural language queries."""
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key or api_key == "mock-key":
            return None

        chunks = cls._extract_text_chunks(record)
        if not chunks:
            return None

        # Build concise turn summaries for the LLM
        lines = []
        for c in chunks:
            text_preview = c["text"].replace("\n", " ")
            if len(text_preview) > 350:
                text_preview = text_preview[:350] + "..."
            lines.append(f"Turn #{c['turn']} [{c['role']}]: {text_preview}")

        turns_text = "\n".join(lines)
        prompt = f"""You are an expert AI Safety & Code Evaluation Search Assistant.
The user is viewing an agent transcript and has entered this search query:
User Query: "{query}"

Here are the chronological conversation turns:
{turns_text}

Analyze the turns to identify the 1-4 most relevant turns that answer or address the user's query.
If the query asks about an issue, error, block, violation, or bug (e.g. "Where is the issue", "why was it blocked", "what went wrong"), prioritize the exact turns where the fault, firewall block, or problematic action occurred.

Respond ONLY with a valid JSON array of objects with the following schema:
[
  {{
    "turn": <int turn number>,
    "matched_span": "<exact quote or concise excerpt of the key sentence, max 180 chars>",
    "explanation": "<1 clear sentence explaining why this turn addresses the user query>",
    "relevance_score": <float between 0.7 and 1.0>
  }}
]
Do not include markdown fences like ```json, just return the raw JSON array string."""

        try:
            model_target = model_name.replace("google/", "").replace("models/", "")
            if model_target in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
                model_target = "gemini-3.5-flash-lite"

            client = genai.Client(api_key=api_key)
            res = client.models.generate_content(
                model=model_target,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            raw = (res.text or "").strip()
            if not raw:
                return None

            data = json.loads(raw)
            if not isinstance(data, list):
                return None

            rec_id = (
                getattr(record, "session_id", None)
                or getattr(record, "id", None)
                or getattr(record, "run_id", "session")
            )
            project = (
                getattr(record, "project_name", None)
                or getattr(record, "task_id", "unnamed")
            )
            agent_model = getattr(record, "model", "unknown")

            results: list[FragmentMatch] = []
            chunk_map = {c["turn"]: c for c in chunks}

            for item in data[:max_fragments]:
                turn_num = int(item.get("turn", 1))
                chunk = chunk_map.get(turn_num)
                role = chunk["role"] if chunk else "assistant"
                span = str(item.get("matched_span", "")).strip()
                if not span and chunk:
                    span = chunk["text"][:180]

                score = float(item.get("relevance_score", 0.95))
                explanation = str(
                    item.get(
                        "explanation",
                        f"Identified by LLM as matching '{query}' in Turn #{turn_num}.",
                    )
                )

                results.append(
                    FragmentMatch(
                        session_id=rec_id,
                        project_or_task=project,
                        model=agent_model,
                        turn_start=turn_num,
                        turn_end=turn_num,
                        matched_role=role,
                        matched_span=span,
                        explanation=explanation,
                        relevance_score=score,
                        match_type="dense",
                        dense_score=score,
                        lexical_score=score,
                        combined_score=score,
                    )
                )

            return results
        except Exception as exc:
            logger.warning("LLM search failed, falling back to hybrid scoring: %s", exc)
            return None

    @classmethod
    def search_fragments(
        cls,
        query: str,
        logs_dir: Path | None = None,
        max_fragments: int = 15,
        llm_model: str = "google/gemini-3.5-flash-lite",
        force_fallback_embeddings: bool = False,
        session_id: str | None = None,
        scope: Literal["session", "all"] = "session",
        candidate_records: list[Any] | None = None,
    ) -> list[FragmentMatch]:
        """Execute hybrid dense vector and lexical fragment retrieval."""
        query_clean = query.strip()
        if not query_clean:
            return []

        clean_lower = query_clean.lower()
        is_issue_query = any(
            k in clean_lower
            for k in [
                "issue", "problem", "error", "block", "fail", "wrong",
                "what happened", "why", "bug", "fault", "crash", "stuck",
                "violation", "where is", "where was"
            ]
        )

        # Tokenize query preserving terms like .env, aws, db, etc.
        raw_tokens = [
            t.lower()
            for t in re.findall(r"[a-zA-Z0-9_\-\.]+", query_clean)
            if len(t) >= 2
        ]
        query_terms = raw_tokens if raw_tokens else [query_clean.lower()]
        security_query = is_issue_query or any(
            k in clean_lower
            for k in [
                "leak", "secret", "credential", "env", "token", "password", "key",
                "exfiltrat", "block", "firewall", "violation", "dump", "sudo", "bypass"
            ]
        )

        if candidate_records is None:
            from server.db import current_user_slug

            watcher_store = get_watcher_store()
            all_sessions = watcher_store.list_sessions()
            if current_user_slug.get() == "demo":
                candidate_records = list(all_sessions)
            else:
                runs_in_memory = global_run_store.list_runs()
                candidate_records = list(all_sessions) + list(runs_in_memory)
                if logs_dir and logs_dir.exists():
                    inspect_records = list_inspect_run_records(logs_dir)
                    candidate_records.extend(inspect_records)

        # Apply session scoping if requested
        scoped_records: list[Any] = []
        if session_id:
            scoped_records = [
                r
                for r in candidate_records
                if (
                    getattr(r, "session_id", None)
                    or getattr(r, "id", None)
                    or getattr(r, "run_id", "")
                )
                == session_id
            ]
            if scoped_records:
                candidate_records = scoped_records

        # Stage 1: Try true LLM semantic search when searching a session
        if candidate_records and len(candidate_records) == 1 and not force_fallback_embeddings:
            llm_matches = cls._llm_search_session_turns(
                record=candidate_records[0],
                query=query_clean,
                max_fragments=max_fragments,
                model_name=llm_model,
            )
            if llm_matches:
                return llm_matches

        embed_svc = TranscriptEmbeddingService.get_instance(
            force_fallback=force_fallback_embeddings
        )
        query_emb = embed_svc.embed_text(query_clean)

        scored_candidates: list[dict[str, Any]] = []
        for record in candidate_records:
            rec_id = (
                getattr(record, "session_id", None)
                or getattr(record, "id", None)
                or getattr(record, "run_id", "")
            )
            is_active_session = bool(session_id and rec_id == session_id)
            chunks = cls._extract_text_chunks(record)
            for chunk in chunks:
                text_lower = chunk["text"].lower()

                # Lexical scoring with word boundaries
                matches = 0
                for term in query_terms:
                    clean_term = term.lstrip(".-") or term
                    pattern = (
                        r"(?:^|[\s_./\-\"\\`])"
                        + re.escape(clean_term)
                        + r"(?:s|ed|ing)?(?:$|[\s_./\-\"\\`])"
                    )
                    if re.search(pattern, text_lower) or term in text_lower:
                        matches += 1

                token_ratio = matches / max(1, len(query_terms))

                # Exact phrase matching bonus
                phrase_bonus = 0.0
                if len(clean_lower) >= 3 and clean_lower in text_lower:
                    phrase_bonus = 0.35
                elif len(clean_lower) >= 3 and clean_lower in rec_id.lower():
                    phrase_bonus = 0.20

                # Security turn detection (firewall block / intercept)
                is_security_turn = any(
                    k in text_lower
                    for k in [
                        "safety firewall blocked",
                        "policy intercept",
                        "violation",
                        "security warning",
                        "blocked by deterministic policy",
                        "blocked by rule",
                        "credential_exfiltration",
                    ]
                )
                if is_issue_query and is_security_turn:
                    sec_bonus = 0.60
                    matches = max(matches, 1)
                else:
                    sec_bonus = 0.15 if (is_security_turn and security_query) else 0.0

                lexical_score = min(1.0, token_ratio * 0.70 + phrase_bonus + sec_bonus)

                # Dense vector cosine similarity
                chunk_emb = embed_svc.embed_text(chunk["text"])
                dense_score = cosine_similarity(query_emb, chunk_emb)

                # Hybrid fusion: 55% Dense Semantic + 45% Lexical
                combined = round(0.55 * dense_score + 0.45 * lexical_score, 3)

                # Admission threshold
                if (matches > 0 and combined >= 0.15) or dense_score >= 0.45:
                    if dense_score >= 0.45 and lexical_score >= 0.35:
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
            session_id_val = (
                getattr(rec, "session_id", None)
                or getattr(rec, "id", None)
                or getattr(rec, "run_id", "session")
            )
            project = (
                getattr(rec, "project_name", None)
                or getattr(rec, "task_id", "unnamed")
            )
            model_name = getattr(rec, "model", "unknown")
            text = cand["text"]
            turn = cand["turn"]

            # Locate best keyword/phrase match position for centering
            start_idx = 0
            phrase_idx = text.lower().find(clean_lower)
            if phrase_idx != -1:
                start_idx = max(0, phrase_idx - 30)
            else:
                for term in query_terms:
                    clean_term = term.lstrip(".-") or term
                    m = re.search(
                        r"(?:^|[\s_./\-\"\\`])" + re.escape(clean_term),
                        text,
                        re.IGNORECASE,
                    )
                    if m:
                        start_idx = max(0, m.start() - 30)
                        break

            # Format centered excerpt
            raw_excerpt = text[start_idx : start_idx + 220].replace("\n", " ").strip()
            raw_excerpt = re.sub(r"\s+", " ", raw_excerpt)
            if start_idx > 0 and len(text) > start_idx + 220:
                span = f"...{raw_excerpt}..."
            elif start_idx > 0:
                span = f"...{raw_excerpt}"
            elif len(text) > 220:
                span = f"{raw_excerpt}..."
            else:
                span = raw_excerpt

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
                    session_id=session_id_val,
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
