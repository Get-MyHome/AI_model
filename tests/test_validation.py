from __future__ import annotations

from conftest import synthetic_pages

from get_myhome_ai.models import Evidence
from get_myhome_ai.normalization import normalize_draft, normalize_unit_type_name
from get_myhome_ai.validation import validate_draft


def _codes(report) -> set[str]:
    return {issue.code for issue in report.issues}


def test_backend_unit_type_name_is_normalized_for_pdf_matching() -> None:
    assert normalize_unit_type_name("059.9883A") == "59A"
    assert normalize_unit_type_name("084.7506B") == "84B"
    assert normalize_unit_type_name("59A") == "59A"


def test_normalization_and_validation_accept_correct_golden(golden_cases) -> None:
    case = golden_cases["2026000372"]
    draft, derived = normalize_draft(case.expected)
    report = validate_draft(
        draft,
        pages=synthetic_pages(case),
        derived_fields=derived,
        sale_price_manwon=case.sale_price_manwon,
    )

    assert report.passed
    assert draft.payment_schedule.balance_payment.total_ratio == 0.30
    assert draft.interim_loan.self_funding_ratio == 0.20


def test_detects_ratio_amount_contradiction(golden_cases) -> None:
    case = golden_cases["2026000376"]
    draft = case.expected.model_copy(deep=True)
    draft.payment_schedule.down_payment.total_amount_manwon = 2000
    normalized, derived = normalize_draft(draft)

    report = validate_draft(
        normalized,
        pages=synthetic_pages(case),
        derived_fields=derived,
        sale_price_manwon=case.sale_price_manwon,
    )

    assert "COMPONENT_RATIO_AMOUNT_MISMATCH" in _codes(report)
    assert not report.passed


def test_detects_incomplete_installment_and_cost_sum(golden_cases) -> None:
    case = golden_cases["2026000358"]
    draft = case.expected.model_copy(deep=True)
    installment = draft.payment_schedule.interim_payment.installments[0]
    installment.due_date = None
    installment.due_text = None
    draft.additional_costs[0].total_amount_manwon = 999
    normalized, derived = normalize_draft(draft)

    report = validate_draft(
        normalized,
        pages=synthetic_pages(case),
        derived_fields=derived,
        sale_price_manwon=case.sale_price_manwon,
    )

    assert {"INSTALLMENT_DUE_MISSING", "ADDITIONAL_COST_PAYMENT_SUM_MISMATCH"} <= _codes(report)
    assert not report.passed


def test_overflowing_derived_ratio_becomes_validation_error(golden_cases) -> None:
    case = golden_cases["2026000358"]
    draft = case.expected.model_copy(deep=True)
    draft.payment_schedule.interim_payment.total_ratio = None
    for installment in draft.payment_schedule.interim_payment.installments:
        installment.ratio = 0.30
    normalized, derived = normalize_draft(draft)

    report = validate_draft(
        normalized,
        pages=synthetic_pages(case),
        derived_fields=derived,
        sale_price_manwon=case.sale_price_manwon,
    )

    assert "INSTALLMENT_RATIO_TOTAL_OUT_OF_RANGE" in _codes(report)
    assert not report.passed


def test_parent_loan_quote_cannot_prove_unrelated_fields(golden_cases) -> None:
    case = golden_cases["2026000358"]
    draft = case.expected.model_copy(deep=True)
    draft.evidence = [
        item
        for item in draft.evidence
        if item.field not in {"/interim_loan/interest_type", "/interim_loan/interest_note"}
    ]
    draft.evidence.append(
        Evidence(
            field="/interim_loan",
            page=37,
            raw_text="대출 조건은 중도금 이자후불제로",
        )
    )
    normalized, derived = normalize_draft(draft)

    report = validate_draft(
        normalized,
        pages=synthetic_pages(case),
        derived_fields=derived,
        sale_price_manwon=case.sale_price_manwon,
    )

    missing_fields = {issue.field for issue in report.issues if issue.code == "EVIDENCE_MISSING"}
    assert "/interim_loan/interest_type" in missing_fields
    assert "/interim_loan/interest_note" in missing_fields


def test_missing_balance_due_and_cost_schedule_create_warnings(golden_cases) -> None:
    case = golden_cases["2026000358"]
    draft = case.expected.model_copy(deep=True)
    balance = draft.payment_schedule.balance_payment
    balance.due_date = None
    balance.due_month = None
    balance.due_text = None
    draft.additional_costs[0].payments = []
    normalized, derived = normalize_draft(draft)

    report = validate_draft(
        normalized,
        pages=synthetic_pages(case),
        derived_fields=derived,
        sale_price_manwon=case.sale_price_manwon,
    )

    assert {"BALANCE_DUE_MISSING", "ADDITIONAL_COST_SCHEDULE_MISSING"} <= _codes(report)


def test_one_manwon_additional_cost_rounding_difference_is_allowed(golden_cases) -> None:
    case = golden_cases["2026000358"]
    draft = case.expected.model_copy(deep=True)
    draft.additional_costs[0].total_amount_manwon = 1499
    normalized, derived = normalize_draft(draft)

    report = validate_draft(
        normalized,
        pages=synthetic_pages(case),
        derived_fields=derived,
        sale_price_manwon=case.sale_price_manwon,
    )

    assert "ADDITIONAL_COST_PAYMENT_SUM_MISMATCH" not in _codes(report)
