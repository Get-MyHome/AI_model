from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from get_myhome_ai.candidates import CandidatePage
from get_myhome_ai.holds import derive_analysis_status, derive_holds
from get_myhome_ai.models import (
    AnalysisResponse,
    ExtractionDraft,
    InterestType,
    LoanArrangementStatus,
    LoanSettlementRequirement,
    PaymentBasis,
    ReviewStatus,
    ValueOrigin,
)
from get_myhome_ai.normalization import normalize_draft
from get_myhome_ai.pdf_text import PdfPage
from get_myhome_ai.providers.ollama_grounding import reground_review_metadata
from get_myhome_ai.summary import build_analysis_summary
from get_myhome_ai.validation import validate_draft

_GROUP_EVIDENCE_PATHS = {
    "/payment_schedule/down_payment",
    "/payment_schedule/interim_payment",
    "/payment_schedule/balance_payment",
    "/payment_schedule/interim_payment/installments",
}


def _has_source_evidence(
    path: str,
    *,
    evidence_fields: set[str],
) -> bool:
    if path in evidence_fields:
        return True
    groups = set(_GROUP_EVIDENCE_PATHS)
    groups.update(
        field
        for field in evidence_fields
        if field.startswith("/additional_costs/") and field.count("/") == 2
    )
    return any(path.startswith(f"{group}/") for group in groups if group in evidence_fields)


def _discard_untrusted_derived_values(draft: ExtractionDraft) -> ExtractionDraft:
    """Remove derivable values that lack direct source evidence.

    ``validation.derived_fields`` belongs to the editable review JSON and is
    therefore never consulted here.  The normalizer will recreate only values
    justified by source-grounded inputs and return a fresh trusted derived list.
    """

    source = draft.model_copy(deep=True)
    evidence_fields = {item.field for item in source.evidence}
    components = (
        ("down_payment", source.payment_schedule.down_payment),
        ("interim_payment", source.payment_schedule.interim_payment),
        ("balance_payment", source.payment_schedule.balance_payment),
    )
    for name, component in components:
        base = f"/payment_schedule/{name}"
        if not _has_source_evidence(
            f"{base}/total_ratio",
            evidence_fields=evidence_fields,
        ):
            component.total_ratio = None
        if not _has_source_evidence(
            f"{base}/total_amount_manwon",
            evidence_fields=evidence_fields,
        ):
            component.total_amount_manwon = None
        component.basis = PaymentBasis.UNKNOWN

    loan = source.interim_loan
    ratio_supported = _has_source_evidence(
        "/interim_loan/self_funding_ratio",
        evidence_fields=evidence_fields,
    )
    amount_supported = _has_source_evidence(
        "/interim_loan/self_funding_amount_manwon",
        evidence_fields=evidence_fields,
    )
    if not ratio_supported:
        loan.self_funding_ratio = None
    if not amount_supported:
        loan.self_funding_amount_manwon = None
    loan.self_funding_origin = (
        ValueOrigin.EXTRACTED if ratio_supported or amount_supported else None
    )

    for index, cost in enumerate(source.additional_costs):
        if not _has_source_evidence(
            f"/additional_costs/{index}/total_amount_manwon",
            evidence_fields=evidence_fields,
        ):
            cost.total_amount_manwon = None

    if loan.arrangement_status == LoanArrangementStatus.NOT_AVAILABLE:
        # These values are semantic consequences of NOT_AVAILABLE.  Recreate
        # their canonical values in normalize_draft instead of trusting edits.
        loan.arranged_ratio = None
        loan.arranged_amount_manwon = None
        loan.self_funding_ratio = None
        loan.self_funding_amount_manwon = None
        loan.self_funding_origin = None
        loan.interest_type = InterestType.UNKNOWN
        loan.settlement_requirement = LoanSettlementRequirement.NOT_STATED

    return source


def save_result(result: AnalysisResponse, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = result.model_dump_json(indent=2)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        delete=False,
    ) as temporary:
        temporary.write(payload)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, destination)


def load_result(path: Path) -> AnalysisResponse:
    return AnalysisResponse.model_validate_json(path.read_text(encoding="utf-8"))


