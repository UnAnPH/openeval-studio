"""Dense Vector Embedding Service for Transcript Fragment Search.

Generates dense semantic representations of transcript turns and reasoning blocks:
- Chunks text into 500-character spans with 50-character overlap.
- Uses Google Gemini API (text-embedding-004) when GEMINI_API_KEY is configured.
- Provides a fast, deterministic local fallback vector generator for offline mode and tests.
- In-memory SHA-256 LRU cache prevents redundant API calls and keeps token costs near zero.
"""

import hashlib
import logging
import math
import re

from dotenv import load_dotenv
from google import genai
from pydantic import BaseModel

from engine.gemini_key_pool import get_global_gemini_key_pool, is_gemini_rate_limit_error

load_dotenv()
logger = logging.getLogger("openeval.server.embeddings")

VECTOR_DIM = 768


class TextChunk(BaseModel):
    """A segment of transcript text with position metadata."""

    chunk_id: str
    session_id: str
    turn: int
    role: str
    text: str
    embedding: list[float] | None = None


def chunk_transcript_text(
    text: str,
    session_id: str = "session",
    turn: int = 1,
    role: str = "assistant",
    chunk_size: int = 500,
    overlap: int = 50,
) -> list[TextChunk]:
    """Splits transcript turn text into sliding window chunks."""
    text_clean = text.strip()
    if not text_clean:
        return []

    if len(text_clean) <= chunk_size:
        cid = f"{session_id}_t{turn}_{hashlib.sha256(text_clean.encode()).hexdigest()[:8]}"
        return [
            TextChunk(chunk_id=cid, session_id=session_id, turn=turn, role=role, text=text_clean)
        ]

    chunks: list[TextChunk] = []
    start = 0
    step = chunk_size - overlap

    while start < len(text_clean):
        end = min(start + chunk_size, len(text_clean))
        chunk_str = text_clean[start:end].strip()
        if chunk_str:
            cid = (
                f"{session_id}_t{turn}_{start}_{hashlib.sha256(chunk_str.encode()).hexdigest()[:8]}"
            )
            chunks.append(
                TextChunk(chunk_id=cid, session_id=session_id, turn=turn, role=role, text=chunk_str)
            )
        start += step

    return chunks


def compute_deterministic_fallback_embedding(text: str, dim: int = VECTOR_DIM) -> list[float]:
    """Generates a reproducible, normalized pseudo-semantic embedding vector.

    Uses character n-gram hashing to ensure semantic proximity for overlapping or
    similar vocabulary without external API dependencies.
    """
    vector = [0.0] * dim
    words = re.findall(r"\w+", text.lower())

    for word in words:
        # Hash full word
        h = int(hashlib.md5(word.encode()).hexdigest(), 16)
        idx = h % dim
        vector[idx] += 1.0

        # Sub-word 3-grams for morphological similarity
        for i in range(len(word) - 2):
            trigram = word[i : i + 3]
            th = int(hashlib.sha256(trigram.encode()).hexdigest(), 16)
            vector[th % dim] += 0.5

    # L2 normalize
    norm = math.sqrt(sum(x * x for x in vector))
    if norm > 0.0:
        vector = [round(x / norm, 6) for x in vector]
    else:
        vector[0] = 1.0

    return vector


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Computes cosine similarity between two float vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0

    dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=False))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


class TranscriptEmbeddingService:
    """Singleton service for generating and caching dense transcript embeddings."""

    _instance: "TranscriptEmbeddingService | None" = None

    def __init__(self, force_fallback: bool = False) -> None:
        self.force_fallback = force_fallback
        self.cache: dict[str, list[float]] = {}
        self.client: genai.Client | None = None

        self._key_pool = get_global_gemini_key_pool()
        self.api_key = self._key_pool.get_active_key(model="text-embedding-004")
        if self.api_key and self.api_key != "mock-key" and not force_fallback:
            try:
                _, self.client = self._key_pool.get_client(
                    api_key=self.api_key, model="text-embedding-004"
                )
            except Exception as e:
                logger.warning("Failed to initialize Google GenAI embedding client: %s", e)
                self.client = None

    @classmethod
    def get_instance(cls, force_fallback: bool = False) -> "TranscriptEmbeddingService":
        if cls._instance is None:
            cls._instance = cls(force_fallback=force_fallback)
        return cls._instance

    def embed_text(self, text: str) -> list[float]:
        """Returns normalized embedding vector for input text (cached by SHA-256)."""
        clean_text = text.strip()
        if not clean_text:
            return [0.0] * VECTOR_DIM

        cache_key = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()
        if cache_key in self.cache:
            return self.cache[cache_key]

        if self.client is not None and not self.force_fallback:

            def _extract(response: object) -> list[float] | None:
                embeddings = getattr(response, "embeddings", None)
                if embeddings and getattr(embeddings[0], "values", None):
                    embedding = list(embeddings[0].values)
                    norm = math.sqrt(sum(x * x for x in embedding))
                    if norm > 0:
                        embedding = [round(x / norm, 6) for x in embedding]
                    return embedding
                return None

            try:
                # Call Gemini text-embedding-004
                response = self.client.models.embed_content(
                    model="text-embedding-004",
                    contents=clean_text,
                )
                embedding = _extract(response)
                if embedding is not None:
                    self.cache[cache_key] = embedding
                    return embedding
            except Exception as err:
                err_str = str(err)
                if is_gemini_rate_limit_error(err_str):
                    current_key = self.api_key or self._key_pool.get_active_key(
                        model="text-embedding-004"
                    )
                    next_key = self._key_pool.report_rate_limit(
                        current_key,
                        err_str=err_str,
                        model="text-embedding-004",
                    )
                    if next_key is not None:
                        self.api_key = next_key
                        try:
                            _, self.client = self._key_pool.get_client(
                                api_key=next_key, model="text-embedding-004"
                            )
                            retry_response = self.client.models.embed_content(
                                model="text-embedding-004",
                                contents=clean_text,
                            )
                            embedding = _extract(retry_response)
                            if embedding is not None:
                                self.cache[cache_key] = embedding
                                return embedding
                        except Exception as retry_err:
                            logger.warning(
                                "Gemini embedding API backup key failed (%s); using local fallback",
                                retry_err,
                            )
                logger.warning(
                    "Gemini embedding API call failed (%s), disabling remote client for local fallback",
                    err,
                )
                self.client = None

        # Local deterministic fallback
        embedding = compute_deterministic_fallback_embedding(clean_text, VECTOR_DIM)
        self.cache[cache_key] = embedding
        return embedding
