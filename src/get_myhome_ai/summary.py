from __future__ import annotations

from get_myhome_ai.models import (
    ExtractionDraft,
    LoanArrangementStatus,
    LoanSettlementRequirement,
    PaymentComponent,
    ValueOrigin,
)


def _ratio_text(value: float) -> str:
    percent = value * 100
    if percent.is_integer():
        return f"{int(percent)}%"
    return f"{percent:.1f}%"


def _component_text(name: str, component: PaymentComponent) -> str:
    if component.total_ratio is not None:
        return f"{name}은 분양가의 {_ratio_text(component.total_ratio)}입니다."
    if component.total_amount_manwon is not None:
        return f"{name}은 {component.total_amount_manwon:,}만 원 정액입니다."
    return f"{name} 조건은 공고문에서 확인하지 못했습니다."


def build_analysis_summary(draft: ExtractionDraft) -> str:
    schedule = draft.payment_schedule
    sentences = [
        _component_text("계약금", schedule.down_payment),
        _component_text("중도금", schedule.interim_payment),
        _component_text("잔금", schedule.balance_payment),
    ]

    loan = draft.interim_loan
    if loan.arrangement_status == LoanArrangementStatus.NOT_AVAILABLE:
        sentences.append("공고문에는 중도금 대출이 불가하다고 적혀 있습니다.")
    elif loan.arranged_ratio is not None:
        ratio = _ratio_text(loan.arranged_ratio)
        if loan.arrangement_status == LoanArrangementStatus.PLANNED:
            sentences.append(
                f"공고문상 분양가의 {ratio} 범위에서 중도금 대출을 알선할 예정입니다. "
                "실제 실행과 개인 승인은 확정되지 않았습니다."
            )
        elif loan.arrangement_status == LoanArrangementStatus.UNDER_DISCUSSION:
            sentences.append(
                f"공고문에는 분양가의 {ratio} 범위가 제시되어 있으나 금융기관과 협의 중이며 "
                "실제 대출 조건은 확정되지 않았습니다."
            )
        elif loan.arrangement_status == LoanArrangementStatus.BANK_SELECTED:
            sentences.append(
                f"공고문상 중도금 대출 범위는 분양가의 {ratio}이며 취급은행은 확인됐습니다. "
                "개인별 대출 승인은 별도 심사가 필요합니다."
            )
        else:
            sentences.append(
                f"공고문상 중도금 대출 관련 비율은 분양가의 {ratio}이지만 "
                "실제 실행 여부는 확인이 필요합니다."
            )
    elif loan.arranged_amount_manwon is not None:
        sentences.append(
            f"공고문상 중도금 대출 가능 금액은 {loan.arranged_amount_manwon:,}만 원입니다."
        )

    if loan.self_funding_ratio is not None and loan.self_funding_ratio > 0:
        ratio = _ratio_text(loan.self_funding_ratio)
        sentences.append(
            f"중도금 중 분양가의 {ratio}는 직접 납부해야 합니다."
            if loan.self_funding_origin == ValueOrigin.EXTRACTED
            else (
                f"중도금 중 분양가의 {ratio}는 사업장 알선 대출로 충당되지 않아 "
                "별도 조달이 필요합니다."
            )
        )
    elif loan.self_funding_amount_manwon is not None and loan.self_funding_amount_manwon > 0:
        amount = f"{loan.self_funding_amount_manwon:,}만 원"
        sentences.append(
            f"중도금 중 {amount}은 직접 납부해야 합니다."
            if loan.self_funding_origin == ValueOrigin.EXTRACTED
            else (f"중도금 중 {amount}은 사업장 알선 대출로 충당되지 않아 별도 조달이 필요합니다.")
        )

    if (
        loan.arrangement_status
        in {LoanArrangementStatus.PLANNED, LoanArrangementStatus.UNDER_DISCUSSION}
        and not loan.bank_names
    ):
        sentences.append("취급은행은 공고문에 공개되지 않았습니다.")

    if (
        loan.settlement_requirement
        == LoanSettlementRequirement.REPAY_OR_CONVERT_TO_MORTGAGE
    ):
        deadline = loan.settlement_deadline_text or "입주 전"
        sentences.append(
            f"공고문상 중도금 대출은 {deadline} 상환하거나 담보대출로 전환해야 합니다."
        )
    elif (
        loan.arrangement_status != LoanArrangementStatus.NOT_AVAILABLE
        and loan.settlement_requirement == LoanSettlementRequirement.NOT_STATED
    ):
        sentences.append("입주 시 중도금 대출의 상환·대환 조건은 공고문에서 확인하지 못했습니다.")

    if loan.extension_contingency_disclosed is True:
        sentences.append(
            "공고문에는 대출기간 만료 시 연장 절차 가능성이 언급돼 있으나 "
            "연장이 보장된 것은 아닙니다."
        )
    return " ".join(sentences)
