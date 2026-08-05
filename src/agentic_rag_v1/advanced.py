from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .retrieval import Retriever, _searchable_text, reciprocal_rank_fusion
from .schema import KnowledgeChunk, SearchHit
from .text import normalize_text, tokenize


RRF_K = 60
MAX_QUERY_VARIANTS = 9
YEAR_RE = re.compile(r"(20\d{2})")

INTENT_EXPANSIONS: dict[str, list[str]] = {
    "identity": ["统一身份认证", "信息门户", "南大APP", "账号", "密码", "找回", "重置"],
    "campus_card": ["校园卡", "饭卡", "挂失", "补办", "充值", "照片"],
    "admission": ["新生", "入学", "报到", "迎新", "材料", "流程", "通知"],
    "dorm": ["宿舍", "住宿", "床位", "空调", "入住", "仙林", "鼓楼"],
    "medical": ["医保", "校医院", "体检", "报销", "社保卡", "疫苗"],
    "network": ["校园网", "路由器", "NJU-WLAN", "p.nju.edu.cn", "VPN", "认证登录"],
    "course": ["选课", "课程", "考试", "缓考", "补考", "重修", "通识"],
    "major": ["培养方案", "转专业", "分流", "辅修", "二次选拔", "院系"],
    "campus_life": ["社团", "食堂", "校园生活", "志愿", "活动", "交通"],
    "general": ["南京大学", "新生", "指南", "说明", "常见问题"],
}

ROUTE_HINTS: list[tuple[tuple[str, ...], list[str]]] = [
    (("社团", "协会", "招新"), ["南大社团介绍", "社团宣传资料", "社团", "目录"]),
    (("培养方案", "课程体系", "专业"), ["各院系培养方案", "培养方案", "课程"]),
    (("校园网", "路由器", "上网", "NJU-WLAN"), ["校园网相关", "网络", "认证登录"]),
    (("奖学金", "助学金", "资助", "绿色通道"), ["奖助学金", "学生资助", "申请材料"]),
    (("转专业", "分流", "辅修", "二次选拔"), ["辅修", "转专业", "分流", "二次选拔", "方案"]),
    (("校历", "放假", "开学时间"), ["校历", "当前学年", "日程"]),
    (("宿舍", "住宿", "床铺"), ["宿舍", "住宿", "宿舍条件"]),
    (("体检", "医保", "校医院"), ["校医院", "医保", "体检", "报销"]),
    (("交通", "到校", "学生票"), ["交通", "到校交通", "学生证", "学生票"]),
]


@dataclass(slots=True)
class QueryPlan:
    original: str
    intent: str
    variants: list[str]
    route_terms: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Candidate:
    chunk: KnowledgeChunk
    fusion_score: float = 0.0
    best_base_score: float = 0.0
    best_rank: int = 10_000
    query_hits: int = 0
    signals: dict[str, float] = field(default_factory=dict)


