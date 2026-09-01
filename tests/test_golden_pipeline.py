from __future__ import annotations

import json
from pathlib import Path

import pytest

from get_myhome_ai.evaluation import evaluate_case
from get_myhome_ai.pipeline import AnalysisPipeline
from get_myhome_ai.providers.fixture import FixtureExtractor
from get_myhome_ai.settings import Settings

EXPECTED = {
    "2026000358": ("PARTIAL", 57, {5, 6, 37, 38}),
    "2026000372": ("PARTIAL", 52, {5, 6, 7, 31, 33}),
    "2026000376": ("READY", 48, {5, 6}),
}


@pytest.mark.parametrize("complex_id", sorted(EXPECTED))
async def test_actual_pdf_golden_replay(
    complex_id,
    golden_cases,
    golden_pdf_dir,
) -> None:
    case = golden_cases[complex_id]
    provider = FixtureExtractor({key: value.expected for key, value in golden_cases.items()})
    pipeline = AnalysisPipeline(
        settings=Settings(ai_provider="fixture"),
        provider=provider,
    )

    result = await pipeline.analyze_file(
        complex_id=case.complex_id,
        path=str(golden_pdf_dir / case.pdf_filename),
        unit_type_name=case.unit_type_name,
        sale_price_manwon=case.sale_price_manwon,
    )

    expected_status, expected_page_count, required_pages = EXPECTED[complex_id]
    assert result.analysis_status == expected_status
    assert result.validation.passed
    assert result.meta.source_page_count == expected_page_count
    assert required_pages <= set(result.meta.candidate_pages)
    manifest = json.loads(Path("tests/fixtures/golden/MANIFEST.json").read_text(encoding="utf-8"))
    assert result.meta.source_sha256 == manifest[case.pdf_filename]["sha256"]
    assert result.meta.source_page_count == manifest[case.pdf_filename]["page_count"]
    evaluation = evaluate_case(result, case.expected)
    assert evaluation.exact_match
    assert evaluation.evidence_error_count == 0


def test_golden_fixtures_capture_fixed_and_derived_cases(golden_cases) -> None:
    fixed = golden_cases["2026000376"].expected
    assert fixed.payment_schedule.interim_payment.total_amount_manwon == 1000
    assert fixed.payment_schedule.interim_payment.total_ratio is None

    derived = golden_cases["2026000372"].expected
    assert derived.payment_schedule.balance_payment.total_ratio is None
    assert derived.interim_loan.self_funding_ratio is None
