from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import text

from agentic_rag.database import Database
from agentic_rag.settings import Settings, get_settings
from agentic_rag_v1.config import RAGConfig
from agentic_rag_v1.schema import KnowledgeChunk
from agentic_rag_v1.storage import load_or_build_chunks

from .dense import serialize_vector
from .embedding import EmbeddingClient


@dataclass(slots=True)
class PreparedChunk:
    chunk: KnowledgeChunk
    document_id: str
    chunk_index: int
    content_hash: str
    embedding: list[float] | None = None


async def ingest(
    settings: Settings,
    rag_config: RAGConfig,
    *,
    force: bool = False,
    dry_run: bool = False,
    batch_size: int = 10,
) -> dict[str, Any]:
    chunks = load_or_build_chunks(rag_config, force=force)
    if not chunks:
        raise RuntimeError("refusing to publish an empty knowledge index")

    prepared = _prepare_chunks(chunks)
    publication = _validate_publication(settings, prepared)
    database = Database(settings.database_url)
    embedder = EmbeddingClient(
        base_url=settings.embedding_base_url,
        api_key=settings.embedding_api_key,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.embedding_timeout_seconds,
    )
    try:
        existing = await _existing_embeddings(database, settings)
        embedding_enabled = settings.dense_retrieval_mode != "off"
        pending = (
            [
                item
                for item in prepared
                if force or existing.get(item.chunk.id) != item.content_hash
            ]
            if embedding_enabled
            else []
        )
        if dry_run:
            return {
                "documents": len({item.document_id for item in prepared}),
                "chunks": len(prepared),
                "embeddings_to_generate": len(pending),
                "embedding_enabled": embedding_enabled,
                "publication": publication,
                "dry_run": True,
            }

        for offset in range(0, len(pending), batch_size):
            batch = pending[offset : offset + batch_size]
            vectors = await embedder.embed([item.chunk.content for item in batch])
            for item, vector in zip(batch, vectors, strict=True):
                item.embedding = vector

        await _publish(database, settings, prepared)
        return {
            "documents": len({item.document_id for item in prepared}),
            "chunks": len(prepared),
            "embeddings_generated": len(pending),
            "embeddings_reused": len(prepared) - len(pending),
            "embedding_enabled": embedding_enabled,
            "embedding_model": settings.embedding_model if embedding_enabled else "disabled",
            "embedding_version": (
                settings.embedding_version if embedding_enabled else "disabled"
            ),
            "publication": publication,
            "dry_run": False,
        }
    finally:
        await database.close()


def _prepare_chunks(chunks: list[KnowledgeChunk]) -> list[PreparedChunk]:
    source_indexes: defaultdict[str, int] = defaultdict(int)
    prepared: list[PreparedChunk] = []
    for chunk in chunks:
        index = source_indexes[chunk.source]
        source_indexes[chunk.source] += 1
        prepared.append(
            PreparedChunk(
                chunk=chunk,
                document_id=str(
                    chunk.metadata.get("document_id") or _stable_id(chunk.source)
                ),
                chunk_index=index,
                content_hash=hashlib.sha256(chunk.content.encode("utf-8")).hexdigest(),
            )
        )
    return prepared


async def _existing_embeddings(
    database: Database,
    settings: Settings,
) -> dict[str, str]:
    async with database.session() as session:
        result = await session.execute(
            text(
                """
                SELECT chunk_id, content_hash
                FROM knowledge_embeddings
                WHERE embedding_model = :model AND embedding_version = :version
                """
            ),
            {"model": settings.embedding_model, "version": settings.embedding_version},
        )
        return {str(row.chunk_id): str(row.content_hash) for row in result}


