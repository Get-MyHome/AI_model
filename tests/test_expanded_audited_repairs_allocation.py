from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

import pytest

from get_myhome_ai.expanded_audited_repairs_allocation import (
    _ALLOCATION_EVIDENCE_FIELD,
    _LOAN_END,
    _LOAN_START,
    _POLICIES,
    EXPANDED_ALLOCATION_TARGET_COUNT,
    ExpandedAllocationRepairError,
    has_exact_installment_allocation_evidence,
    repair_expanded_audited_allocation,
)
from get_myhome_ai.models import (
    AdditionalCostType,
    AnalysisResponse,
    AnalyzeRequest,
    InterestType,
    LoanArrangementStatus,
    ReviewStatus,
    ValueOrigin,
)
from get_myhome_ai.pdf_text import DownloadedPdf, PdfPage, extract_pdf_pages, load_pdf_from_path
from get_myhome_ai.pipeline import AnalysisPipeline
from get_myhome_ai.providers.fixture import FixtureExtractor
from get_myhome_ai.review import prepare_review_draft
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
    content = b"%PDF-expanded-allocation-audit-seed"

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


def _repair(
    seed: AnalysisResponse,
    pages: dict[str, list[PdfPage]],
    complex_id: str,
    unit_type_id: str,
) -> AnalysisResponse:
    return repair_expanded_audited_allocation(
        _candidate(seed, complex_id, unit_type_id),
        pages=pages[complex_id],
    )


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_all_keys(item) for item in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value), set())
    return set()


@pytest.mark.parametrize(("complex_id", "unit_type_id"), CASES)
def test_all_11_exact_tuples_repair_from_literal_source_without_approval(
    seed_result: AnalysisResponse,
    source_pages: dict[str, list[PdfPage]],
    complex_id: str,
    unit_type_id: str,
) -> None:
    policy = _POLICIES[complex_id]
    target = policy.targets[unit_type_id]
    candidate = _candidate(seed_result, complex_id, unit_type_id)
    corrected = repair_expanded_audited_allocation(
        candidate,
        pages=source_pages[complex_id],
    )

    assert corrected is not candidate
    assert corrected.review_status != ReviewStatus.REVIEWED
    assert corrected.reviewer is None
    assert corrected.reviewed_at is None
    assert corrected.validation.passed is True

    schedule = corrected.payment_schedule
    assert schedule.down_payment.total_ratio == pytest.approx(0.10)
    assert schedule.interim_payment.total_ratio == pytest.approx(0.60)
    assert schedule.balance_payment.total_ratio == pytest.approx(0.30)
    assert schedule.down_payment.total_amount_manwon == target.sale_price_manwon // 10
    assert schedule.interim_payment.total_amount_manwon == target.sale_price_manwon * 6 // 10
    assert schedule.balance_payment.total_amount_manwon == target.sale_price_manwon * 3 // 10
    assert [item.amount_manwon for item in schedule.interim_payment.installments] == [
        target.sale_price_manwon // 10
    ] * 6
    assert [item.due_date for item in schedule.interim_payment.installments] == list(
        policy.payment_dates
    )
    assert schedule.balance_payment.due_month == policy.move_in_month
    assert schedule.balance_payment.due_text == "입주지정일"

    loan = corrected.interim_loan
    assert loan.arrangement_status == LoanArrangementStatus.PLANNED
    assert loan.arranged_ratio == pytest.approx(0.40)
    assert loan.arranged_amount_manwon == target.sale_price_manwon * 4 // 10
    assert loan.self_funding_ratio == pytest.approx(0.20)
    assert loan.self_funding_amount_manwon == target.sale_price_manwon * 2 // 10
    assert loan.self_funding_origin == ValueOrigin.EXTRACTED
    assert loan.interest_type == InterestType.DEFERRED_INTEREST
    assert loan.bank_names == []

    assert len(corrected.additional_costs) == 1
    balcony = corrected.additional_costs[0]
    assert balcony.type == AdditionalCostType.BALCONY_EXTENSION
    assert balcony.total_amount_manwon == target.balcony_total_manwon
    assert balcony.required is False
    assert balcony.included_in_sale_price is False
    assert balcony.applicable_unit_type == target.unit_name
    if complex_id == "2026000355":
        expected_cost_payments = [
            target.balcony_total_manwon // 10,
            target.balcony_total_manwon // 10,
            target.balcony_total_manwon * 8 // 10,
        ]
    else:
        expected_cost_payments = [
            target.balcony_total_manwon // 10,
            target.balcony_total_manwon * 9 // 10,
        ]
    assert [item.amount_manwon for item in balcony.payments] == expected_cost_payments

    allocation = [item for item in corrected.evidence if item.field == _ALLOCATION_EVIDENCE_FIELD]
    assert len(allocation) == 1
    loan_page = source_pages[complex_id][policy.loan_page - 1].text
    start = loan_page.index(_LOAN_START)
    end = loan_page.index(_LOAN_END, start) + len(_LOAN_END)
    assert allocation[0].raw_text == loan_page[start:end].strip()
    assert "1~4회차 대출을 받았을 경우" in allocation[0].raw_text
    assert "5~6회차 중도금" in allocation[0].raw_text
    assert has_exact_installment_allocation_evidence(corrected)

    pages_by_number = {page.number: page for page in source_pages[complex_id]}
    for evidence in corrected.evidence:
        assert _normalized(evidence.raw_text) in _normalized(pages_by_number[evidence.page].text)
    for clause in corrected.risk_clauses:
        for evidence in clause.evidence:
            assert _normalized(evidence.raw_text) in _normalized(
                pages_by_number[evidence.page].text
            )

    assert "first_shortfall" not in _all_keys(corrected.model_dump(mode="json"))


