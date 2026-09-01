from __future__ import annotations

from get_myhome_ai.models import (
    AnalysisStatus,
    ExceptionFlag,
    ExtractionDraft,
    Hold,
    HoldKind,
    HoldReasonCode,
    InterestType,
    IssueSeverity,
    LoanArrangementStatus,
    ValidationReport,
)

HOLD_TEXT: dict[HoldReasonCode, tuple[str, str]] = {
    HoldReasonCode.DOWN_PAYMENT_MISSING: (
        "계약금 조건을 확인하지 못했어요.",
        "공고문 공급금액 표의 계약금 비율·정액을 확인하세요.",
    ),
    HoldReasonCode.INTERIM_PAYMENT_MISSING: (
        "중도금 조건을 확인하지 못했어요.",
        "시행사에 중도금 총액과 회차별 납부일을 확인하세요.",
    ),
    HoldReasonCode.BALANCE_PAYMENT_MISSING: (
        "잔금 조건을 확인하지 못했어요.",
        "잔금 금액과 입주지정일을 확인하세요.",
    ),
    HoldReasonCode.INTERIM_SCHEDULE_MISSING: (
        "중도금 납부 일정 일부가 확인되지 않았어요.",
        "회차별 비율 또는 금액과 납부일을 확인하세요.",
    ),
    HoldReasonCode.INTERIM_LOAN_RATIO_MISSING: (
        "중도금 대출 가능 범위를 확인하지 못했어요.",
        "시행사에 분양가 대비 대출 가능 비율을 확인하세요.",
    ),
    HoldReasonCode.BANK_NOT_DISCLOSED: (
        "공고문에서 취급은행을 확인할 수 없어요.",
        "시행사에 취급은행·금리·신청 기간을 확인하세요.",
    ),
    HoldReasonCode.LOAN_ARRANGEMENT_ONLY: (
        "대출 알선은 예정이지만 보장된 조건은 아니에요.",
        "알선 확정 여부와 불가 시 자납 일정을 확인하세요.",
    ),
    HoldReasonCode.SELF_FUNDING_SCHEDULE_UNKNOWN: (
        "직접 납부할 중도금은 확인됐지만 어느 회차에 낼지는 불명확해요.",
        "자납분의 회차별 금액과 납부일을 시행사에 확인하세요.",
    ),
    HoldReasonCode.SELF_FUNDING_REQUIRED: (
        "중도금 일부를 직접 마련해야 해요.",
        "이전 응답 코드입니다. 자납 회차가 불명확하면 회차별 금액과 납부일을 확인하세요.",
    ),
    HoldReasonCode.GUARANTEE_PROVIDER_UNKNOWN: (
        "중도금 대출 보증기관을 확인하지 못했어요.",
        "시행사 또는 취급은행에 보증기관을 확인하세요.",
    ),
    HoldReasonCode.INTEREST_TERMS_UNKNOWN: (
        "중도금 이자 방식을 확인하지 못했어요.",
        "무이자·이자후불·직접 부담 중 어느 방식인지 확인하세요.",
    ),
    HoldReasonCode.INDIVIDUAL_REVIEW_REQUIRED: (
        "개인별 대출 심사가 남아 있어요.",
        "소득·기존 대출을 기준으로 실제 한도를 금융기관에 확인하세요.",
    ),
    HoldReasonCode.BALANCE_CONVERSION_UNCERTAIN: (
        "입주 시 잔금대출 전환 조건이 확정되지 않았어요.",
        "잔금대출의 재심사 여부와 전환 조건을 금융기관에 확인하세요.",
    ),
    HoldReasonCode.TERMS_DIFFER_BY_HOUSING_TYPE: (
        "주택형에 따라 조건이 달라요.",
        "선택한 주택형에 적용되는 조건인지 확인하세요.",
    ),
    HoldReasonCode.UNIT_SELECTION_REQUIRED: (
        "주택형이나 층에 따라 금액이 달라요.",
        "선택 주택형과 분양가를 지정한 뒤 다시 계산하세요.",
    ),
    HoldReasonCode.ADDITIONAL_COST_UNKNOWN: (
        "추가비용의 금액 또는 납부 시점이 불명확해요.",
        "선택품목 계약서에서 총액과 회차를 확인하세요.",
    ),
    HoldReasonCode.TABLE_REVIEW_REQUIRED: (
        "표 구조를 자동으로 확정하기 어려워요.",
        "표시된 공고문 페이지를 사람이 대조하세요.",
    ),
    HoldReasonCode.SOURCE_CONFLICT: (
        "공고문 안의 조건이 서로 달라요.",
        "정정공고와 최신 안내문 중 적용 문서를 확인하세요.",
    ),
    HoldReasonCode.EVIDENCE_MISSING: (
        "추출값의 원문 근거를 확인하지 못했어요.",
        "표시된 필드를 사람이 원문과 대조하세요.",
    ),
    HoldReasonCode.PDF_TEXT_UNAVAILABLE: (
        "PDF에서 읽을 수 있는 텍스트가 부족해요.",
        "원본 PDF를 직접 확인하거나 OCR 검수를 진행하세요.",
    ),
}


