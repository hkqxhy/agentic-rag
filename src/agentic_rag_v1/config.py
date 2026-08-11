from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SOURCE_NAMES = (
    "南哪QA.qa",
    "南哪23级本科①群.txt.qa",
    "data",
    "QQ",
    "Documents",
)

FALLBACK_SOURCE_NAMES = ("knowledge/fixtures",)


def _split_paths(value: str) -> list[str]:
    if not value.strip():
        return []
    raw_parts: list[str] = []
    for part in value.split(os.pathsep):
        raw_parts.extend(part.split(";"))
    return [part.strip() for part in raw_parts if part.strip()]


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(slots=True)
class RAGConfig:
    root: Path
    source_paths: list[Path] = field(default_factory=list)
    index_dir: Path | None = None
    top_k: int = 5
    candidate_k: int = 40
    min_confidence: float = 0.16
    use_cache: bool = True
    use_graphrag: bool = True
    llm_base_url: str = ""
    llm_api_key: str = "EMPTY"
    llm_model: str = "qwen-plus"
    llm_timeout: float = 30.0
    dense_rrf_weight: float = 1.0
    dense_min_similarity: float = 0.45

    def __post_init__(self) -> None:
        self.root = self.root.resolve()
        if not self.source_paths:
            local_sources = [self.root / name for name in DEFAULT_SOURCE_NAMES]
            existing_sources = [path for path in local_sources if path.exists()]
            self.source_paths = existing_sources or [
                self.root / name for name in FALLBACK_SOURCE_NAMES
            ]
        else:
            self.source_paths = [
                path if path.is_absolute() else (self.root / path)
                for path in self.source_paths
            ]
        if self.index_dir is None:
            self.index_dir = self.root / ".agentic_rag_v1_index"
        else:
            self.index_dir = (
                self.index_dir
                if self.index_dir.is_absolute()
                else (self.root / self.index_dir)
            )

    @classmethod
    def from_env(cls, root: str | Path | None = None) -> RAGConfig:
        project_root = Path(root or os.getenv("AGENTIC_RAG_ROOT", ".")).resolve()
        load_env_file(project_root / ".env.local")
        source_value = os.getenv("AGENTIC_RAG_SOURCE_PATHS", "")
        source_paths = [Path(part) for part in _split_paths(source_value)]

        return cls(
            root=project_root,
            source_paths=source_paths,
            index_dir=Path(os.getenv("AGENTIC_RAG_INDEX_DIR", ".agentic_rag_v1_index")),
            top_k=int(os.getenv("AGENTIC_RAG_TOP_K", "5")),
            candidate_k=int(os.getenv("AGENTIC_RAG_CANDIDATE_K", "40")),
            min_confidence=float(os.getenv("AGENTIC_RAG_MIN_CONFIDENCE", "0.16")),
            use_cache=os.getenv("AGENTIC_RAG_USE_CACHE", "1") != "0",
            use_graphrag=os.getenv("AGENTIC_RAG_USE_GRAPHRAG", "1") != "0",
            llm_base_url=os.getenv("AGENTIC_RAG_LLM_BASE_URL", "").strip(),
            llm_api_key=os.getenv("AGENTIC_RAG_LLM_API_KEY", "EMPTY"),
            llm_model=os.getenv("AGENTIC_RAG_LLM_MODEL", "qwen-plus"),
            llm_timeout=float(os.getenv("AGENTIC_RAG_LLM_TIMEOUT", "30")),
            dense_rrf_weight=float(os.getenv("AGENTIC_RAG_DENSE_RRF_WEIGHT", "1.0")),
            dense_min_similarity=float(
                os.getenv("AGENTIC_RAG_DENSE_MIN_SIMILARITY", "0.45")
            ),
        )
