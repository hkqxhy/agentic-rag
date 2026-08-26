from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from agentic_rag.database import Database
from agentic_rag.settings import Settings
from agentic_rag_v1.schema import KnowledgeChunk, SearchHit

from .embedding import EmbeddingClient

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class DenseRetrievalResult:
    hits: list[SearchHit] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)


class DenseRetrievalService:
    """Generate a query embedding and retrieve active chunks from pgvector."""

    def __init__(
        self,
        settings: Settings,
        embedding_client: EmbeddingClient | None = None,
    ) -> None:
        self.settings = settings
        self.embedding_client = embedding_client or EmbeddingClient(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            timeout_seconds=settings.embedding_timeout_seconds,
        )

    async def search(self, query: str, database: Database) -> DenseRetrievalResult:
        mode = self.settings.dense_retrieval_mode
        if mode == "off":
            return DenseRetrievalResult(
                diagnostics={"mode": "off", "status": "disabled"}
            )

        started = time.perf_counter()
        try:
            embedding_started = time.perf_counter()
            query_vector = (await self.embedding_client.embed([query]))[0]
            embedding_ms = (time.perf_counter() - embedding_started) * 1000

            vector_started = time.perf_counter()
            vector_literal = serialize_vector(query_vector)
            async with database.session() as session:
                await session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
                result = await session.execute(
                    text(
                        """
                        SELECT
                            c.id,
                            c.content,
                            c.source,
                            c.title,
                            c.metadata,
                            1 - (e.embedding <=> CAST(:embedding AS vector)) AS similarity
                        FROM knowledge_embeddings AS e
                        JOIN knowledge_chunks AS c ON c.id = e.chunk_id
                        JOIN knowledge_documents AS d ON d.id = c.document_id
                        WHERE c.status = 'active'
                          AND d.status = 'active'
                          AND d.authority_level IN ('official', 'maintained')
                          AND e.embedding_model = :embedding_model
                          AND e.embedding_version = :embedding_version
                        ORDER BY e.embedding <=> CAST(:embedding AS vector)
                        LIMIT :candidate_k
                        """
                    ),
                    {
                        "embedding": vector_literal,
                        "embedding_model": self.settings.embedding_model,
                        "embedding_version": self.settings.embedding_version,
                        "candidate_k": self.settings.dense_candidate_k,
                    },
                )
                rows = result.mappings().all()
            vector_ms = (time.perf_counter() - vector_started) * 1000

            hits: list[SearchHit] = []
            for row in rows:
                similarity = float(row["similarity"])
                if similarity < self.settings.dense_min_similarity:
                    continue
                metadata = row["metadata"]
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)
                chunk = KnowledgeChunk(
                    id=str(row["id"]),
                    content=str(row["content"]),
                    source=str(row["source"]),
                    title=str(row["title"] or ""),
                    metadata=dict(metadata or {}),
                )
                hits.append(
                    SearchHit(
                        chunk=chunk,
                        score=similarity,
                        rank=len(hits) + 1,
                        signals={
                            "dense_similarity": similarity,
                            "dense_rank": float(len(hits) + 1),
                        },
                    )
                )

            diagnostics = {
                "mode": mode,
                "status": "ok",
                "embedding_model": self.settings.embedding_model,
                "embedding_version": self.settings.embedding_version,
                "candidate_count": len(hits),
                "top_similarity": round(hits[0].score, 4) if hits else 0.0,
                "top_chunk_ids": [hit.chunk.id for hit in hits[:5]],
                "embedding_latency_ms": round(embedding_ms, 2),
                "vector_query_latency_ms": round(vector_ms, 2),
                "total_latency_ms": round((time.perf_counter() - started) * 1000, 2),
            }
            return DenseRetrievalResult(hits=hits, diagnostics=diagnostics)
        except Exception as exc:
            LOGGER.warning("Dense retrieval degraded to lexical retrieval: %s", exc)
            return DenseRetrievalResult(
                diagnostics={
                    "mode": mode,
                    "status": "fallback",
                    "reason": type(exc).__name__,
                    "total_latency_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            )


def serialize_vector(vector: list[float]) -> str:
    return "[" + ",".join(format(value, ".9g") for value in vector) + "]"
