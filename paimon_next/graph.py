from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

from .advanced import AdvancedRetriever
from .retrieval import _searchable_text
from .schema import KnowledgeChunk, SearchHit
from .text import normalize_text, stable_hash, token_counter, tokenize


GRAPH_VERSION = 2
MAX_TERMS_PER_CHUNK = 12
MAX_EDGE_TERMS_PER_CHUNK = 8
MAX_TERM_CHUNKS = 80
MAX_COMMUNITY_CHUNKS = 160

GLOBAL_QUERY_TERMS = {
    "哪些",
    "有哪些",
    "有什么",
    "汇总",
    "介绍",
    "概述",
    "目录",
    "清单",
    "分类",
    "整体",
    "主要",
    "list",
    "overview",
    "summary",
}

DOMAIN_TERMS = [
    "南京大学",
    "南大",
    "新生",
    "入学",
    "报到",
    "迎新",
    "学号",
    "统一身份认证",
    "信息门户",
    "南大APP",
    "校园卡",
    "饭卡",
    "挂失",
    "补办",
    "充值",
    "宿舍",
    "住宿",
    "仙林",
    "鼓楼",
    "医保",
    "校医院",
    "体检",
    "报销",
    "校园网",
    "路由器",
    "NJU-WLAN",
    "VPN",
    "选课",
    "考试",
    "通识",
    "通修",
    "培养方案",
    "转专业",
    "分流",
    "辅修",
    "二次选拔",
    "院系",
    "奖学金",
    "助学金",
    "资助",
    "绿色通道",
    "社团",
    "协会",
    "招新",
    "交通",
    "学生票",
    "学生证",
    "校历",
    "军训",
    "英语分级",
    "防诈骗",
    "户口",
    "请假",
    "毕业论文",
    "科研",
    "推免",
    "保研",
    "交换",
    "出国",
    "软件学院",
    "计算机科学与技术系",
    "人工智能学院",
]

STOP_TERMS = {
    "Documents",
    "QQ",
    "data",
    "SUMMARY",
    "README",
    "index",
    "txt",
    "pdf",
    "docx",
    "json",
    "qa",
    "http",
    "https",
    "www",
    "com",
    "cn",
    "edu",
    "nju",
    "可以",
    "参加",
    "哪些",
    "有哪",
    "什么",
    "怎么",
    "如何",
    "可以的",
}
STOP_TERMS_LOWER = {item.lower() for item in STOP_TERMS}
BROAD_TERMS = {"南京大学", "南大", "新生", "指南", "说明", "问题", "答案", "资料", "Documents"}

SPLIT_RE = re.compile(r"[\\/\s\t\r\n,，.。;；:：|、&()\[\]{}（）【】《》<>\"'“”‘’]+")
PHRASE_RE = re.compile(r"[A-Za-z0-9_\-.]{2,32}|[\u4e00-\u9fff][\u4e00-\u9fffA-Za-z0-9_\-]{1,24}")


