from __future__ import annotations

import math
from collections import Counter, defaultdict

from .schema import KnowledgeChunk, SearchHit
from .text import cosine_from_counters, normalize_text, token_counter, tokenize


NEW_STUDENT_TERMS = {
    "新生",
    "入学",
    "报到",
    "报道",
    "迎新",
    "学号",
    "校园卡",
    "统一身份认证",
    "宿舍",
    "医保",
    "体检",
    "选课",
    "军训",
    "辅导员",
}

ACTION_TERMS = {
    "忘记",
    "修改",
    "冻结",
    "登录",
    "补办",
    "挂失",
    "充值",
    "领取",
    "报到",
    "报道",
    "体检",
    "预约",
    "参保",
    "报销",
    "选课",
    "补考",
    "缓考",
    "重修",
    "分流",
    "转专业",
}

OVERVIEW_TERMS = {"哪些", "有什么", "有哪些", "列表", "汇总", "介绍", "目录", "清单"}


class BM25Index:
    def __init__(self, chunks: list[KnowledgeChunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1 = k1
        self.b = b
        self.doc_tokens: list[Counter[str]] = []
        self.doc_lengths: list[int] = []
        self.doc_freq: Counter[str] = Counter()
        self.avgdl = 0.0
        self._build()

    def _build(self) -> None:
        total_length = 0
        for chunk in self.chunks:
            searchable = _searchable_text(chunk)
            counts = token_counter(searchable)
            self.doc_tokens.append(counts)
            length = sum(counts.values())
            self.doc_lengths.append(length)
            total_length += length
            self.doc_freq.update(counts.keys())
        self.avgdl = total_length / max(1, len(self.chunks))

    def search(self, query: str, limit: int) -> list[tuple[int, float]]:
        query_terms = tokenize(query)
        if not query_terms:
            return []
        query_counts = Counter(query_terms)
        scores: list[tuple[int, float]] = []
        doc_count = len(self.chunks)
        for index, counts in enumerate(self.doc_tokens):
            score = 0.0
            length = self.doc_lengths[index] or 1
            for term, query_weight in query_counts.items():
                tf = counts.get(term, 0)
                if tf == 0:
                    continue
                df = self.doc_freq.get(term, 0)
                idf = math.log(1 + (doc_count - df + 0.5) / (df + 0.5))
                denominator = tf + self.k1 * (1 - self.b + self.b * length / max(self.avgdl, 1))
                score += query_weight * idf * (tf * (self.k1 + 1) / denominator)
            if score > 0:
                scores.append((index, score))
        scores.sort(key=lambda item: item[1], reverse=True)
        return scores[:limit]


class NgramIndex:
    def __init__(self, chunks: list[KnowledgeChunk]):
        self.chunks = chunks
        self.vectors = [token_counter(_searchable_text(chunk)) for chunk in chunks]

    def search(self, query: str, limit: int) -> list[tuple[int, float]]:
        query_vector = token_counter(query)
        if not query_vector:
            return []
        scores = [
            (index, cosine_from_counters(query_vector, vector))
            for index, vector in enumerate(self.vectors)
        ]
        scores = [(index, score) for index, score in scores if score > 0]
        scores.sort(key=lambda item: item[1], reverse=True)
        return scores[:limit]


class Retriever:
    def __init__(self, chunks: list[KnowledgeChunk]):
        self.chunks = chunks
        self.bm25 = BM25Index(chunks)
        self.ngram = NgramIndex(chunks)

    def search(self, query: str, top_k: int = 5, candidate_k: int = 40) -> list[SearchHit]:
        if not self.chunks:
            return []
        bm25_results = self.bm25.search(query, candidate_k)
        ngram_results = self.ngram.search(query, candidate_k)
        fused = reciprocal_rank_fusion([bm25_results, ngram_results])
        reranked = self._rerank(query, fused[:candidate_k])
        return [
            SearchHit(
                chunk=self.chunks[index],
                score=score,
                rank=rank,
                signals=signals,
            )
            for rank, (index, score, signals) in enumerate(reranked[:top_k], start=1)
        ]

    def _rerank(
        self,
        query: str,
        fused: list[tuple[int, float, dict[str, float]]],
    ) -> list[tuple[int, float, dict[str, float]]]:
        query_text = normalize_text(query)
        query_tokens = set(tokenize(query_text))
        output: list[tuple[int, float, dict[str, float]]] = []
        for index, fused_score, signals in fused:
            chunk = self.chunks[index]
            searchable = normalize_text(_searchable_text(chunk))
            doc_tokens = set(tokenize(searchable))
            coverage = len(query_tokens & doc_tokens) / max(1, len(query_tokens))
            exact = 1.0 if query_text and query_text in searchable else 0.0
            title_match = _short_text_overlap(query_text, chunk.title or chunk.question)
            vertical = _vertical_boost(query_text, chunk)
            action = _action_boost(query_text, chunk)
            overview = _overview_boost(query_text, chunk)
            category = _category_boost(query_text, chunk)
            qa_boost = 0.04 if chunk.metadata.get("kind") == "qa" else 0.0
            final = (
                fused_score
                + 0.22 * coverage
                + 0.10 * exact
                + 0.08 * title_match
                + vertical
                + action
                + overview
                + category
                + qa_boost
            )
            merged_signals = dict(signals)
            merged_signals.update(
                {
                    "coverage": coverage,
                    "exact": exact,
                    "title_match": title_match,
                    "vertical": vertical,
                    "action": action,
                    "overview": overview,
                    "category": category,
                    "qa_boost": qa_boost,
                }
            )
            output.append((index, final, merged_signals))
        output.sort(key=lambda item: item[1], reverse=True)
        return output


def reciprocal_rank_fusion(
    rankings: list[list[tuple[int, float]]],
    rrf_k: int = 60,
) -> list[tuple[int, float, dict[str, float]]]:
    scores: defaultdict[int, float] = defaultdict(float)
    signals: defaultdict[int, dict[str, float]] = defaultdict(dict)
    for source_index, ranking in enumerate(rankings):
        source_name = f"ranker_{source_index + 1}"
        for rank, (doc_index, raw_score) in enumerate(ranking, start=1):
            scores[doc_index] += 1.0 / (rrf_k + rank)
            signals[doc_index][source_name] = raw_score
            signals[doc_index][f"{source_name}_rank"] = float(rank)
    fused = [
        (doc_index, score, signals[doc_index])
        for doc_index, score in scores.items()
    ]
    fused.sort(key=lambda item: item[1], reverse=True)
    return fused


def _searchable_text(chunk: KnowledgeChunk) -> str:
    parts = [
        chunk.title,
        chunk.question,
        chunk.answer,
        chunk.content,
        chunk.category,
        " ".join(map(str, chunk.metadata.get("keywords", []))),
    ]
    return "\n".join(part for part in parts if part)


def _short_text_overlap(query: str, title: str) -> float:
    query_tokens = set(tokenize(query))
    title_tokens = set(tokenize(title))
    if not query_tokens or not title_tokens:
        return 0.0
    return len(query_tokens & title_tokens) / max(1, len(query_tokens))


def _vertical_boost(query_text: str, chunk: KnowledgeChunk) -> float:
    chunk_text = f"{chunk.title}\n{chunk.category}\n{chunk.content}"
    overlap = sum(
        1 for term in NEW_STUDENT_TERMS
        if term in query_text and term in chunk_text
    )
    if overlap:
        return min(0.05, 0.02 * overlap)
    return 0.0


def _action_boost(query: str, chunk: KnowledgeChunk) -> float:
    question = chunk.question or chunk.title
    content = chunk.content
    score = 0.0
    for term in ACTION_TERMS:
        if term not in query:
            continue
        if term in question:
            score += 0.045
        elif term in content:
            score += 0.015
    return min(0.09, score)


def _overview_boost(query: str, chunk: KnowledgeChunk) -> float:
    if chunk.metadata.get("kind") != "directory":
        return 0.0
    if any(term in query for term in OVERVIEW_TERMS):
        return 0.08
    return 0.0


def _category_boost(query: str, chunk: KnowledgeChunk) -> float:
    category_path = str(chunk.metadata.get("category_path") or chunk.source)
    score = 0.0
    if "社团" in query and "南大社团介绍" in category_path:
        score += 0.18
    if ("培养方案" in query or "课程" in query) and "各院系培养方案" in category_path:
        score += 0.10
    if ("校园网" in query or "路由器" in query or "网络" in query) and "校园网相关" in category_path:
        score += 0.10
    if ("奖学金" in query or "助学金" in query or "资助" in query) and "奖助学金" in category_path:
        score += 0.10
    if ("校历" in query or "放假" in query) and "校历" in category_path:
        score += 0.10
    return score
