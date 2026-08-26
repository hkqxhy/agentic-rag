from __future__ import annotations

import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from agentic_rag.agent import AgentRuntime
from agentic_rag.agent.evaluate import run_effect_evaluation
from agentic_rag_v1.config import RAGConfig
from agentic_rag_v1.llm import OpenAICompatibleLLM
from agentic_rag_v1.schema import KnowledgeChunk, SearchHit
from agentic_rag_v1.service import (
    _filter_unsupported_suffix,
    _unsupported_channels,
    _unsupported_urls,
)


def test_official_effect_suite_passes_without_model_cost(tmp_path: Path) -> None:
    official = tmp_path / "knowledge" / "official"
    official.mkdir(parents=True)
    document = official / "student-services.md"
    document.write_text(
        """---
document_id: official-student-services
title: 校园卡与统一身份认证
authority_level: official
status: active
source_url: https://itsc.nju.edu.cn/21469/listm.htm
---

# 校园卡与统一身份认证

校园卡弄丢后应先挂失，再携带有效身份证件按官方流程补办。

统一身份认证密码忘记后，应在认证页面选择忘记密码，通过绑定的手机号或邮箱重置。
""",
        encoding="utf-8",
    )
    runtime = AgentRuntime(
        RAGConfig(
            root=tmp_path,
            source_paths=[official],
            index_dir=tmp_path / "index",
            use_cache=False,
            use_graphrag=False,
            min_confidence=0.01,
            llm_base_url="",
            allow_test_knowledge=False,
        )
    )

    payload = run_effect_evaluation(runtime, require_llm=False)

    assert payload["passed"] is True
    assert payload["summary"]["route_accuracy"] == 1.0
    assert payload["summary"]["grounding_accuracy"] == 1.0
    assert payload["summary"]["citation_accuracy"] == 1.0
    assert payload["summary"]["llm_success_rate"] == 0.0


def test_model_gateway_reports_safe_http_failure_reason() -> None:
    gateway = OpenAICompatibleLLM(
        base_url="https://example.invalid/compatible-mode/v1",
        api_key="secret-must-not-leak",
        model="qwen-plus",
    )
    failure = urllib.error.HTTPError(
        gateway.base_url,
        401,
        "Unauthorized",
        hdrs=Message(),
        fp=None,
    )

    with patch("urllib.request.urlopen", side_effect=failure):
        answer = gateway.chat([{"role": "user", "content": "hello"}])

    assert answer is None
    assert gateway.last_error == "http_401"
    assert "secret" not in gateway.last_error


def test_unsupported_model_urls_are_detected() -> None:
    hit = SearchHit(
        chunk=KnowledgeChunk(
            id="source-1",
            content="唯一允许的入口是 https://example.edu.cn/notice 。",
            source="fixture.md",
        ),
        score=1.0,
        rank=1,
    )

    assert _unsupported_urls("请访问 https://example.edu.cn/notice [S1]", [hit]) == []
    assert _unsupported_urls("请访问 https://invented.example/ [S1]", [hit]) == [
        "https://invented.example/"
    ]


def test_unsupported_official_channels_are_detected() -> None:
    hit = SearchHit(
        chunk=KnowledgeChunk(
            id="source-1",
            content="资料不足时请以当年官方通知为准。",
            source="fixture.md",
        ),
        score=1.0,
        rank=1,
    )

    assert _unsupported_channels("请查看本科招生网或迎新系统 [S1]", [hit]) == [
        "本科招生网",
        "迎新系统",
    ]


def test_unsupported_recommendation_suffix_is_safely_truncated() -> None:
    hit = SearchHit(
        chunk=KnowledgeChunk(
            id="source-1",
            content="这是测试资料，不代表真实通知。",
            source="fixture.md",
        ),
        score=1.0,
        rank=1,
    )
    answer = (
        "这只是测试资料，不代表真实通知 [S1]。\n\n"
        "温馨提示：\n请访问本科招生网了解详情。"
    )

    filtered, reason = _filter_unsupported_suffix(answer, [hit])

    assert filtered == "这只是测试资料，不代表真实通知 [S1]。"
    assert reason == "unsupported_channel"
