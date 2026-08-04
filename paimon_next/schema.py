from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class KnowledgeChunk:
    """A searchable knowledge unit."""

    id: str
    content: str
    source: str
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def question(self) -> str:
        return str(self.metadata.get("question", ""))

    @property
    def answer(self) -> str:
        return str(self.metadata.get("answer", ""))

    @property
    def category(self) -> str:
        return str(self.metadata.get("category", ""))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "source": self.source,
            "title": self.title,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KnowledgeChunk":
        return cls(
            id=str(data["id"]),
            content=str(data["content"]),
            source=str(data["source"]),
            title=str(data.get("title", "")),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(slots=True)
class SearchHit:
    """A retrieved chunk with score details."""

    chunk: KnowledgeChunk
    score: float
    rank: int
    signals: dict[str, float] = field(default_factory=dict)

    def to_source(self, citation_id: str | None = None) -> dict[str, Any]:
        data = {
            "id": citation_id or f"S{self.rank}",
            "score": round(self.score, 4),
            "title": self.chunk.title or self.chunk.question or self.chunk.source,
            "source": self.chunk.source,
            "content": self.chunk.content,
            "metadata": self.chunk.metadata,
            "signals": {k: round(v, 4) for k, v in self.signals.items()},
        }
        return data


@dataclass(slots=True)
class AnswerResult:
    """The public response object returned by the assistant."""

    question: str
    answer: str
    confidence: float
    sources: list[dict[str, Any]]
    intent: str = "general"
    need_clarification: bool = False
    warnings: list[str] = field(default_factory=list)
    rewritten_query: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "confidence": round(self.confidence, 4),
            "intent": self.intent,
            "need_clarification": self.need_clarification,
            "warnings": self.warnings,
            "rewritten_query": self.rewritten_query,
            "diagnostics": self.diagnostics,
            "reference": self.sources,
            "sources": self.sources,
        }
