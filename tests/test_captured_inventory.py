from __future__ import annotations

import hashlib
import json

import pytest

from get_myhome_ai.captured_inventory import (
    CapturedInventoryError,
    build_captured_inventory,
)
from get_myhome_ai.models import AnalysisResponse, AnalyzeRequest
from get_myhome_ai.pdf_text import DownloadedPdf
from get_myhome_ai.review_capture import capture_review_result, capture_review_source


def _capture(golden_cases, tmp_path):
    case = golden_cases["2026000358"]
    content = b"%PDF-captured-inventory"
    source_sha256 = hashlib.sha256(content).hexdigest()
    request = AnalyzeRequest(
        complex_id=case.complex_id,
        pdf_url="https://example.com/source.pdf",
        unit_type_id="01",
        unit_type_name=case.unit_type_name,
        sale_price_manwon=case.sale_price_manwon,
    )
    capture_dir = tmp_path / "capture"
    request_key = capture_review_source(
        capture_dir,
        request,
        DownloadedPdf(content=content, sha256=source_sha256),
    )
    result = AnalysisResponse(
        complex_id=case.complex_id,
        analysis_status="PARTIAL",
        review_status="AUTO_EXTRACTED",
        reviewer=None,
        reviewed_at=None,
        target_unit={
            "unit_type_id": "01",
            "unit_type_name": case.unit_type_name,
            "sale_price_manwon": case.sale_price_manwon,
        },
        payment_schedule=case.expected.payment_schedule,
        interim_loan=case.expected.interim_loan,
        additional_costs=case.expected.additional_costs,
        risk_clauses=[],
        analysis_summary="검수 전 자동결과",
        holds=[],
        exception_flags=[],
        evidence=case.expected.evidence,
        validation={"passed": True, "issues": [], "derived_fields": []},
        meta={
            "schema_version": "v0.3",
            "extractor_version": "0.2.0",
            "prompt_version": "extract-v1",
            "provider": "fixture",
            "model": "fixture",
            "source_sha256": source_sha256,
            "source_page_count": 58,
            "candidate_pages": [1],
            "analyzed_at": "2026-09-04T00:00:00Z",
        },
    )
    capture_review_result(capture_dir, request_key, result)
    return capture_dir, source_sha256


def test_builds_exact_inventory_from_url_free_capture(golden_cases, tmp_path) -> None:
    capture_dir, source_sha256 = _capture(golden_cases, tmp_path)
    output = tmp_path / "captured-inventory.json"

    payload = build_captured_inventory(capture_dir=capture_dir, output_path=output)

    stored = json.loads(output.read_text(encoding="utf-8"))
    assert payload == stored
    assert payload["summary"] == {
        "captured_target_tuple_count": 1,
        "pdf_document_count": 1,
    }
    assert payload["targets"][0]["source_sha256"] == source_sha256
    assert payload["targets"][0]["source_page_count"] == 58
    assert payload["targets"][0]["pdf_path"] == f"{source_sha256}.pdf"
    assert "pdf_url" not in output.read_text(encoding="utf-8")


def test_rejects_tampered_captured_pdf(golden_cases, tmp_path) -> None:
    capture_dir, source_sha256 = _capture(golden_cases, tmp_path)
    (capture_dir / "sources" / f"{source_sha256}.pdf").write_bytes(b"tampered")

    with pytest.raises(CapturedInventoryError, match="SHA-256"):
        build_captured_inventory(
            capture_dir=capture_dir,
            output_path=tmp_path / "captured-inventory.json",
        )


def test_rejects_tampered_captured_auto_result(golden_cases, tmp_path) -> None:
    capture_dir, _ = _capture(golden_cases, tmp_path)
    auto_path = next((capture_dir / "auto").glob("*.json"))
    auto_path.write_bytes(auto_path.read_bytes() + b"\n")

    with pytest.raises(CapturedInventoryError, match="자동결과 파일 SHA-256"):
        build_captured_inventory(
            capture_dir=capture_dir,
            output_path=tmp_path / "captured-inventory.json",
        )
