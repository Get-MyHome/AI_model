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
    ]
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
