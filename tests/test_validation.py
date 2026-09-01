from __future__ import annotations

from conftest import synthetic_pages

from get_myhome_ai.normalization import normalize_draft
from get_myhome_ai.validation import validate_draft


def _codes(report) -> set[str]:
    return {issue.code for issue in report.issues}


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
