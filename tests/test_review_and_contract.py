from __future__ import annotations

import hashlib

import pytest
from conftest import synthetic_pages

from get_myhome_ai.contracts.backend_v1 import to_backend_v1
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
    reviewed = approve_result(load_result(auto_path), reviewer="안지홍")
    save_result(reviewed, reviewed_path)

    assert load_result(auto_path).review_status == "AUTO_EXTRACTED"
    assert load_result(reviewed_path).review_status == "REVIEWED"
    assert "공고문 AI 추출 검수표" in sheet_path.read_text(encoding="utf-8")


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
