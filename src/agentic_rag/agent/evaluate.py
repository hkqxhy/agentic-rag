from __future__ import annotations

import argparse
import json
import re
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agentic_rag_v1.config import RAGConfig
from agentic_rag_v1.service import HIGH_RISK_CHANNEL_TERMS
from agentic_rag_v1.text import extract_urls

from .runtime import AgentRoute, AgentRuntime

_CITATION_PATTERN = re.compile(r"\[S\d+]")


@dataclass(slots=True, frozen=True)
class EffectCase:
    case_id: str
    question: str
    expected_route: AgentRoute
    expected_grounded: bool
    expect_citations: bool
    expect_llm: bool = False


CASES = (
    EffectCase("greeting", "你好", "direct", True, False),
    EffectCase("fixture_notice", "这是真实的学校通知吗？", "fast_rag", True, True, True),
    EffectCase(
        "fixture_privacy",
        "为什么仓库里没有原始知识库？",
        "research_rag",
        True,
        True,
        True,
    ),
    EffectCase("out_of_scope", "量子计算机如何制冷？", "fast_rag", False, False),
)


def run_effect_evaluation(
    runtime: AgentRuntime,
    *,
    require_llm: bool,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for case in CASES:
        started_at = time.perf_counter()
        outcome = runtime.invoke(case.question, f"effect-{case.case_id}")
        latency_ms = (time.perf_counter() - started_at) * 1000
        generation = outcome.diagnostics.get("generation") or {}
        generation_mode = str(generation.get("mode", outcome.diagnostics.get("mode", "unknown")))
        has_citations = bool(_CITATION_PATTERN.search(outcome.answer))
        evidence_urls = {
            url.rstrip("/")
            for source in outcome.sources
            for url in extract_urls(str(source.get("content", "")))
        }
        unsupported_urls = [
            url
            for url in extract_urls(outcome.answer)
            if url.rstrip("/") not in evidence_urls
        ]
        evidence_text = "\n".join(str(source.get("content", "")) for source in outcome.sources)
        unsupported_channels = sorted(
            {
                term
                for term in HIGH_RISK_CHANNEL_TERMS
                if term in outcome.answer and term not in evidence_text
            }
        )
        checks = {
            "route": outcome.route == case.expected_route,
            "grounded": outcome.grounded == case.expected_grounded,
            "citations": has_citations == case.expect_citations,
            "url_grounding": not unsupported_urls,
            "channel_grounding": not unsupported_channels,
            "llm": not (require_llm and case.expect_llm) or generation_mode == "llm",
        }
        results.append(
            {
                **asdict(case),
                "actual_route": outcome.route,
                "actual_grounded": outcome.grounded,
                "generation_mode": generation_mode,
                "fallback_reason": generation.get("fallback_reason", ""),
                "safety_filter": generation.get("safety_filter", ""),
                "latency_ms": round(latency_ms, 2),
                "source_count": len(outcome.sources),
                "has_citations": has_citations,
                "unsupported_urls": unsupported_urls,
                "unsupported_channels": unsupported_channels,
                "answer_preview": outcome.answer[:240],
                "checks": checks,
                "passed": all(checks.values()),
            }
        )

    latencies = [float(item["latency_ms"]) for item in results]
    llm_cases = [item for item in results if item["expect_llm"]]
    llm_successes = sum(item["generation_mode"] == "llm" for item in llm_cases)
    filtered_responses = sum(bool(item["safety_filter"]) for item in llm_cases)
    llm_latencies = [float(item["latency_ms"]) for item in llm_cases]
    return {
        "passed": all(item["passed"] for item in results),
        "require_llm": require_llm,
        "summary": {
            "case_count": len(results),
            "passed_count": sum(item["passed"] for item in results),
            "route_accuracy": _mean_check(results, "route"),
            "grounding_accuracy": _mean_check(results, "grounded"),
            "citation_accuracy": _mean_check(results, "citations"),
            "url_grounding_accuracy": _mean_check(results, "url_grounding"),
            "channel_grounding_accuracy": _mean_check(results, "channel_grounding"),
            "llm_success_rate": llm_successes / max(1, len(llm_cases)),
            "safety_filtered_rate": filtered_responses / max(1, len(llm_cases)),
            "latency_p50_ms": round(statistics.median(latencies), 2),
            "latency_p95_ms": round(_percentile(latencies, 0.95), 2),
            "llm_latency_p50_ms": round(statistics.median(llm_latencies), 2),
            "llm_latency_p95_ms": round(_percentile(llm_latencies, 0.95), 2),
        },
        "results": results,
    }


def _mean_check(results: list[dict[str, Any]], check: str) -> float:
    return sum(bool(item["checks"][check]) for item in results) / max(1, len(results))


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the bounded Phase 2 Agent effect checks.")
    parser.add_argument("--require-llm", action="store_true")
    args = parser.parse_args()

    runtime = AgentRuntime(RAGConfig.from_env(Path.cwd()))
    payload = run_effect_evaluation(runtime, require_llm=args.require_llm)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not payload["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
