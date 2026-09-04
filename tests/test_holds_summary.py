from __future__ import annotations

from conftest import synthetic_pages

from get_myhome_ai.holds import derive_analysis_status, derive_holds
from get_myhome_ai.models import ExceptionFlag, HoldReasonCode
from get_myhome_ai.normalization import normalize_draft
from get_myhome_ai.summary import build_analysis_summary
from get_myhome_ai.validation import validate_draft


def test_holds_and_summary_are_deterministic(golden_cases) -> None:
    case = golden_cases["2026000358"]
    draft, derived = normalize_draft(case.expected)
    report = validate_draft(
        draft,
        pages=synthetic_pages(case),
        derived_fields=derived,
        sale_price_manwon=case.sale_price_manwon,
    )

    first = derive_holds(draft, report, unit_type_name=case.unit_type_name)
    second = derive_holds(draft, report, unit_type_name=case.unit_type_name)

    assert first == second
    assert all(hold.blocking for hold in first)
    assert [hold.reason_code for hold in first] == [
        HoldReasonCode.LOAN_ARRANGEMENT_ONLY,
        HoldReasonCode.BANK_NOT_DISCLOSED,
        HoldReasonCode.SELF_FUNDING_SCHEDULE_UNKNOWN,
        HoldReasonCode.BALANCE_CONVERSION_UNCERTAIN,
    ]
    schedule_hold = next(
        hold
        for hold in first
        if hold.reason_code == HoldReasonCode.SELF_FUNDING_SCHEDULE_UNKNOWN
    )
    assert "알선 범위 밖 중도금" in schedule_hold.message
    assert "직접 납부할 중도금은 확인" not in schedule_hold.message
    assert "각각 어느 회차에 얼마씩 적용" in schedule_hold.next_action
    assert derive_analysis_status(report, first) == "PARTIAL"
    assert build_analysis_summary(draft) == build_analysis_summary(draft)
    assert "분양가의 40% 범위에서 중도금 대출을 알선할 예정" in build_analysis_summary(draft)


def test_unreadable_text_adds_explicit_hold(golden_cases) -> None:
    case = golden_cases["2026000358"]
    draft, derived = normalize_draft(case.expected)
    report = validate_draft(
        draft,
        pages=synthetic_pages(case),
        derived_fields=derived,
        sale_price_manwon=case.sale_price_manwon,
    )
    holds = derive_holds(
        draft,
        report,
        unit_type_name=case.unit_type_name,
        text_available=False,
    )
    assert HoldReasonCode.PDF_TEXT_UNAVAILABLE in {hold.reason_code for hold in holds}


def test_derived_uncovered_ratio_is_not_described_as_cash_only(golden_cases) -> None:
    case = golden_cases["2026000372"]
    draft, _ = normalize_draft(case.expected)

    summary = build_analysis_summary(draft)

    assert "사업장 알선 대출로 충당되지 않아 별도 조달이 필요" in summary
    assert "20%는 직접 납부" not in summary


def test_individual_review_hold_is_non_blocking(golden_cases) -> None:
    case = golden_cases["2026000358"]
    draft, derived = normalize_draft(case.expected)
    draft.exception_flags.append(ExceptionFlag.INDIVIDUAL_REVIEW_NOTED)
    report = validate_draft(
        draft,
        pages=synthetic_pages(case),
        derived_fields=derived,
        sale_price_manwon=case.sale_price_manwon,
    )
    holds = derive_holds(draft, report, unit_type_name=case.unit_type_name)
    personal = next(
        hold for hold in holds if hold.reason_code == HoldReasonCode.INDIVIDUAL_REVIEW_REQUIRED
    )
    assert personal.kind == "PERSONAL_REVIEW"
    assert personal.blocking is False


def test_limited_optional_cost_scope_is_non_blocking_and_does_not_lower_status(
    golden_cases,
) -> None:
    case = golden_cases["2026000358"]
    draft, derived = normalize_draft(case.expected)
    draft.exception_flags = [ExceptionFlag.ADDITIONAL_COST_SCOPE_LIMITED]
    report = validate_draft(
        draft,
        pages=synthetic_pages(case),
        derived_fields=derived,
        sale_price_manwon=case.sale_price_manwon,
    )

    baseline_draft = draft.model_copy(deep=True)
    baseline_draft.exception_flags = []
    baseline_holds = derive_holds(
        baseline_draft,
        report,
        unit_type_name=case.unit_type_name,
    )
    holds = derive_holds(draft, report, unit_type_name=case.unit_type_name)
    scope = next(
        hold
        for hold in holds
        if hold.reason_code == HoldReasonCode.ADDITIONAL_COST_SCOPE_LIMITED
    )

    assert scope.kind == "DOCUMENT_UNCERTAINTY"
    assert scope.blocking is False
    assert derive_analysis_status(report, holds) == derive_analysis_status(
        report,
        baseline_holds,
    )
