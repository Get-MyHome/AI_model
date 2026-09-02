from __future__ import annotations

import hashlib

import pytest
from conftest import synthetic_pages

from get_myhome_ai.contracts.backend_v1 import to_backend_v1
from get_myhome_ai.models import (
    AnalysisStatus,
    LoanSettlementRequirement,
)
from get_myhome_ai.pdf_text import DownloadedPdf
from get_myhome_ai.pipeline import AnalysisPipeline
from get_myhome_ai.providers.fixture import FixtureExtractor
from get_myhome_ai.review import approve_result, load_result, save_result, write_review_sheet
from get_myhome_ai.settings import Settings


async def _result(case):
    pages = synthetic_pages(case)

    async def loader(_url, _settings):
        content = b"%PDF-synthetic"
        return DownloadedPdf(content=content, sha256=hashlib.sha256(content).hexdigest())

    pipeline = AnalysisPipeline(
        settings=Settings(ai_provider="fixture"),
        provider=FixtureExtractor({case.complex_id: case.expected}),
        url_loader=loader,
        page_extractor=lambda _content, _settings: pages,
    )
    from get_myhome_ai.models import AnalyzeRequest

    return await pipeline.analyze_url(
        AnalyzeRequest(
            complex_id=case.complex_id,
            pdf_url="https://example.com/file.pdf",
            unit_type_id="golden-unit",
            unit_type_name=case.unit_type_name,
            sale_price_manwon=case.sale_price_manwon,
        )
    )


async def test_review_artifacts_are_separate_and_round_trip(golden_cases, tmp_path) -> None:
    result = await _result(golden_cases["2026000376"])
    auto_path = tmp_path / "auto" / "result.json"
    sheet_path = tmp_path / "auto" / "result.review.md"
    reviewed_path = tmp_path / "reviewed" / "result.json"

    save_result(result, auto_path)
    write_review_sheet(result, sheet_path)
    reviewed = approve_result(
        load_result(auto_path),
        reviewer="안지홍",
        source_sha256=result.meta.source_sha256,
        pages=synthetic_pages(golden_cases["2026000376"]),
    )
    save_result(reviewed, reviewed_path)

    assert load_result(auto_path).review_status == "AUTO_EXTRACTED"
    assert load_result(reviewed_path).review_status == "REVIEWED"
    assert "공고문 AI 추출 검수표" in sheet_path.read_text(encoding="utf-8")


async def test_review_rejects_different_pdf_or_edited_unsupported_evidence(golden_cases) -> None:
    case = golden_cases["2026000376"]
    result = await _result(case)
    pages = synthetic_pages(case)

    with pytest.raises(ValueError, match="SHA-256"):
        approve_result(
            result,
            reviewer="안지홍",
            source_sha256="0" * 64,
            pages=pages,
        )

    edited = result.model_copy(deep=True)
    edited.evidence[0].raw_text = "원본 PDF에 없는 문장"
    with pytest.raises(ValueError, match="재검증"):
        approve_result(
            edited,
            reviewer="안지홍",
            source_sha256=result.meta.source_sha256,
            pages=pages,
        )


async def test_review_ignores_editable_derived_fields_evidence_bypass(golden_cases) -> None:
    case = golden_cases["2026000358"]
    result = await _result(case)
    edited = result.model_copy(deep=True)
    edited.interim_loan.bank_names = ["근거 없는 은행"]
    edited.validation.derived_fields.append("/interim_loan/bank_names")

    with pytest.raises(ValueError, match="재검증"):
        approve_result(
            edited,
            reviewer="안지홍",
            source_sha256=result.meta.source_sha256,
            pages=synthetic_pages(case),
        )


async def test_review_regrounds_settlement_and_regenerates_outputs(golden_cases) -> None:
    case = golden_cases["2026000358"]
    result = await _result(case)
    edited = result.model_copy(deep=True)
    edited.interim_loan.settlement_requirement = (
        LoanSettlementRequirement.CONTINUE_EXPLICITLY_ALLOWED
    )
    edited.evidence = [
        item
        for item in edited.evidence
        if item.field != "/interim_loan/settlement_requirement"
    ]
    edited.validation.derived_fields.append("/interim_loan/settlement_requirement")
    edited.holds = []
    edited.analysis_status = AnalysisStatus.READY
    edited.analysis_summary = "수정 후 남은 오래된 요약"

    approved = approve_result(
        edited,
        reviewer="안지홍",
        source_sha256=result.meta.source_sha256,
        pages=synthetic_pages(case),
    )

    assert (
        approved.interim_loan.settlement_requirement
        == LoanSettlementRequirement.NOT_STATED
    )
    assert "/interim_loan/settlement_requirement" not in approved.validation.derived_fields
    assert approved.holds
    assert approved.analysis_status == AnalysisStatus.PARTIAL
    assert approved.analysis_summary != "수정 후 남은 오래된 요약"
    assert "상환·대환 조건은 공고문에서 확인하지 못했습니다" in approved.analysis_summary


@pytest.mark.parametrize("complex_id", ["2026000358", "2026000372", "2026000376"])
async def test_legacy_adapter_refuses_lossy_golden_cases(complex_id, golden_cases) -> None:
    legacy = to_backend_v1(await _result(golden_cases[complex_id]))
    assert legacy.paymentSchedule is None
    assert legacy.additionalCosts == []


async def test_legacy_adapter_refuses_invalid_result(golden_cases) -> None:
    result = await _result(golden_cases["2026000376"])
    result.validation.passed = False
    legacy = to_backend_v1(result)
    assert legacy.paymentSchedule is None
    assert legacy.compatibilityWarnings == ["LEGACY_UNSAFE_ANALYSIS_OMITTED"]
