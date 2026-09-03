from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from get_myhome_ai.models import (
    AnalysisMeta,
    AnalysisResponse,
    AnalysisStatus,
    ReviewStatus,
    TargetUnit,
    ValidationReport,
)
from get_myhome_ai.owned_corpus_extraction import (
    ExtractionJob,
    OwnedCorpusExtractionError,
    load_extraction_jobs,
    run_extraction_jobs,
)
from get_myhome_ai.settings import Settings


def _result(case, *, status: ReviewStatus, digest: str, settings: Settings):
    expected = case.expected
    return AnalysisResponse(
        complex_id=case.complex_id,
        analysis_status=AnalysisStatus.PARTIAL,
        review_status=status,
        reviewer="tester" if status == ReviewStatus.REVIEWED else None,
        reviewed_at=datetime.now(UTC) if status == ReviewStatus.REVIEWED else None,
        target_unit=TargetUnit(
            unit_type_id="01",
            unit_type_name="59A",
            sale_price_manwon=108650,
        ),
        payment_schedule=expected.payment_schedule,
        interim_loan=expected.interim_loan,
        additional_costs=expected.additional_costs,
        risk_clauses=expected.risk_clauses,
        analysis_summary="검수 전 자동 추출 결과",
        holds=[],
        exception_flags=expected.exception_flags,
        evidence=expected.evidence,
        validation=ValidationReport(passed=True, issues=[], derived_fields=[]),
        meta=AnalysisMeta(
            schema_version=settings.schema_version,
            extractor_version=settings.extractor_version,
            prompt_version=settings.prompt_version,
            provider="fixture",
            model="fixture",
            source_sha256=digest,
            source_page_count=52,
            candidate_pages=[],
            analyzed_at=datetime.now(UTC),
        ),
    )


