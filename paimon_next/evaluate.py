from __future__ import annotations

import argparse
import html
import json
import statistics
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .advanced import AdvancedRetriever
from .config import DEFAULT_SOURCE_NAMES, RAGConfig
from .graph import GraphRAGRetriever
from .retrieval import BM25Index, Retriever
from .schema import KnowledgeChunk, SearchHit
from .storage import load_or_build_chunks


DEFAULT_SUITE = "regression"
CASE_DIR = Path("eval") / "cases"
DEFAULT_VARIANTS = (
    "no_rag",
    "old_kb_hybrid",
    "full_kb_bm25",
    "full_kb_hybrid",
    "full_kb_advanced",
    "full_kb_graphrag",
)
QUALITY_METRICS = ("top1_hit_rate", "top3_hit_rate", "keyword_coverage", "avg_confidence")
LATENCY_METRIC = "avg_latency_ms"


EVAL_CASES: list[dict[str, Any]] = [
    {
        "id": "identity_password",
        "question": "统一身份认证密码忘了怎么办？",
        "expected_sources": ["南哪QA.qa"],
        "expected_keywords": ["密保", "soft@nju.edu.cn", "重置"],
        "category": "账号服务",
    },
    {
        "id": "campus_card_lost",
        "question": "校园卡丢了怎么补办？",
        "expected_sources": ["南哪QA.qa"],
        "expected_keywords": ["挂失", "补办", "20元"],
        "category": "校园生活",
    },
    {
        "id": "freshman_exam",
        "question": "新生体检要注意什么？",
        "expected_sources": ["南哪QA.qa", "新生入学体检"],
        "expected_keywords": ["南京大学医院", "校园卡", "空腹"],
        "category": "新生入学",
    },
    {
        "id": "student_number",
        "question": "新生如何查询学号？",
        "expected_sources": ["新生学号查询方式", "南哪QA.qa"],
        "expected_keywords": ["迎新系统", "考生号", "身份证"],
        "category": "新生入学",
    },
    {
        "id": "cs_program",
        "question": "计算机科学与技术系培养方案有哪些内容？",
        "expected_sources": ["计算机科学与技术系培养方案.pdf"],
        "expected_keywords": ["计算机科学", "培养方案", "课程"],
        "category": "培养方案",
    },
    {
        "id": "network_router",
        "question": "宿舍校园网和路由器怎么用？",
        "expected_sources": ["南大宿舍校园网与路由器使用指南.pdf"],
        "expected_keywords": ["路由器", "p.nju.edu.cn", "无感知认证"],
        "category": "校园网",
    },
    {
        "id": "clubs_overview",
        "question": "南大有哪些社团可以参加？",
        "expected_sources": ["南大社团介绍"],
        "expected_keywords": ["CAC动漫社", "行远社", "艺术表演类"],
        "category": "校园生活",
    },
    {
        "id": "major_transfer",
        "question": "有哪些辅修转专业分流方案资料？",
        "expected_sources": ["辅修&转专业&分流方案", "分流&转专业&二次选拔"],
        "expected_keywords": ["辅修", "转专业", "分流"],
        "category": "教务",
    },
    {
        "id": "scholarship",
        "question": "奖助学金申请需要看哪些材料？",
        "expected_sources": ["奖助学金"],
        "expected_keywords": ["助学金", "申请", "材料"],
        "category": "资助",
    },
    {
        "id": "english_placement",
        "question": "新生英语分级考试是什么？",
        "expected_sources": ["英语分级考试", "关于英语分级考试"],
        "expected_keywords": ["英语", "分级考试", "新生"],
        "category": "新生入学",
    },
    {
        "id": "freshman_checklist",
        "question": "新生开学物品清单有什么？",
        "expected_sources": ["开学物品清单"],
        "expected_keywords": ["物品", "清单", "新生"],
        "category": "新生入学",
    },
    {
        "id": "second_selection",
        "question": "本科生二次选拔有哪些资料？",
        "expected_sources": ["二次选拔"],
        "expected_keywords": ["二次选拔", "本科生", "方案"],
        "category": "教务",
    },
    {
        "id": "atmosphere_school",
        "question": "请介绍南京大学大气科学学院。",
        "expected_sources": ["大气科学学院"],
        "expected_keywords": ["大气科学", "学院", "专业"],
        "category": "院系介绍",
    },
    {
        "id": "student_ticket",
        "question": "学生证可以享受哪些交通优惠？",
        "expected_sources": ["学生票", "学生证可享受"],
        "expected_keywords": ["学生证", "学生票", "优惠"],
        "category": "交通",
    },
]