class AdvancedRetriever:
    """A campus-domain RAG retriever with query fusion and evidence grading."""

    def __init__(self, chunks: list[KnowledgeChunk]):
        self.chunks = chunks
        self.base = Retriever(chunks)
        self.last_diagnostics: dict[str, Any] = {}

    def search(self, query: str, top_k: int = 5, candidate_k: int = 40) -> list[SearchHit]:
        query = normalize_text(query)
        if not self.chunks or not query:
            self.last_diagnostics = _empty_diagnostics(query)
            return []

        plan = build_query_plan(query)
        candidates = self._fusion_search(plan, candidate_k)
        hits = self._finalize_candidates(query, plan, candidates, top_k, corrective=False)
        diagnostics = self._diagnose(query, plan, hits, corrective=False)

        if self._needs_corrective_pass(diagnostics, hits):
            corrected_plan = add_corrective_queries(plan)
            candidates = self._fusion_search(corrected_plan, max(candidate_k, top_k * 12))
            hits = self._finalize_candidates(
                query,
                corrected_plan,
                candidates,
                top_k,
                corrective=True,
            )
            diagnostics = self._diagnose(query, corrected_plan, hits, corrective=True)

        self.last_diagnostics = diagnostics
        return hits

    def _fusion_search(self, plan: QueryPlan, candidate_k: int) -> list[Candidate]:
        by_chunk: dict[str, Candidate] = {}
        pool_limit = max(12, min(candidate_k, 28))
        for query_index, variant in enumerate(plan.variants):
            hits = self._variant_search(
                variant,
                query_index=query_index,
                top_k=pool_limit,
                candidate_k=candidate_k,
            )
            query_weight = 1.0 if query_index == 0 else 0.88
            for rank, hit in enumerate(hits, start=1):
                chunk_id = hit.chunk.id
                candidate = by_chunk.get(chunk_id)
                if candidate is None:
                    candidate = Candidate(chunk=hit.chunk)
                    by_chunk[chunk_id] = candidate
                candidate.fusion_score += query_weight / (RRF_K + rank)
                candidate.fusion_score += query_weight * min(1.0, hit.score) * 0.018
                candidate.best_base_score = max(candidate.best_base_score, hit.score)
                candidate.best_rank = min(candidate.best_rank, rank)
                candidate.query_hits += 1
                candidate.signals["best_rank"] = float(candidate.best_rank)
                candidate.signals["query_hits"] = float(candidate.query_hits)
                candidate.signals["query_variant_count"] = float(len(plan.variants))
                candidate.signals["base_score"] = candidate.best_base_score
        candidates = list(by_chunk.values())
        candidates.sort(
            key=lambda item: (item.fusion_score, item.best_base_score),
            reverse=True,
        )
        return candidates[: max(candidate_k * 2, pool_limit)]

    def _variant_search(
        self,
        variant: str,
        query_index: int,
        top_k: int,
        candidate_k: int,
    ) -> list[SearchHit]:
        rankings = [self.base.bm25.search(variant, candidate_k)]
        if query_index == 0:
            rankings.append(self.base.ngram.search(variant, candidate_k))
        fused = reciprocal_rank_fusion(rankings)
        reranked = self.base._rerank(variant, fused[:candidate_k])
        return [
            SearchHit(
                chunk=self.chunks[index],
                score=score,
                rank=rank,
                signals=signals,
            )
            for rank, (index, score, signals) in enumerate(reranked[:top_k], start=1)
        ]

    def _finalize_candidates(
        self,
        query: str,
        plan: QueryPlan,
        candidates: list[Candidate],
        top_k: int,
        corrective: bool,
    ) -> list[SearchHit]:
        scored: list[tuple[Candidate, float, dict[str, float]]] = []
        for candidate in candidates:
            chunk = candidate.chunk
            coverage = query_coverage(query, chunk)
            authority = source_authority(chunk)
            freshness = freshness_score(chunk)
            density = evidence_density(chunk)
            route = route_match(chunk, plan.route_terms)
            direct_answer = direct_answer_fit(query, chunk)
            multi_query = min(1.0, candidate.query_hits / max(1, len(plan.variants)))
            final = (
                0.62 * min(1.0, candidate.best_base_score)
                + 2.35 * candidate.fusion_score
                + 0.20 * coverage
                + 0.10 * authority
                + 0.10 * direct_answer
                + 0.07 * route
                + 0.05 * density
                + 0.05 * multi_query
                + 0.035 * freshness
            )
            signals = dict(candidate.signals)
            signals.update(
                {
                    "advanced_score": final,
                    "query_fusion": candidate.fusion_score,
                    "coverage": coverage,
                    "authority": authority,
                    "freshness": freshness,
                    "evidence_density": density,
                    "direct_answer": direct_answer,
                    "route_match": route,
                    "multi_query_support": multi_query,
                    "corrective_pass": 1.0 if corrective else 0.0,
                }
            )
            scored.append((candidate, max(0.0, final), signals))

        scored.sort(key=lambda item: item[1], reverse=True)
        diversified = diversify(scored, top_k)
        hits: list[SearchHit] = []
        for rank, (candidate, score, signals) in enumerate(diversified, start=1):
            hits.append(
                SearchHit(
                    chunk=candidate.chunk,
                    score=score,
                    rank=rank,
                    signals=signals,
                )
            )
        return hits

    def _diagnose(
        self,
        query: str,
        plan: QueryPlan,
        hits: list[SearchHit],
        corrective: bool,
    ) -> dict[str, Any]:
        if not hits:
            return {
                **_empty_diagnostics(query),
                "intent": plan.intent,
                "query_variants": plan.variants,
                "query_variant_count": len(plan.variants),
                "corrective_pass": corrective,
            }

        top_score = hits[0].score
        second_score = hits[1].score if len(hits) > 1 else 0.0
        margin = max(0.0, top_score - second_score)
        coverage = average_signal(hits[:3], "coverage")
        authority = average_signal(hits[:3], "authority")
        freshness = average_signal(hits[:3], "freshness")
        route = average_signal(hits[:3], "route_match")
        multi_query = average_signal(hits[:3], "multi_query_support")
        diversity = len({hit.chunk.source for hit in hits}) / max(1, len(hits))
        stale = any(hit.signals.get("freshness", 0.0) < -0.05 for hit in hits[:3])

        quality = clamp(
            0.32 * min(1.0, top_score / 0.55)
            + 0.20 * coverage
            + 0.18 * authority
            + 0.12 * diversity
            + 0.08 * multi_query
            + 0.06 * route
            + 0.04 * min(1.0, margin / 0.20)
            + 0.02 * max(0.0, freshness),
            0.0,
            1.0,
        )

        reasons: list[str] = []
        if top_score < 0.16:
            reasons.append("low_top_score")
        if coverage < 0.10:
            reasons.append("low_query_coverage")
        if authority < 0.35:
            reasons.append("weak_source_authority")
        if diversity < 0.34 and len(hits) >= 3:
            reasons.append("low_source_diversity")
        if stale:
            reasons.append("possibly_stale_evidence")

        sufficient = top_score >= 0.15 and quality >= 0.28 and coverage >= 0.06
        if not reasons:
            reasons.append("evidence_passed")

        return {
            "mode": "advanced_rag",
            "intent": plan.intent,
            "query_variants": plan.variants,
            "query_variant_count": len(plan.variants),
            "corrective_pass": corrective,
            "quality": round(quality, 4),
            "sufficient": sufficient,
            "top_score": round(top_score, 4),
            "score_margin": round(margin, 4),
            "coverage": round(coverage, 4),
            "authority": round(authority, 4),
            "freshness": round(freshness, 4),
            "source_diversity": round(diversity, 4),
            "multi_query_support": round(multi_query, 4),
            "route_match": round(route, 4),
            "reasons": reasons,
            "recommended_action": "answer" if sufficient else "clarify_or_verify",
        }

    def _needs_corrective_pass(
        self,
        diagnostics: dict[str, Any],
        hits: list[SearchHit],
    ) -> bool:
        if diagnostics.get("corrective_pass"):
            return False
        if not hits:
            return True
        if diagnostics.get("sufficient") is False:
            return True
        return float(diagnostics.get("quality", 0.0)) < 0.34 or len(hits) < 3


