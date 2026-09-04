from __future__ import annotations

import os
import tempfile
from datetime import UTC, date, datetime
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


def _revalidate_result(
    result: AnalysisResponse,
    *,
    source_sha256: str,
    pages: list[PdfPage],
) -> AnalysisResponse:
    """Rebuild every deterministic field against the source-locked PDF."""

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
    grounded = reground_review_metadata(
        source_draft,
        pages=source_pages,
        unit_type_name=result.target_unit.unit_type_name,
    )
    normalized, derived_fields = normalize_draft(grounded)
    validation = validate_draft(
        normalized,
        pages=pages,
        derived_fields=derived_fields,
        sale_price_manwon=result.target_unit.sale_price_manwon,
    )
    text_available = sum(len(page.text.strip()) for page in pages) >= 100
    holds = derive_holds(
        normalized,
        validation,
        unit_type_name=result.target_unit.unit_type_name,
        text_available=text_available,
    )
    prepared = result.model_copy(deep=True)
    prepared.payment_schedule = normalized.payment_schedule
    prepared.interim_loan = normalized.interim_loan
    prepared.additional_costs = normalized.additional_costs
    prepared.risk_clauses = normalized.risk_clauses
    prepared.exception_flags = normalized.exception_flags
    prepared.evidence = normalized.evidence
    prepared.validation = validation
    prepared.holds = holds
    prepared.analysis_status = derive_analysis_status(validation, holds)
    prepared.analysis_summary = build_analysis_summary(normalized)
    prepared.review_status = (
        ReviewStatus.AUTO_EXTRACTED if validation.passed else ReviewStatus.NEEDS_REVIEW
    )
    prepared.reviewer = None
    prepared.reviewed_at = None
    return prepared


def prepare_review_draft(
    result: AnalysisResponse,
    *,
    source_sha256: str,
    pages: list[PdfPage],
) -> AnalysisResponse:
    """Create a source-revalidated draft without granting human-review status."""

    if result.review_status == ReviewStatus.REVIEWED:
        raise ValueError("REVIEWED 결과는 검수 초안 입력으로 사용할 수 없습니다.")
    return _revalidate_result(
        result,
        source_sha256=source_sha256,
        pages=pages,
    )


def approve_result(
    result: AnalysisResponse,
    *,
    reviewer: str,
    source_sha256: str,
    pages: list[PdfPage],
) -> AnalysisResponse:
    """Approve only after revalidating the edited artifact against the exact PDF."""

    reviewer = reviewer.strip()
    if not reviewer:
        raise ValueError("검수자 이름이 필요합니다.")
    approved = _revalidate_result(
        result,
        source_sha256=source_sha256,
        pages=pages,
    )
    if not approved.validation.passed:
        raise ValueError("원본 PDF 재검증에 실패한 결과는 승인할 수 없습니다.")
    approved.review_status = ReviewStatus.REVIEWED
    approved.reviewer = reviewer
    approved.reviewed_at = datetime.now(UTC)
    return approved


def _review_value(value: object | None) -> str:
    if value is None:
        return "미확인(null)"
    if isinstance(value, bool):
        return "예" if value else "아니요"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _review_ratio(value: float | None) -> str:
    return "미확인(null)" if value is None else f"{value * 100:g}%"


def _review_amount(value: int | None) -> str:
    return "미확인(null)" if value is None else f"{value:,}만원"