@dataclass(slots=True)
class GraphTerm:
    label: str
    weight: float
    chunks: list[tuple[str, float]] = field(default_factory=list)
    neighbors: list[tuple[str, float]] = field(default_factory=list)
    communities: list[tuple[str, float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "weight": round(self.weight, 6),
            "chunks": [[chunk_id, round(score, 6)] for chunk_id, score in self.chunks],
            "neighbors": [[term, round(score, 6)] for term, score in self.neighbors],
            "communities": [
                [community_id, round(score, 6)]
                for community_id, score in self.communities
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GraphTerm":
        return cls(
            label=str(data["label"]),
            weight=float(data.get("weight", 0.0)),
            chunks=[(str(item[0]), float(item[1])) for item in data.get("chunks", [])],
            neighbors=[(str(item[0]), float(item[1])) for item in data.get("neighbors", [])],
            communities=[
                (str(item[0]), float(item[1]))
                for item in data.get("communities", [])
            ],
        )


@dataclass(slots=True)
class GraphCommunity:
    id: str
    label: str
    weight: float
    terms: list[tuple[str, float]]
    chunk_ids: list[str]
    sources: list[str]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "weight": round(self.weight, 6),
            "terms": [[term, round(score, 6)] for term, score in self.terms],
            "chunk_ids": self.chunk_ids,
            "sources": self.sources,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GraphCommunity":
        return cls(
            id=str(data["id"]),
            label=str(data["label"]),
            weight=float(data.get("weight", 0.0)),
            terms=[(str(item[0]), float(item[1])) for item in data.get("terms", [])],
            chunk_ids=[str(item) for item in data.get("chunk_ids", [])],
            sources=[str(item) for item in data.get("sources", [])],
            summary=str(data.get("summary", "")),
        )


@dataclass(slots=True)
class GraphSearchResult:
    hits: list[SearchHit]
    chunk_boosts: dict[str, float]
    diagnostics: dict[str, Any]


class KnowledgeGraph:
    def __init__(
        self,
        fingerprint: str,
        terms: dict[str, GraphTerm],
        communities: dict[str, GraphCommunity],
        chunk_terms: dict[str, list[str]],
        chunks: list[KnowledgeChunk],
    ):
        self.fingerprint = fingerprint
        self.terms = terms
        self.communities = communities
        self.chunk_terms = chunk_terms
        self.chunks = chunks
        self.chunk_by_id = {chunk.id: chunk for chunk in chunks}

    @classmethod
    def build(cls, chunks: list[KnowledgeChunk]) -> "KnowledgeGraph":
        fingerprint = graph_fingerprint(chunks)
        chunk_terms: dict[str, list[str]] = {}
        term_weight: Counter[str] = Counter()
        term_chunks: dict[str, Counter[str]] = defaultdict(Counter)
        term_communities: dict[str, Counter[str]] = defaultdict(Counter)
        cooccurrence: dict[str, Counter[str]] = defaultdict(Counter)
        community_chunks: dict[str, Counter[str]] = defaultdict(Counter)
        community_terms: dict[str, Counter[str]] = defaultdict(Counter)
        community_sources: dict[str, Counter[str]] = defaultdict(Counter)
        community_labels: dict[str, str] = {}

        for chunk in chunks:
            terms = extract_chunk_terms(chunk, MAX_TERMS_PER_CHUNK)
            if not terms:
                continue
            chunk_terms[chunk.id] = terms
            community_id, community_label = community_for_chunk(chunk)
            community_labels[community_id] = community_label
            weight = chunk_weight(chunk)
            community_chunks[community_id][chunk.id] += weight
            community_sources[community_id][chunk.source] += weight

            for term in terms:
                term_weight[term] += weight
                term_chunks[term][chunk.id] += weight
                term_communities[term][community_id] += weight
                community_terms[community_id][term] += weight

            edge_terms = terms[:MAX_EDGE_TERMS_PER_CHUNK]
            for left, right in combinations(edge_terms, 2):
                edge_weight = 0.4 + weight
                cooccurrence[left][right] += edge_weight
                cooccurrence[right][left] += edge_weight

        terms: dict[str, GraphTerm] = {}
        for term, weight in term_weight.items():
            terms[term] = GraphTerm(
                label=term,
                weight=float(weight),
                chunks=top_counter_items(term_chunks[term], MAX_TERM_CHUNKS),
                neighbors=top_counter_items(cooccurrence[term], 20),
                communities=top_counter_items(term_communities[term], 8),
            )

        communities: dict[str, GraphCommunity] = {}
        for community_id, chunks_counter in community_chunks.items():
            label = community_labels.get(community_id, community_id)
            top_terms = top_counter_items(community_terms[community_id], 16)
            top_chunks = [chunk_id for chunk_id, _ in top_counter_items(chunks_counter, MAX_COMMUNITY_CHUNKS)]
            top_sources = [
                source
                for source, _ in top_counter_items(community_sources[community_id], 8)
            ]
            summary = build_community_summary(label, top_terms, top_chunks, top_sources, chunks)
            communities[community_id] = GraphCommunity(
                id=community_id,
                label=label,
                weight=float(sum(chunks_counter.values())),
                terms=top_terms,
                chunk_ids=top_chunks,
                sources=top_sources,
                summary=summary,
            )

        return cls(
            fingerprint=fingerprint,
            terms=terms,
            communities=communities,
            chunk_terms=chunk_terms,
            chunks=chunks,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": GRAPH_VERSION,
            "fingerprint": self.fingerprint,
            "terms": {key: term.to_dict() for key, term in self.terms.items()},
            "communities": {
                key: community.to_dict()
                for key, community in self.communities.items()
            },
            "chunk_terms": self.chunk_terms,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        chunks: list[KnowledgeChunk],
    ) -> "KnowledgeGraph":
        return cls(
            fingerprint=str(data["fingerprint"]),
            terms={
                str(key): GraphTerm.from_dict(value)
                for key, value in dict(data.get("terms", {})).items()
            },
            communities={
                str(key): GraphCommunity.from_dict(value)
                for key, value in dict(data.get("communities", {})).items()
            },
            chunk_terms={
                str(key): [str(term) for term in value]
                for key, value in dict(data.get("chunk_terms", {})).items()
            },
            chunks=chunks,
        )

    def search(self, query: str, top_k: int = 5) -> GraphSearchResult:
        query = normalize_text(query)
        if not query:
            return GraphSearchResult([], {}, empty_graph_diagnostics())

        query_terms = extract_query_terms(query)
        global_likelihood = global_query_likelihood(query)
        term_scores = self._score_terms(query, query_terms)
        expanded_scores = self._expand_term_scores(term_scores)
        community_scores = self._score_communities(query, expanded_scores, global_likelihood)
        chunk_boosts = self._score_chunks(expanded_scores, community_scores)

        hits: list[SearchHit] = []
        for rank, (community_id, score) in enumerate(top_score_items(community_scores, max(2, top_k)), start=1):
            community = self.communities.get(community_id)
            if community is None:
                continue
            if score < 0.08:
                continue
            hits.append(
                SearchHit(
                    chunk=community_to_chunk(community),
                    score=clamp(score, 0.0, 1.0),
                    rank=rank,
                    signals={
                        "graph_community_score": clamp(score, 0.0, 1.0),
                        "global_likelihood": global_likelihood,
                        "community_weight": community.weight,
                    },
                )
            )

        start_rank = len(hits) + 1
        for offset, (chunk_id, score) in enumerate(top_score_items(chunk_boosts, top_k * 2)):
            chunk = self.chunk_by_id.get(chunk_id)
            if chunk is None:
                continue
            hits.append(
                SearchHit(
                    chunk=chunk,
                    score=clamp(score, 0.0, 1.0),
                    rank=start_rank + offset,
                    signals={
                        "graph_chunk_score": clamp(score, 0.0, 1.0),
                        "global_likelihood": global_likelihood,
                    },
                )
            )

        diagnostics = {
            "mode": "graph_index",
            "terms": len(self.terms),
            "communities": len(self.communities),
            "query_terms": query_terms[:10],
            "matched_terms": [term for term, _ in top_score_items(term_scores, 10)],
            "expanded_terms": [term for term, _ in top_score_items(expanded_scores, 10)],
            "matched_communities": [
                self.communities[key].label
                for key, _ in top_score_items(community_scores, 5)
                if key in self.communities
            ],
            "global_likelihood": round(global_likelihood, 4),
        }
        return GraphSearchResult(
            hits=hits,
            chunk_boosts={
                chunk_id: clamp(score, 0.0, 1.0)
                for chunk_id, score in chunk_boosts.items()
            },
            diagnostics=diagnostics,
        )

    def _score_terms(self, query: str, query_terms: list[str]) -> dict[str, float]:
        query_lower = query.lower()
        query_tokens = set(tokenize(query))
        scores: dict[str, float] = {}
        for label, term in self.terms.items():
            if label in STOP_TERMS or label.lower() in STOP_TERMS_LOWER:
                continue
            label_lower = label.lower()
            score = 0.0
            if label_lower in query_lower:
                score += 1.0
            if any(item.lower() == label_lower for item in query_terms):
                score += 0.85
            label_tokens = set(tokenize(label))
            if query_tokens and label_tokens:
                overlap = len(query_tokens & label_tokens) / max(1, len(label_tokens))
                if overlap >= 0.8:
                    score += 0.35 * overlap
            if score > 0:
                specificity = 0.28 if label in BROAD_TERMS else 1.0
                scores[label] = min(1.0, score) * specificity * (1.0 + min(0.35, term.weight / 600.0))
        return scores

    def _expand_term_scores(self, term_scores: dict[str, float]) -> dict[str, float]:
        expanded = dict(term_scores)
        for term, score in term_scores.items():
            graph_term = self.terms.get(term)
            if graph_term is None:
                continue
            for neighbor, weight in graph_term.neighbors[:12]:
                expanded[neighbor] = max(
                    expanded.get(neighbor, 0.0),
                    score * 0.42 * min(1.0, weight / max(1.0, graph_term.weight)),
                )
        return expanded

    def _score_communities(
        self,
        query: str,
        term_scores: dict[str, float],
        global_likelihood: float,
    ) -> dict[str, float]:
        scores: defaultdict[str, float] = defaultdict(float)
        for term, term_score in term_scores.items():
            graph_term = self.terms.get(term)
            if graph_term is None:
                continue
            for community_id, community_weight in graph_term.communities:
                share = min(1.0, community_weight / max(1.0, graph_term.weight))
                scores[community_id] += term_score * (share ** 0.65)
        query_tokens = set(tokenize(query))
        query_lower = query.lower()
        for community_id, community in self.communities.items():
            label = community.label
            label_lower = label.lower()
            label_tokens = set(tokenize(label))
            label_score = 0.0
            if label and label_lower in query_lower:
                label_score += 3.0
            for term, term_score in term_scores.items():
                if term.lower() in label_lower:
                    label_score += 2.5 * min(1.0, term_score)
            if query_tokens and label_tokens:
                label_score += 0.75 * len(query_tokens & label_tokens) / max(1, len(label_tokens))
            if label_score:
                scores[community_id] += label_score
        for community_id, score in list(scores.items()):
            community = self.communities.get(community_id)
            if community is None:
                continue
            scores[community_id] = (
                score
                * (0.55 + 0.45 * global_likelihood)
                * (1.0 + min(0.20, community.weight / 1800.0))
                * community_priority(community)
            )
        return dict(scores)

    def _score_chunks(
        self,
        term_scores: dict[str, float],
        community_scores: dict[str, float],
    ) -> dict[str, float]:
        scores: defaultdict[str, float] = defaultdict(float)
        for term, term_score in term_scores.items():
            graph_term = self.terms.get(term)
            if graph_term is None:
                continue
            for chunk_id, chunk_score in graph_term.chunks[:50]:
                scores[chunk_id] += term_score * 0.50 * min(1.0, chunk_score / max(1.0, graph_term.weight))
        for community_id, community_score in community_scores.items():
            community = self.communities.get(community_id)
            if community is None:
                continue
            for chunk_id in community.chunk_ids[:24]:
                scores[chunk_id] += community_score * 0.06
        return dict(scores)


class GraphRAGRetriever(AdvancedRetriever):
    """Advanced RAG plus graph communities and entity expansion."""

    def __init__(
        self,
        chunks: list[KnowledgeChunk],
        index_dir: Path | None = None,
        use_cache: bool = True,
        force_graph: bool = False,
    ):
        super().__init__(chunks)
        self.graph = load_or_build_graph(chunks, index_dir, use_cache=use_cache, force=force_graph)

    def search(self, query: str, top_k: int = 5, candidate_k: int = 40) -> list[SearchHit]:
        graph_result = self.graph.search(query, top_k=max(top_k, 4))
        base_hits = super().search(query, top_k=top_k + 4, candidate_k=candidate_k)
        advanced_diagnostics = dict(self.last_diagnostics)
        hits = merge_graph_and_rag_hits(
            base_hits=base_hits,
            graph_hits=graph_result.hits,
            chunk_boosts=graph_result.chunk_boosts,
            top_k=top_k,
        )
        self.last_diagnostics = combine_diagnostics(
            advanced_diagnostics=advanced_diagnostics,
            graph_diagnostics=graph_result.diagnostics,
            hits=hits,
        )
        return hits


def load_or_build_graph(
    chunks: list[KnowledgeChunk],
    index_dir: Path | None,
    use_cache: bool = True,
    force: bool = False,
) -> KnowledgeGraph:
    fingerprint = graph_fingerprint(chunks)
    graph_file = (index_dir or Path(".paimon_index")) / "graph.json"
    if use_cache and not force and graph_file.exists():
        cached = load_graph_cache(graph_file)
        if cached and cached.get("fingerprint") == fingerprint:
            return KnowledgeGraph.from_dict(cached, chunks)

    graph = KnowledgeGraph.build(chunks)
    graph_file.parent.mkdir(parents=True, exist_ok=True)
    graph_file.write_text(
        json.dumps(graph.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return graph


def load_graph_cache(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if data.get("version") != GRAPH_VERSION:
        return None
    if not isinstance(data.get("terms"), dict):
        return None
    if not isinstance(data.get("communities"), dict):
        return None
    return data


def graph_fingerprint(chunks: list[KnowledgeChunk]) -> str:
    lines = [
        f"{chunk.id}:{chunk.source}:{len(chunk.content)}:{chunk.title}"
        for chunk in chunks
    ]
    return stable_hash("\n".join(sorted(lines)), length=24)


def extract_chunk_terms(chunk: KnowledgeChunk, limit: int) -> list[str]:
    parts = [
        chunk.source,
        chunk.title,
        chunk.question,
        chunk.category,
        str(chunk.metadata.get("category_path", "")),
        " ".join(map(str, chunk.metadata.get("keywords", []))),
        chunk.content[:1000],
    ]
    return extract_terms_from_text("\n".join(part for part in parts if part), limit)


def extract_query_terms(query: str) -> list[str]:
    return extract_terms_from_text(query, 10)


def extract_terms_from_text(text: str, limit: int) -> list[str]:
    text = normalize_text(text)
    lowered = text.lower()
    scores: Counter[str] = Counter()

    for term in DOMAIN_TERMS:
        if term.lower() in lowered:
            scores[term] += 12

    for raw in SPLIT_RE.split(text):
        term = clean_term(raw)
        if valid_term(term):
            scores[term] += 6

    for match in PHRASE_RE.finditer(text[:1800]):
        term = clean_term(match.group(0))
        if valid_term(term):
            scores[term] += 3

    for token, count in token_counter(text[:800]).most_common(35):
        term = clean_term(token)
        if valid_term(term):
            scores[term] += min(4, count)

    terms = [
        term
        for term, _ in sorted(scores.items(), key=lambda item: (item[1], len(item[0])), reverse=True)
    ]
    return terms[:limit]


def clean_term(term: str) -> str:
    return term.strip(" \t\r\n-_·:：,，.。;；!?！？()（）[]【】{}<>《》\"'“”‘’")


def valid_term(term: str) -> bool:
    if not term:
        return False
    if term in STOP_TERMS:
        return False
    if term.lower() in STOP_TERMS_LOWER:
        return False
    if len(term) < 2 or len(term) > 32:
        return False
    if term.isdigit():
        return False
    if "http" in term.lower() or "://" in term:
        return False
    if "?" in term or "？" in term:
        return False
    if len(set(term)) == 1:
        return False
    return True


def community_priority(community: GraphCommunity) -> float:
    label = community.label
    if label == "QQ咨询沉淀":
        return 0.25
    if label == "结构化QA":
        return 0.55
    if any("Documents" in source.replace("\\", "/") for source in community.sources):
        return 1.12
    return 1.0


def community_for_chunk(chunk: KnowledgeChunk) -> tuple[str, str]:
    source = chunk.source.replace("\\", "/")
    category_path = str(chunk.metadata.get("category_path", "")).replace("\\", "/")
    path = category_path or source
    parts = [part for part in SPLIT_RE.split(path) if part]
    label = ""
    if "Documents" in parts:
        index = parts.index("Documents")
        if index + 1 < len(parts):
            label = parts[index + 1]
    elif "QQ" in parts:
        label = "QQ咨询沉淀"
    elif "data" in parts:
        label = "data资料"
    if not label:
        kind = str(chunk.metadata.get("kind", ""))
        if kind == "qa":
            label = "结构化QA"
        else:
            label = parts[-2] if len(parts) >= 2 else "通用资料"
    community_id = "community:" + stable_hash(label, length=12)
    return community_id, label


def community_label_from_id(community_id: str) -> str:
    return community_id


def chunk_weight(chunk: KnowledgeChunk) -> float:
    kind = str(chunk.metadata.get("kind", ""))
    source = chunk.source.replace("\\", "/")
    score = 1.0
    if kind == "pdf":
        score += 0.25
    elif kind == "document":
        score += 0.18
    elif kind == "qa":
        score += 0.14
    elif kind == "directory":
        score += 0.10
    if "Documents" in source:
        score += 0.18
    if "QQ" in source:
        score -= 0.18
    return max(0.3, score)


def build_community_summary(
    label: str,
    terms: list[tuple[str, float]],
    chunk_ids: list[str],
    sources: list[str],
    chunks: list[KnowledgeChunk],
) -> str:
    chunk_by_id = {chunk.id: chunk for chunk in chunks}
    term_text = "、".join(term for term, _ in terms[:10]) or "暂无高频主题"
    source_titles: list[str] = []
    for chunk_id in chunk_ids[:8]:
        chunk = chunk_by_id.get(chunk_id)
        if chunk is None:
            continue
        title = chunk.title or chunk.question or Path(chunk.source).name
        if title and title not in source_titles:
            source_titles.append(title)
    source_text = "；".join(source_titles[:6]) or "暂无代表资料"
    return (
        f"GraphRAG社区：{label}\n"
        f"该社区聚合 {len(chunk_ids)} 个知识块，主要主题包括：{term_text}。\n"
        f"代表资料：{source_text}。\n"
        "适合回答总览、分类、资料清单以及跨文档关联问题。"
    )


def community_to_chunk(community: GraphCommunity) -> KnowledgeChunk:
    return KnowledgeChunk(
        id=f"graph:{community.id}",
        title=f"GraphRAG社区：{community.label}",
        source=f"GraphRAG/{community.label}",
        content=community.summary,
        metadata={
            "kind": "graph_community",
            "community_id": community.id,
            "community_label": community.label,
            "terms": [term for term, _ in community.terms],
            "chunk_count": len(community.chunk_ids),
            "representative_sources": community.sources,
        },
    )


def merge_graph_and_rag_hits(
    base_hits: list[SearchHit],
    graph_hits: list[SearchHit],
    chunk_boosts: dict[str, float],
    top_k: int,
) -> list[SearchHit]:
    merged: dict[str, SearchHit] = {}
    global_likelihood = 0.0
    for hit in graph_hits:
        global_likelihood = max(global_likelihood, float(hit.signals.get("global_likelihood", 0.0)))

    for hit in base_hits:
        boost = chunk_boosts.get(hit.chunk.id, 0.0)
        signals = dict(hit.signals)
        signals["graph_boost"] = boost
        score = hit.score + 0.28 * boost
        merged[hit.chunk.id] = SearchHit(
            chunk=hit.chunk,
            score=score,
            rank=hit.rank,
            signals=signals,
        )

    community_added = 0
    for hit in graph_hits:
        kind = hit.chunk.metadata.get("kind")
        if kind == "graph_community":
            if global_likelihood < 0.35 or community_added >= 1:
                continue
            community_added += 1
            community_weight = 0.18 + 0.58 * global_likelihood
            score = 0.24 + 0.55 * community_weight * hit.score
            key = hit.chunk.id
        else:
            score = 0.18 + 0.35 * hit.score
            key = hit.chunk.id
        existing = merged.get(key)
        if existing is None or score > existing.score:
            signals = dict(hit.signals)
            signals["graph_rag_hit"] = 1.0
            merged[key] = SearchHit(
                chunk=hit.chunk,
                score=score,
                rank=hit.rank,
                signals=signals,
            )

    selected = diversify_hits(sorted(merged.values(), key=lambda item: item.score, reverse=True), top_k)
    return [
        SearchHit(chunk=hit.chunk, score=hit.score, rank=index, signals=hit.signals)
        for index, hit in enumerate(selected, start=1)
    ]


def diversify_hits(hits: list[SearchHit], top_k: int) -> list[SearchHit]:
    selected: list[SearchHit] = []
    for hit in hits:
        source = hit.chunk.source
        same_source = sum(1 for item in selected if item.chunk.source == source)
        if same_source >= 2:
            continue
        selected.append(hit)
        if len(selected) >= top_k:
            break
    if len(selected) < top_k:
        for hit in hits:
            if hit not in selected:
                selected.append(hit)
                if len(selected) >= top_k:
                    break
    return selected


def combine_diagnostics(
    advanced_diagnostics: dict[str, Any],
    graph_diagnostics: dict[str, Any],
    hits: list[SearchHit],
) -> dict[str, Any]:
    graph_hit_count = sum(1 for hit in hits if hit.chunk.metadata.get("kind") == "graph_community")
    graph_boosted = sum(1 for hit in hits if hit.signals.get("graph_boost", 0.0) > 0)
    quality = float(advanced_diagnostics.get("quality", 0.0))
    if graph_diagnostics.get("matched_terms"):
        quality = max(quality, min(1.0, quality + 0.08))
    return {
        **advanced_diagnostics,
        "mode": "graph_rag",
        "quality": round(quality, 4),
        "graph": graph_diagnostics,
        "graph_hit_count": graph_hit_count,
        "graph_boosted_hits": graph_boosted,
        "reasons": list(advanced_diagnostics.get("reasons", [])) + ["graph_context_added"],
    }


def top_counter_items(counter: Counter[str], limit: int) -> list[tuple[str, float]]:
    return [
        (str(key), float(value))
        for key, value in counter.most_common(limit)
        if value > 0
    ]


def top_score_items(scores: dict[str, float], limit: int) -> list[tuple[str, float]]:
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)[:limit]


def global_query_likelihood(query: str) -> float:
    query_lower = query.lower()
    matched = sum(1 for term in GLOBAL_QUERY_TERMS if term.lower() in query_lower)
    if matched:
        return min(1.0, 0.42 + 0.18 * matched)
    return 0.12


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def empty_graph_diagnostics() -> dict[str, Any]:
    return {
        "mode": "graph_index",
        "terms": 0,
        "communities": 0,
        "query_terms": [],
        "matched_terms": [],
        "expanded_terms": [],
        "matched_communities": [],
        "global_likelihood": 0.0,
    }