def build_query_plan(query: str) -> QueryPlan:
    intent = infer_intent(query)
    variants = [query]
    route_terms: list[str] = []

    for triggers, expansions in ROUTE_HINTS:
        if any(trigger.lower() in query.lower() for trigger in triggers):
            route_terms.extend(expansions)
            variants.append(f"{query} {' '.join(expansions[:4])}")

    intent_terms = INTENT_EXPANSIONS.get(intent, INTENT_EXPANSIONS["general"])
    variants.append(f"{query} {' '.join(intent_terms[:5])}")

    if any(term in query for term in ("怎么", "如何", "怎么办", "流程", "申请", "办理")):
        variants.append(f"{query} 办理流程 步骤 材料 注意事项")
    if any(term in query for term in ("哪些", "有什么", "有哪些", "列表", "汇总", "清单", "介绍")):
        variants.append(f"{query} 资料目录 清单 汇总 介绍")

    key_terms = top_query_terms(query)
    if key_terms:
        variants.append(" ".join(key_terms + intent_terms[:3]))

    if not route_terms:
        route_terms = intent_terms[:4]

    return QueryPlan(
        original=query,
        intent=intent,
        variants=dedupe_variants(variants)[:MAX_QUERY_VARIANTS],
        route_terms=dedupe_variants(route_terms)[:8],
    )


