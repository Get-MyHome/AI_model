from __future__ import annotations

import asyncio
import hmac
import logging
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
    FundingStressUnavailableError,
)
from get_myhome_ai.funding_stress import calculate_funding_stress
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
from get_myhome_ai.review_capture import capture_review_result, capture_review_source
from get_myhome_ai.reviewed_store import find_reviewed_artifact
from get_myhome_ai.runtime_source import (
    RUNNING_SOURCE_FINGERPRINT_SHA256,
    SOURCE_FINGERPRINT_ALGORITHM,
)
from get_myhome_ai.settings import Settings, get_settings
from get_myhome_ai.stress_models import FundingStressRequest, FundingStressResponse

logger = logging.getLogger(__name__)


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
        return HealthResponse(
            status="ok",
            version=active_settings.app_version,
            source_fingerprint_algorithm=SOURCE_FINGERPRINT_ALGORITHM,
            source_fingerprint_sha256=RUNNING_SOURCE_FINGERPRINT_SHA256,
        )

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
        downloaded = await active_pipeline.download_url(payload)
        reviewed = find_reviewed_artifact(
            request=payload,
            source_sha256=downloaded.sha256,
            reviewed_artifact_dir=active_settings.reviewed_artifact_dir,
            schema_version=active_settings.schema_version,
            extractor_version=active_settings.extractor_version,
        )
        if reviewed is not None:
            return reviewed

        capture_key: str | None = None
        if active_settings.review_capture_dir is not None:
            try:
                capture_key = await asyncio.to_thread(
                    capture_review_source,
                    active_settings.review_capture_dir,
                    payload,
                    downloaded,
                )
            except (OSError, ValueError):
                logger.exception(
                    "AI 검수 캡처 원본 저장 실패: complex_id=%s",
                    payload.complex_id,
                )

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
                    result = await active_pipeline.analyze_downloaded(payload, downloaded)
                    if capture_key is not None and active_settings.review_capture_dir is not None:
                        try:
                            await asyncio.to_thread(
                                capture_review_result,
                                active_settings.review_capture_dir,
                                capture_key,
                                result,
                            )
                        except (OSError, ValueError):
                            logger.exception(
                                "AI 검수 캡처 결과 저장 실패: complex_id=%s",
                                payload.complex_id,
                            )
                    return result
            except TimeoutError as exc:
                raise AnalysisTimeoutError("PDF AI 분석 시간이 초과됐습니다.") from exc
        finally:
            semaphore.release()

    async def require_reviewed_analysis(payload: AnalyzeRequest) -> AnalysisResponse:
        downloaded = await active_pipeline.download_url(payload)
        reviewed = find_reviewed_artifact(
            request=payload,
            source_sha256=downloaded.sha256,
            reviewed_artifact_dir=active_settings.reviewed_artifact_dir,
            schema_version=active_settings.schema_version,
            extractor_version=active_settings.extractor_version,
        )
        if reviewed is None:
            raise FundingStressUnavailableError(
                "정확한 PDF·주택형·분양가·추출기 버전의 REVIEWED 검수본이 필요합니다."
            )
        return reviewed

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

    @app.post(
        "/api/funding-stress",
        response_model=FundingStressResponse,
        dependencies=[Depends(authenticate)],
    )
    async def funding_stress(payload: FundingStressRequest) -> FundingStressResponse:
        analysis = await require_reviewed_analysis(payload.analysis_request)
        # The calculator is deterministic CPU work. Keep it outside the async
        # event loop so health checks and PDF requests remain responsive.
        return await asyncio.to_thread(calculate_funding_stress, payload, analysis)

    return app


def build_default_app() -> FastAPI:
    return create_app()


# ASGI entry point. Environment is read only when the server imports this module.
app = build_default_app()
