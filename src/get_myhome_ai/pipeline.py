from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from get_myhome_ai.candidates import select_candidate_pages
from get_myhome_ai.holds import derive_analysis_status, derive_holds
from get_myhome_ai.models import (
    AnalysisMeta,
    AnalysisResponse,
    AnalyzeRequest,
    ExtractionDraft,
    InterestType,
    InterimLoan,
    LoanArrangementStatus,
    PaymentBasis,
    PaymentComponent,
    PaymentSchedule,
    ReviewStatus,
    TargetUnit,
)
from get_myhome_ai.normalization import normalize_draft
from get_myhome_ai.pdf_text import (
    DownloadedPdf,
    PdfPage,
    extract_pdf_pages,
    load_pdf_from_path,
    load_pdf_from_url,
)
from get_myhome_ai.providers.base import ExtractorProvider
from get_myhome_ai.settings import Settings
from get_myhome_ai.summary import build_analysis_summary
from get_myhome_ai.validation import validate_draft

UrlLoader = Callable[[str, Settings], Awaitable[DownloadedPdf]]
PageExtractor = Callable[[bytes, Settings], list[PdfPage]]


def _unknown_component() -> PaymentComponent:
    return PaymentComponent(
        total_ratio=None,
        total_amount_manwon=None,
        basis=PaymentBasis.UNKNOWN,
        installments=[],
        due_date=None,
        due_month=None,
        due_text=None,
    )


def _empty_draft() -> ExtractionDraft:
    return ExtractionDraft(
        payment_schedule=PaymentSchedule(
            down_payment=_unknown_component(),
            interim_payment=_unknown_component(),
            balance_payment=_unknown_component(),
        ),
        interim_loan=InterimLoan(
            arrangement_status=LoanArrangementStatus.NOT_STATED,
            arranged_ratio=None,
            arranged_amount_manwon=None,
            self_funding_ratio=None,
            self_funding_amount_manwon=None,
            self_funding_origin=None,
            bank_names=[],
            guarantee_provider=None,
            interest_type=InterestType.UNKNOWN,
            interest_note=None,
            prepay_requirement_ratio=None,
        ),
        additional_costs=[],
        evidence=[],
        exception_flags=[],
    )


class AnalysisPipeline:
    def __init__(
        self,
        *,
        settings: Settings,
        provider: ExtractorProvider,
        url_loader: UrlLoader = load_pdf_from_url,
        page_extractor: PageExtractor = extract_pdf_pages,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.url_loader = url_loader
        self.page_extractor = page_extractor

    async def analyze_url(self, request: AnalyzeRequest) -> AnalysisResponse:
        downloaded = await self.url_loader(str(request.pdf_url), self.settings)
        return await self._analyze(
            complex_id=request.complex_id,
            downloaded=downloaded,
            unit_type_id=request.unit_type_id,
            unit_type_name=request.unit_type_name,
            sale_price_manwon=request.sale_price_manwon,
        )

    async def analyze_file(
        self,
        *,
        complex_id: str,
        path: str,
        unit_type_id: str | None = None,
        unit_type_name: str | None = None,
        sale_price_manwon: int | None = None,
    ) -> AnalysisResponse:
        downloaded = load_pdf_from_path(path, self.settings)
        return await self._analyze(
            complex_id=complex_id,
            downloaded=downloaded,
            unit_type_id=unit_type_id,
            unit_type_name=unit_type_name,
            sale_price_manwon=sale_price_manwon,
        )

    async def _analyze(
        self,
        *,
        complex_id: str,
        downloaded: DownloadedPdf,
        unit_type_id: str | None,
        unit_type_name: str | None,
        sale_price_manwon: int | None,
    ) -> AnalysisResponse:
        pages = await asyncio.to_thread(
            self.page_extractor,
            downloaded.content,
            self.settings,
        )
        candidates = select_candidate_pages(
            pages,
            max_pages=self.settings.max_candidate_pages,
            max_chars=self.settings.max_candidate_chars,
        )
        if candidates:
            draft = await self.provider.extract(
                complex_id=complex_id,
                pages=candidates,
                unit_type_id=unit_type_id,
                unit_type_name=unit_type_name,
                sale_price_manwon=sale_price_manwon,
            )
        else:
            draft = _empty_draft()

        normalized, derived_fields = normalize_draft(draft)
        validation = validate_draft(
            normalized,
            pages=pages,
            derived_fields=derived_fields,
            sale_price_manwon=sale_price_manwon,
        )
        text_available = sum(len(page.text.strip()) for page in pages) >= 100
        holds = derive_holds(
            normalized,
            validation,
            unit_type_name=unit_type_name,
            text_available=text_available,
        )
        status = derive_analysis_status(validation, holds)

        return AnalysisResponse(
            complex_id=complex_id,
            analysis_status=status,
            review_status=(
                ReviewStatus.AUTO_EXTRACTED if validation.passed else ReviewStatus.NEEDS_REVIEW
            ),
            reviewer=None,
            reviewed_at=None,
            target_unit=TargetUnit(
                unit_type_id=unit_type_id,
                unit_type_name=unit_type_name,
                sale_price_manwon=sale_price_manwon,
            ),
            payment_schedule=normalized.payment_schedule,
            interim_loan=normalized.interim_loan,
            additional_costs=normalized.additional_costs,
            analysis_summary=build_analysis_summary(normalized),
            holds=holds,
            exception_flags=normalized.exception_flags,
            evidence=normalized.evidence,
            validation=validation,
            meta=AnalysisMeta(
                schema_version=self.settings.schema_version,
                extractor_version=self.settings.extractor_version,
                prompt_version=self.settings.prompt_version,
                provider=self.provider.name,
                model=self.provider.model_name,
                source_sha256=downloaded.sha256,
                source_page_count=len(pages),
                candidate_pages=[page.number for page in candidates],
                analyzed_at=datetime.now(UTC),
            ),
        )
