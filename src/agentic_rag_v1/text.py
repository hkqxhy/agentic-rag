from __future__ import annotations

import hashlib
import html
import re
from collections import Counter


URL_RE = re.compile(r"https?://[^\s\"'<>，。；、）)]+", re.IGNORECASE)
TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+")
SPACE_RE = re.compile(r"[\t\f\v\r \u3000\xa0]+")
BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
HTML_TAG_RE = re.compile(r"<[^>]+>")
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


def stable_hash(text: str, length: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def normalize_text(text: str) -> str:
    text = html.unescape(text or "")
    text = BR_RE.sub("\n", text)
    text = MARKDOWN_IMAGE_RE.sub("", text)
    text = MARKDOWN_LINK_RE.sub(r"\1 \2", text)
    text = HTML_TAG_RE.sub("", text)
    text = SPACE_RE.sub(" ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_urls(text: str) -> list[str]:
    return URL_RE.findall(text or "")


def _char_ngrams(text: str, min_n: int = 2, max_n: int = 3) -> list[str]:
    chars = [ch for ch in text if "\u4e00" <= ch <= "\u9fff"]
    grams: list[str] = []
    for n in range(min_n, max_n + 1):
        grams.extend("".join(chars[i : i + n]) for i in range(max(0, len(chars) - n + 1)))
    return grams


def tokenize(text: str) -> list[str]:
    text = normalize_text(text).lower()
    tokens: list[str] = []
    for match in TOKEN_RE.finditer(text):
        value = match.group(0)
        if re.fullmatch(r"[\u4e00-\u9fff]+", value):
            tokens.extend(value)
            tokens.extend(
                value[i : i + 2] for i in range(max(0, len(value) - 1))
            )
            if len(value) >= 4:
                tokens.extend(
                    value[i : i + 3] for i in range(max(0, len(value) - 2))
                )
        else:
            tokens.append(value)
    return [token for token in tokens if token]


def token_counter(text: str) -> Counter[str]:
    return Counter(tokenize(text))


def chunk_text(text: str, size: int = 700, overlap: int = 120) -> list[str]:
    text = normalize_text(text)
    if not text:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for paragraph in paragraphs:
        if len(paragraph) > size:
            flush()
            start = 0
            while start < len(paragraph):
                chunks.append(paragraph[start : start + size].strip())
                start += max(1, size - overlap)
            continue
        if len(current) + len(paragraph) + 2 <= size:
            current = f"{current}\n\n{paragraph}".strip()
        else:
            flush()
            current = paragraph
    flush()

    if overlap <= 0 or len(chunks) <= 1:
        return chunks

    with_overlap: list[str] = []
    previous_tail = ""
    for chunk in chunks:
        merged = f"{previous_tail}\n{chunk}".strip() if previous_tail else chunk
        with_overlap.append(merged)
        previous_tail = chunk[-overlap:]
    return with_overlap


def cosine_from_counters(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    common = set(left) & set(right)
    numerator = sum(left[token] * right[token] for token in common)
    left_norm = sum(value * value for value in left.values()) ** 0.5
    right_norm = sum(value * value for value in right.values()) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)
