from __future__ import annotations

from pathlib import Path

import pytest

from agentic_rag.agent.runtime import AgentRuntime
from agentic_rag.knowledge.dense import serialize_vector
from agentic_rag.knowledge.embedding import EmbeddingClient, EmbeddingError
from agentic_rag.knowledge.evaluate import corpus_case_coverage, load_retrieval_cases
from agentic_rag.settings import Settings
from agentic_rag_v1.advanced import AdvancedRetriever
from agentic_rag_v1.schema import KnowledgeChunk, SearchHit


def test_dense_hit_extends_lexical_candidates_and_semantic_sufficiency() -> None:
    semantic_chunk = KnowledgeChunk(
        id="campus-card-replacement",
        title="校园卡挂失补办",
        source="Documents/校园卡服务指南.md",
        content="校园卡遗失后先挂失，再携带身份证件前往服务点补办。",
        metadata={"kind": "document", "authority_level": "official"},
    )
    unrelated_chunk = KnowledgeChunk(
        id="course-selection",
        title="选课说明",
        source="Documents/选课说明.md",
        content="学生按照培养方案选择课程。",
        metadata={"kind": "document"},
    )
    dense_hits = [
        SearchHit(
            chunk=semantic_chunk,
            score=0.91,
            rank=1,
            signals={"dense_similarity": 0.91},
        )
    ]
    retriever = AdvancedRetriever([semantic_chunk, unrelated_chunk])

    hits = retriever.search(
        "饭卡弄丢以后去哪里重新弄一张？",
        top_k=2,
        dense_hits=dense_hits,
        dense_diagnostics={"mode": "hybrid", "status": "ok"},
    )

    assert hits[0].chunk.id == semantic_chunk.id
    assert hits[0].signals["dense_similarity"] == pytest.approx(0.91)
    assert retriever.last_diagnostics["mode"] == "hybrid_dense_sparse_rag"
    assert retriever.last_diagnostics["semantic_support"] > 0
    assert retriever.last_diagnostics["dense"]["status"] == "ok"


def test_embedding_response_validates_count_dimensions_and_finite_values() -> None:
    client = EmbeddingClient(
        base_url="https://example.test/v1",
        api_key="test-key",
        model="test-model",
        dimensions=3,
    )

    vectors = client._parse_response(  # noqa: SLF001 - validates provider boundary
        {"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]},
        expected=1,
    )
    assert vectors == [[0.1, 0.2, 0.3]]

    with pytest.raises(EmbeddingError, match="dimension mismatch"):
        client._parse_response(  # noqa: SLF001
            {"data": [{"index": 0, "embedding": [0.1, 0.2]}]},
            expected=1,
        )


def test_dense_mode_requires_embedding_credentials() -> None:
    with pytest.raises(ValueError, match="EMBEDDING_BASE_URL"):
        Settings(_env_file=None, dense_retrieval_mode="shadow")  # type: ignore[call-arg]

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        dense_retrieval_mode="hybrid",
        embedding_base_url="https://example.test/v1",
        embedding_api_key="test-key",
    )
    assert settings.embedding_dimensions == 1024


def test_embedding_dimensions_accepts_compose_environment_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTIC_RAG_EMBEDDING_DIMENSIONS", "1024")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.embedding_dimensions == 1024
    assert isinstance(settings.embedding_dimensions, int)


def test_vector_literal_is_stable_and_compact() -> None:
    assert serialize_vector([0.0, 0.125, -1.0]) == "[0,0.125,-1]"


def test_direct_and_clarification_routes_skip_dense_retrieval() -> None:
    assert AgentRuntime.requires_retrieval("你好") is False
    assert AgentRuntime.requires_retrieval("？") is False
    assert AgentRuntime.requires_retrieval("校园卡丢了怎么办") is True


def test_fixture_retrieval_suite_matches_sanitized_corpus(tmp_path: Path) -> None:
    cases, source = load_retrieval_cases(tmp_path, "fixture")
    chunks = [
        KnowledgeChunk(
            id="fixture-demo",
            title="Agentic RAG 演示知识",
            source="/app/knowledge/fixtures/demo_faq.md",
            content="这是测试资料。仓库不保存包含个人信息的原始文件。",
        )
    ]

    coverage = corpus_case_coverage(cases, chunks)

    assert source == "builtin:FIXTURE_RETRIEVAL_CASES"
    assert len(cases) == 4
    assert coverage["supported_case_count"] == 4
    assert coverage["coverage"] == 1.0


def test_formal_suite_reports_zero_corpus_coverage_for_fixture_only_corpus(
    tmp_path: Path,
) -> None:
    cases, _ = load_retrieval_cases(tmp_path, "smoke")
    chunks = [
        KnowledgeChunk(
            id="fixture-demo",
            source="/app/knowledge/fixtures/demo_faq.md",
            content="仅用于自动化测试。",
        )
    ]

    coverage = corpus_case_coverage(cases, chunks)

    assert coverage["supported_case_count"] == 0
    assert coverage["coverage"] == 0.0
    assert len(coverage["unsupported_case_ids"]) == len(cases)
