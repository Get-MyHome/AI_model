from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from get_myhome_ai.models import AnalysisResponse, AnalysisStatus, PaymentBasis, PaymentStage


class LegacyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LegacyPaymentSchedule(LegacyModel):
    downPaymentRatio: float
    interimPaymentRatio: float
    interimLoanRatio: float
    balanceRatio: float
    balanceDueDate: str | None


class LegacyAdditionalCost(LegacyModel):
    type: str
    amount: int
    stage: str
    required: bool


class BackendV1Response(LegacyModel):
    complexId: str
    paymentSchedule: LegacyPaymentSchedule | None
    additionalCosts: list[LegacyAdditionalCost]
    compatibilityWarnings: list[str]


def _legacy_schedule(result: AnalysisResponse, warnings: list[str]) -> LegacyPaymentSchedule | None:
    schedule = result.payment_schedule
    components = (
        schedule.down_payment,
        schedule.interim_payment,
        schedule.balance_payment,
    )
    if any(
        component.basis != PaymentBasis.RATIO or component.total_ratio is None
        for component in components
    ):
        warnings.append("LEGACY_FIXED_AMOUNT_UNSUPPORTED")
        return None
    if result.interim_loan.arranged_ratio is None or schedule.interim_payment.total_ratio == 0:
        warnings.append("LEGACY_INTERIM_LOAN_RATIO_MISSING")
        return None

    internal_ratio = result.interim_loan.arranged_ratio / schedule.interim_payment.total_ratio
    backend_percent = int(internal_ratio * 100)
    represented_ratio = schedule.interim_payment.total_ratio * backend_percent / 100
    if abs(represented_ratio - result.interim_loan.arranged_ratio) > 0.0001:
        warnings.append("LEGACY_INTERIM_LOAN_RATIO_LOSSY")
        return None

    balance_due_date = (
        schedule.balance_payment.due_date.isoformat()
        if schedule.balance_payment.due_date is not None
        else None
    )
    return LegacyPaymentSchedule(
        downPaymentRatio=schedule.down_payment.total_ratio,
        interimPaymentRatio=schedule.interim_payment.total_ratio,
        interimLoanRatio=internal_ratio,
        balanceRatio=schedule.balance_payment.total_ratio,
        balanceDueDate=balance_due_date,
    )


def to_backend_v1(result: AnalysisResponse) -> BackendV1Response:
    warnings: list[str] = []
    if not result.validation.passed or result.analysis_status == AnalysisStatus.HOLD:
        return BackendV1Response(
            complexId=result.complex_id,
            paymentSchedule=None,
            additionalCosts=[],
            compatibilityWarnings=["LEGACY_UNSAFE_ANALYSIS_OMITTED"],
        )
    additional_costs: list[LegacyAdditionalCost] = []
    for cost in result.additional_costs:
        target_name = result.target_unit.unit_type_name
        applies_to_target = cost.applicable_unit_type is None or (
            target_name is not None
            and cost.applicable_unit_type.replace(" ", "").lower()
            == target_name.replace(" ", "").lower()
        )
        safe = (
            applies_to_target
            and cost.required is True
            and cost.included_in_sale_price is False
            and cost.total_amount_manwon is not None
            and cost.payments
            and all(payment.stage == PaymentStage.BALANCE for payment in cost.payments)
        )
        if not safe:
            warnings.append("LEGACY_ADDITIONAL_COST_OMITTED")
            continue
        additional_costs.append(
            LegacyAdditionalCost(
                type=cost.type.value,
                amount=cost.total_amount_manwon,
                stage=PaymentStage.BALANCE.value,
                required=True,
            )
        )

    return BackendV1Response(
        complexId=result.complex_id,
        paymentSchedule=_legacy_schedule(result, warnings),
        additionalCosts=additional_costs,
        compatibilityWarnings=list(dict.fromkeys(warnings)),
    )
