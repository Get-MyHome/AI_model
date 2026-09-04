from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from pathlib import Path

import pytest

from get_myhome_ai.expanded_audited_repairs_a import (
    _POLICIES,
    EXPANDED_AUDITED_TARGET_COUNT_A,
    ExpandedAuditedRepairError,
    repair_expanded_audited_candidate_a,
)
from get_myhome_ai.models import (
    AdditionalCostType,
    AnalysisResponse,
    AnalyzeRequest,
    HoldReasonCode,
    InterestType,
    LoanArrangementStatus,
    LoanSettlementRequirement,
    ReviewStatus,
)
from get_myhome_ai.pdf_text import DownloadedPdf, PdfPage, extract_pdf_pages, load_pdf_from_path
from get_myhome_ai.pipeline import AnalysisPipeline
from get_myhome_ai.providers.fixture import FixtureExtractor
from get_myhome_ai.settings import Settings

CASES = [
    (complex_id, unit_type_id)
    for complex_id, policy in _POLICIES.items()
    for unit_type_id in policy.targets
]


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


@pytest.fixture(scope="module")
def source_pages(golden_pdf_dir: Path) -> dict[str, list[PdfPage]]:
    settings = Settings(ai_provider="fixture")
    loaded: dict[str, list[PdfPage]] = {}
    for complex_id, policy in _POLICIES.items():
        path = next(golden_pdf_dir.glob(f"{complex_id}_*.pdf"))
        document = load_pdf_from_path(str(path), settings)
        pages = extract_pdf_pages(document.content, settings)
        assert document.sha256 == policy.source_sha256
        assert len(pages) == policy.source_page_count
        loaded[complex_id] = pages
    return loaded


@pytest.fixture(scope="module")
def seed_result(golden_cases) -> AnalysisResponse:
    case = golden_cases["2026000372"]
    content = b"%PDF-expanded-audit-seed"

    async def loader(_url, _settings):
        return DownloadedPdf(content=content, sha256=hashlib.sha256(content).hexdigest())

    pipeline = AnalysisPipeline(
        settings=Settings(ai_provider="fixture"),
        provider=FixtureExtractor({case.complex_id: case.expected}),
        url_loader=loader,
        page_extractor=lambda _content, _settings: [
            PdfPage(number=number, text="seed") for number in range(1, 53)
        ],
    )
    return asyncio.run(
        pipeline.analyze_url(
            AnalyzeRequest(
                complex_id=case.complex_id,
                pdf_url="https://example.com/announcement.pdf",
                unit_type_id="01",
                unit_type_name="059.9883A",
                sale_price_manwon=case.sale_price_manwon,
            )
        )
    )


def _candidate(seed: AnalysisResponse, complex_id: str, unit_type_id: str) -> AnalysisResponse:
    policy = _POLICIES[complex_id]
    target = policy.targets[unit_type_id]
    result = seed.model_copy(deep=True)
    result.complex_id = complex_id
    result.target_unit.unit_type_id = unit_type_id
    result.target_unit.unit_type_name = target.unit_name
    result.target_unit.sale_price_manwon = target.sale_price_manwon
    result.meta.source_sha256 = policy.source_sha256
    result.meta.source_page_count = policy.source_page_count
    result.review_status = ReviewStatus.AUTO_EXTRACTED
    result.reviewer = None
    result.reviewed_at = None
    return result


