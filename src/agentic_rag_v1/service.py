from __future__ import annotations

import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Iterator

from .advanced import AdvancedRetriever
from .config import RAGConfig
from .graph import GraphRAGRetriever
from .llm import OpenAICompatibleLLM
from .schema import AnswerResult, KnowledgeChunk, SearchHit
from .storage import load_or_build_chunks
from .text import extract_urls, normalize_text


INTENT_KEYWORDS: dict[str, set[str]] = {
    "identity": {"统一身份认证", "学号", "密码", "信息门户", "南大APP", "网上办事大厅"},
    "campus_card": {"校园卡", "饭卡", "充值", "挂失", "补卡", "照片"},
    "admission": {"新生", "入学", "报到", "报道", "通知书", "迎新", "行李"},
    "dorm": {"宿舍", "住宿", "床", "床铺", "床帘", "空调"},
    "medical": {"医保", "校医院", "体检", "社保卡", "疫苗", "报销"},
    "network": {"校园网", "无感知认证", "网费", "vpn", "上网"},
    "course": {"选课", "补考", "缓考", "重修", "缓修", "课程", "考试"},
    "major": {"专业", "分流", "转专业", "学院", "试验班", "保研"},
    "campus_life": {"食堂", "社团", "志愿", "活动", "校区", "生活"},
}

INTENT_PRIORITY = {
    "medical": 0,
    "campus_card": 1,
    "identity": 2,
    "dorm": 3,
    "network": 4,
    "course": 5,
    "major": 6,
    "admission": 7,
    "campus_life": 8,
    "general": 9,
}

STALE_PATTERNS = (
    re.compile(r"202[0-4]\s*年?"),
    re.compile(r"2[0-4]\s*级"),
    re.compile(r"\d+\s*元"),
    re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*日"),
)

HIGH_RISK_CHANNEL_TERMS = (
    "本科招生网",
    "招生官网",
    "学校官网",
    "迎新系统",
    "信息门户",
    "网上办事大厅",
    "微信公众号",
    "公众号",
    "招生办公室",
    "教务处",
    "学生工作处",
    "辅导员",
    "服务热线",
    "官方邮箱",
)


