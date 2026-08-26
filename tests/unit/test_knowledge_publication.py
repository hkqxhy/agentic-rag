from __future__ import annotations

import pytest

from agentic_rag.knowledge.ingest import (
    PreparedChunk,
    _prepare_chunks,
    _validate_publication,
)
from agentic_rag.settings import Settings
from agentic_rag_v1.schema import KnowledgeChunk


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        environment="staging",
        knowledge_min_documents=1,
        knowledge_min_chunks=1,
    )


def test_staging_publication_accepts_active_official_nju_document() -> None:
    chunks = [
        KnowledgeChunk(
            id="campus-card-1",
            title="校园卡挂失与补办",
            source="/app/knowledge/official/campus-card.md",
            content="校园卡遗失后应立即挂失。",
            metadata={
                "document_id": "nju-campus-card",
                "authority_level": "official",
                "status": "active",
                "source_url": "https://itsc.nju.edu.cn/21469/listm.htm",
            },
        )
    ]

    prepared = _prepare_chunks(chunks)
    result = _validate_publication(_settings(), prepared)

    assert prepared[0].document_id == "nju-campus-card"
    assert result["document_count"] == 1
    assert result["active_chunk_count"] == 1
    assert result["authorities"] == ["official"]


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        (
            {
                "authority_level": "fixture",
                "status": "test_only",
                "source_url": "",
            },
            "authority=fixture",
        ),
        (
            {
                "authority_level": "official",
                "status": "active",
                "source_url": "https://example.com/not-official",
            },
            "invalid official source_url",
        ),
        (
            {
                "authority_level": "official",
                "status": "draft",
                "source_url": "https://www.nju.edu.cn/example",
            },
            "status=draft",
        ),
    ],
)
def test_staging_publication_rejects_unpublishable_sources(
    metadata: dict[str, str],
    message: str,
) -> None:
    prepared = [
        PreparedChunk(
            chunk=KnowledgeChunk(
                id="bad-1",
                source="/app/knowledge/fixtures/bad.md",
                content="不应发布",
                metadata=metadata,
            ),
            document_id="bad-document",
            chunk_index=0,
            content_hash="hash",
        )
    ]

    with pytest.raises(RuntimeError, match=message):
        _validate_publication(_settings(), prepared)
