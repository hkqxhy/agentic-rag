from __future__ import annotations

from pathlib import Path

from agentic_rag.agent import AgentRuntime
from agentic_rag_v1.config import RAGConfig


def _runtime(tmp_path: Path) -> AgentRuntime:
    fixture = tmp_path / "knowledge.md"
    fixture.write_text(
        "# 测试知识\n\n为什么仓库里没有原始知识库？\n\n"
        "原始资料可能包含个人信息和历史政策，因此生产环境通过受控摄取接口维护。\n",
        encoding="utf-8",
    )
    return AgentRuntime(
        RAGConfig(
            root=tmp_path,
            source_paths=[fixture],
            index_dir=tmp_path / "index",
            use_cache=False,
            use_graphrag=False,
            min_confidence=0.01,
        )
    )


def test_langgraph_direct_route_is_bounded(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    outcome = runtime.invoke("你好", "conversation-1")

    assert outcome.route == "direct"
    assert outcome.grounded is True
    assert not outcome.sources
    assert [step["node"] for step in outcome.trace] == [
        "normalize",
        "classify",
        "direct",
        "verify",
    ]


def test_langgraph_rag_route_returns_sources_and_trace(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    outcome = runtime.invoke("为什么仓库里没有原始知识库？", "conversation-2")

    assert outcome.route == "research_rag"
    assert outcome.sources
    assert outcome.grounded is True
    assert "[S1]" in outcome.answer
    assert [step["node"] for step in outcome.trace] == [
        "normalize",
        "classify",
        "rag",
        "verify",
    ]


def test_agent_metadata_is_json_safe(tmp_path: Path) -> None:
    outcome = _runtime(tmp_path).invoke("为什么仓库里没有原始知识库？", "conversation-3")
    metadata = outcome.message_metadata()

    assert metadata["agent"]["framework"] == "langgraph"
    assert metadata["agent"]["graph_version"] == "phase2.1"
    assert metadata["sources"]


def test_low_quality_retrieval_is_not_marked_grounded(tmp_path: Path) -> None:
    outcome = _runtime(tmp_path).invoke("量子计算机如何制冷？", "conversation-4")

    assert outcome.route == "fast_rag"
    assert outcome.need_clarification is True
    assert outcome.grounded is False
    assert "没有找到足够可靠的依据" in outcome.answer
