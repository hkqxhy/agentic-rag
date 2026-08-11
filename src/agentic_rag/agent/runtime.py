from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from agentic_rag_v1.config import RAGConfig
from agentic_rag_v1.schema import SearchHit
from agentic_rag_v1.service import NewStudentAssistant, classify_intent
from agentic_rag_v1.text import normalize_text

AgentRoute = Literal["direct", "clarify", "fast_rag", "research_rag"]

_GREETING_PATTERNS = {
    "hi",
    "hello",
    "你好",
    "您好",
    "在吗",
    "谢谢",
    "你是谁",
    "你能做什么",
}
_RESEARCH_MARKERS = {
    "比较",
    "区别",
    "分别",
    "全部",
    "有哪些",
    "汇总",
    "流程",
    "方案",
    "为什么",
}
_CITATION_PATTERN = re.compile(r"\[S\d+]")


class AgentState(TypedDict, total=False):
    question: str
    conversation_id: str
    normalized_query: str
    route: AgentRoute
    intent: str
    answer: str
    confidence: float
    sources: list[dict[str, Any]]
    warnings: list[str]
    diagnostics: dict[str, Any]
    grounded: bool
    need_clarification: bool
    trace: list[dict[str, Any]]
    dense_hits: list[SearchHit]
    dense_diagnostics: dict[str, Any]


@dataclass(slots=True, frozen=True)
class AgentOutcome:
    answer: str
    route: AgentRoute
    intent: str
    confidence: float
    sources: list[dict[str, Any]]
    warnings: list[str]
    diagnostics: dict[str, Any]
    grounded: bool
    need_clarification: bool
    trace: list[dict[str, Any]]

    def message_metadata(self) -> dict[str, Any]:
        return _json_safe(
            {
                "agent": {
                    "framework": "langgraph",
                    "graph_version": "phase2.1",
                    "route": self.route,
                    "intent": self.intent,
                    "confidence": round(self.confidence, 4),
                    "grounded": self.grounded,
                    "need_clarification": self.need_clarification,
                    "trace": self.trace,
                },
                "sources": self.sources,
                "warnings": self.warnings,
                "retrieval": self.diagnostics,
            }
        )


