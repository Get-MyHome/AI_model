from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from pathlib import Path

import pytest

import get_myhome_ai.funding_stress as funding_stress_module
from get_myhome_ai.expanded_audited_candidate_repairs import (
    EXPANDED_AUDITED_POLICY_DATA,
)
from get_myhome_ai.models import (
    AnalysisResponse,
    AnalyzeRequest,
    ExceptionFlag,
    ReviewStatus,
)
from get_myhome_ai.pdf_text import DownloadedPdf, PdfPage, extract_pdf_pages, load_pdf_from_path
from get_myhome_ai.pipeline import AnalysisPipeline
from get_myhome_ai.providers.fixture import FixtureExtractor
from get_myhome_ai.review_candidate_correction import correct_audited_review_candidate
from get_myhome_ai.settings import Settings
from get_myhome_ai.stress_models import StressHoldCode

CASES = [
    (complex_id, unit_type_id, unit_name, sale_price)
    for complex_id, values in EXPANDED_AUDITED_POLICY_DATA.items()
    for unit_type_id, unit_name, sale_price in values["targets"]
]


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


@pytest.fixture(scope="module")
def source_pages(golden_pdf_dir: Path) -> dict[str, list[PdfPage]]:
    settings = Settings(ai_provider="fixture")
    loaded: dict[str, list[PdfPage]] = {}
    for complex_id, values in EXPANDED_AUDITED_POLICY_DATA.items():
        path = next(golden_pdf_dir.glob(f"{complex_id}_*.pdf"))
        document = load_pdf_from_path(str(path), settings)
        pages = extract_pdf_pages(document.content, settings)
        assert document.sha256 == values["source_sha256"]
        assert len(pages) == values["source_page_count"]
        loaded[complex_id] = pages
    return loaded


@pytest.fixture(scope="module")
def seed_result(golden_cases) -> AnalysisResponse:
    case = golden_cases["2026000372"]
    content = b"%PDF-expanded-audited-candidate-seed"

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


def _candidate(
    seed: AnalysisResponse,
    complex_id: str,
    unit_type_id: str,
    unit_name: str,
    sale_price: int,
) -> AnalysisResponse:
    values = EXPANDED_AUDITED_POLICY_DATA[complex_id]
    result = seed.model_copy(deep=True)
    result.complex_id = complex_id
    result.target_unit.unit_type_id = unit_type_id
    result.target_unit.unit_type_name = unit_name
    result.target_unit.sale_price_manwon = sale_price
    result.meta.source_sha256 = values["source_sha256"]
    result.meta.source_page_count = values["source_page_count"]
    result.review_status = ReviewStatus.AUTO_EXTRACTED
    result.reviewer = None
    result.reviewed_at = None
    return result


def _repair(
    seed: AnalysisResponse,
    pages: dict[str, list[PdfPage]],
    complex_id: str,
    unit_type_id: str,
    unit_name: str,
    sale_price: int,
) -> AnalysisResponse:
    values = EXPANDED_AUDITED_POLICY_DATA[complex_id]
    corrected, _ = correct_audited_review_candidate(
        _candidate(seed, complex_id, unit_type_id, unit_name, sale_price),
        source_sha256=values["source_sha256"],
        pages=pages[complex_id],
    )
    return corrected


@pytest.mark.parametrize(
    ("complex_id", "unit_type_id", "unit_name", "sale_price"),
    CASES,
)
def test_all_19_expanded_tuples_are_source_locked_pending_drafts(
    seed_result: AnalysisResponse,
    source_pages: dict[str, list[PdfPage]],
    complex_id: str,
    unit_type_id: str,
    unit_name: str,
    sale_price: int,
) -> None:
    corrected = _repair(
        seed_result,
        source_pages,
        complex_id,
        unit_type_id,
        unit_name,
        sale_price,
    )

    assert corrected.review_status == ReviewStatus.AUTO_EXTRACTED
    assert corrected.reviewer is None
    assert corrected.reviewed_at is None
    assert corrected.validation.passed is True
    assert ExceptionFlag.ADDITIONAL_COST_SCOPE_LIMITED in corrected.exception_flags

    page_map = {page.number: page for page in source_pages[complex_id]}
    for evidence in corrected.evidence:
        assert _normalized(evidence.raw_text) in _normalized(page_map[evidence.page].text)


def test_half_manwon_core_tuple_abstains_in_funding_stress(
    seed_result: AnalysisResponse,
    source_pages: dict[str, list[PdfPage]],
) -> None:
    corrected = _repair(seed_result, source_pages, "2026000331", "03", "99", 71_035)

    assert corrected.payment_schedule.down_payment.total_amount_manwon is None
    assert corrected.payment_schedule.down_payment.installments[1].amount_manwon is None
    assert corrected.payment_schedule.balance_payment.total_amount_manwon is None
    obligations, holds = funding_stress_module._obligations(corrected, 71_035)
    assert obligations.contract is None
    assert obligations.balance is None
    assert StressHoldCode.PAYMENT_VALUE_UNKNOWN in {hold.code for hold in holds}


def test_sub_manwon_balcony_keeps_total_but_abstains_installment_amounts(
    seed_result: AnalysisResponse,
    source_pages: dict[str, list[PdfPage]],
) -> None:
    corrected = _repair(seed_result, source_pages, "2026000342", "01", "84A", 51_800)
    balcony = corrected.additional_costs[0]

    assert balcony.total_amount_manwon == 1_045
    assert [payment.amount_manwon for payment in balcony.payments] == [None, None, None]


def test_mandatory_free_balcony_is_an_included_fact_not_zero_cost_selection(
    seed_result: AnalysisResponse,
    source_pages: dict[str, list[PdfPage]],
) -> None:
    corrected = _repair(seed_result, source_pages, "2026000354", "01", "84A", 163_000)
    balcony = corrected.additional_costs[0]

    assert balcony.required is True
    assert balcony.included_in_sale_price is True
    assert balcony.total_amount_manwon is None
    assert balcony.payments == []