def write_review_sheet(
    result: AnalysisResponse,
    destination: Path,
    *,
    preface: list[str] | None = None,
) -> None:
    """Write a human-readable checklist without changing the machine result."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# 공고문 AI 추출 검수표 — {result.complex_id}",
        "",
    ]
    if preface:
        lines.extend([*preface, ""])
    lines.extend(
        [
            f"- 분석 상태: `{result.analysis_status}`",
            f"- 검수 상태: `{result.review_status}`",
            f"- unit_type_id: `{result.target_unit.unit_type_id or '-'}`",
            f"- unit_type_name: `{result.target_unit.unit_type_name or '-'}`",
            f"- sale_price_manwon: `{result.target_unit.sale_price_manwon or '-'}`",
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
            "- [ ] 기타 유상옵션이 있으면 전체 카탈로그 미포함 범위 안내가 표시됐다.",
            "- [ ] HOLD 질문과 다음 행동이 실제 불확실성과 맞는다.",
            "",
            "## 고정 요약",
            "",
            result.analysis_summary,
            "",
            "## 납부구조",
            "",
            "| 구간 | 총비율 | 총금액 | 기준 | 회차 수 | 납부일·월·문구 |",
            "| --- | ---: | ---: | --- | ---: | --- |",
        ]
    )
    schedule_rows = (
        ("계약금", result.payment_schedule.down_payment),
        ("중도금", result.payment_schedule.interim_payment),
        ("잔금", result.payment_schedule.balance_payment),
    )
    for name, component in schedule_rows:
        due = component.due_date or component.due_month or component.due_text
        lines.append(
            f"| {name} | {_review_ratio(component.total_ratio)} | "
            f"{_review_amount(component.total_amount_manwon)} | {component.basis} | "
            f"{len(component.installments)} | {_review_value(due)} |"
        )
        for installment in component.installments:
            installment_due = installment.due_date or installment.due_text
            lines.append(
                f"| ↳ {installment.number}회 | {_review_ratio(installment.ratio)} | "
                f"{_review_amount(installment.amount_manwon)} | - | - | "
                f"{_review_value(installment_due)} |"
            )

    loan = result.interim_loan
    lines.extend(
        [
            "",
            "## 중도금 금융조건",
            "",
            f"- 알선 상태: `{loan.arrangement_status}`",
            f"- 공고문상 알선 비율: `{_review_ratio(loan.arranged_ratio)}`",
            f"- 공고문상 알선 금액: `{_review_amount(loan.arranged_amount_manwon)}`",
            f"- 알선 범위 밖 비율: `{_review_ratio(loan.self_funding_ratio)}`",
            f"- 알선 범위 밖 금액: `{_review_amount(loan.self_funding_amount_manwon)}`",
            f"- 위 값의 출처: `{_review_value(loan.self_funding_origin)}`",
            f"- 취급은행: `{', '.join(loan.bank_names) if loan.bank_names else '미확인(null)'}`",
            f"- 보증기관: `{_review_value(loan.guarantee_provider)}`",
            f"- 이자 방식: `{loan.interest_type}`",
            f"- 선납 조건: `{_review_ratio(loan.prepay_requirement_ratio)}`",
            f"- 상환·대환 조건: `{loan.settlement_requirement}`",
            f"- 상환·대환 시점: `{_review_value(loan.settlement_deadline_text)}`",
            "",
            "## 추가비용",
            "",
        ]
    )
    if result.additional_costs:
        for index, cost in enumerate(result.additional_costs, start=1):
            lines.extend(
                [
                    f"### 추가비용 {index} — {cost.name}",
                    "",
                    f"- 유형: `{cost.type}`",
                    f"- 총금액: `{_review_amount(cost.total_amount_manwon)}`",
                    f"- 필수 여부: `{_review_value(cost.required)}`",
                    f"- 분양가 포함 여부: `{_review_value(cost.included_in_sale_price)}`",
                    f"- 적용 주택형: `{_review_value(cost.applicable_unit_type)}`",
                    f"- 비고: `{_review_value(cost.note)}`",
                ]
            )
            for payment in cost.payments:
                payment_due = payment.due_date or payment.due_text
                lines.append(
                    f"- 납부 {payment.number}: `{payment.stage}` / "
                    f"`{_review_amount(payment.amount_manwon)}` / "
                    f"`{_review_value(payment_due)}`"
                )
            lines.append("")
    else:
        lines.extend(["- 추출된 추가비용 없음(원문 미기재인지 추출 누락인지 확인 필요)", ""])

    lines.extend(["## 위험조항", ""])
    if result.risk_clauses:
        for clause in result.risk_clauses:
            pages = ", ".join(str(item.page) for item in clause.evidence)
            lines.append(
                f"- `{clause.code}` / 영향 구간 `{clause.impact_stage}` / "
                f"근거 p.{pages} — {clause.message} 다음 행동: {clause.next_action}"
            )
    else:
        lines.append("- 없음(원문에 위험조항이 없는지 추출 누락인지 확인 필요)")
    lines.extend(["", "## HOLD", ""])
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
