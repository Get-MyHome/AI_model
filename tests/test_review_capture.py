from __future__ import annotations

import hashlib
import json
import stat

from conftest import synthetic_pages
from fastapi.testclient import TestClient

from get_myhome_ai.api import create_app
from get_myhome_ai.pdf_text import DownloadedPdf
from get_myhome_ai.pipeline import AnalysisPipeline
from get_myhome_ai.providers.fixture import FixtureExtractor
from get_myhome_ai.settings import Settings


def test_api_captures_unreviewed_source_without_presigned_url(golden_cases, tmp_path) -> None:
    case = golden_cases["2026000358"]
    content = b"%PDF-review-capture"
    source_sha256 = hashlib.sha256(content).hexdigest()
    presigned_url = "https://example.com/file.pdf?X-Amz-Signature=secret-value"

    async def loader(_url, _settings):
        return DownloadedPdf(content=content, sha256=source_sha256)

    capture_dir = tmp_path / "review-capture"
    settings = Settings(
        ai_provider="fixture",
        allow_unauthenticated_dev=True,
        allow_unrestricted_pdf_hosts_dev=True,
        review_capture_dir=capture_dir,
    )
    provider = FixtureExtractor({case.complex_id: case.expected})
    pipeline = AnalysisPipeline(
        settings=settings,
        provider=provider,
        url_loader=loader,
        page_extractor=lambda _content, _settings: synthetic_pages(case),
    )
    app = create_app(settings=settings, pipeline=pipeline)
    payload = {
        "complex_id": case.complex_id,
        "pdf_url": presigned_url,
        "unit_type_id": "01",
        "unit_type_name": case.unit_type_name,
        "sale_price_manwon": case.sale_price_manwon,
    }

    with TestClient(app) as client:
        response = client.post("/api/analyze", json=payload)

    assert response.status_code == 200
    source_path = capture_dir / "sources" / f"{source_sha256}.pdf"
    assert source_path.read_bytes() == content
    assert stat.S_IMODE(source_path.stat().st_mode) == 0o600

    request_paths = list((capture_dir / "requests").glob("*.json"))
    result_paths = list((capture_dir / "auto").glob("*.json"))
    assert len(request_paths) == 1
    assert len(result_paths) == 1
    request_metadata = json.loads(request_paths[0].read_text(encoding="utf-8"))
    assert request_metadata["source_sha256"] == source_sha256
    assert request_metadata["complex_id"] == case.complex_id
    assert "pdf_url" not in request_metadata
    assert "secret-value" not in request_paths[0].read_text(encoding="utf-8")
    assert "secret-value" not in result_paths[0].read_text(encoding="utf-8")


def test_review_capture_is_disabled_by_default(golden_cases, tmp_path, monkeypatch) -> None:
    case = golden_cases["2026000358"]
    content = b"%PDF-review-capture-disabled"

    async def loader(_url, _settings):
        return DownloadedPdf(content=content, sha256=hashlib.sha256(content).hexdigest())

    monkeypatch.chdir(tmp_path)
    settings = Settings(
        ai_provider="fixture",
        allow_unauthenticated_dev=True,
        allow_unrestricted_pdf_hosts_dev=True,
    )
    provider = FixtureExtractor({case.complex_id: case.expected})
    pipeline = AnalysisPipeline(
        settings=settings,
        provider=provider,
        url_loader=loader,
        page_extractor=lambda _content, _settings: synthetic_pages(case),
    )
    app = create_app(settings=settings, pipeline=pipeline)

    with TestClient(app) as client:
        response = client.post(
            "/api/analyze",
            json={
                "complex_id": case.complex_id,
                "pdf_url": "https://example.com/file.pdf",
                "unit_type_id": "01",
                "unit_type_name": case.unit_type_name,
                "sale_price_manwon": case.sale_price_manwon,
            },
        )

    assert response.status_code == 200
    assert not (tmp_path / "review-capture").exists()


def test_result_capture_failure_never_changes_successful_analysis(
    golden_cases,
    tmp_path,
    monkeypatch,
) -> None:
    case = golden_cases["2026000358"]
    content = b"%PDF-review-capture-result-failure"

    async def loader(_url, _settings):
        return DownloadedPdf(content=content, sha256=hashlib.sha256(content).hexdigest())

    def fail_capture(*_args, **_kwargs):
        raise ValueError("simulated immutable capture conflict")

    monkeypatch.setattr("get_myhome_ai.api.capture_review_result", fail_capture)
    settings = Settings(
        ai_provider="fixture",
        allow_unauthenticated_dev=True,
        allow_unrestricted_pdf_hosts_dev=True,
        review_capture_dir=tmp_path / "review-capture",
    )
    pipeline = AnalysisPipeline(
        settings=settings,
        provider=FixtureExtractor({case.complex_id: case.expected}),
        url_loader=loader,
        page_extractor=lambda _content, _settings: synthetic_pages(case),
    )
    app = create_app(settings=settings, pipeline=pipeline)

    with TestClient(app) as client:
        response = client.post(
            "/api/analyze",
            json={
                "complex_id": case.complex_id,
                "pdf_url": "https://example.com/file.pdf",
                "unit_type_id": "01",
                "unit_type_name": case.unit_type_name,
                "sale_price_manwon": case.sale_price_manwon,
            },
        )

    assert response.status_code == 200
    assert response.json()["review_status"] == "AUTO_EXTRACTED"