def _make(code: HoldReasonCode) -> Hold:
    message, next_action = HOLD_TEXT[code]
    personal_review_codes = {HoldReasonCode.INDIVIDUAL_REVIEW_REQUIRED}
    kind = (
        HoldKind.PERSONAL_REVIEW if code in personal_review_codes else HoldKind.DOCUMENT_UNCERTAINTY
    )
    return Hold(
        reason_code=code,
        kind=kind,
        blocking=kind == HoldKind.DOCUMENT_UNCERTAINTY,
        message=message,
        next_action=next_action,
    )


def derive_holds(
    draft: ExtractionDraft,
    validation: ValidationReport,
    *,
    unit_type_name: str | None,
    text_available: bool = True,
) -> list[Hold]:
    codes: list[HoldReasonCode] = []
    if not text_available:
        codes.append(HoldReasonCode.PDF_TEXT_UNAVAILABLE)
    schedule = draft.payment_schedule
    if (
        schedule.down_payment.total_ratio is None
        and schedule.down_payment.total_amount_manwon is None
    ):
        codes.append(HoldReasonCode.DOWN_PAYMENT_MISSING)
    if (
        schedule.interim_payment.total_ratio is None
        and schedule.interim_payment.total_amount_manwon is None
    ):
        codes.append(HoldReasonCode.INTERIM_PAYMENT_MISSING)
    balance_value_missing = (
        schedule.balance_payment.total_ratio is None
        and schedule.balance_payment.total_amount_manwon is None
    )
    balance_due_missing = all(
        value is None
        for value in (
            schedule.balance_payment.due_date,
            schedule.balance_payment.due_month,
            schedule.balance_payment.due_text,
        )
    )
    if balance_value_missing or balance_due_missing:
        codes.append(HoldReasonCode.BALANCE_PAYMENT_MISSING)
    interim_schedule_issue_codes = {
        "INSTALLMENT_VALUE_MISSING",
        "INSTALLMENT_RATIO_SUM_MISMATCH",
        "INSTALLMENT_AMOUNT_SUM_MISMATCH",
        "INSTALLMENT_DATE_ORDER_INVALID",
        "INSTALLMENT_NUMBER_INVALID",
        "INSTALLMENT_DUE_MISSING",
    }
    if not schedule.interim_payment.installments or any(
        issue.code in interim_schedule_issue_codes
        and issue.field is not None
        and issue.field.startswith("/payment_schedule/interim_payment")
        for issue in validation.issues
    ):
        codes.append(HoldReasonCode.INTERIM_SCHEDULE_MISSING)

    loan = draft.interim_loan
    if loan.arrangement_status == LoanArrangementStatus.NOT_STATED:
        codes.append(HoldReasonCode.INTERIM_LOAN_RATIO_MISSING)
    elif loan.arrangement_status in {
        LoanArrangementStatus.PLANNED,
        LoanArrangementStatus.UNDER_DISCUSSION,
    }:
        codes.append(HoldReasonCode.LOAN_ARRANGEMENT_ONLY)
        if not loan.bank_names:
            codes.append(HoldReasonCode.BANK_NOT_DISCLOSED)
    elif loan.arrangement_status == LoanArrangementStatus.BANK_SELECTED and not loan.bank_names:
        codes.append(HoldReasonCode.BANK_NOT_DISCLOSED)

    if (
        loan.arrangement_status != LoanArrangementStatus.NOT_AVAILABLE
        and loan.arranged_ratio is None
        and loan.arranged_amount_manwon is None
    ):
        codes.append(HoldReasonCode.INTERIM_LOAN_RATIO_MISSING)
    if loan.arrangement_status != LoanArrangementStatus.NOT_AVAILABLE and (
        (loan.self_funding_ratio or 0) > 0 or (loan.self_funding_amount_manwon or 0) > 0
    ):
        # The known self-funding burden is a risk factor, not a HOLD by itself.
        # Until the contract carries per-installment loan coverage, however, the
        # backend cannot claim an exact first shortfall date.
        codes.append(HoldReasonCode.SELF_FUNDING_SCHEDULE_UNKNOWN)
    if (
        loan.arrangement_status != LoanArrangementStatus.NOT_AVAILABLE
        and loan.interest_type == InterestType.UNKNOWN
    ):
        codes.append(HoldReasonCode.INTEREST_TERMS_UNKNOWN)

    flags = set(draft.exception_flags)
    if ExceptionFlag.TERMS_DIFFER_BY_TYPE in flags and unit_type_name is None:
        codes.append(HoldReasonCode.UNIT_SELECTION_REQUIRED)
    if ExceptionFlag.INDIVIDUAL_REVIEW_NOTED in flags:
        codes.append(HoldReasonCode.INDIVIDUAL_REVIEW_REQUIRED)

    additional_cost_issue_codes = {
        "ADDITIONAL_COST_INCOMPLETE",
        "ADDITIONAL_COST_SCHEDULE_MISSING",
    }
    if any(issue.code in additional_cost_issue_codes for issue in validation.issues):
        codes.append(HoldReasonCode.ADDITIONAL_COST_UNKNOWN)
    if any(issue.code == "EVIDENCE_MISSING" for issue in validation.issues):
        codes.append(HoldReasonCode.EVIDENCE_MISSING)
    if any(issue.code == "EVIDENCE_TEXT_NOT_FOUND" for issue in validation.issues):
        codes.append(HoldReasonCode.TABLE_REVIEW_REQUIRED)
    if any("CONFLICT" in issue.code for issue in validation.issues):
        codes.append(HoldReasonCode.SOURCE_CONFLICT)

    unique_codes = list(dict.fromkeys(codes))
    return [_make(code) for code in unique_codes]


