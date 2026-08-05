from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentic_rag_v1.api import main as api_main
from agentic_rag_v1.config import RAGConfig
from agentic_rag_v1.service import NewStudentAssistant


@dataclass(slots=True)
class Document:
    page_content: str
    metadata: dict[str, Any]


class AgenticRAGV1:
    """Compatibility facade for the upgraded Agentic RAG V1 RAG engine."""

    def __init__(self, memory_window: int = 5, bot_name: str = "Agentic RAG", verbose: bool = False):
        self.name = bot_name
        self.verbose = verbose
        self.memory_window = memory_window
        self.root = Path(__file__).resolve().parent
        self.config = RAGConfig.from_env(self.root)
        self.assistant = NewStudentAssistant(self.config)

    def load(self, file_list: list[str] | None = None) -> bool:
        if file_list:
            self.config.source_paths = [
                Path(path) if Path(path).is_absolute() else self.root / path
                for path in file_list
            ]
        status = self.assistant.reindex()
        if self.verbose:
            print(f"向量/检索索引加载完成，共 {status['chunks']} 个知识块。")
        return bool(status["chunks"])

    def clear_history(self) -> None:
        self.assistant.clear()

    def search(self, question: str) -> list[Document]:
        hits = self.assistant.retriever.search(
            question,
            top_k=self.config.top_k,
            candidate_k=self.config.candidate_k,
        )
        return [
            Document(page_content=hit.chunk.content, metadata=hit.to_source())
            for hit in hits
        ]

    def chat(self, question: str, reference: list[Document] | None = None) -> dict[str, Any]:
        result = self.assistant.chat(question)
        return result.to_dict()

    def rag(self, question: str) -> dict[str, Any]:
        return self.assistant.ask(question).to_dict()


if __name__ == "__main__":
    api_main()
