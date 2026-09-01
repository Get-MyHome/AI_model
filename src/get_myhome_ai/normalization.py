from __future__ import annotations

import re

from get_myhome_ai.models import (
    ExceptionFlag,
    ExtractionDraft,
    InterestType,
    LoanArrangementStatus,
    PaymentBasis,
    PaymentComponent,
    ValueOrigin,
)

BACKEND_UNIT_TYPE = re.compile(r"^0*(?P<area>\d{2,3})\.\d+(?P<suffix>[A-Za-z]*)$")


def normalize_unit_type_name(value: str | None) -> str | None:
    """백엔드 전용면적 표기를 PDF 약식 주택형 표기로 바꾼다."""
    if value is None:
        return None
    stripped = value.strip()
    match = BACKEND_UNIT_TYPE.fullmatch(stripped)
    if not match:
        return stripped
    area = int(match.group("area"))
    suffix = match.group("suffix").upper()
    return f"{area}{suffix}"


def _infer_component(component: PaymentComponent, path: str, derived: list[str]) -> None:
    ratio_values = [item.ratio for item in component.installments]
    amount_values = [item.amount_manwon for item in component.installments]

    if (
        component.total_ratio is None
        and ratio_values
        and all(value is not None for value in ratio_values)
    ):
        ratio_total = sum(value for value in ratio_values if value is not None)
        if 0 <= ratio_total <= 1:
            component.total_ratio = ratio_total
            derived.append(f"{path}/total_ratio")
    if (
        component.total_amount_manwon is None
        and amount_values
        and all(value is not None for value in amount_values)
    ):
        component.total_amount_manwon = sum(value for value in amount_values if value is not None)
        derived.append(f"{path}/total_amount_manwon")

    expected_basis = PaymentBasis.UNKNOWN
    if component.total_ratio is not None and component.total_amount_manwon is not None:
        expected_basis = PaymentBasis.MIXED
    elif component.total_ratio is not None:
        expected_basis = PaymentBasis.RATIO
    elif component.total_amount_manwon is not None:
        expected_basis = PaymentBasis.FIXED_AMOUNT
    if component.basis != expected_basis:
        component.basis = expected_basis
        derived.append(f"{path}/basis")


def normalize_draft(draft: ExtractionDraft) -> tuple[ExtractionDraft, list[str]]:
    normalized = draft.model_copy(deep=True)
    derived: list[str] = []

    components = (
        (normalized.payment_schedule.down_payment, "/payment_schedule/down_payment"),
        (normalized.payment_schedule.interim_payment, "/payment_schedule/interim_payment"),
        (normalized.payment_schedule.balance_payment, "/payment_schedule/balance_payment"),
    )
    for component, path in components:
        _infer_component(component, path, derived)

    schedule = normalized.payment_schedule
    if (
        schedule.balance_payment.total_ratio is None
        and schedule.down_payment.total_ratio is not None
        and schedule.interim_payment.total_ratio is not None
    ):
        remainder = 1.0 - (schedule.down_payment.total_ratio + schedule.interim_payment.total_ratio)
        if remainder >= 0:
            schedule.balance_payment.total_ratio = round(remainder, 10)
            derived.append("/payment_schedule/balance_payment/total_ratio")
            _infer_component(
                schedule.balance_payment,
                "/payment_schedule/balance_payment",
                derived,
            )

    loan = normalized.interim_loan
    interim = schedule.interim_payment
    if loan.arrangement_status == LoanArrangementStatus.NOT_AVAILABLE:
        if loan.arranged_ratio is None:
            loan.arranged_ratio = 0.0
            derived.append("/interim_loan/arranged_ratio")
        if loan.arranged_amount_manwon is None:
            loan.arranged_amount_manwon = 0
            derived.append("/interim_loan/arranged_amount_manwon")
        if loan.self_funding_ratio is None and interim.total_ratio is not None:
            loan.self_funding_ratio = interim.total_ratio
            loan.self_funding_origin = ValueOrigin.DERIVED
            derived.extend(
                [
                    "/interim_loan/self_funding_ratio",
                    "/interim_loan/self_funding_origin",
                ]
            )
        if loan.self_funding_amount_manwon is None and interim.total_amount_manwon is not None:
            loan.self_funding_amount_manwon = interim.total_amount_manwon
            loan.self_funding_origin = ValueOrigin.DERIVED
            derived.extend(
                [
                    "/interim_loan/self_funding_amount_manwon",
                    "/interim_loan/self_funding_origin",
                ]
            )
        if loan.interest_type != InterestType.NOT_APPLICABLE:
            loan.interest_type = InterestType.NOT_APPLICABLE
            derived.append("/interim_loan/interest_type")

    if (
        loan.self_funding_ratio is None
        and interim.total_ratio is not None
        and loan.arranged_ratio is not None
        and interim.total_ratio >= loan.arranged_ratio
    ):
        loan.self_funding_ratio = round(interim.total_ratio - loan.arranged_ratio, 10)
        loan.self_funding_origin = ValueOrigin.DERIVED
        derived.extend(
            [
                "/interim_loan/self_funding_ratio",
                "/interim_loan/self_funding_origin",
            ]
        )

    for index, cost in enumerate(normalized.additional_costs):
        amounts = [payment.amount_manwon for payment in cost.payments]
        if (
            cost.total_amount_manwon is None
            and amounts
            and all(value is not None for value in amounts)
        ):
            cost.total_amount_manwon = sum(value for value in amounts if value is not None)
            derived.append(f"/additional_costs/{index}/total_amount_manwon")

    flags = list(dict.fromkeys(normalized.exception_flags))
    if (
        any(component.basis == PaymentBasis.FIXED_AMOUNT for component, _ in components)
        and ExceptionFlag.FIXED_AMOUNT_PAYMENT not in flags
    ):
        flags.append(ExceptionFlag.FIXED_AMOUNT_PAYMENT)
        derived.append("/exception_flags")
    if (
        (loan.self_funding_ratio or 0) > 0 or (loan.self_funding_amount_manwon or 0) > 0
    ) and ExceptionFlag.SELF_FUNDING_REQUIRED not in flags:
        flags.append(ExceptionFlag.SELF_FUNDING_REQUIRED)
        derived.append("/exception_flags")
    normalized.exception_flags = flags

    return normalized, list(dict.fromkeys(derived))
