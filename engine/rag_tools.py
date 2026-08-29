"""Dynamic Agentic RAG & Knowledge Retrieval Tools for OpenEval Studio.

Provides native Inspect AI @tool callables for document querying,
semantic knowledge lookup, and evidence retrieval with citations.
"""

from inspect_ai.tool import Tool, tool

# In-memory benchmark documentation repository
DEFAULT_KNOWLEDGE_BASE = {
    "doc_tls_openssl": {
        "title": "OpenSSL 3.0 TLS Certificate Management Manual",
        "content": (
            "To generate a self-signed ECDSA certificate with OpenSSL 3.0: "
            "1. Generate ecparam: openssl ecparam -name prime256v1 -genkey -noout -out key.pem\n"
            "2. Generate certificate: openssl req -new -x509 -key key.pem -out cert.pem "
            "-days 365 -subj '/CN=localhost'\n"
            "3. Extract SHA-256 fingerprint: openssl x509 -in cert.pem -noout -fingerprint -sha256"
        ),
    },
    "doc_async_cancellation": {
        "title": "Python Asyncio Concurrency & TaskGroup Cancellation Spec",
        "content": (
            "When using asyncio.TaskGroup in Python 3.11+, cancelling a parent task "
            "propagates CancellationError to all running child tasks. Cleanups in "
            "try/finally or async with context blocks are guaranteed to execute before "
            "the group exits."
        ),
    },
    "doc_sqlite_wal": {
        "title": "Optimistic Concurrency & SQLite WAL Isolation",
        "content": (
            "Under SQLite Write-Ahead Logging (WAL), readers do not block writers and "
            "writers do not block readers. "
            "Use BEGIN IMMEDIATE for transaction isolation to avoid SQLITE_BUSY deadlocks."
        ),
    },
}


@tool(name="knowledge_search")
def knowledge_search_tool(
    knowledge_base: dict[str, dict[str, str]] | None = None,
) -> Tool:
    """Search reference manuals and specifications for technical guidance."""
    kb = knowledge_base or DEFAULT_KNOWLEDGE_BASE

    async def execute(query: str) -> str:
        """Search the technical knowledge base for relevant manual sections.

        Args:
            query (str): The search query or technical keywords.
        """
        q_lower = query.lower().strip()
        matched_sections = []

        for doc_id, doc in kb.items():
            title = doc.get("title", "")
            content = doc.get("content", "")
            if q_lower in title.lower() or q_lower in content.lower():
                matched_sections.append(f"[{doc_id}] {title}\n{content.strip()}\n")

        if not matched_sections:
            # Return title list
            available = ", ".join(f"{k} ({v['title']})" for k, v in kb.items())
            return f"No exact matches for '{query}'. Available documents: {available}"

        return "\n---\n".join(matched_sections)

    return execute