class AgentRuntime:
    """Bounded LangGraph workflow over the existing advanced retrieval baseline."""

    def __init__(self, rag_config: RAGConfig | None = None) -> None:
        config = rag_config or RAGConfig.from_env(Path.cwd())
        self.assistant = NewStudentAssistant(config)
        self.graph = self._build_graph()

    def retrieval_query(self, question: str, conversation_id: str) -> str:
        return self.assistant.rewrite_query(question, conversation_id)

    @staticmethod
    def requires_retrieval(question: str) -> bool:
        return _route_for_query(normalize_text(question)) in {"fast_rag", "research_rag"}

    def invoke(
        self,
        question: str,
        conversation_id: str,
        dense_hits: list[SearchHit] | None = None,
        dense_diagnostics: dict[str, Any] | None = None,
    ) -> AgentOutcome:
        state = self.graph.invoke(
            {
                "question": question,
                "conversation_id": conversation_id,
                "trace": [],
                "dense_hits": dense_hits or [],
                "dense_diagnostics": dense_diagnostics or {
                    "mode": "off",
                    "status": "disabled",
                },
            }
        )
        return AgentOutcome(
            answer=str(state["answer"]),
            route=state["route"],
            intent=str(state.get("intent", "general")),
            confidence=float(state.get("confidence", 0.0)),
            sources=list(state.get("sources", [])),
            warnings=list(state.get("warnings", [])),
            diagnostics=dict(state.get("diagnostics", {})),
            grounded=bool(state.get("grounded", False)),
            need_clarification=bool(state.get("need_clarification", False)),
            trace=list(state.get("trace", [])),
        )

    def _build_graph(self):
        builder = StateGraph(AgentState)
        builder.add_node("normalize", self._normalize)
        builder.add_node("classify", self._classify)
        builder.add_node("direct", self._direct_answer)
        builder.add_node("clarify", self._clarify)
        builder.add_node("rag", self._rag)
        builder.add_node("verify", self._verify)
        builder.add_edge(START, "normalize")
        builder.add_edge("normalize", "classify")
        builder.add_conditional_edges(
            "classify",
            lambda state: state["route"],
            {
                "direct": "direct",
                "clarify": "clarify",
                "fast_rag": "rag",
                "research_rag": "rag",
            },
        )
        builder.add_edge("direct", "verify")
        builder.add_edge("clarify", "verify")
        builder.add_edge("rag", "verify")
        builder.add_edge("verify", END)
        return builder.compile()

    @staticmethod
    def _normalize(state: AgentState) -> AgentState:
        normalized = normalize_text(state.get("question", ""))
        return {
            "normalized_query": normalized,
            "trace": _append_trace(state, "normalize", query_length=len(normalized)),
        }

    @staticmethod
    def _classify(state: AgentState) -> AgentState:
        query = state.get("normalized_query", "")
        route = _route_for_query(query)
        intent = classify_intent(query, [])
        return {
            "route": route,
            "intent": intent,
            "trace": _append_trace(state, "classify", route=route, intent=intent),
        }

    @staticmethod
    def _direct_answer(state: AgentState) -> AgentState:
        answer = (
            "你好！我是 Agentic RAG 新生助手。你可以询问报到、校园卡、统一身份认证、"
            "宿舍、校园网、体检和选课等问题；涉及校务规则时，我会优先依据知识库并给出引用。"
        )
        return {
            "answer": answer,
            "confidence": 1.0,
            "sources": [],
            "warnings": [],
            "diagnostics": {"mode": "direct"},
            "need_clarification": False,
            "trace": _append_trace(state, "direct"),
        }

    @staticmethod
    def _clarify(state: AgentState) -> AgentState:
        return {
            "answer": "请补充一个更具体的问题，例如你想了解哪个校区、哪项业务或哪个时间范围？",
            "confidence": 0.0,
            "sources": [],
            "warnings": ["问题信息不足，未执行知识检索。"],
            "diagnostics": {"mode": "clarification"},
            "need_clarification": True,
            "trace": _append_trace(state, "clarify"),
        }

    def _rag(self, state: AgentState) -> AgentState:
        result = self.assistant.ask(
            state.get("normalized_query", ""),
            session_id=state.get("conversation_id", "default"),
            dense_hits=state.get("dense_hits", []),
            dense_diagnostics=state.get("dense_diagnostics", {}),
        )
        return {
            "answer": result.answer,
            "intent": result.intent,
            "confidence": result.confidence,
            "sources": result.sources,
            "warnings": result.warnings,
            "diagnostics": result.diagnostics,
            "need_clarification": result.need_clarification,
            "trace": _append_trace(
                state,
                "rag",
                route=state.get("route", "fast_rag"),
                source_count=len(result.sources),
                confidence=round(result.confidence, 4),
                corrective=bool(result.diagnostics.get("corrective")),
                generation_mode=(result.diagnostics.get("generation") or {}).get("mode"),
            ),
        }

    @staticmethod
    def _verify(state: AgentState) -> AgentState:
        answer = state.get("answer", "").strip()
        sources = state.get("sources", [])
        need_clarification = state.get("need_clarification", False)
        citations = _CITATION_PATTERN.findall(answer)
        if sources and not citations and not need_clarification:
            references = "；".join(
                f"[{source.get('id', f'S{index}')}] {source.get('title', '知识库资料')}"
                for index, source in enumerate(sources[:3], start=1)
            )
            answer = f"{answer}\n\n参考：{references}"
            citations = _CITATION_PATTERN.findall(answer)
        route = state.get("route", "clarify")
        grounded = route == "direct" or bool(
            sources and citations and not need_clarification
        )
        warnings = list(state.get("warnings", []))
        if route in {"fast_rag", "research_rag"} and not grounded:
            warnings.append("当前知识证据不足，请以学校官方最新通知为准。")
        return {
            "answer": answer,
            "grounded": grounded,
            "warnings": warnings,
            "trace": _append_trace(
                state,
                "verify",
                grounded=grounded,
                citation_count=len(citations),
            ),
        }


def _append_trace(state: AgentState, node: str, **details: Any) -> list[dict[str, Any]]:
    return [*state.get("trace", []), {"node": node, **details}]


def _route_for_query(query: str) -> AgentRoute:
    folded = query.casefold().strip("!?！？。,.， ")
    if not query:
        return "clarify"
    if folded in _GREETING_PATTERNS:
        return "direct"
    if len(query) < 3:
        return "clarify"
    if any(marker in query for marker in _RESEARCH_MARKERS):
        return "research_rag"
    return "fast_rag"


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))
