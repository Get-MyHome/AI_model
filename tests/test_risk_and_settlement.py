from __future__ import annotations

import pytest

from get_myhome_ai.candidates import CandidatePage, select_candidate_pages
from get_myhome_ai.models import (
    LoanSettlementRequirement,
    RiskClauseCode,
    ValueOrigin,
)
from get_myhome_ai.normalization import normalize_draft
from get_myhome_ai.pdf_text import extract_pdf_pages, load_pdf_from_path
from get_myhome_ai.pipeline import _empty_draft
from get_myhome_ai.providers.ollama_grounding import ground_ollama_draft
from get_myhome_ai.settings import Settings
from get_myhome_ai.validation import validate_draft

EXPECTED = {
    "2026000358": {
        "settlement": LoanSettlementRequirement.REPAY_OR_CONVERT_TO_MORTGAGE,
        "settlement_page": 38,
        "risks": {
            RiskClauseCode.LOAN_MEDIATION_NOT_GUARANTEED,
            RiskClauseCode.INDIVIDUAL_REVIEW_REQUIRED,
            RiskClauseCode.SELF_FUNDING_REQUIRED,
            RiskClauseCode.INTEREST_PAYMENT_RISK,
        },
    },
    "2026000372": {
        "settlement": LoanSettlementRequirement.REPAY_OR_CONVERT_TO_MORTGAGE,
        "settlement_page": 31,
        "risks": {
            RiskClauseCode.LOAN_MEDIATION_NOT_GUARANTEED,
            RiskClauseCode.INDIVIDUAL_REVIEW_REQUIRED,
            RiskClauseCode.SELF_FUNDING_REQUIRED,
            RiskClauseCode.INTEREST_PAYMENT_RISK,
        },
    },
    "2026000376": {
        "settlement": LoanSettlementRequirement.NOT_APPLICABLE,
        "settlement_page": 6,
        "risks": {RiskClauseCode.LOAN_NOT_AVAILABLE},
    },
}


@pytest.mark.parametrize("complex_id", sorted(EXPECTED))
def test_real_golden_announcements_ground_risks_and_settlement(
    complex_id,
    golden_cases,
    golden_pdf_dir,
) -> None:
    case = golden_cases[complex_id]
    settings = Settings(ai_provider="fixture")
    downloaded = load_pdf_from_path(golden_pdf_dir / case.pdf_filename, settings)
    pages = extract_pdf_pages(downloaded.content, settings)
    candidates = select_candidate_pages(
        pages,
        max_pages=settings.max_candidate_pages,
        max_chars=settings.max_candidate_chars,
    )
    grounded = ground_ollama_draft(
        case.expected.model_copy(deep=True),
        pages=candidates,
        unit_type_name=case.unit_type_name,
        sale_price_manwon=case.sale_price_manwon,
    )
    normalized, derived_fields = normalize_draft(grounded)
    validation = validate_draft(
        normalized,
        pages=pages,
        derived_fields=derived_fields,
        sale_price_manwon=case.sale_price_manwon,
    )

    expected = EXPECTED[complex_id]
    assert normalized.interim_loan.settlement_requirement == expected["settlement"]
    settlement_evidence = next(
        item
        for item in normalized.evidence
        if item.field == "/interim_loan/settlement_requirement"
    )
    assert settlement_evidence.page == expected["settlement_page"]
    assert {item.code for item in normalized.risk_clauses} == expected["risks"]
    assert len(normalized.risk_clauses) == len(expected["risks"])
    if complex_id != "2026000376":
        assert validation.passed, validation.issues
    else:
        assert not [
            issue
            for issue in validation.issues
            if issue.field
            and (
                issue.field.startswith("/risk_clauses")
                or issue.field.startswith("/interim_loan/settlement")
            )
        ]

    page_text = {page.number: page.text for page in pages}
    for risk in normalized.risk_clauses:
        for evidence in risk.evidence:
            assert "".join(evidence.raw_text.split()) in "".join(
                page_text[evidence.page].split()
            )

    self_funding = next(
        (
            item
            for item in normalized.risk_clauses
            if item.code == RiskClauseCode.SELF_FUNDING_REQUIRED
        ),
        None,
    )
    if complex_id == "2026000358":
        assert self_funding is not None
        assert self_funding.origin == ValueOrigin.EXTRACTED
    elif complex_id == "2026000372":
        assert self_funding is not None
        assert self_funding.origin == ValueOrigin.DERIVED
        assert len(self_funding.evidence) == 2


def test_extension_clause_does_not_claim_loan_can_continue() -> None:
    text = (
        "대출기간 만료 시 준공 후 미입주 등의 사유로 금융기관의 "
        "대출 기간 연장이 필요한 경우 별도의 절차없이 동의합니다."
    )
    page = CandidatePage(40, text, 100, frozenset({"settlement", "loan"}))

    grounded = ground_ollama_draft(
        _empty_draft(),
        pages=[page],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert (
        grounded.interim_loan.settlement_requirement
        == LoanSettlementRequirement.NOT_STATED
    )
    assert grounded.interim_loan.extension_contingency_disclosed is True


def test_housing_type_price_difference_is_not_a_loan_risk() -> None:
    text = "주택형별·향별·층별 분양가격은 상이하며 중도금 납부계좌는 동일합니다."
    page = CandidatePage(8, text, 100, frozenset({"payment", "loan"}))

    grounded = ground_ollama_draft(
        _empty_draft(),
        pages=[page],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert RiskClauseCode.TERMS_DIFFER_BY_HOUSING_TYPE not in {
        item.code for item in grounded.risk_clauses
    }
