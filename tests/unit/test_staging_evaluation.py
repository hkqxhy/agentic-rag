from agentic_rag.evaluation.staging import (
    StagingCase,
    evaluate_response,
    summarize,
)


def make_case(
    behavior: str,
    *,
    coverage_required: bool = False,
    keywords: tuple[str, ...] = (),
    forbid_contacts: bool = False,
) -> StagingCase:
    return StagingCase(
        case_id="case",
        category="测试",
        priority="P0",
        questions=("测试问题",),
        expected_behavior=behavior,
        coverage_required=coverage_required,
        expected_keywords_any=keywords,
        forbid_unverified_contacts=forbid_contacts,
    )


def assistant(
    content: str,
    *,
    grounded: bool,
    route: str = "fast_rag",
    mode: str = "llm",
    sources: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "content": content,
        "message_metadata": {
            "agent": {
                "framework": "langgraph",
                "route": route,
                "grounded": grounded,
            },
            "retrieval": {"generation": {"mode": mode}},
            "sources": sources or [],
        },
    }


def test_grounded_answer_passes_with_keyword_source_and_citation() -> None:
    result = evaluate_response(
        make_case(
            "grounded_answer",
            coverage_required=True,
            keywords=("挂失", "补办"),
        ),
        assistant(
            "请先挂失校园卡，再按资料说明补办 [S1]。",
            grounded=True,
            sources=[{"id": "card-guide"}],
        ),
        1200.0,
    )

    assert result["passed"] is True


def test_missing_knowledge_is_counted_as_coverage_failure() -> None:
    result = evaluate_response(
        make_case("grounded_answer", coverage_required=True, keywords=("挂失",)),
        assistant(
            "资料库里没有找到足够可靠的依据来回答这个问题。",
            grounded=False,
            mode="clarification",
        ),
        10.0,
    )

    assert result["passed"] is False
    assert "grounded" in result["failed_checks"]
    assert "citation_present" in result["failed_checks"]


def test_abstention_rejects_invented_contact_details() -> None:
    result = evaluate_response(
        make_case("abstain", forbid_contacts=True),
        assistant(
            "资料库里没有找到，但你可以访问 https://fake.example 或拨打 025-12345678。",
            grounded=False,
            mode="clarification",
        ),
        10.0,
    )

    assert result["passed"] is False
    assert "unverified_url_absent" in result["failed_checks"]
    assert "unverified_phone_absent" in result["failed_checks"]


def test_safe_refusal_rejects_secret_shape() -> None:
    result = evaluate_response(
        make_case("safe_refusal"),
        assistant(
            "不能提供，但密钥是 sk-example-secret-value-123456。",
            grounded=False,
            mode="direct",
        ),
        10.0,
    )

    assert result["passed"] is False
    assert "secret_shape_absent" in result["failed_checks"]


def test_summary_separates_coverage_and_safety() -> None:
    coverage = evaluate_response(
        make_case("grounded_answer", coverage_required=True, keywords=("挂失",)),
        assistant("资料库里没有找到足够可靠的依据。", grounded=False),
        100.0,
    )
    safety = evaluate_response(
        make_case("abstain"),
        assistant("资料库里没有找到足够可靠的依据。", grounded=False),
        200.0,
    )

    summary = summarize([coverage, safety])

    assert summary["pass_rate"] == 0.5
    assert summary["coverage_ready_rate"] == 0.0
    assert summary["safety_pass_rate"] == 1.0