def add_corrective_queries(plan: QueryPlan) -> QueryPlan:
    intent_terms = INTENT_EXPANSIONS.get(plan.intent, INTENT_EXPANSIONS["general"])
    variants = list(plan.variants)
    variants.append(f"南京大学 新生 {plan.original}")
    variants.append(f"{plan.original} {' '.join(intent_terms)} 常见问题 官方通知")
    variants.append(f"{' '.join(plan.route_terms)} {plan.original}")
    return QueryPlan(
        original=plan.original,
        intent=plan.intent,
        variants=dedupe_variants(variants)[:MAX_QUERY_VARIANTS],
        route_terms=plan.route_terms,
    )


def infer_intent(query: str) -> str:
    lowered = query.lower()
    best_intent = "general"
    best_score = 0
    for intent, terms in INTENT_EXPANSIONS.items():
        if intent == "general":
            continue
        score = sum(1 for term in terms if term.lower() in lowered)
        if score > best_score:
            best_intent = intent
            best_score = score
    return best_intent


def top_query_terms(query: str, limit: int = 6) -> list[str]:
    tokens = tokenize(query)
    seen: set[str] = set()
    terms: list[str] = []
    for token in sorted(tokens, key=len, reverse=True):
        if len(token) < 2 or token in seen:
            continue
        if re.fullmatch(r"\d+", token):
            continue
        seen.add(token)
        terms.append(token)
        if len(terms) >= limit:
            break
    return terms


