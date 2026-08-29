"""Grounding & Citation Judge for Agentic RAG in OpenEval Studio.

Evaluates whether model actions, reasoning, and final claims are strictly
grounded in retrieved documents and citations rather than ungrounded hallucinations.
"""

from inspect_ai.scorer import (
    CORRECT,
    INCORRECT,
    Score,
    Scorer,
    Target,
    accuracy,
    mean,
    scorer,
    stderr,
)
from inspect_ai.solver import TaskState


@scorer(metrics=[accuracy(), mean(), stderr()])
def grounding_and_citation_scorer() -> Scorer:
    """Inspect AI Scorer that checks citation and evidence grounding in transcript."""

    async def score(state: TaskState, target: Target) -> Score:
        messages = state.messages or []
        has_retrieval = False
        retrieved_doc_ids: set[str] = set()
        citations_found: set[str] = set()

        for msg in messages:
            # Check tool results for knowledge_search
            if getattr(msg, "role", None) == "tool":
                content_str = str(msg.content)
                if "[" in content_str and "]" in content_str:
                    has_retrieval = True
                    for part in content_str.split("["):
                        if "]" in part:
                            doc_id = part.split("]")[0]
                            if doc_id.startswith("doc_"):
                                retrieved_doc_ids.add(doc_id)

            # Check assistant messages for citations
            if getattr(msg, "role", None) == "assistant":
                content_str = str(msg.content)
                for doc_id in retrieved_doc_ids:
                    if doc_id in content_str:
                        citations_found.add(doc_id)

        # If knowledge was retrieved, ensure it was cited
        if has_retrieval:
            if citations_found:
                cite_str = ", ".join(citations_found)
                return Score(
                    value=CORRECT,
                    explanation=(
                        f"Grounded response: Model correctly cited {len(citations_found)} "
                        f"retrieved document(s) ({cite_str})."
                    ),
                    metadata={"citations": list(citations_found)},
                )
            else:
                return Score(
                    value=INCORRECT,
                    explanation=(
                        "Ungrounded reasoning: Model retrieved technical documents but failed "
                        "to ground or cite them in its actions."
                    ),
                )

        return Score(
            value=CORRECT,
            explanation="Task executed without external RAG retrieval requirement.",
        )

    return score