def derive_analysis_status(validation: ValidationReport, holds: list[Hold]) -> AnalysisStatus:
    if any(issue.severity == IssueSeverity.ERROR for issue in validation.issues):
        return AnalysisStatus.HOLD
    uncertain_codes = {
        HoldReasonCode.DOWN_PAYMENT_MISSING,
        HoldReasonCode.INTERIM_PAYMENT_MISSING,
        HoldReasonCode.BALANCE_PAYMENT_MISSING,
        HoldReasonCode.INTERIM_SCHEDULE_MISSING,
        HoldReasonCode.INTERIM_LOAN_RATIO_MISSING,
        HoldReasonCode.BANK_NOT_DISCLOSED,
        HoldReasonCode.LOAN_ARRANGEMENT_ONLY,
        HoldReasonCode.SELF_FUNDING_SCHEDULE_UNKNOWN,
        HoldReasonCode.INTEREST_TERMS_UNKNOWN,
        HoldReasonCode.UNIT_SELECTION_REQUIRED,
        HoldReasonCode.ADDITIONAL_COST_UNKNOWN,
        HoldReasonCode.TABLE_REVIEW_REQUIRED,
        HoldReasonCode.SOURCE_CONFLICT,
        HoldReasonCode.EVIDENCE_MISSING,
        HoldReasonCode.PDF_TEXT_UNAVAILABLE,
    }
    if any(hold.reason_code in uncertain_codes for hold in holds):
        return AnalysisStatus.PARTIAL
    return AnalysisStatus.READY
