from __future__ import annotations

import csv
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree

from .schema import KnowledgeChunk
from .text import chunk_text, extract_urls, normalize_text, stable_hash


SUPPORTED_SUFFIXES = {".qa", ".json", ".md", ".txt", ".csv", ".docx", ".pdf"}


def iter_source_files(paths: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and child.suffix.lower() in SUPPORTED_SUFFIXES:
                    files.append(child)
        elif path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
            files.append(path)
    return files


def load_sources(paths: Iterable[Path]) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    source_paths = list(paths)
    for path in iter_source_files(source_paths):
        chunks.extend(load_file(path))
    chunks.extend(load_directory_overviews(source_paths))
    return chunks


def load_directory_overviews(paths: Iterable[Path]) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    for root in paths:
        if not root.is_dir():
            continue
        for directory in sorted([root, *[p for p in root.rglob("*") if p.is_dir()]]):
            files = [
                child
                for child in sorted(directory.iterdir())
                if child.is_file() and child.suffix.lower() in SUPPORTED_SUFFIXES
            ]
            if len(files) < 2:
                continue
            names = [file.stem for file in files]
            for index, group in enumerate(_batched(names, 80), start=1):
                title = f"{directory.name} 资料目录"
                content = (
                    f"目录：{_category_path(directory / 'index.txt') or directory.name}\n"
                    "该目录包含以下资料：\n"
                    + "\n".join(f"- {name}" for name in group)
                )
                chunk_id = stable_hash(f"dir:{directory}:{index}:{content}")
                chunks.append(
                    KnowledgeChunk(
                        id=chunk_id,
                        content=content,
                        source=str(directory),
                        title=title,
                        metadata={
                            "kind": "directory",
                            "index": index,
                            "category_path": _category_path(directory / "index.txt"),
                            "file_count": len(files),
                        },
                    )
                )
    return chunks


def _batched(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def load_file(path: Path) -> list[KnowledgeChunk]:
    suffix = path.suffix.lower()
    if suffix in {".qa", ".json"}:
        loaded = _load_json_qa(path)
        if loaded:
            return loaded
    if suffix == ".csv":
        return _load_csv(path)
    if suffix == ".docx":
        text = _read_docx(path)
        return _load_text_chunks(path, text)
    if suffix == ".pdf":
        return _load_pdf(path)
    else:
        text = path.read_text(encoding="utf-8", errors="ignore")
    return _load_text_chunks(path, text)


def _load_json_qa(path: Path) -> list[KnowledgeChunk]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []

    chunks: list[KnowledgeChunk] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        parsed = _parse_conversation_item(item) or _parse_group_qa_item(item)
        if parsed is None:
            continue
        question, answer, metadata = parsed
        question = normalize_text(question)
        answer = normalize_text(answer)
        if not question or not answer:
            continue
        metadata.update(
            {
                "kind": "qa",
                "question": question,
                "answer": answer,
                "urls": extract_urls(answer),
                "category_path": _category_path(path),
                "row": index + 1,
            }
        )
        title = question[:80]
        content = f"问题：{question}\n答案：{answer}"
        if metadata.get("category"):
            content += f"\n分类：{metadata['category']}"
        if metadata.get("keywords"):
            content += "\n关键词：" + "，".join(map(str, metadata["keywords"]))
        chunk_id = stable_hash(f"{path}:{metadata.get('source_id', index)}:{question}:{answer}")
        chunks.append(
            KnowledgeChunk(
                id=chunk_id,
                content=content,
                source=str(path),
                title=title,
                metadata=metadata,
            )
        )
    return chunks


def _parse_conversation_item(item: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
    conversations = item.get("conversations")
    if not isinstance(conversations, list):
        return None
    user_value = ""
    assistant_value = ""
    for message in conversations:
        if not isinstance(message, dict):
            continue
        role = str(message.get("from", message.get("role", ""))).lower()
        value = str(message.get("value", message.get("content", "")))
        if role in {"user", "human"} and not user_value:
            user_value = value
        elif role in {"assistant", "gpt", "ai"} and not assistant_value:
            assistant_value = value
    if not user_value or not assistant_value:
        return None
    metadata = {
        "source_id": item.get("id", ""),
        "category": item.get("type", ""),
        "keywords": item.get("keywords", []),
    }
    return user_value, assistant_value, metadata


def _parse_group_qa_item(item: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
    question = item.get("Q") or item.get("question")
    answer = item.get("A") or item.get("answer")
    if not question or not answer:
        return None
    metadata = {
        "source_id": item.get("id", ""),
        "category": item.get("type", ""),
        "keywords": item.get("keywords", []),
        "ref": item.get("ref", []),
        "author": item.get("author", ""),
    }
    return str(question), str(answer), metadata


def _load_csv(path: Path) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as file:
        reader = csv.DictReader(file)
        for row_index, row in enumerate(reader, start=1):
            values = [normalize_text(str(value)) for value in row.values() if value]
            text = "\n".join(value for value in values if value)
            if not text:
                continue
            chunk_id = stable_hash(f"{path}:{row_index}:{text}")
            chunks.append(
                KnowledgeChunk(
                    id=chunk_id,
                    content=text,
                    source=str(path),
                    title=f"{path.name}#{row_index}",
                    metadata={
                        "kind": "csv",
                        "row": row_index,
                        "columns": row,
                        "category_path": _category_path(path),
                    },
                )
            )
    return chunks


def _load_text_chunks(path: Path, text: str) -> list[KnowledgeChunk]:
    title = _guess_title(path, text)
    chunks: list[KnowledgeChunk] = []
    for index, content in enumerate(chunk_text(text), start=1):
        chunk_id = stable_hash(f"{path}:{index}:{content}")
        chunks.append(
            KnowledgeChunk(
                id=chunk_id,
                content=content,
                source=str(path),
                title=title or f"{path.name}#{index}",
                metadata={
                    "kind": "document",
                    "index": index,
                    "category_path": _category_path(path),
                    "urls": extract_urls(content),
                },
            )
        )
    return chunks


def _guess_title(path: Path, text: str) -> str:
    path_title = path.stem.strip()
    for line in text.splitlines():
        line = normalize_text(re.sub(r"^#+\s*", "", line))
        if line and len(line) <= 80:
            return line[:80]
    return path_title or path.name


def _category_path(path: Path) -> str:
    parts = list(path.parts)
    for marker in ("Documents", "QQ", "data"):
        if marker in parts:
            index = parts.index(marker)
            return "/".join(parts[index:-1])
    return ""


def _load_pdf(path: Path) -> list[KnowledgeChunk]:
    text_pages = _read_pdf_pages(path)
    chunks: list[KnowledgeChunk] = []
    title = path.stem
    for page_number, text in text_pages:
        for index, content in enumerate(chunk_text(text), start=1):
            chunk_id = stable_hash(f"{path}:{page_number}:{index}:{content}")
            chunks.append(
                KnowledgeChunk(
                    id=chunk_id,
                    content=content,
                    source=str(path),
                    title=title,
                    metadata={
                        "kind": "pdf",
                        "page": page_number,
                        "index": index,
                        "category_path": _category_path(path),
                        "urls": extract_urls(content),
                    },
                )
            )
    return chunks


def _read_pdf_pages(path: Path) -> list[tuple[int, str]]:
    try:
        import fitz
    except ImportError:
        return []
    pages: list[tuple[int, str]] = []
    try:
        with fitz.open(path) as document:
            for page_index, page in enumerate(document, start=1):
                text = normalize_text(page.get_text("text"))
                if text:
                    pages.append((page_index, text))
    except Exception:
        return []
    return pages


def _read_docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as docx:
            xml = docx.read("word/document.xml")
    except Exception:
        return ""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return ""

    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        texts = [
            node.text or ""
            for node in paragraph.findall(".//w:t", namespace)
        ]
        line = normalize_text("".join(texts))
        if line:
            paragraphs.append(line)
    return "\n\n".join(paragraphs)