def approve_result(
    result: AnalysisResponse,
    *,
    reviewer: str,
    source_sha256: str,
    pages: list[PdfPage],
) -> AnalysisResponse:
    """Approve only after revalidating the edited artifact against the exact PDF."""

    if source_sha256 != result.meta.source_sha256:
        raise ValueError("검수한 PDF SHA-256이 분석 원본과 다릅니다.")
    if len(pages) != result.meta.source_page_count:
        raise ValueError("검수한 PDF 페이지 수가 분석 원본과 다릅니다.")
    edited_draft = ExtractionDraft(
        payment_schedule=result.payment_schedule,
        interim_loan=result.interim_loan,
        additional_costs=result.additional_costs,
        risk_clauses=result.risk_clauses,
        evidence=result.evidence,
        exception_flags=result.exception_flags,
    )
    source_draft = _discard_untrusted_derived_values(edited_draft)
    source_pages = [
        CandidatePage(
            number=page.number,
            text=page.text,
            score=0,
            categories=frozenset(),
        )
        for page in pages
    ]
    grounded = reground_review_metadata(source_draft, pages=source_pages)
    normalized, derived_fields = normalize_draft(grounded)
    validation = validate_draft(
        normalized,
        pages=pages,
        derived_fields=derived_fields,
        sale_price_manwon=result.target_unit.sale_price_manwon,
    )
    if not validation.passed:
        raise ValueError("원본 PDF 재검증에 실패한 결과는 승인할 수 없습니다.")
    text_available = sum(len(page.text.strip()) for page in pages) >= 100
    holds = derive_holds(
        normalized,
        validation,
        unit_type_name=result.target_unit.unit_type_name,
        text_available=text_available,
    )
    approved = result.model_copy(deep=True)
    approved.payment_schedule = normalized.payment_schedule
    approved.interim_loan = normalized.interim_loan
    approved.additional_costs = normalized.additional_costs
    approved.risk_clauses = normalized.risk_clauses
    approved.exception_flags = normalized.exception_flags
    approved.evidence = normalized.evidence
    approved.validation = validation
    approved.holds = holds
    approved.analysis_status = derive_analysis_status(validation, holds)
    approved.analysis_summary = build_analysis_summary(normalized)
    approved.review_status = ReviewStatus.REVIEWED
    approved.reviewer = reviewer
    approved.reviewed_at = datetime.now(UTC)
    return approved


def write_review_sheet(result: AnalysisResponse, destination: Path) -> None:
    """Write a human-readable checklist without changing the machine result."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# 공고문 AI 추출 검수표 — {result.complex_id}",
        "",
        f"- 분석 상태: `{result.analysis_status}`",
        f"- 자동 검증: `{'PASS' if result.validation.passed else 'FAIL'}`",
        f"- PDF SHA-256: `{result.meta.source_sha256}`",
        f"- 물리 페이지 수: `{result.meta.source_page_count}`",
        f"- 후보 페이지: `{', '.join(map(str, result.meta.candidate_pages))}`",
        "",
        "## 검수 절차",
        "",
        "- [ ] 아래 추출값을 선택 주택형·동·층과 대조했다.",
        "- [ ] 각 근거 문장이 표시된 물리 PDF 페이지에 존재한다.",
        "- [ ] 선택비용과 분양가 포함비용을 기본 필요자금에 더하지 않았다.",
        "- [ ] HOLD 질문과 다음 행동이 실제 불확실성과 맞는다.",
        "",
        "## 고정 요약",
        "",
        result.analysis_summary,
        "",
        "## HOLD",
        "",
    ]
    if result.holds:
        for hold in result.holds:
            lines.append(f"- `{hold.reason_code}` — {hold.message} 다음 행동: {hold.next_action}")
    else:
        lines.append("- 없음")
    lines.extend(["", "## 근거", ""])
    for evidence in result.evidence:
        lines.append(f"- `{evidence.field}` / p.{evidence.page}: {evidence.raw_text}")
    lines.extend(["", "## 자동 검증 이슈", ""])
    if result.validation.issues:
        for issue in result.validation.issues:
            lines.append(
                f"- `{issue.severity}:{issue.code}` `{issue.field or '-'}` — {issue.message}"
            )
    else:
        lines.append("- 없음")
    payload = "\n".join(lines) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        delete=False,
    ) as temporary:
        temporary.write(payload)
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, destination)