@dataclass(slots=True)
class VariantResult:
    variant: str
    case_id: str
    question: str
    answer: str
    latency_ms: float
    confidence: float
    top1_hit: bool
    top3_hit: bool
    keyword_coverage: float
    matched_keywords: list[str]
    sources: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "case_id": self.case_id,
            "question": self.question,
            "answer": self.answer,
            "latency_ms": round(self.latency_ms, 2),
            "confidence": round(self.confidence, 4),
            "top1_hit": self.top1_hit,
            "top3_hit": self.top3_hit,
            "keyword_coverage": round(self.keyword_coverage, 4),
            "matched_keywords": self.matched_keywords,
            "sources": self.sources,
        }


class BM25Retriever:
    def __init__(self, chunks: list[KnowledgeChunk]):
        self.chunks = chunks
        self.index = BM25Index(chunks)

    def search(self, query: str, top_k: int = 5, candidate_k: int = 40) -> list[SearchHit]:
        results = self.index.search(query, candidate_k)[:top_k]
        max_score = max([score for _, score in results], default=1.0)
        hits: list[SearchHit] = []
        for rank, (index, score) in enumerate(results, start=1):
            hits.append(
                SearchHit(
                    chunk=self.chunks[index],
                    score=score / max_score if max_score else 0.0,
                    rank=rank,
                    signals={"bm25": score},
                )
            )
        return hits