class NewStudentAssistant:
    def __init__(self, config: RAGConfig | None = None):
        self.config = config or RAGConfig.from_env(Path(__file__).resolve().parents[1])
        self.chunks = load_or_build_chunks(self.config)
        self.retriever = self._create_retriever()
        self.llm = OpenAICompatibleLLM(
            base_url=self.config.llm_base_url,
            api_key=self.config.llm_api_key,
            model=self.config.llm_model,
            timeout=self.config.llm_timeout,
        )
        self.history: defaultdict[str, deque[tuple[str, str]]] = defaultdict(
            lambda: deque(maxlen=6)
        )

    def reindex(self) -> dict[str, Any]:
        self.chunks = load_or_build_chunks(self.config, force=True)
        self.retriever = self._create_retriever(force_graph=True)
        status = {"chunks": len(self.chunks)}
        graph = getattr(self.retriever, "graph", None)
        if graph is not None:
            status.update({"graph_terms": len(graph.terms), "graph_communities": len(graph.communities)})
        return status

    def _create_retriever(self, force_graph: bool = False) -> AdvancedRetriever:
        if self.config.use_graphrag:
            return GraphRAGRetriever(
                self.chunks,
                index_dir=self.config.index_dir,
                use_cache=self.config.use_cache,
                force_graph=force_graph,
            )
        return AdvancedRetriever(self.chunks)

    def clear(self, session_id: str = "default") -> None:
        self.history.pop(session_id, None)

    def ask(
        self,
        question: str,
        session_id: str = "default",
        top_k: int | None = None,
    ) -> AnswerResult:
        question = normalize_text(question)
        if not question:
            return AnswerResult(
                question=question,
                answer="请先输入一个具体问题。",
                confidence=0.0,
                sources=[],
                need_clarification=True,
            )

        rewritten = self._rewrite_query(question, session_id)
        hits = self.retriever.search(
            rewritten,
            top_k=top_k or self.config.top_k,
            candidate_k=self.config.candidate_k,
        )
        diagnostics = self._retrieval_diagnostics()
        confidence = self._blend_confidence(self._confidence(hits), diagnostics)
        intent = classify_intent(rewritten, hits)
        if diagnostics.get("sufficient") is False:
            intent = classify_intent(rewritten, [])
        warnings = self._warnings(hits)

        if self._should_clarify(hits, confidence, diagnostics):
            diagnostics["generation"] = self._generation_diagnostics("clarification")
            result = self._insufficient_answer(
                question,
                rewritten,
                hits,
                intent,
                confidence,
                diagnostics,
            )
            self._remember(session_id, question, result.answer)
            return result

        sources = [
            hit.to_source(f"S{index}")
            for index, hit in enumerate(hits, start=1)
        ]
        answer = self._llm_answer(question, rewritten, hits, sources, diagnostics)
        if answer is None:
            answer = self._extractive_answer(question, hits, sources)
            generation_mode = "extractive"
        else:
            generation_mode = "llm"
        diagnostics["generation"] = self._generation_diagnostics(generation_mode)
        if warnings:
            answer = f"{answer}\n\n提醒：{' '.join(warnings)}"

        result = AnswerResult(
            question=question,
            answer=answer,
            confidence=confidence,
            sources=sources,
            intent=intent,
            need_clarification=False,
            warnings=warnings,
            rewritten_query=rewritten if rewritten != question else "",
            diagnostics=diagnostics,
        )
        self._remember(session_id, question, answer)
        return result

    def ask_stream(
        self,
        question: str,
        session_id: str = "default",
        top_k: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        question = normalize_text(question)
        if not question:
            result = AnswerResult(
                question=question,
                answer="请先输入一个具体问题。",
                confidence=0.0,
                sources=[],
                need_clarification=True,
            )
            yield from _stream_text(result.answer)
            yield {"type": "final", "result": result.to_dict()}
            return

        rewritten = self._rewrite_query(question, session_id)
        hits = self.retriever.search(
            rewritten,
            top_k=top_k or self.config.top_k,
            candidate_k=self.config.candidate_k,
        )
        diagnostics = self._retrieval_diagnostics()
        confidence = self._blend_confidence(self._confidence(hits), diagnostics)
        intent = classify_intent(rewritten, hits)
        if diagnostics.get("sufficient") is False:
            intent = classify_intent(rewritten, [])
        warnings = self._warnings(hits)

        if self._should_clarify(hits, confidence, diagnostics):
            diagnostics["generation"] = self._generation_diagnostics("clarification")
            result = self._insufficient_answer(
                question,
                rewritten,
                hits,
                intent,
                confidence,
                diagnostics,
            )
            yield from _stream_text(result.answer)
            self._remember(session_id, question, result.answer)
            yield {"type": "final", "result": result.to_dict()}
            return

        sources = [
            hit.to_source(f"S{index}")
            for index, hit in enumerate(hits, start=1)
        ]
        answer = ""

        if self.llm.enabled:
            messages = self._llm_messages(question, rewritten, hits, sources, diagnostics)
            for delta in self.llm.stream_chat(messages):
                answer += delta
                yield {"type": "delta", "delta": delta}

        if not answer.strip():
            answer = self._extractive_answer(question, hits, sources)
            generation_mode = "extractive"
            yield from _stream_text(answer)
        else:
            generation_mode = "llm"
            if not _has_citation(answer):
                suffix = f"\n\n依据：{', '.join(source['id'] for source in sources[:2])}"
                answer += suffix
                yield from _stream_text(suffix)

        if warnings:
            warning_text = f"\n\n提醒：{' '.join(warnings)}"
            answer += warning_text
            yield from _stream_text(warning_text)

        diagnostics["generation"] = self._generation_diagnostics(generation_mode)

        result = AnswerResult(
            question=question,
            answer=answer,
            confidence=confidence,
            sources=sources,
            intent=intent,
            need_clarification=False,
            warnings=warnings,
            rewritten_query=rewritten if rewritten != question else "",
            diagnostics=diagnostics,
        )
        self._remember(session_id, question, answer)
        yield {"type": "final", "result": result.to_dict()}

    def chat(self, question: str, session_id: str = "default") -> AnswerResult:
        question = normalize_text(question)
        if not question:
            return AnswerResult(question="", answer="请先输入一个具体问题。", confidence=0.0, sources=[])
        messages = [
            {
                "role": "system",
                "content": "你是面向南京大学新生的问答助手。请直接、友善、简洁地回答。",
            },
            {"role": "user", "content": question},
        ]
        answer = self.llm.chat(messages) if self.llm.enabled else None
        if answer is None:
            answer = (
                "当前没有配置可用的 LLM 服务。你可以使用 /ask 或 /RAG/chat，"
                "系统会基于本地资料库给出带来源的回答。"
            )
        result = AnswerResult(
            question=question,
            answer=answer,
            confidence=0.0,
            sources=[],
            intent=classify_intent(question, []),
        )
        self._remember(session_id, question, answer)
        return result

    def _rewrite_query(self, question: str, session_id: str) -> str:
        history = self.history.get(session_id)
        if not history:
            return question
        if classify_intent(question, []) != "general":
            return question
        short_follow_up = len(question) <= 12 or re.search(r"这个|那个|它|怎么办|还有|呢", question)
        if not short_follow_up:
            return question
        last_question, last_answer = history[-1]
        context = re.sub(r"\s+", " ", f"{last_question} {last_answer}")[:120]
        return f"{context}；追问：{question}"

    def _llm_answer(
        self,
        question: str,
        rewritten: str,
        hits: list[SearchHit],
        sources: list[dict[str, Any]],
        diagnostics: dict[str, Any],
    ) -> str | None:
        if not self.llm.enabled:
            return None
        messages = self._llm_messages(question, rewritten, hits, sources, diagnostics)
        answer = self.llm.chat(messages)
        if answer:
            answer, filter_reason = _filter_unsupported_suffix(answer, hits)
            if filter_reason:
                self.llm.last_filter = filter_reason
            if answer is None:
                self.llm.last_error = filter_reason
                return None
        if answer and _has_citation(answer):
            return answer
        if answer:
            return f"{answer}\n\n依据：{', '.join(source['id'] for source in sources[:2])}"
        return None

    def _llm_messages(
        self,
        question: str,
        rewritten: str,
        hits: list[SearchHit],
        sources: list[dict[str, Any]],
        diagnostics: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        context_blocks = []
        for source, hit in zip(sources, hits):
            context_blocks.append(
                f"[{source['id']}]\n标题：{source['title']}\n来源：{source['source']}\n内容：{hit.chunk.content}"
            )
        diagnostic_text = _format_diagnostics(diagnostics or {})
        if diagnostic_text:
            context_blocks.insert(0, f"[DIAGNOSTICS]\n{diagnostic_text}")
        return [
            {
                "role": "system",
                "content": (
                    "你是南京大学新生问答助手。只基于给定资料回答，关键结论后标注引用编号，"
                    "例如 [S1]。如果资料不足，说明无法回答并给出下一步咨询建议。"
                    "涉及年份、金额、报到时间、系统入口时要提醒以当年官方通知为准。"
                    "不得输出资料中没有原样出现的网址、邮箱、电话、办理入口或政策结论。"
                    "不得自行推荐资料中没有原样出现的网站、系统、公众号、部门或咨询渠道；"
                    "资料没有提供下一步渠道时，只说明资料未提供，不要用常识补充。"
                    "不要依靠常识补全学校信息，也不要把建议写成已经确认的事实。"
                    "回答要清晰、温和、面向第一次接触大学流程的新生。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"原问题：{question}\n检索查询：{rewritten}\n\n"
                    "资料：\n" + "\n\n".join(context_blocks)
                ),
            },
        ]

    def _extractive_answer(
        self,
        question: str,
        hits: list[SearchHit],
        sources: list[dict[str, Any]],
    ) -> str:
        selected: list[tuple[str, str]] = []
        seen: set[str] = set()
        for source, hit in zip(sources, hits):
            answer = _chunk_answer(hit.chunk)
            if not answer or answer in seen:
                continue
            selected.append((source["id"], answer))
            seen.add(answer)
            if len(selected) >= 2:
                break

        if not selected:
            return "资料库中找到了可能相关的内容，但没有抽取到可直接回答的结论。建议换一个更具体的问法。"

        if len(selected) == 1:
            citation, answer = selected[0]
            return f"根据资料，{answer} [{citation}]"

        lines = ["根据资料，可以先参考这几条信息："]
        for citation, answer in selected:
            lines.append(f"- {answer} [{citation}]")
        return "\n".join(lines)

    def _insufficient_answer(
        self,
        question: str,
        rewritten: str,
        hits: list[SearchHit],
        intent: str,
        confidence: float,
        diagnostics: dict[str, Any] | None = None,
    ) -> AnswerResult:
        suggestions = _clarifying_suggestions(intent)
        if hits:
            sources = [hit.to_source(f"S{index}") for index, hit in enumerate(hits[:3], start=1)]
        else:
            sources = []
        answer = (
            "资料库里没有找到足够可靠的依据来回答这个问题。"
            f"{suggestions}"
            "如果这是和当年报到、费用、时间节点相关的问题，建议以学校最新官方通知为准。"
        )
        return AnswerResult(
            question=question,
            answer=answer,
            confidence=confidence,
            sources=sources,
            intent=intent,
            need_clarification=True,
            rewritten_query=rewritten if rewritten != question else "",
            diagnostics=diagnostics or {},
        )

    def _retrieval_diagnostics(self) -> dict[str, Any]:
        diagnostics = getattr(self.retriever, "last_diagnostics", {})
        return dict(diagnostics) if isinstance(diagnostics, dict) else {}

    def _generation_diagnostics(self, mode: str) -> dict[str, Any]:
        diagnostics = {
            "mode": mode,
            "model": self.config.llm_model if self.llm.enabled else "none",
            "llm_attempted": self.llm.enabled and mode != "clarification",
        }
        if mode == "extractive" and self.llm.enabled and self.llm.last_error:
            diagnostics["fallback_reason"] = self.llm.last_error
        if mode == "llm" and self.llm.last_filter:
            diagnostics["safety_filter"] = self.llm.last_filter
        return diagnostics

    def _blend_confidence(
        self,
        hit_confidence: float,
        diagnostics: dict[str, Any],
    ) -> float:
        quality = diagnostics.get("quality")
        if not isinstance(quality, (int, float)):
            return hit_confidence
        blended = 0.62 * hit_confidence + 0.38 * float(quality)
        if diagnostics.get("sufficient") is False:
            blended = min(blended, max(float(quality), hit_confidence * 0.82))
        return max(0.0, min(1.0, blended))

    def _should_clarify(
        self,
        hits: list[SearchHit],
        confidence: float,
        diagnostics: dict[str, Any],
    ) -> bool:
        if not hits or confidence < self.config.min_confidence:
            return True
        if diagnostics.get("sufficient") is False:
            quality = diagnostics.get("quality", 0.0)
            if not isinstance(quality, (int, float)) or quality < 0.65:
                return True
        return False

    def _confidence(self, hits: list[SearchHit]) -> float:
        if not hits:
            return 0.0
        top = hits[0].score
        second = hits[1].score if len(hits) > 1 else 0.0
        margin = max(0.0, top - second)
        confidence = min(1.0, top / 0.42 + margin / 0.24)
        return max(0.0, confidence)

    def _warnings(self, hits: list[SearchHit]) -> list[str]:
        combined = "\n".join(hit.chunk.content for hit in hits[:3])
        warnings: list[str] = []
        if any(pattern.search(combined) for pattern in STALE_PATTERNS):
            warnings.append("检索到的部分资料包含历史年份、级别、日期或费用，实际安排请以当前年度官方通知为准。")
        urls = []
        for hit in hits[:3]:
            urls.extend(hit.chunk.metadata.get("urls") or extract_urls(hit.chunk.content))
        if urls:
            warnings.append("如答案中含链接，建议打开原链接核对最新版本。")
        return warnings

    def _remember(self, session_id: str, question: str, answer: str) -> None:
        self.history[session_id].append((question, answer))


def classify_intent(query: str, hits: list[SearchHit] | list[Any]) -> str:
    query_intent, query_score = _score_intent(query)
    if query_score > 0:
        return query_intent
    text = "\n".join(
        hit.chunk.content if isinstance(hit, SearchHit) else ""
        for hit in hits[:2]
    )
    hit_intent, hit_score = _score_intent(text)
    return hit_intent if hit_score > 0 else "general"


def _score_intent(text: str) -> tuple[str, int]:
    scores: dict[str, int] = {}
    for intent, keywords in INTENT_KEYWORDS.items():
        scores[intent] = sum(1 for keyword in keywords if keyword.lower() in text.lower())
    best = max(
        scores.items(),
        key=lambda item: (item[1], -INTENT_PRIORITY.get(item[0], 99)),
    )
    return best


def _chunk_answer(chunk: KnowledgeChunk) -> str:
    if chunk.answer:
        return chunk.answer
    content = chunk.content
    marker = "答案："
    if marker in content:
        return content.split(marker, 1)[1].strip()
    return content.strip()


def _has_citation(answer: str) -> bool:
    return bool(re.search(r"\[S\d+\]", answer))


def _unsupported_urls(answer: str, hits: list[SearchHit]) -> list[str]:
    evidence_urls = {
        url.rstrip("/")
        for hit in hits
        for url in extract_urls(hit.chunk.content)
    }
    return [
        url
        for url in extract_urls(answer)
        if url.rstrip("/") not in evidence_urls
    ]


def _unsupported_channels(answer: str, hits: list[SearchHit]) -> list[str]:
    evidence = "\n".join(hit.chunk.content for hit in hits)
    return sorted(
        {
            term
            for term in HIGH_RISK_CHANNEL_TERMS
            if term in answer and term not in evidence
        }
    )


def _filter_unsupported_suffix(
    answer: str,
    hits: list[SearchHit],
) -> tuple[str | None, str]:
    lines = answer.splitlines()
    for index, line in enumerate(lines):
        if _unsupported_urls(line, hits):
            return _safe_prefix(lines, index), "unsupported_url"
        if _unsupported_channels(line, hits):
            return _safe_prefix(lines, index), "unsupported_channel"
    return answer, ""


def _safe_prefix(lines: list[str], stop: int) -> str | None:
    candidate = "\n".join(lines[:stop]).strip()
    while candidate.endswith(("温馨提示：", "提醒：", "建议：")):
        candidate = candidate.rsplit("\n", 1)[0].strip() if "\n" in candidate else ""
    return candidate if candidate and _has_citation(candidate) else None


def _stream_text(text: str, chunk_size: int = 18) -> Iterator[dict[str, str]]:
    for start in range(0, len(text), chunk_size):
        yield {"type": "delta", "delta": text[start : start + chunk_size]}


def _format_diagnostics(diagnostics: dict[str, Any]) -> str:
    if not diagnostics:
        return ""
    keys = [
        "mode",
        "quality",
        "sufficient",
        "corrective_pass",
        "authority",
        "coverage",
        "source_diversity",
        "multi_query_support",
        "recommended_action",
    ]
    parts = [f"{key}={diagnostics[key]}" for key in keys if key in diagnostics]
    reasons = diagnostics.get("reasons")
    if isinstance(reasons, list) and reasons:
        parts.append("reasons=" + ",".join(map(str, reasons[:4])))
    return "; ".join(parts)


def _clarifying_suggestions(intent: str) -> str:
    by_intent = {
        "identity": "你可以补充是忘记密码、首次登录、账号冻结，还是信息门户/南大 APP 登录失败。",
        "campus_card": "你可以补充是领取、充值、挂失补办、消费密码，还是更换照片。",
        "admission": "你可以补充年级、校区、书院，以及关注的是报到时间、材料还是交通安排。",
        "dorm": "你可以补充校区、宿舍楼，或你想问床铺尺寸、床帘、空调还是入住流程。",
        "medical": "你可以补充是医保参保、社保卡、校医院就诊、体检还是报销。",
        "course": "你可以补充课程类型，以及是选课、缓考、补考、重修还是考试复习。",
        "major": "你可以补充学院/大类，以及是分流、转专业、培养方案还是升学就业。",
    }
    suggestion = by_intent.get(intent)
    return f"{suggestion}" if suggestion else "你可以补充具体场景、校区、年级或系统名称。"
