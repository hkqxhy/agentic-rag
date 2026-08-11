from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any

from agentic_rag.database import Database
from agentic_rag.settings import get_settings
from agentic_rag_v1.advanced import AdvancedRetriever
from agentic_rag_v1.config import RAGConfig
from agentic_rag_v1.evaluate import load_eval_cases, source_hit, validate_eval_cases
from agentic_rag_v1.schema import KnowledgeChunk, SearchHit
from agentic_rag_v1.storage import load_or_build_chunks

from .dense import DenseRetrievalService

FIXTURE_RETRIEVAL_CASES: list[dict[str, Any]] = [
    {
        "id": "fixture_notice_exact",
        "question": "这是真实的学校通知吗？",
        "expected_sources": ["demo_faq.md"],
        "expected_keywords": ["测试资料", "正式资料"],
        "category": "fixture",
    },
    {
        "id": "fixture_notice_paraphrase",
        "question": "这份入学安排可以当成学校正式发布的通知吗？",
        "expected_sources": ["demo_faq.md"],
        "expected_keywords": ["测试资料", "学校政策"],
        "category": "fixture",
    },
    {
        "id": "fixture_privacy_exact",
        "question": "为什么仓库里没有原始知识库？",
        "expected_sources": ["demo_faq.md"],
        "expected_keywords": ["个人信息", "受控"],
        "category": "fixture",
    },
    {
        "id": "fixture_privacy_paraphrase",
        "question": "代码库为什么不直接保存聊天记录和完整资料？",
        "expected_sources": ["demo_faq.md"],
        "expected_keywords": ["个人信息", "源代码仓库"],
        "category": "fixture",
    },
]


async def evaluate(
    suite: str,
    *,
    top_k: int = 5,
    output: Path | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if settings.dense_retrieval_mode == "off":
        raise RuntimeError("set AGENTIC_RAG_DENSE_RETRIEVAL_MODE=shadow or hybrid")

    rag_config = RAGConfig.from_env(Path.cwd())
    chunks = load_or_build_chunks(rag_config)
    cases, case_source = load_retrieval_cases(rag_config.root, suite)
    corpus_coverage = corpus_case_coverage(cases, chunks)
    if corpus_coverage["supported_case_count"] == 0:
        raise RuntimeError(
            f"evaluation suite '{suite}' has no expected sources in the loaded corpus; "
            "use --suite fixture for the repository fixture, or load the formal knowledge "
            "sources required by this suite"
        )
    baseline = AdvancedRetriever(chunks)
    hybrid = AdvancedRetriever(
        chunks,
        dense_rrf_weight=settings.dense_rrf_weight,
        dense_min_similarity=settings.dense_min_similarity,
    )
    database = Database(settings.database_url)
    dense_service = DenseRetrievalService(settings)
    rows: list[dict[str, Any]] = []
    try:
        for case in cases:
            question = str(case["question"])
            dense_result = await dense_service.search(question, database)
            baseline_started = time.perf_counter()
            baseline_hits = baseline.search(question, top_k=top_k)
            baseline_ms = (time.perf_counter() - baseline_started) * 1000
            hybrid_started = time.perf_counter()
            hybrid_hits = hybrid.search(
                question,
                top_k=top_k,
                dense_hits=dense_result.hits,
                dense_diagnostics=dense_result.diagnostics,
            )
            hybrid_ms = (time.perf_counter() - hybrid_started) * 1000
            hybrid_e2e_ms = hybrid_ms + float(
                dense_result.diagnostics.get("total_latency_ms", 0.0)
            )
            rows.extend(
                [
                    _case_row("lexical_advanced", case, baseline_hits, baseline_ms),
                    _case_row(
                        "dense_only",
                        case,
                        dense_result.hits[:top_k],
                        float(dense_result.diagnostics.get("total_latency_ms", 0.0)),
                    ),
                    _case_row("hybrid_pgvector", case, hybrid_hits, hybrid_e2e_ms),
                ]
            )
    finally:
        await database.close()

    payload = {
        "suite": suite,
        "case_source": case_source,
        "case_count": len(cases),
        "embedding_model": settings.embedding_model,
        "embedding_version": settings.embedding_version,
        "corpus_coverage": corpus_coverage,
        "summary": summarize(rows),
        "results": rows,
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def load_retrieval_cases(root: Path, suite: str) -> tuple[list[dict[str, Any]], str]:
    if suite == "fixture":
        return validate_eval_cases(FIXTURE_RETRIEVAL_CASES), "builtin:FIXTURE_RETRIEVAL_CASES"
    return load_eval_cases(root, suite=suite)


def corpus_case_coverage(
    cases: list[dict[str, Any]],
    chunks: list[KnowledgeChunk],
) -> dict[str, Any]:
    corpus_sources = [
        {
            "source": chunk.source,
            "title": chunk.title,
            "metadata": chunk.metadata,
            "content": chunk.content,
        }
        for chunk in chunks
    ]
    supported_ids = [
        str(case["id"])
        for case in cases
        if source_hit(corpus_sources, list(case["expected_sources"]))
    ]
    supported = set(supported_ids)
    unsupported_ids = [str(case["id"]) for case in cases if str(case["id"]) not in supported]
    return {
        "case_count": len(cases),
        "supported_case_count": len(supported_ids),
        "coverage": round(len(supported_ids) / max(1, len(cases)), 4),
        "unsupported_case_ids": unsupported_ids,
    }


def _case_row(
    variant: str,
    case: dict[str, Any],
    hits: list[SearchHit],
    latency_ms: float,
) -> dict[str, Any]:
    sources = [hit.to_source(f"S{index}") for index, hit in enumerate(hits, start=1)]
    expected = list(case["expected_sources"])
    reciprocal_rank = 0.0
    for rank in range(1, len(sources) + 1):
        if source_hit(sources[rank - 1 : rank], expected):
            reciprocal_rank = 1.0 / rank
            break
    return {
        "variant": variant,
        "case_id": case["id"],
        "question": case["question"],
        "recall_at_1": source_hit(sources[:1], expected),
        "recall_at_3": source_hit(sources[:3], expected),
        "recall_at_5": source_hit(sources[:5], expected),
        "reciprocal_rank": reciprocal_rank,
        "latency_ms": round(latency_ms, 2),
        "top_sources": [source["source"] for source in sources[:5]],
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["variant"]), []).append(row)
    return {
        variant: {
            "recall_at_1": _mean(items, "recall_at_1"),
            "recall_at_3": _mean(items, "recall_at_3"),
            "recall_at_5": _mean(items, "recall_at_5"),
            "mrr": _mean(items, "reciprocal_rank"),
            "avg_latency_ms": _mean(items, "latency_ms"),
        }
        for variant, items in grouped.items()
    }


def _mean(items: list[dict[str, Any]], key: str) -> float:
    return round(statistics.mean(float(item[key]) for item in items), 4)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare lexical, dense, and hybrid retrieval")
    parser.add_argument("--suite", default="smoke")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    payload = asyncio.run(evaluate(args.suite, top_k=args.top_k, output=args.output))
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