async def _publish(
    database: Database,
    settings: Settings,
    prepared: list[PreparedChunk],
) -> None:
    documents: defaultdict[str, list[PreparedChunk]] = defaultdict(list)
    for item in prepared:
        documents[item.document_id].append(item)

    async with database.session() as session:
        await session.execute(text("UPDATE knowledge_chunks SET status = 'archived'"))
        await session.execute(
            text("UPDATE knowledge_documents SET status = 'archived' WHERE status = 'active'")
        )
        for document_id, items in documents.items():
            first = items[0].chunk
            checksum = hashlib.sha256(
                "\n".join(item.content_hash for item in items).encode("utf-8")
            ).hexdigest()
            await session.execute(
                text(
                    """
                    INSERT INTO knowledge_documents (
                        id, source_uri, title, status, authority_level, checksum, metadata
                    ) VALUES (
                        :id, :source_uri, :title, :status, :authority, :checksum,
                        CAST(:metadata AS jsonb)
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        source_uri = EXCLUDED.source_uri,
                        title = EXCLUDED.title,
                        status = EXCLUDED.status,
                        authority_level = EXCLUDED.authority_level,
                        checksum = EXCLUDED.checksum,
                        metadata = EXCLUDED.metadata,
                        updated_at = now()
                    """
                ),
                {
                    "id": document_id,
                    "source_uri": str(
                        first.metadata.get("source_url") or first.source
                    ),
                    "title": first.title or Path(first.source).name,
                    "status": _document_status(first),
                    "authority": _authority_level(first),
                    "checksum": f"sha256:{checksum}",
                    "metadata": json.dumps(
                        {
                            **first.metadata,
                            "local_source": first.source,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                },
            )

        for item in prepared:
            chunk = item.chunk
            await session.execute(
                text(
                    """
                    INSERT INTO knowledge_chunks (
                        id, document_id, chunk_index, content, title, source,
                        metadata, content_hash, status
                    ) VALUES (
                        :id, :document_id, :chunk_index, :content, :title, :source,
                        CAST(:metadata AS jsonb), :content_hash, 'active'
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        document_id = EXCLUDED.document_id,
                        chunk_index = EXCLUDED.chunk_index,
                        content = EXCLUDED.content,
                        title = EXCLUDED.title,
                        source = EXCLUDED.source,
                        metadata = EXCLUDED.metadata,
                        content_hash = EXCLUDED.content_hash,
                        status = 'active',
                        updated_at = now()
                    """
                ),
                {
                    "id": chunk.id,
                    "document_id": item.document_id,
                    "chunk_index": item.chunk_index,
                    "content": chunk.content,
                    "title": chunk.title,
                    "source": chunk.source,
                    "metadata": json.dumps(chunk.metadata, ensure_ascii=False, sort_keys=True),
                    "content_hash": item.content_hash,
                },
            )
            if item.embedding is not None:
                await session.execute(
                    text(
                        """
                        INSERT INTO knowledge_embeddings (
                            chunk_id, embedding_model, embedding_version,
                            dimensions, content_hash, embedding
                        ) VALUES (
                            :chunk_id, :model, :version, :dimensions, :content_hash,
                            CAST(:embedding AS vector)
                        )
                        ON CONFLICT (chunk_id, embedding_model, embedding_version)
                        DO UPDATE SET
                            dimensions = EXCLUDED.dimensions,
                            content_hash = EXCLUDED.content_hash,
                            embedding = EXCLUDED.embedding,
                            created_at = now()
                        """
                    ),
                    {
                        "chunk_id": chunk.id,
                        "model": settings.embedding_model,
                        "version": settings.embedding_version,
                        "dimensions": settings.embedding_dimensions,
                        "content_hash": item.content_hash,
                        "embedding": serialize_vector(item.embedding),
                    },
                )
        await session.commit()


def _validate_publication(
    settings: Settings,
    prepared: list[PreparedChunk],
) -> dict[str, Any]:
    document_ids = {item.document_id for item in prepared}
    active_chunks = [
        item for item in prepared
        if _document_status(item.chunk) == "active"
    ]
    authorities = sorted({_authority_level(item.chunk) for item in prepared})
    result = {
        "document_count": len(document_ids),
        "chunk_count": len(prepared),
        "active_chunk_count": len(active_chunks),
        "authorities": authorities,
    }
    if settings.environment not in {"staging", "production"}:
        return result

    invalid: list[str] = []
    for item in prepared:
        chunk = item.chunk
        authority = _authority_level(chunk)
        status = _document_status(chunk)
        source_url = str(chunk.metadata.get("source_url") or "")
        if authority not in {"official", "maintained"}:
            invalid.append(f"{chunk.source}: authority={authority}")
        if status != "active":
            invalid.append(f"{chunk.source}: status={status}")
        if authority == "official" and not _is_trusted_official_url(source_url):
            invalid.append(f"{chunk.source}: invalid official source_url")

    if len(document_ids) < settings.knowledge_min_documents:
        invalid.append(
            "document gate failed: "
            f"{len(document_ids)} < {settings.knowledge_min_documents}"
        )
    if len(prepared) < settings.knowledge_min_chunks:
        invalid.append(
            "chunk gate failed: "
            f"{len(prepared)} < {settings.knowledge_min_chunks}"
        )
    if invalid:
        detail = "; ".join(dict.fromkeys(invalid))
        raise RuntimeError(f"knowledge publication rejected: {detail}")
    return result


def _is_trusted_official_url(value: str) -> bool:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").casefold()
    return (
        parsed.scheme == "https"
        and bool(hostname)
        and (hostname == "nju.edu.cn" or hostname.endswith(".nju.edu.cn"))
    )


def _stable_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _authority_level(chunk: KnowledgeChunk) -> str:
    explicit = str(chunk.metadata.get("authority_level", ""))
    if explicit in {"official", "maintained", "community", "opinion", "fixture"}:
        return explicit
    source = chunk.source.replace("\\", "/").lower()
    if "fixture" in source:
        return "fixture"
    if "/documents/" in f"/{source}":
        return "maintained"
    if "/qq/" in f"/{source}":
        return "community"
    return "maintained"


def _document_status(chunk: KnowledgeChunk) -> str:
    explicit = str(chunk.metadata.get("status", ""))
    if explicit in {"draft", "active", "stale", "archived", "rejected", "test_only"}:
        return explicit
    return "test_only" if _authority_level(chunk) == "fixture" else "active"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and publish the pgvector knowledge index")
    parser.add_argument("--force", action="store_true", help="regenerate all embeddings")
    parser.add_argument("--dry-run", action="store_true", help="show changes without API calls")
    parser.add_argument("--batch-size", type=int, default=10, choices=range(1, 11))
    return parser


async def _main_async() -> None:
    args = _parser().parse_args()
    settings = get_settings()
    result = await ingest(
        settings,
        RAGConfig.from_env(Path.cwd()),
        force=args.force,
        dry_run=args.dry_run,
        batch_size=args.batch_size,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
