from __future__ import annotations

import hashlib

from conftest import synthetic_pages
from fastapi.testclient import TestClient

from get_myhome_ai.api import create_app
from get_myhome_ai.pdf_text import DownloadedPdf
from get_myhome_ai.pipeline import AnalysisPipeline
from get_myhome_ai.providers.fixture import FixtureExtractor
from get_myhome_ai.settings import Settings


def test_api_is_closed_by_default(golden_cases) -> None:
    case = golden_cases["2026000358"]
    settings = Settings(ai_provider="fixture")
    provider = FixtureExtractor({case.complex_id: case.expected})
    app = create_app(settings=settings, provider=provider)

    with TestClient(app) as client:
        assert client.get("/ready").status_code == 503
        response = client.post(
            "/api/analyze",
            json={
                "complex_id": case.complex_id,
                "pdf_url": "https://example.com/file.pdf",
            },
        )
        assert response.status_code == 401


def test_health_ready_and_analyze_contract(golden_cases) -> None:
    case = golden_cases["2026000358"]

    async def loader(_url, _settings):
        content = b"%PDF-synthetic"
        return DownloadedPdf(content=content, sha256=hashlib.sha256(content).hexdigest())

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
        assert client.get("/health").json()["status"] == "ok"
        readiness = client.get("/ready").json()
        assert readiness["ready"] is True

        payload = {
            "complex_id": case.complex_id,
            "pdf_url": "https://example.com/file.pdf",
            "unit_type_id": "01",
            "unit_type_name": case.unit_type_name,
            "sale_price_manwon": case.sale_price_manwon,
        }
        response = client.post("/api/analyze", json=payload)
        assert response.status_code == 200
        body = response.json()
        assert body["complex_id"] == case.complex_id
        assert body["payment_schedule"]["interim_payment"]["total_ratio"] == 0.60
        assert body["interim_loan"]["arranged_ratio"] == 0.40
        assert body["analysis_summary"]
        assert body["evidence"]

        legacy = client.post("/api/analyze/legacy", json=payload)
        assert legacy.status_code == 200
        assert legacy.json()["complexId"] == case.complex_id
        assert legacy.json()["paymentSchedule"] is None

    api_key = "test-secret-key-with-at-least-32-chars"
    secured_settings = Settings(
        ai_provider="fixture",
        ai_api_key=api_key,
        pdf_allowed_hosts="example.com",
    )
    secured_pipeline = AnalysisPipeline(
        settings=secured_settings,
        provider=provider,
        url_loader=loader,
        page_extractor=lambda _content, _settings: synthetic_pages(case),
    )
    secured_app = create_app(settings=secured_settings, pipeline=secured_pipeline)
    with TestClient(secured_app) as client:
        assert client.post("/api/analyze", json=payload).status_code == 401
        authenticated = client.post(
            "/api/analyze",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert authenticated.status_code == 200


def test_target_unit_fields_must_be_sent_together(golden_cases) -> None:
    case = golden_cases["2026000358"]
    settings = Settings(
        ai_provider="fixture",
        allow_unauthenticated_dev=True,
        allow_unrestricted_pdf_hosts_dev=True,
    )
    provider = FixtureExtractor({case.complex_id: case.expected})
    app = create_app(settings=settings, provider=provider)

    with TestClient(app) as client:
        response = client.post(
            "/api/analyze",
            json={
                "complex_id": case.complex_id,
                "pdf_url": "https://example.com/file.pdf",
                "unit_type_name": case.unit_type_name,
                "sale_price_manwon": case.sale_price_manwon,
            },
        )
        assert response.status_code == 422