@pytest.mark.parametrize(("complex_id", "unit_type_id"), CASES)
def test_repairs_are_exactly_grounded_without_granting_review(
    seed_result: AnalysisResponse,
    source_pages: dict[str, list[PdfPage]],
    complex_id: str,
    unit_type_id: str,
) -> None:
    candidate = _candidate(seed_result, complex_id, unit_type_id)
    policy = _POLICIES[complex_id]
    target = policy.targets[unit_type_id]

    corrected = repair_expanded_audited_candidate_a(
        candidate,
        pages=source_pages[complex_id],
    )

    assert corrected is not candidate
    assert corrected.review_status in {ReviewStatus.AUTO_EXTRACTED, ReviewStatus.NEEDS_REVIEW}
    assert corrected.review_status != ReviewStatus.REVIEWED
    assert corrected.reviewer is None
    assert corrected.reviewed_at is None
    assert corrected.validation.passed is True
    assert corrected.interim_loan.arrangement_status == LoanArrangementStatus.PLANNED
    assert corrected.interim_loan.arranged_ratio == pytest.approx(0.60)
    assert corrected.interim_loan.interest_type == InterestType.DEFERRED_INTEREST
    assert corrected.interim_loan.bank_names == []
    assert len(corrected.additional_costs) == 1
    assert corrected.additional_costs[0].type == AdditionalCostType.BALCONY_EXTENSION
    assert corrected.additional_costs[0].total_amount_manwon == target.balcony_total_manwon

    expected_parts = (
        (corrected.payment_schedule.down_payment, target.payment.down_won),
        (corrected.payment_schedule.interim_payment, target.payment.interim_won),
        (corrected.payment_schedule.balance_payment, (target.payment.balance_won,)),
    )
    for component, exact_values in expected_parts:
        assert [item.amount_manwon for item in component.installments] == [
            value // 10_000 if value % 10_000 == 0 else None for value in exact_values
        ]
        total = sum(exact_values)
        assert component.total_amount_manwon == (total // 10_000 if total % 10_000 == 0 else None)

    pages_by_number = {page.number: page for page in source_pages[complex_id]}
    for evidence in corrected.evidence:
        assert _normalized(evidence.raw_text) in _normalized(pages_by_number[evidence.page].text)
    for clause in corrected.risk_clauses:
        for evidence in clause.evidence:
            assert _normalized(evidence.raw_text) in _normalized(
                pages_by_number[evidence.page].text
            )


def test_target_count_and_fail_closed_boundaries(
    seed_result: AnalysisResponse,
    source_pages: dict[str, list[PdfPage]],
) -> None:
    assert EXPANDED_AUDITED_TARGET_COUNT_A == 35
    candidate = _candidate(seed_result, "2026000282", "01")
    candidate.meta.source_sha256 = "0" * 64
    with pytest.raises(ExpandedAuditedRepairError, match="source lock"):
        repair_expanded_audited_candidate_a(candidate, pages=source_pages["2026000282"])

    candidate = _candidate(seed_result, "2026000282", "01")
    candidate.target_unit.sale_price_manwon += 1
    with pytest.raises(ExpandedAuditedRepairError, match="exact unit"):
        repair_expanded_audited_candidate_a(candidate, pages=source_pages["2026000282"])

    unrelated = seed_result.model_copy(deep=True)
    assert repair_expanded_audited_candidate_a(unrelated, pages=[]) is unrelated


def test_tampered_exact_payment_row_is_rejected(
    seed_result: AnalysisResponse,
    source_pages: dict[str, list[PdfPage]],
) -> None:
    candidate = _candidate(seed_result, "2026000323", "01")
    pages = list(source_pages["2026000323"])
    source = pages[6]
    pages[6] = PdfPage(number=source.number, text=source.text.replace("295,000,000", "294,000,000"))
    with pytest.raises(ExpandedAuditedRepairError, match="exact 금액 행"):
        repair_expanded_audited_candidate_a(candidate, pages=pages)


@pytest.mark.parametrize("unit_type_id", ["02", "06", "12"])
def test_half_manwon_values_are_never_truncated(
    seed_result: AnalysisResponse,
    source_pages: dict[str, list[PdfPage]],
    unit_type_id: str,
) -> None:
    corrected = repair_expanded_audited_candidate_a(
        _candidate(seed_result, "2026000282", unit_type_id),
        pages=source_pages["2026000282"],
    )
    assert any(
        item.amount_manwon is None
        for component in (
            corrected.payment_schedule.down_payment,
            corrected.payment_schedule.balance_payment,
        )
        for item in component.installments
    )


def test_0323_generic_84c_abstains_from_subtype_cost(
    seed_result: AnalysisResponse,
    source_pages: dict[str, list[PdfPage]],
) -> None:
    corrected = repair_expanded_audited_candidate_a(
        _candidate(seed_result, "2026000323", "05"),
        pages=source_pages["2026000323"],
    )
    assert corrected.additional_costs[0].total_amount_manwon is None
    assert HoldReasonCode.ADDITIONAL_COST_UNKNOWN in {hold.reason_code for hold in corrected.holds}


def test_loan_semantic_exceptions_remain_visible(
    seed_result: AnalysisResponse,
    source_pages: dict[str, list[PdfPage]],
) -> None:
    conflict = repair_expanded_audited_candidate_a(
        _candidate(seed_result, "2026000282", "03"),
        pages=source_pages["2026000282"],
    )
    assert HoldReasonCode.SOURCE_CONFLICT in {hold.reason_code for hold in conflict.holds}
    assert (
        conflict.interim_loan.settlement_requirement
        == LoanSettlementRequirement.REPAY_OR_CONVERT_TO_MORTGAGE
    )

    unknown = repair_expanded_audited_candidate_a(
        _candidate(seed_result, "2026000323", "03"),
        pages=source_pages["2026000323"],
    )
    assert unknown.interim_loan.settlement_requirement == LoanSettlementRequirement.NOT_STATED
    assert HoldReasonCode.BALANCE_CONVERSION_UNCERTAIN in {
        hold.reason_code for hold in unknown.holds
    }