@pytest.mark.parametrize(("complex_id", "unit_type_id"), CASES)
def test_prepare_review_draft_revalidation_stays_valid_and_keeps_allocation_literal(
    seed_result: AnalysisResponse,
    source_pages: dict[str, list[PdfPage]],
    complex_id: str,
    unit_type_id: str,
) -> None:
    corrected = _repair(seed_result, source_pages, complex_id, unit_type_id)
    policy = _POLICIES[complex_id]

    prepared = prepare_review_draft(
        corrected,
        source_sha256=policy.source_sha256,
        pages=source_pages[complex_id],
    )

    assert prepared.validation.passed is True
    assert prepared.review_status != ReviewStatus.REVIEWED
    assert has_exact_installment_allocation_evidence(prepared)
    assert "first_shortfall" not in _all_keys(prepared.model_dump(mode="json"))


def test_target_count_and_fail_closed_boundaries(
    seed_result: AnalysisResponse,
    source_pages: dict[str, list[PdfPage]],
) -> None:
    assert EXPANDED_ALLOCATION_TARGET_COUNT == 11

    bad_hash = _candidate(seed_result, "2026000355", "01")
    bad_hash.meta.source_sha256 = "0" * 64
    with pytest.raises(ExpandedAllocationRepairError, match="source lock"):
        repair_expanded_audited_allocation(bad_hash, pages=source_pages["2026000355"])

    bad_tuple = _candidate(seed_result, "2026000364", "01")
    bad_tuple.target_unit.sale_price_manwon += 1
    with pytest.raises(ExpandedAllocationRepairError, match="exact unit tuple"):
        repair_expanded_audited_allocation(bad_tuple, pages=source_pages["2026000364"])

    reviewed = _candidate(seed_result, "2026000355", "01")
    reviewed.review_status = ReviewStatus.REVIEWED
    reviewed.reviewer = "human"
    reviewed.reviewed_at = datetime.now(UTC)
    with pytest.raises(ExpandedAllocationRepairError, match="REVIEWED"):
        repair_expanded_audited_allocation(reviewed, pages=source_pages["2026000355"])

    unrelated = seed_result.model_copy(deep=True)
    assert repair_expanded_audited_allocation(unrelated, pages=[]) is unrelated


def test_payment_and_allocation_source_tampering_fail_closed(
    seed_result: AnalysisResponse,
    source_pages: dict[str, list[PdfPage]],
) -> None:
    candidate = _candidate(seed_result, "2026000355", "01")
    pages = list(source_pages["2026000355"])
    source = pages[8]
    pages[8] = PdfPage(
        number=source.number,
        text=source.text.replace("695,900,000", "695,800,000"),
    )
    with pytest.raises(ExpandedAllocationRepairError, match="exact 주택형·금액 행"):
        repair_expanded_audited_allocation(candidate, pages=pages)

    candidate = _candidate(seed_result, "2026000364", "01")
    pages = list(source_pages["2026000364"])
    source = pages[38]
    pages[38] = PdfPage(
        number=source.number,
        text=source.text.replace("5~6회차 중도금", "5회차 중도금"),
    )
    with pytest.raises(ExpandedAllocationRepairError, match="조건문"):
        repair_expanded_audited_allocation(candidate, pages=pages)