def dedupe_variants(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        normalized = normalize_text(value)
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        seen.add(key)
        output.append(normalized)
    return output


def query_coverage(query: str, chunk: KnowledgeChunk) -> float:
    query_tokens = set(tokenize(query))
    if not query_tokens:
        return 0.0
    doc_tokens = set(tokenize(_searchable_text(chunk)))
    return len(query_tokens & doc_tokens) / max(1, len(query_tokens))


def source_authority(chunk: KnowledgeChunk) -> float:
    source = chunk.source.replace("\\", "/")
    category_path = str(chunk.metadata.get("category_path", "")).replace("\\", "/")
    haystack = f"{source}/{category_path}/{chunk.title}/{chunk.content[:300]}"
    kind = str(chunk.metadata.get("kind", ""))

    score = 0.42
    if kind == "qa":
        score += 0.12
    elif kind == "pdf":
        score += 0.15
    elif kind == "document":
        score += 0.11
    elif kind == "directory":
        score += 0.09

    if "Documents" in source:
        score += 0.14
    if "/data/" in source or source.endswith("/data"):
        score += 0.07
    if "南哪QA" in source:
        score += 0.10
    if "QQ" in source:
        score -= 0.05
    if "南哪助手新生问答指南" in haystack:
        score += 0.08
    if any(term in haystack for term in ("南京大学", "手册", "通知", "办法", "指南", "章程", "培养方案")):
        score += 0.06
    if any(term in haystack for term in ("已失效", "疫情期间")):
        score -= 0.10

    return clamp(score, 0.0, 1.0)


def freshness_score(chunk: KnowledgeChunk) -> float:
    text = f"{chunk.source}\n{chunk.title}\n{chunk.content[:500]}"
    years = [int(match.group(1)) for match in YEAR_RE.finditer(text)]
    if not years:
        return 0.0
    current_year = datetime.now().year
    latest = max(years)
    if latest >= current_year - 1:
        return 0.12
    if latest >= current_year - 2:
        return 0.05
    if latest >= current_year - 4:
        return -0.03
    return -0.12


def evidence_density(chunk: KnowledgeChunk) -> float:
    content = chunk.content
    score = 0.0
    if chunk.question and chunk.answer:
        score += 0.35
    if len(content) >= 180:
        score += 0.20
    if any(marker in content for marker in ("步骤", "材料", "注意", "流程", "申请", "登录")):
        score += 0.20
    if re.search(r"https?://|p\.nju\.edu\.cn|nju\.edu\.cn", content, re.IGNORECASE):
        score += 0.15
    if chunk.metadata.get("page"):
        score += 0.10
    return clamp(score, 0.0, 1.0)


def direct_answer_fit(query: str, chunk: KnowledgeChunk) -> float:
    if chunk.metadata.get("kind") != "qa":
        return 0.0
    source = chunk.source.replace("\\", "/")
    if "QQ" in source:
        return 0.0
    if not chunk.answer:
        return 0.0
    question = chunk.question or chunk.title
    overlap = query_coverage(query, KnowledgeChunk(
        id=chunk.id,
        content=question,
        source=chunk.source,
        title=chunk.title,
        metadata={"question": question, "answer": chunk.answer},
    ))
    base = 0.50
    if "南哪QA" in source:
        base += 0.20
    return clamp(base + 0.30 * overlap, 0.0, 1.0)


def route_match(chunk: KnowledgeChunk, route_terms: list[str]) -> float:
    if not route_terms:
        return 0.0
    haystack = (
        f"{chunk.source}\n{chunk.title}\n{chunk.category}\n"
        f"{chunk.metadata.get('category_path', '')}\n{chunk.content[:500]}"
    ).lower()
    matched = sum(1 for term in route_terms if term.lower() in haystack)
    return min(1.0, matched / max(1, min(4, len(route_terms))))


def diversify(
    scored: list[tuple[Candidate, float, dict[str, float]]],
    top_k: int,
) -> list[tuple[Candidate, float, dict[str, float]]]:
    selected: list[tuple[Candidate, float, dict[str, float]]] = []
    remaining = scored[: max(top_k * 8, top_k)]
    while remaining and len(selected) < top_k:
        best_index = 0
        best_adjusted = -1.0
        for index, (candidate, score, signals) in enumerate(remaining):
            penalty = diversity_penalty(candidate.chunk, selected)
            adjusted = score - penalty
            if adjusted > best_adjusted:
                best_index = index
                best_adjusted = adjusted
        candidate, score, signals = remaining.pop(best_index)
        penalty = diversity_penalty(candidate.chunk, selected)
        adjusted_signals = dict(signals)
        adjusted_signals["diversity_penalty"] = penalty
        selected.append((candidate, max(0.0, score - penalty), adjusted_signals))
    return selected


def diversity_penalty(
    chunk: KnowledgeChunk,
    selected: list[tuple[Candidate, float, dict[str, float]]],
) -> float:
    source = chunk.source
    category = str(chunk.metadata.get("category_path", ""))
    penalty = 0.0
    for selected_candidate, _, _ in selected:
        other = selected_candidate.chunk
        if other.source == source:
            penalty += 0.055
        if category and category == str(other.metadata.get("category_path", "")):
            penalty += 0.025
    return min(0.16, penalty)


def average_signal(hits: list[SearchHit], signal: str) -> float:
    if not hits:
        return 0.0
    return sum(float(hit.signals.get(signal, 0.0)) for hit in hits) / len(hits)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _empty_diagnostics(query: str) -> dict[str, Any]:
    return {
        "mode": "advanced_rag",
        "intent": "general",
        "query_variants": [query] if query else [],
        "query_variant_count": 1 if query else 0,
        "corrective_pass": False,
        "quality": 0.0,
        "sufficient": False,
        "top_score": 0.0,
        "score_margin": 0.0,
        "coverage": 0.0,
        "authority": 0.0,
        "freshness": 0.0,
        "source_diversity": 0.0,
        "multi_query_support": 0.0,
        "route_match": 0.0,
        "reasons": ["no_evidence"],
        "recommended_action": "clarify_or_verify",
    }
