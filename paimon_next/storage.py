from __future__ import annotations

import json
from pathlib import Path

from .config import RAGConfig
from .loaders import iter_source_files, load_sources
from .schema import KnowledgeChunk
from .text import stable_hash


INDEX_VERSION = 1


def load_or_build_chunks(config: RAGConfig, force: bool = False) -> list[KnowledgeChunk]:
    assert config.index_dir is not None
    index_file = config.index_dir / "index.json"
    fingerprint = source_fingerprint(config.source_paths)
    if config.use_cache and not force and index_file.exists():
        cached = _load_cache(index_file)
        if cached and cached.get("fingerprint") == fingerprint:
            return [
                KnowledgeChunk.from_dict(item)
                for item in cached.get("chunks", [])
            ]

    chunks = load_sources(config.source_paths)
    config.index_dir.mkdir(parents=True, exist_ok=True)
    index_file.write_text(
        json.dumps(
            {
                "version": INDEX_VERSION,
                "fingerprint": fingerprint,
                "chunks": [chunk.to_dict() for chunk in chunks],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return chunks


def source_fingerprint(paths: list[Path]) -> str:
    parts: list[str] = []
    for path in iter_source_files(paths):
        try:
            stat = path.stat()
        except OSError:
            continue
        parts.append(f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}")
    return stable_hash("\n".join(sorted(parts)), length=24)


def _load_cache(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if data.get("version") != INDEX_VERSION:
        return None
    if not isinstance(data.get("chunks"), list):
        return None
    return data
