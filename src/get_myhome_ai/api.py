from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from get_myhome_ai.contracts.backend_v1 import BackendV1Response, to_backend_v1
from get_myhome_ai.errors import AnalysisError
from get_myhome_ai.models import (
    AnalysisResponse,
    AnalyzeRequest,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    ReadinessResponse,
)
from get_myhome_ai.pipeline import AnalysisPipeline
from get_myhome_ai.providers.base import ExtractorProvider
from get_myhome_ai.providers.factory import create_provider
from get_myhome_ai.settings import Settings, get_settings


def _temporary_directory_is_writable() -> bool:
    try:
        with tempfile.NamedTemporaryFile():
            return True
    except OSError:
        return False


def create_app(
    *,
    settings: Settings | None = None,
    provider: ExtractorProvider | None = None,
    pipeline: AnalysisPipeline | None = None,
) -> FastAPI:
    active_settings = settings or get_settings()
    if pipeline is not None and provider is not None:
        raise ValueError("pipeline과 provider는 동시에 지정할 수 없습니다.")
    active_provider = (
        pipeline.provider
        if pipeline is not None
        else (provider or create_provider(active_settings))
    )
    active_pipeline = pipeline or AnalysisPipeline(
        settings=active_settings,
        provider=active_provider,
    )
    semaphore = asyncio.Semaphore(active_settings.max_concurrent_analyses)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.pipeline = active_pipeline
        application.state.semaphore = semaphore
        yield

    app = FastAPI(
        title="Get-MyHome PDF Extraction API",
        version=active_settings.app_version,
        lifespan=lifespan,
    )

    @app.exception_handler(AnalysisError)
    async def analysis_error_handler(_: Request, exc: AnalysisError) -> JSONResponse:
        payload = ErrorResponse(
            error=ErrorDetail(code=exc.code, message=exc.message, retryable=exc.retryable)
        )
        return JSONResponse(status_code=exc.status_code, content=payload.model_dump(mode="json"))

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", version=active_settings.app_version)

    @app.get("/ready", response_model=ReadinessResponse)
    async def ready() -> ReadinessResponse:
        checks = {
            "pdftotext": shutil.which("pdftotext") is not None,
            "temporary_directory": _temporary_directory_is_writable(),
            "provider": (
                active_provider.name == "fixture" or active_settings.openai_api_key is not None
            ),
        }
        return ReadinessResponse(
            ready=all(checks.values()),
            provider=active_provider.name,
            checks=checks,
        )

    async def run_analysis(payload: AnalyzeRequest) -> AnalysisResponse:
        async with semaphore:
            return await active_pipeline.analyze_url(payload)

    @app.post("/api/analyze", response_model=AnalysisResponse)
    async def analyze(payload: AnalyzeRequest) -> AnalysisResponse:
        return await run_analysis(payload)

    @app.post("/api/analyze/legacy", response_model=BackendV1Response)
    async def analyze_legacy(payload: AnalyzeRequest) -> BackendV1Response:
        return to_backend_v1(await run_analysis(payload))

    return app


def build_default_app() -> FastAPI:
    return create_app()


# ASGI entry point. Environment is read only when the server imports this module.
app = build_default_app()
