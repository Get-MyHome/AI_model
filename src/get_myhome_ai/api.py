from __future__ import annotations

import asyncio
import hmac
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse

from get_myhome_ai.contracts.backend_v1 import BackendV1Response, to_backend_v1
from get_myhome_ai.errors import (
    AnalysisBusyError,
    AnalysisError,
    AnalysisTimeoutError,
    AuthenticationError,
)
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
        docs_url="/docs" if active_settings.enable_docs else None,
        redoc_url="/redoc" if active_settings.enable_docs else None,
        openapi_url="/openapi.json" if active_settings.enable_docs else None,
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
    async def ready():
        try:
            async with asyncio.timeout(active_settings.readiness_timeout_seconds):
                provider_ready = await active_provider.ready()
        except TimeoutError:
            provider_ready = False
        checks = {
            "pdftotext": shutil.which("pdftotext") is not None,
            "temporary_directory": _temporary_directory_is_writable(),
            "provider": provider_ready,
            "authentication": (
                active_settings.ai_api_key is not None or active_settings.allow_unauthenticated_dev
            ),
            "pdf_host_allowlist": (
                bool(active_settings.allowed_pdf_hosts)
                or active_settings.allow_unrestricted_pdf_hosts_dev
            ),
        }
        payload = ReadinessResponse(
            ready=all(checks.values()),
            provider=active_provider.name,
            checks=checks,
        )
        if not payload.ready:
            return JSONResponse(status_code=503, content=payload.model_dump(mode="json"))
        return payload

    async def authenticate(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        configured = active_settings.ai_api_key
        if configured is None:
            if active_settings.allow_unauthenticated_dev:
                return
            raise AuthenticationError("AI_API_KEY가 설정되지 않았습니다.")
        supplied = authorization or ""
        expected = f"Bearer {configured.get_secret_value()}"
        if not hmac.compare_digest(supplied, expected):
            raise AuthenticationError("유효한 API 인증 정보가 필요합니다.")

    async def run_analysis(payload: AnalyzeRequest) -> AnalysisResponse:
        try:
            async with asyncio.timeout(active_settings.analysis_queue_timeout_seconds):
                await semaphore.acquire()
        except TimeoutError as exc:
            raise AnalysisBusyError(
                "AI 분석 서버가 처리 중입니다. 잠시 후 다시 시도하세요."
            ) from exc
        try:
            try:
                async with asyncio.timeout(active_settings.analysis_timeout_seconds):
                    return await active_pipeline.analyze_url(payload)
            except TimeoutError as exc:
                raise AnalysisTimeoutError("PDF AI 분석 시간이 초과됐습니다.") from exc
        finally:
            semaphore.release()

    @app.post(
        "/api/analyze",
        response_model=AnalysisResponse,
        dependencies=[Depends(authenticate)],
    )
    async def analyze(payload: AnalyzeRequest) -> AnalysisResponse:
        return await run_analysis(payload)

    @app.post(
        "/api/analyze/legacy",
        response_model=BackendV1Response,
        dependencies=[Depends(authenticate)],
    )
    async def analyze_legacy(payload: AnalyzeRequest) -> BackendV1Response:
        return to_backend_v1(await run_analysis(payload))

    return app


def build_default_app() -> FastAPI:
    return create_app()


# ASGI entry point. Environment is read only when the server imports this module.
app = build_default_app()