def load_eval_cases(
    root: Path,
    suite: str = DEFAULT_SUITE,
    case_file: Path | None = None,
) -> tuple[list[dict[str, Any]], str]:
    if case_file is None:
        default_path = root / CASE_DIR / f"{suite}.jsonl"
        if default_path.exists():
            case_file = default_path

    if case_file is None:
        return validate_eval_cases(EVAL_CASES), "builtin:EVAL_CASES"

    if not case_file.is_absolute():
        case_file = root / case_file
    if not case_file.exists():
        raise FileNotFoundError(f"Evaluation case file not found: {case_file}")

    if case_file.suffix.lower() == ".json":
        raw_cases = json.loads(case_file.read_text(encoding="utf-8"))
        if not isinstance(raw_cases, list):
            raise ValueError(f"Evaluation case JSON must be a list: {case_file}")
    else:
        raw_cases = []
        for line_number, raw_line in enumerate(case_file.read_text(encoding="utf-8").splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                raw_cases.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {case_file}:{line_number}: {exc}") from exc

    return validate_eval_cases(raw_cases), str(case_file)


def validate_eval_cases(raw_cases: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    required = {"id", "question", "expected_sources", "expected_keywords", "category"}
    for index, case in enumerate(raw_cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"Evaluation case #{index} must be an object.")
        missing = required - set(case)
        if missing:
            raise ValueError(f"Evaluation case #{index} is missing: {', '.join(sorted(missing))}")
        case_id = str(case["id"])
        if case_id in seen_ids:
            raise ValueError(f"Duplicate evaluation case id: {case_id}")
        seen_ids.add(case_id)
        normalized = dict(case)
        normalized["id"] = case_id
        normalized["question"] = str(case["question"])
        normalized["category"] = str(case["category"])
        normalized["expected_sources"] = _string_list(case["expected_sources"], "expected_sources", case_id)
        normalized["expected_keywords"] = _string_list(case["expected_keywords"], "expected_keywords", case_id)
        normalized["tags"] = _string_list(case.get("tags", []), "tags", case_id)
        cases.append(normalized)
    if not cases:
        raise ValueError("Evaluation case set is empty.")
    return cases


def _string_list(value: Any, field_name: str, case_id: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} for case {case_id} must be a list of strings.")
    return value


def parse_csv_list(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return default
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    return items or default


def build_variants(
    variant_names: Iterable[str],
    full_chunks: list[KnowledgeChunk],
    old_chunks: list[KnowledgeChunk],
    full_config: RAGConfig,
) -> dict[str, Retriever | BM25Retriever | AdvancedRetriever | GraphRAGRetriever | None]:
    variants: dict[str, Retriever | BM25Retriever | AdvancedRetriever | GraphRAGRetriever | None] = {}
    for name in variant_names:
        if name == "no_rag":
            variants[name] = None
        elif name == "old_kb_hybrid":
            variants[name] = Retriever(old_chunks)
        elif name == "full_kb_bm25":
            variants[name] = BM25Retriever(full_chunks)
        elif name == "full_kb_hybrid":
            variants[name] = Retriever(full_chunks)
        elif name == "full_kb_advanced":
            variants[name] = AdvancedRetriever(full_chunks)
        elif name == "full_kb_graphrag":
            variants[name] = GraphRAGRetriever(
                full_chunks,
                index_dir=full_config.index_dir,
                use_cache=full_config.use_cache,
            )
        else:
            raise ValueError(f"Unknown evaluation variant: {name}")
    if not variants:
        raise ValueError("At least one evaluation variant is required.")
    return variants


def run_experiment(
    root: Path,
    output_dir: Path,
    top_k: int = 5,
    cases: list[dict[str, Any]] | None = None,
    case_source: str = "builtin:EVAL_CASES",
    suite: str = DEFAULT_SUITE,
    variant_names: Iterable[str] = DEFAULT_VARIANTS,
    focus_variant: str | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    started_at = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)
    eval_cases = cases or validate_eval_cases(EVAL_CASES)

    full_config = RAGConfig.from_env(root)
    full_config.llm_base_url = ""
    full_chunks = load_or_build_chunks(full_config)

    old_config = RAGConfig(
        root=root,
        source_paths=[root / name for name in DEFAULT_SOURCE_NAMES[:3]],
        index_dir=root / ".paimon_eval_old_index",
        use_cache=True,
    )
    old_chunks = load_or_build_chunks(old_config)

    variants = build_variants(variant_names, full_chunks, old_chunks, full_config)
    variant_list = list(variants.keys())
    selected_focus_variant = focus_variant if focus_variant in variants else (
        "full_kb_graphrag" if "full_kb_graphrag" in variants else variant_list[-1]
    )

    results: list[VariantResult] = []
    for case in eval_cases:
        for variant_name, retriever in variants.items():
            results.append(evaluate_case(variant_name, retriever, case, top_k))

    result_dicts = [result.to_dict() for result in results]
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "suite": suite,
        "case_source": case_source,
        "top_k": top_k,
        "case_count": len(eval_cases),
        "variants": variant_list,
        "focus_variant": selected_focus_variant,
        "knowledge_base": {
            "old_chunks": len(old_chunks),
            "full_chunks": len(full_chunks),
        },
        "summary": summarize(results),
        "failures": summarize_failures(results, eval_cases, selected_focus_variant),
        "cases": eval_cases,
        "results": result_dicts,
    }

    json_path = output_dir / f"rag_eval_{started_at}.json"
    html_path = output_dir / f"rag_eval_{started_at}.html"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(render_html_report(payload), encoding="utf-8")
    return json_path, html_path, payload


def evaluate_case(
    variant: str,
    retriever: Retriever | BM25Retriever | AdvancedRetriever | GraphRAGRetriever | None,
    case: dict[str, Any],
    top_k: int,
) -> VariantResult:
    start = time.perf_counter()
    if retriever is None:
        hits: list[SearchHit] = []
        answer = "未使用检索资料，无法给出可追溯回答。"
        confidence = 0.0
    else:
        hits = retriever.search(case["question"], top_k=top_k, candidate_k=max(40, top_k * 5))
        answer = extractive_answer(hits)
        confidence = confidence_from_hits(hits)
    latency_ms = (time.perf_counter() - start) * 1000

    sources = [hit.to_source(f"S{index}") for index, hit in enumerate(hits, start=1)]
    top1_hit = source_hit(sources[:1], case["expected_sources"])
    top3_hit = source_hit(sources[:3], case["expected_sources"])
    matched_keywords = [
        keyword
        for keyword in case["expected_keywords"]
        if keyword.lower() in answer.lower()
    ]
    keyword_coverage = len(matched_keywords) / max(1, len(case["expected_keywords"]))

    return VariantResult(
        variant=variant,
        case_id=case["id"],
        question=case["question"],
        answer=answer,
        latency_ms=latency_ms,
        confidence=confidence,
        top1_hit=top1_hit,
        top3_hit=top3_hit,
        keyword_coverage=keyword_coverage,
        matched_keywords=matched_keywords,
        sources=sources,
    )


def extractive_answer(hits: list[SearchHit]) -> str:
    if not hits:
        return "没有检索到相关资料。"
    parts: list[str] = []
    seen: set[str] = set()
    for hit in hits[:2]:
        answer = hit.chunk.answer or hit.chunk.content
        answer = answer.strip()
        if not answer or answer in seen:
            continue
        seen.add(answer)
        parts.append(answer)
    return "\n".join(parts) if parts else hits[0].chunk.content


def confidence_from_hits(hits: list[SearchHit]) -> float:
    if not hits:
        return 0.0
    top = hits[0].score
    second = hits[1].score if len(hits) > 1 else 0.0
    return min(1.0, max(0.0, top / 0.42 + max(0.0, top - second) / 0.24))


def source_hit(sources: list[dict[str, Any]], expected_fragments: list[str]) -> bool:
    for source in sources:
        haystack = "\n".join(
            [
                str(source.get("source", "")),
                str(source.get("title", "")),
                str(source.get("metadata", {}).get("category_path", "")),
                str(source.get("content", ""))[:500],
            ]
        ).lower()
        if any(fragment.lower() in haystack for fragment in expected_fragments):
            return True
    return False


def summarize(results: list[VariantResult]) -> dict[str, dict[str, float]]:
    by_variant: dict[str, list[VariantResult]] = {}
    for result in results:
        by_variant.setdefault(result.variant, []).append(result)
    summary: dict[str, dict[str, float]] = {}
    for variant, items in by_variant.items():
        summary[variant] = {
            "top1_hit_rate": mean([item.top1_hit for item in items]),
            "top3_hit_rate": mean([item.top3_hit for item in items]),
            "keyword_coverage": statistics.mean([item.keyword_coverage for item in items]),
            "avg_confidence": statistics.mean([item.confidence for item in items]),
            "avg_latency_ms": statistics.mean([item.latency_ms for item in items]),
        }
    return summary


def summarize_failures(
    results: list[VariantResult],
    cases: list[dict[str, Any]],
    focus_variant: str,
) -> list[dict[str, Any]]:
    cases_by_id = {case["id"]: case for case in cases}
    failures: list[dict[str, Any]] = []
    for result in results:
        if result.variant != focus_variant:
            continue
        if result.top1_hit and result.top3_hit and result.keyword_coverage >= 1.0:
            continue
        case = cases_by_id[result.case_id]
        missed_keywords = [
            keyword
            for keyword in case["expected_keywords"]
            if keyword not in result.matched_keywords
        ]
        failures.append(
            {
                "case_id": result.case_id,
                "question": result.question,
                "category": case["category"],
                "top1_hit": result.top1_hit,
                "top3_hit": result.top3_hit,
                "keyword_coverage": round(result.keyword_coverage, 4),
                "missed_keywords": missed_keywords,
                "latency_ms": round(result.latency_ms, 2),
                "top_sources": [
                    {
                        "title": source.get("title", ""),
                        "source": source.get("source", ""),
                        "score": source.get("score", 0),
                    }
                    for source in result.sources[:3]
                ],
            }
        )
    return failures


def evaluate_gate(
    payload: dict[str, Any],
    gate_variant: str,
    min_top1: float | None = None,
    min_top3: float | None = None,
    min_keyword_coverage: float | None = None,
    max_avg_latency_ms: float | None = None,
    baseline_payload: dict[str, Any] | None = None,
    fail_on_regression: bool = False,
    regression_tolerance: float = 0.0,
) -> dict[str, Any]:
    summary = payload.get("summary", {})
    current = summary.get(gate_variant)
    failures: list[str] = []
    if current is None:
        return {
            "variant": gate_variant,
            "passed": False,
            "failures": [f"Variant {gate_variant} is not present in this evaluation run."],
        }

    threshold_checks = [
        ("top1_hit_rate", _rate_threshold(min_top1), ">="),
        ("top3_hit_rate", _rate_threshold(min_top3), ">="),
        ("keyword_coverage", _rate_threshold(min_keyword_coverage), ">="),
        ("avg_latency_ms", max_avg_latency_ms, "<="),
    ]
    for metric, threshold, direction in threshold_checks:
        if threshold is None:
            continue
        value = float(current[metric])
        if direction == ">=" and value < threshold:
            failures.append(f"{metric}={value:.4f} below threshold {threshold:.4f}")
        if direction == "<=" and value > threshold:
            failures.append(f"{metric}={value:.2f} above threshold {threshold:.2f}")

    if baseline_payload is not None:
        baseline = baseline_payload.get("summary", {}).get(gate_variant)
        if baseline is None:
            failures.append(f"Baseline does not contain variant {gate_variant}.")
        else:
            regressions = compare_to_baseline(current, baseline, regression_tolerance)
            if fail_on_regression:
                failures.extend(regressions)

    return {
        "variant": gate_variant,
        "passed": not failures,
        "failures": failures,
    }


def compare_to_baseline(
    current: dict[str, Any],
    baseline: dict[str, Any],
    tolerance: float,
) -> list[str]:
    regressions: list[str] = []
    for metric in QUALITY_METRICS:
        current_value = float(current[metric])
        baseline_value = float(baseline[metric])
        if current_value + tolerance < baseline_value:
            regressions.append(
                f"{metric} regressed from {baseline_value:.4f} to {current_value:.4f}"
            )
    current_latency = float(current[LATENCY_METRIC])
    baseline_latency = float(baseline[LATENCY_METRIC])
    if current_latency > baseline_latency + tolerance:
        regressions.append(
            f"{LATENCY_METRIC} regressed from {baseline_latency:.2f} to {current_latency:.2f}"
        )
    return regressions


def _rate_threshold(value: float | None) -> float | None:
    if value is None:
        return None
    return value / 100 if value > 1 else value


def mean(values: list[bool]) -> float:
    return sum(1 for value in values if value) / max(1, len(values))


def render_html_report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    variants = payload["variants"]
    results = payload["results"]
    failures = payload.get("failures", [])
    gate = payload.get("gate")
    cases = {case["id"]: case for case in payload["cases"]}
    rows = "\n".join(render_result_row(result, cases[result["case_id"]]) for result in results)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PAIMON RAG Evaluation</title>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #f6f8fb; color: #172033; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px 18px 46px; }}
    h1 {{ margin: 0 0 6px; }}
    h2 {{ margin-top: 28px; }}
    .muted {{ color: #64748b; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
    .card {{ background: #fff; border: 1px solid #d8dee8; border-radius: 8px; padding: 16px; }}
    table {{ border-collapse: collapse; width: 100%; background: #fff; border: 1px solid #d8dee8; border-radius: 8px; overflow: hidden; }}
    th, td {{ border-bottom: 1px solid #e5eaf1; padding: 9px 10px; text-align: left; vertical-align: top; }}
    th {{ background: #eef2f7; }}
    tr:last-child td {{ border-bottom: 0; }}
    .ok {{ color: #087f5b; font-weight: 700; }}
    .bad {{ color: #c92a2a; font-weight: 700; }}
    .bar {{ display: grid; gap: 10px; }}
    .bar-row {{ display: grid; grid-template-columns: 140px 1fr 70px; gap: 10px; align-items: center; }}
    .track {{ height: 16px; background: #edf2f7; border-radius: 999px; overflow: hidden; }}
    .fill {{ height: 100%; background: #2f6fed; }}
    .gate-pass {{ color: #087f5b; font-weight: 700; }}
    .gate-fail {{ color: #c92a2a; font-weight: 700; }}
    .failure-list {{ display: grid; gap: 12px; }}
    .failure {{ background: #fff; border: 1px solid #f0c2c2; border-left: 4px solid #c92a2a; border-radius: 8px; padding: 12px; }}
    .failure p {{ margin: 4px 0; }}
    code {{ background: #eef2f7; padding: 2px 5px; border-radius: 5px; }}
    @media (max-width: 820px) {{ .grid {{ grid-template-columns: 1fr; }} .bar-row {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
<main>
  <h1>PAIMON RAG 对比实验报告</h1>
  <p class="muted">生成时间：{escape(payload["created_at"])}；套件：{escape(payload.get("suite", ""))}；测试集：{payload["case_count"]} 题；Top-K：{payload["top_k"]}；关注变体：{escape(payload.get("focus_variant", ""))}；旧知识块：{payload["knowledge_base"]["old_chunks"]}；新知识块：{payload["knowledge_base"]["full_chunks"]}</p>
  <p class="muted">Case 来源：{escape(payload.get("case_source", ""))}</p>
  {render_gate_box(gate)}

  <h2>核心指标</h2>
  <div class="grid">
    <div class="card"><h3>Top-3 来源命中率</h3>{render_bar_chart(summary, variants, "top3_hit_rate", True)}</div>
    <div class="card"><h3>答案关键词覆盖率</h3>{render_bar_chart(summary, variants, "keyword_coverage", True)}</div>
    <div class="card"><h3>Top-1 来源命中率</h3>{render_bar_chart(summary, variants, "top1_hit_rate", True)}</div>
    <div class="card"><h3>平均延迟</h3>{render_bar_chart(summary, variants, "avg_latency_ms", False)}</div>
  </div>

  <h2>汇总表</h2>
  {render_summary_table(summary, variants)}

  <h2>失败样例</h2>
  {render_failure_section(failures)}

  <h2>逐题结果</h2>
  <table>
    <thead><tr><th>题目</th><th>变体</th><th>Top1</th><th>Top3</th><th>关键词覆盖</th><th>延迟</th><th>Top 来源</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</main>
</body>
</html>"""


def render_gate_box(gate: dict[str, Any] | None) -> str:
    if not gate:
        return ""
    status_class = "gate-pass" if gate.get("passed") else "gate-fail"
    status = "通过" if gate.get("passed") else "未通过"
    failures = gate.get("failures") or []
    if failures:
        details = "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in failures) + "</ul>"
    else:
        details = "<p class='muted'>所有已配置门禁均满足。</p>"
    return (
        "<h2>门禁结果</h2>"
        f"<div class='card'><p>变体 <code>{escape(gate.get('variant', ''))}</code>："
        f"<span class='{status_class}'>{status}</span></p>{details}</div>"
    )


def render_failure_section(failures: list[dict[str, Any]]) -> str:
    if not failures:
        return "<p class='muted'>关注变体没有 Top-1、Top-3 或关键词覆盖失败项。</p>"
    items = []
    for failure in failures:
        sources = "; ".join(
            f"{source.get('title', '')} ({source.get('score', 0)})"
            for source in failure.get("top_sources", [])
        )
        missed = ", ".join(failure.get("missed_keywords", [])) or "-"
        items.append(
            "<div class='failure'>"
            f"<strong>{escape(failure['case_id'])}</strong>"
            f"<p>{escape(failure['question'])}</p>"
            f"<p class='muted'>Top1: {flag(bool(failure['top1_hit']))}；"
            f"Top3: {flag(bool(failure['top3_hit']))}；"
            f"关键词覆盖：{float(failure['keyword_coverage']) * 100:.0f}%；"
            f"延迟：{float(failure['latency_ms']):.1f} ms</p>"
            f"<p class='muted'>遗漏关键词：{escape(missed)}</p>"
            f"<p class='muted'>Top 来源：{escape(sources or '-')}</p>"
            "</div>"
        )
    return "<div class='failure-list'>" + "".join(items) + "</div>"


def render_bar_chart(
    summary: dict[str, dict[str, float]],
    variants: list[str],
    metric: str,
    percentage: bool,
) -> str:
    values = [summary[variant][metric] for variant in variants]
    max_value = max(values) if values else 1.0
    if percentage:
        max_value = 1.0
    rows = []
    for variant in variants:
        value = summary[variant][metric]
        width = 0 if max_value == 0 else min(100, value / max_value * 100)
        label = f"{value * 100:.1f}%" if percentage else f"{value:.1f} ms"
        rows.append(
            f"""<div class="bar-row"><code>{escape(variant)}</code><div class="track"><div class="fill" style="width:{width:.1f}%"></div></div><span>{label}</span></div>"""
        )
    return f"""<div class="bar">{''.join(rows)}</div>"""


def render_summary_table(summary: dict[str, dict[str, float]], variants: list[str]) -> str:
    rows = []
    for variant in variants:
        item = summary[variant]
        rows.append(
            "<tr>"
            f"<td><code>{escape(variant)}</code></td>"
            f"<td>{item['top1_hit_rate'] * 100:.1f}%</td>"
            f"<td>{item['top3_hit_rate'] * 100:.1f}%</td>"
            f"<td>{item['keyword_coverage'] * 100:.1f}%</td>"
            f"<td>{item['avg_confidence']:.3f}</td>"
            f"<td>{item['avg_latency_ms']:.1f} ms</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>变体</th><th>Top1</th><th>Top3</th><th>关键词覆盖</th>"
        "<th>平均置信度</th><th>平均延迟</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def render_result_row(result: dict[str, Any], case: dict[str, Any]) -> str:
    top_source = result["sources"][0]["title"] if result["sources"] else "-"
    return (
        "<tr>"
        f"<td>{escape(case['question'])}<br><span class='muted'>{escape(case['category'])}</span></td>"
        f"<td><code>{escape(result['variant'])}</code></td>"
        f"<td>{flag(result['top1_hit'])}</td>"
        f"<td>{flag(result['top3_hit'])}</td>"
        f"<td>{result['keyword_coverage'] * 100:.0f}%<br><span class='muted'>{escape(', '.join(result['matched_keywords']))}</span></td>"
        f"<td>{result['latency_ms']:.1f} ms</td>"
        f"<td>{escape(top_source)}</td>"
        "</tr>"
    )


def flag(value: bool) -> str:
    return "<span class='ok'>是</span>" if value else "<span class='bad'>否</span>"


def escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_report(payload: dict[str, Any], json_path: Path, html_path: Path) -> None:
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(render_html_report(payload), encoding="utf-8")


def print_run_summary(payload: dict[str, Any], gate: dict[str, Any] | None = None) -> None:
    focus_variant = payload.get("focus_variant", "")
    summary = payload["summary"].get(focus_variant, {})
    if summary:
        print(
            "Focus "
            f"{focus_variant}: top1={summary['top1_hit_rate'] * 100:.1f}%, "
            f"top3={summary['top3_hit_rate'] * 100:.1f}%, "
            f"keywords={summary['keyword_coverage'] * 100:.1f}%, "
            f"latency={summary['avg_latency_ms']:.1f} ms"
        )
    failures = payload.get("failures") or []
    if failures:
        print(f"Failure summary for {focus_variant}: {len(failures)} case(s)")
        for failure in failures[:8]:
            missed = ", ".join(failure.get("missed_keywords", [])) or "-"
            print(
                f"- {failure['case_id']}: top1={failure['top1_hit']} "
                f"top3={failure['top3_hit']} keywords={failure['keyword_coverage']:.2f} "
                f"missed={missed}"
            )
    else:
        print(f"Failure summary for {focus_variant}: none")
    if gate:
        state = "passed" if gate.get("passed") else "failed"
        print(f"Gate {state} for {gate.get('variant')}")
        for failure in gate.get("failures", []):
            print(f"- {failure}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate PAIMON RAG retrieval quality.")
    parser.add_argument("--root", default=str(Path.cwd()))
    parser.add_argument("--output-dir", default="reports")
    parser.add_argument("--suite", default=DEFAULT_SUITE, help="Case suite under eval/cases/<suite>.jsonl.")
    parser.add_argument("--case-file", default="", help="Explicit JSON or JSONL case file.")
    parser.add_argument("--variants", default=",".join(DEFAULT_VARIANTS), help="Comma-separated variants to run.")
    parser.add_argument("--gate-variant", default="full_kb_graphrag", help="Variant used for failure summary and gates.")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--min-top1", type=float, default=None, help="Minimum Top-1 hit rate, e.g. 0.9 or 90.")
    parser.add_argument("--min-top3", type=float, default=None, help="Minimum Top-3 hit rate, e.g. 0.98 or 98.")
    parser.add_argument("--min-keyword-coverage", type=float, default=None, help="Minimum keyword coverage.")
    parser.add_argument("--max-avg-latency-ms", type=float, default=None, help="Maximum average latency for the gate variant.")
    parser.add_argument("--baseline", default="", help="Previous rag_eval JSON used for regression comparison.")
    parser.add_argument("--fail-on-regression", action="store_true", help="Fail when the gate variant regresses from --baseline.")
    parser.add_argument("--regression-tolerance", type=float, default=0.0, help="Allowed metric drift when comparing to --baseline.")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    case_file = Path(args.case_file) if args.case_file else None
    cases, case_source = load_eval_cases(root=root, suite=args.suite, case_file=case_file)
    variant_names = parse_csv_list(args.variants, DEFAULT_VARIANTS)
    json_path, html_path, payload = run_experiment(
        root=root,
        output_dir=Path(args.output_dir),
        top_k=args.top_k,
        cases=cases,
        case_source=case_source,
        suite=args.suite,
        variant_names=variant_names,
        focus_variant=args.gate_variant,
    )

    baseline_payload = load_report(Path(args.baseline)) if args.baseline else None
    gate_requested = any(
        value is not None
        for value in (
            args.min_top1,
            args.min_top3,
            args.min_keyword_coverage,
            args.max_avg_latency_ms,
        )
    ) or args.fail_on_regression
    gate = None
    if gate_requested:
        gate = evaluate_gate(
            payload,
            gate_variant=args.gate_variant,
            min_top1=args.min_top1,
            min_top3=args.min_top3,
            min_keyword_coverage=args.min_keyword_coverage,
            max_avg_latency_ms=args.max_avg_latency_ms,
            baseline_payload=baseline_payload,
            fail_on_regression=args.fail_on_regression,
            regression_tolerance=args.regression_tolerance,
        )
        payload["gate"] = gate
        write_report(payload, json_path, html_path)

    print(f"Wrote JSON: {json_path}")
    print(f"Wrote HTML: {html_path}")
    print_run_summary(payload, gate)
    if gate and not gate.get("passed"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
