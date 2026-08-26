from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from agentic_rag_v1.advanced import AdvancedRetriever
from agentic_rag_v1.config import RAGConfig
from agentic_rag_v1.evaluate import (
    VariantResult,
    evaluate_gate,
    load_eval_cases,
    summarize_failures,
)
from agentic_rag_v1.graph import GraphRAGRetriever
from agentic_rag_v1.loaders import load_directory_overviews, load_sources
from agentic_rag_v1.retrieval import Retriever
from agentic_rag_v1.schema import KnowledgeChunk
from agentic_rag_v1.service import NewStudentAssistant


class AgenticRAGV1Tests(unittest.TestCase):
    def test_config_uses_official_corpus_when_raw_sources_are_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            official_dir = root / "knowledge" / "official"
            official_dir.mkdir(parents=True)

            config = RAGConfig(root=root)

        self.assertEqual(config.source_paths, [official_dir])

    def test_config_loads_env_local(self) -> None:
        keys = ["AGENTIC_RAG_LLM_BASE_URL", "AGENTIC_RAG_LLM_MODEL", "AGENTIC_RAG_LLM_API_KEY"]
        previous = {key: os.environ.pop(key, None) for key in keys}
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                (root / ".env.local").write_text(
                    "\n".join(
                        [
                            "AGENTIC_RAG_LLM_BASE_URL=https://example.test/v1",
                            "AGENTIC_RAG_LLM_MODEL=qwen-plus",
                            "AGENTIC_RAG_LLM_API_KEY=test-key",
                        ]
                    ),
                    encoding="utf-8",
                )
                config = RAGConfig.from_env(root)

            self.assertEqual(config.llm_base_url, "https://example.test/v1")
            self.assertEqual(config.llm_model, "qwen-plus")
            self.assertEqual(config.llm_api_key, "test-key")
        finally:
            for key in keys:
                os.environ.pop(key, None)
                if previous[key] is not None:
                    os.environ[key] = previous[key]

    def test_loads_supported_qa_formats(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            conversation_file = root / "conversation.qa"
            group_file = root / "group.qa"
            conversation_file.write_text(
                json.dumps(
                    [
                        {
                            "id": "identity_1",
                            "conversations": [
                                {"from": "user", "value": "忘记统一身份认证密码"},
                                {"from": "assistant", "value": "可以通过密保手机号找回；未设置则联系老师重置。"},
                            ],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            group_file.write_text(
                json.dumps(
                    [
                        {
                            "Q": "入学体检要带什么？",
                            "A": "请携带校园卡和身份证，按预约时间前往校医院。",
                            "type": "新生入学准备",
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            chunks = load_sources([conversation_file, group_file])

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].metadata["kind"], "qa")
        self.assertIn("统一身份认证", chunks[0].content)
        self.assertIn("新生入学准备", chunks[1].content)

    def test_builds_directory_overview_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            docs = root / "Documents" / "南大社团介绍" / "艺术表演类"
            docs.mkdir(parents=True)
            (docs / "CAC动漫社.txt").write_text("动漫社介绍", encoding="utf-8")
            (docs / "古琴社.txt").write_text("古琴社介绍", encoding="utf-8")

            chunks = load_directory_overviews([root / "Documents"])

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].metadata["kind"], "directory")
        self.assertIn("CAC动漫社", chunks[0].content)
        self.assertIn("南大社团介绍", chunks[0].metadata["category_path"])

    def test_retriever_finds_new_student_answer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            qa_file = root / "qa.qa"
            qa_file.write_text(
                json.dumps(
                    [
                        {
                            "Q": "忘记统一身份认证密码怎么办？",
                            "A": "先尝试通过密保手机号或邮箱找回；未设置密保时联系学校工作人员重置。",
                            "type": "账号服务",
                        },
                        {
                            "Q": "校园卡怎么充值？",
                            "A": "可以通过南京大学信息门户的校园卡服务进行充值。",
                            "type": "校园生活服务",
                        },
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            chunks = load_sources([qa_file])

        hits = Retriever(chunks).search("统一身份认证密码忘了", top_k=1)
        self.assertEqual(len(hits), 1)
        self.assertIn("密保", hits[0].chunk.answer)

    def test_advanced_retriever_reports_evidence_diagnostics(self) -> None:
        chunks = [
            KnowledgeChunk(
                id="official-network",
                title="Campus network router guide",
                source="Documents/AAA需增添水印的新文件/校园网相关/南大宿舍校园网与路由器使用指南.pdf",
                content=(
                    "NJU-WLAN router login guide. Visit p.nju.edu.cn for campus "
                    "network authentication and router setup steps."
                ),
                metadata={"kind": "pdf", "category_path": "Documents/校园网相关"},
            ),
            KnowledgeChunk(
                id="chat-random",
                title="Food chat",
                source="QQ/random.qa",
                content="Students discussed lunch and weekend activities.",
                metadata={"kind": "qa", "category_path": "QQ"},
            ),
        ]

        retriever = AdvancedRetriever(chunks)
        hits = retriever.search("How to use NJU-WLAN router p.nju.edu.cn?", top_k=1)

        self.assertEqual(hits[0].chunk.id, "official-network")
        self.assertGreaterEqual(retriever.last_diagnostics["query_variant_count"], 2)
        self.assertEqual(retriever.last_diagnostics["mode"], "advanced_rag")
        self.assertIn("authority", hits[0].signals)

    def test_graphrag_retriever_adds_graph_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            chunks = [
                KnowledgeChunk(
                    id="club-art",
                    title="Art Club Guide",
                    source="Documents/Clubs/Art/art-club.txt",
                    content="Art club, music club, and drama club welcome new students.",
                    metadata={"kind": "document", "category_path": "Documents/Clubs/Art"},
                ),
                KnowledgeChunk(
                    id="club-tech",
                    title="Tech Club Guide",
                    source="Documents/Clubs/Tech/linux-club.txt",
                    content="Linux club and robotics club hold weekly workshops.",
                    metadata={"kind": "document", "category_path": "Documents/Clubs/Tech"},
                ),
            ]

            retriever = GraphRAGRetriever(chunks, index_dir=root, use_cache=False)
            hits = retriever.search("list overview of clubs for students", top_k=2)

        self.assertEqual(retriever.last_diagnostics["mode"], "graph_rag")
        self.assertGreater(retriever.last_diagnostics["graph"]["communities"], 0)
        self.assertTrue(hits)
        self.assertTrue(any(hit.chunk.metadata.get("kind") == "graph_community" for hit in hits))

    def test_assistant_answers_without_llm(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            qa_file = root / "qa.qa"
            qa_file.write_text(
                json.dumps(
                    [
                        {
                            "Q": "入学体检有什么注意事项？",
                            "A": "体检前一日注意休息、清淡饮食，体检当日需空腹抽血。",
                            "type": "新生入学准备",
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            config = RAGConfig(
                root=root,
                source_paths=[qa_file],
                index_dir=root / ".agentic_rag_v1_index",
                use_cache=False,
                min_confidence=0.05,
            )
            assistant = NewStudentAssistant(config)
            result = assistant.ask("新生体检当天需要注意什么？")

        self.assertGreater(result.confidence, 0)
        self.assertIn("空腹抽血", result.answer)
        self.assertTrue(result.sources)
        self.assertEqual(result.diagnostics.get("mode"), "graph_rag")

    def test_loads_external_eval_cases_from_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case_dir = root / "eval" / "cases"
            case_dir.mkdir(parents=True)
            (case_dir / "smoke.jsonl").write_text(
                "\n".join(
                    [
                        "# comments are allowed",
                        json.dumps(
                            {
                                "id": "identity_password",
                                "question": "统一身份认证密码忘了怎么办？",
                                "expected_sources": ["南哪QA.qa"],
                                "expected_keywords": ["密保", "重置"],
                                "category": "账号服务",
                                "tags": ["smoke"],
                            },
                            ensure_ascii=False,
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            cases, source = load_eval_cases(root, suite="smoke")

        self.assertEqual(source.endswith("smoke.jsonl"), True)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["id"], "identity_password")
        self.assertEqual(cases[0]["tags"], ["smoke"])

    def test_summarize_failures_lists_missed_keywords(self) -> None:
        cases = [
            {
                "id": "clubs_overview",
                "question": "南大有哪些社团可以参加？",
                "expected_sources": ["南大社团介绍"],
                "expected_keywords": ["CAC动漫社", "行远社", "艺术表演类"],
                "category": "校园生活",
            }
        ]
        result = VariantResult(
            variant="full_kb_graphrag",
            case_id="clubs_overview",
            question="南大有哪些社团可以参加？",
            answer="可以参考 CAC动漫社 和 行远社。",
            latency_ms=12.5,
            confidence=0.8,
            top1_hit=False,
            top3_hit=True,
            keyword_coverage=2 / 3,
            matched_keywords=["CAC动漫社", "行远社"],
            sources=[
                {
                    "title": "社团资料目录",
                    "source": "Documents/南大社团介绍",
                    "score": 0.9,
                }
            ],
        )

        failures = summarize_failures([result], cases, "full_kb_graphrag")

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["missed_keywords"], ["艺术表演类"])
        self.assertFalse(failures[0]["top1_hit"])

    def test_evaluate_gate_fails_thresholds(self) -> None:
        payload = {
            "summary": {
                "full_kb_graphrag": {
                    "top1_hit_rate": 0.9,
                    "top3_hit_rate": 0.95,
                    "keyword_coverage": 0.8,
                    "avg_confidence": 0.7,
                    "avg_latency_ms": 5100.0,
                }
            }
        }

        gate = evaluate_gate(
            payload,
            gate_variant="full_kb_graphrag",
            min_top3=1.0,
            min_keyword_coverage=0.9,
            max_avg_latency_ms=4000,
        )

        self.assertFalse(gate["passed"])
        self.assertEqual(len(gate["failures"]), 3)


if __name__ == "__main__":
    unittest.main()
