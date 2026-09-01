from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from conftest import synthetic_pages

from get_myhome_ai.models import AnalyzeRequest, ReviewStatus
from get_myhome_ai.pipeline import AnalysisPipeline
from get_myhome_ai.providers.fixture import FixtureExtractor
from get_myhome_ai.review import save_result
from get_myhome_ai.reviewed_store import find_reviewed_artifact
from get_myhome_ai.settings import Settings


def _reviewed_copy(result, *, unit_type_id: str | None = None):
    reviewed = result.model_copy(deep=True)
    reviewed.review_status = ReviewStatus.REVIEWED
    reviewed.reviewer = "TEST_REVIEWER"
    reviewed.reviewed_at = datetime.now(UTC)
    if unit_type_id is not None:
        reviewed.target_unit.unit_type_id = unit_type_id
    return reviewed


async def _automatic_result(tmp_path: Path, case):
    pdf_path = tmp_path / "source.pdf"
    pdf_path.write_bytes(b"%PDF-synthetic")
    settings = Settings(ai_provider="fixture")
    pipeline = AnalysisPipeline(
        settings=settings,
        provider=FixtureExtractor({case.complex_id: case.expected}),
        page_extractor=lambda _content, _settings: synthetic_pages(case),
    )
    result = await pipeline.analyze_file(
        complex_id=case.complex_id,
        path=str(pdf_path),
        unit_type_id="01",
        unit_type_name=case.unit_type_name,
        sale_price_manwon=case.sale_price_manwon,
    )
    assert result.validation.passed
    return result


async def test_exact_source_and_target_review_is_selected(tmp_path: Path, golden_cases) -> None:
    case = golden_cases["2026000358"]
    automatic = await _automatic_result(tmp_path, case)
    reviewed = _reviewed_copy(automatic)
    save_result(reviewed, tmp_path / "reviewed.json")
    request = AnalyzeRequest(
        complex_id=case.complex_id,
        pdf_url="https://example.com/source.pdf",
        unit_type_id="01",
        unit_type_name=case.unit_type_name,
        sale_price_manwon=case.sale_price_manwon,
    )

    actual = find_reviewed_artifact(
        request=request,
        source_sha256=automatic.meta.source_sha256,
        reviewed_artifact_dir=tmp_path,
        schema_version="v0.3",
    )

    assert actual is not None
    assert actual.review_status == ReviewStatus.REVIEWED
    assert actual.reviewer == "TEST_REVIEWER"


async def test_stale_or_wrong_target_review_is_never_selected(tmp_path: Path, golden_cases) -> None:
    case = golden_cases["2026000358"]
    automatic = await _automatic_result(tmp_path, case)
    wrong_target = _reviewed_copy(automatic, unit_type_id="02")
    save_result(wrong_target, tmp_path / "wrong-target.json")
    stale = _reviewed_copy(automatic)
    stale.meta.source_sha256 = "0" * 64
    save_result(stale, tmp_path / "stale.json")
    request = AnalyzeRequest(
        complex_id=case.complex_id,
        pdf_url="https://example.com/source.pdf",
        unit_type_id="01",
        unit_type_name=case.unit_type_name,
        sale_price_manwon=case.sale_price_manwon,
    )

    actual = find_reviewed_artifact(
        request=request,
        source_sha256=automatic.meta.source_sha256,
        reviewed_artifact_dir=tmp_path,
        schema_version="v0.3",
    )

    assert actual is None