def _inventory(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    pdf = source / "2026000001_1.pdf"
    pdf.write_bytes(b"locked pdf")
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    payload = {
        "schema_version": "owned_corpus_inventory_v1",
        "source_directory": str(source),
        "targets": [
            {
                "complex_id": "2026000001",
                "unit_type_id": "01",
                "unit_type_name": "059.9000A",
                "sale_price_manwon": 50000,
                "pdf_available": True,
                "pdf_path": pdf.name,
                "source_sha256": digest,
                "source_page_count": 10,
            },
            {
                "complex_id": "2026000002",
                "unit_type_id": "01",
                "unit_type_name": "084.0000A",
                "sale_price_manwon": 70000,
                "pdf_available": False,
                "pdf_path": None,
                "source_sha256": None,
                "source_page_count": None,
            },
        ],
    }
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_extraction_jobs_keeps_only_exact_pdf_backed_targets(tmp_path: Path) -> None:
    jobs = load_extraction_jobs(_inventory(tmp_path))

    assert len(jobs) == 1
    assert jobs[0].complex_id == "2026000001"
    assert jobs[0].unit_type_id == "01"
    assert jobs[0].normalized_unit_type_name == "59A"
    assert jobs[0].sale_price_manwon == 50000


def test_load_extraction_jobs_rejects_source_hash_change(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    (tmp_path / "source" / "2026000001_1.pdf").write_bytes(b"changed")

    with pytest.raises(OwnedCorpusExtractionError, match="SHA-256"):
        load_extraction_jobs(inventory)


@pytest.mark.asyncio
async def test_batch_never_promotes_and_resumes(
    tmp_path: Path,
    golden_cases,
) -> None:
    settings = Settings()
    expected = _result(
        golden_cases["2026000372"],
        status=ReviewStatus.AUTO_EXTRACTED,
        digest="a" * 64,
        settings=settings,
    )
    job = ExtractionJob(
        complex_id=expected.complex_id,
        unit_type_id="01",
        unit_type_name="059.9883A",
        normalized_unit_type_name="59A",
        sale_price_manwon=108650,
        pdf_path=tmp_path / "source.pdf",
        source_sha256="a" * 64,
        source_page_count=52,
    )
    calls = 0

    async def analyze(_job: ExtractionJob):
        nonlocal calls
        calls += 1
        return expected.model_copy(deep=True)

    output = tmp_path / "auto"
    first = await run_extraction_jobs(
        jobs=[job],
        output_dir=output,
        settings=settings,
        analyzer=analyze,
    )
    second = await run_extraction_jobs(
        jobs=[job],
        output_dir=output,
        settings=settings,
        analyzer=analyze,
    )

    assert calls == 1
    assert first["completed_target_count"] == 1
    assert second["skipped_target_count"] == 1
    result = json.loads((output / f"{job.key}.json").read_text(encoding="utf-8"))
    assert result["review_status"] == "AUTO_EXTRACTED"
    assert "reviewer" not in result or result["reviewer"] is None


@pytest.mark.asyncio
async def test_batch_rejects_reviewed_result(tmp_path: Path, golden_cases) -> None:
    settings = Settings()
    reviewed = _result(
        golden_cases["2026000372"],
        status=ReviewStatus.REVIEWED,
        digest="b" * 64,
        settings=settings,
    )
    job = ExtractionJob(
        complex_id=reviewed.complex_id,
        unit_type_id="01",
        unit_type_name="059.9883A",
        normalized_unit_type_name="59A",
        sale_price_manwon=108650,
        pdf_path=tmp_path / "source.pdf",
        source_sha256="b" * 64,
        source_page_count=52,
    )

    async def analyze(_job: ExtractionJob):
        return reviewed

    report = await run_extraction_jobs(
        jobs=[job],
        output_dir=tmp_path / "auto",
        settings=settings,
        analyzer=analyze,
    )

    assert report["failed_target_count"] == 1
    assert report["failed"][0]["error_type"] == "OwnedCorpusExtractionError"


@pytest.mark.asyncio
async def test_batch_never_overwrites_existing_reviewed_artifact(
    tmp_path: Path,
    golden_cases,
) -> None:
    settings = Settings()
    reviewed = _result(
        golden_cases["2026000372"],
        status=ReviewStatus.REVIEWED,
        digest="c" * 64,
        settings=settings,
    )
    job = ExtractionJob(
        complex_id=reviewed.complex_id,
        unit_type_id="01",
        unit_type_name="059.9883A",
        normalized_unit_type_name="59A",
        sale_price_manwon=108650,
        pdf_path=tmp_path / "source.pdf",
        source_sha256="c" * 64,
        source_page_count=52,
    )
    output = tmp_path / "auto"
    output.mkdir()
    destination = output / f"{job.key}.json"
    destination.write_text(reviewed.model_dump_json(), encoding="utf-8")
    original = destination.read_bytes()

    async def should_not_run(_job: ExtractionJob):
        raise AssertionError("analyzer must not run")

    report = await run_extraction_jobs(
        jobs=[job],
        output_dir=output,
        settings=settings,
        analyzer=should_not_run,
        force=True,
    )

    assert report["failed_target_count"] == 1
    assert destination.read_bytes() == original


@pytest.mark.asyncio
async def test_force_never_overwrites_unparseable_existing_artifact(
    tmp_path: Path,
    golden_cases,
) -> None:
    settings = Settings()
    automatic = _result(
        golden_cases["2026000372"],
        status=ReviewStatus.AUTO_EXTRACTED,
        digest="e" * 64,
        settings=settings,
    )
    job = ExtractionJob(
        complex_id=automatic.complex_id,
        unit_type_id="01",
        unit_type_name="059.9883A",
        normalized_unit_type_name="59A",
        sale_price_manwon=108650,
        pdf_path=tmp_path / "source.pdf",
        source_sha256="e" * 64,
        source_page_count=52,
    )
    output = tmp_path / "auto"
    output.mkdir()
    destination = output / f"{job.key}.json"
    destination.write_text("unknown protected content", encoding="utf-8")

    async def should_not_run(_job: ExtractionJob):
        raise AssertionError("analyzer must not run")

    report = await run_extraction_jobs(
        jobs=[job],
        output_dir=output,
        settings=settings,
        analyzer=should_not_run,
        force=True,
    )

    assert report["failed_target_count"] == 1
    assert destination.read_text(encoding="utf-8") == "unknown protected content"


@pytest.mark.asyncio
async def test_interrupted_batch_persists_report(tmp_path: Path, golden_cases) -> None:
    settings = Settings()
    automatic = _result(
        golden_cases["2026000372"],
        status=ReviewStatus.AUTO_EXTRACTED,
        digest="d" * 64,
        settings=settings,
    )
    job = ExtractionJob(
        complex_id=automatic.complex_id,
        unit_type_id="01",
        unit_type_name="059.9883A",
        normalized_unit_type_name="59A",
        sale_price_manwon=108650,
        pdf_path=tmp_path / "source.pdf",
        source_sha256="d" * 64,
        source_page_count=52,
    )
    output = tmp_path / "auto"

    async def cancel(_job: ExtractionJob):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_extraction_jobs(
            jobs=[job],
            output_dir=output,
            settings=settings,
            analyzer=cancel,
        )

    report = json.loads((output / "run-report.json").read_text(encoding="utf-8"))
    assert report["run_state"] == "INTERRUPTED"
    assert report["completed_target_count"] == 0
