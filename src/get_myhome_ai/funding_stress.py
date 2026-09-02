from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from get_myhome_ai.errors import FundingStressUnavailableError
from get_myhome_ai.models import (
    AnalysisResponse,
    InterestType,
    LoanArrangementStatus,
    LoanSettlementRequirement,
    PaymentComponent,
    PaymentStage,
    ReviewStatus,
)
from get_myhome_ai.stress_models import (
    AnalysisFingerprint,
    CashSnapshotTiming,
    DocumentCapComparison,
    FirstShortfall,
    FundingCertainty,
    FundingScenario,
    FundingStressRequest,
    FundingStressResponse,
    MarginStatus,
    RatioMargin,
    RatioThreshold,
    RouteLimitCase,
    RouteStatus,
    RouteStressCase,
    ScenarioStatus,
    StageMargin,
    StressHold,
    StressHoldCode,
    ThresholdStatus,
)

CALCULATOR_VERSION = "0.1.0"
BPS_DENOMINATOR = 10_000
THRESHOLD_RESOLUTION_BPS = 1


@dataclass(frozen=True)
class _Obligations:
    contract: int | None
    interim: int | None
    balance: int | None
    conditional: bool


def _ratio_amount(ratio: float, sale_price_manwon: int) -> int:
    return int(
        (Decimal(str(ratio)) * Decimal(sale_price_manwon)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def _bps_amount(ratio_bps: int, sale_price_manwon: int) -> int:
    return int(
        (Decimal(ratio_bps) * Decimal(sale_price_manwon) / Decimal(BPS_DENOMINATOR)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def _amount_bps(amount_manwon: int, sale_price_manwon: int) -> int:
    if sale_price_manwon <= 0:
        raise FundingStressUnavailableError("분양가가 0원이면 비율을 계산할 수 없습니다.")
    return min(
        BPS_DENOMINATOR,
        int(
            (Decimal(amount_manwon) * Decimal(BPS_DENOMINATOR) / sale_price_manwon).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        ),
    )


def _component_amount(component: PaymentComponent, sale_price_manwon: int) -> int | None:
    if component.total_amount_manwon is not None:
        return component.total_amount_manwon
    if component.total_ratio is not None:
        return _ratio_amount(component.total_ratio, sale_price_manwon)
    return None


def _hold(
    code: StressHoldCode,
    message: str,
    next_action: str,
    *,
    blocking: bool,
) -> StressHold:
    return StressHold(code=code, blocking=blocking, message=message, next_action=next_action)


def _dedupe_holds(holds: list[StressHold]) -> list[StressHold]:
    unique: dict[tuple[object, ...], StressHold] = {}
    for hold in holds:
        key = (hold.code, hold.blocking, hold.message, hold.next_action)
        unique.setdefault(key, hold)
    return list(unique.values())


def _canonical_housing_type(value: str) -> str | None:
    text = value.strip().upper()
    if not text:
        return None
    match = re.match(r"^0*(\d{2,3})(?:\.\d+)?\s*([A-Z]+)?(?:\s|$|형|타입)", text)
    if match is None:
        return None
    area = str(int(match.group(1)))
    suffix = match.group(2) or ""
    return f"{area}{suffix}"


def _cost_applies_to_target(
    applicable_unit_type: str | None,
    *,
    unit_type_id: str | None,
    unit_type_name: str | None,
) -> bool | None:
    if applicable_unit_type is None or not applicable_unit_type.strip():
        return None
    applicable = applicable_unit_type.strip()
    if applicable.casefold() in {"전체", "공통", "all", "전 주택형"}:
        return True

    exact_targets = {
        value.strip().casefold()
        for value in (unit_type_id, unit_type_name)
        if value is not None and value.strip()
    }
    if applicable.casefold() in exact_targets:
        return True

    applicable_types = {
        canonical
        for part in re.split(r"[,/|\xb7]", applicable)
        if (canonical := _canonical_housing_type(part)) is not None
    }
    target_type = _canonical_housing_type(unit_type_name) if unit_type_name else None
    if applicable_types and target_type is not None:
        return target_type in applicable_types
    return None


def _required_additional_costs(
    analysis: AnalysisResponse,
) -> tuple[dict[PaymentStage, int], list[StressHold], bool]:
    amounts = {
        PaymentStage.CONTRACT: 0,
        PaymentStage.INTERIM: 0,
        PaymentStage.BALANCE: 0,
    }
    holds: list[StressHold] = []
    conditional = False
    optional_seen = False

    for cost in analysis.additional_costs:
        if cost.required is False:
            optional_seen = True
            continue

        if cost.included_in_sale_price is True:
            continue

        applies = _cost_applies_to_target(
            cost.applicable_unit_type,
            unit_type_id=analysis.target_unit.unit_type_id,
            unit_type_name=analysis.target_unit.unit_type_name,
        )
        if applies is False:
            continue
        if applies is None:
            conditional = True
            holds.append(
                _hold(
                    StressHoldCode.ADDITIONAL_COST_APPLICABILITY_UNKNOWN,
                    f"'{cost.name}'이(가) 선택한 주택형에 적용되는지 확정하지 못했습니다.",
                    "선택 주택형의 추가비용 항목인지 원문 표와 계약서를 확인하세요.",
                    blocking=True,
                )
            )

        if cost.included_in_sale_price is None:
            conditional = True
            holds.append(
                _hold(
                    StressHoldCode.ADDITIONAL_COST_INCLUSION_UNKNOWN,
                    f"'{cost.name}'이(가) 분양가에 이미 포함되었는지 확정하지 못했습니다.",
                    "공급금액 표와 선택품목 계약서에서 분양가 포함 여부를 확인하세요.",
                    blocking=True,
                )
            )

        if cost.required is None:
            conditional = True
            holds.append(
                _hold(
                    StressHoldCode.REQUIRED_ADDITIONAL_COST_UNKNOWN,
                    f"'{cost.name}'의 필수 납부 여부가 확정되지 않아 계산에서 제외했습니다.",
                    "선택품목 계약서에서 필수 여부·금액·납부일을 확인하세요.",
                    blocking=True,
                )
            )

        if (
            applies is not True
            or cost.included_in_sale_price is not False
            or cost.required is not True
        ):
            continue

        known_payments = [
            payment
            for payment in cost.payments
            if payment.amount_manwon is not None
            and payment.stage
            in {
                PaymentStage.CONTRACT,
                PaymentStage.INTERIM,
                PaymentStage.BALANCE,
                PaymentStage.MOVE_IN,
            }
        ]
        if not known_payments:
            conditional = True
            holds.append(
                _hold(
                    StressHoldCode.REQUIRED_ADDITIONAL_COST_UNKNOWN,
                    f"'{cost.name}'은 필수 비용이지만 납부 구간별 금액을 확정하지 못했습니다.",
                    "총액과 회차별 납부 금액을 원문에서 확인하세요.",
                    blocking=True,
                )
            )
            continue
        payment_sum = 0
        for payment in known_payments:
            stage = PaymentStage.BALANCE if payment.stage == PaymentStage.MOVE_IN else payment.stage
            amount = payment.amount_manwon
            assert amount is not None
            amounts[stage] += amount
            payment_sum += amount
        if (
            cost.total_amount_manwon is None
            or payment_sum != cost.total_amount_manwon
            or len(known_payments) != len(cost.payments)
        ):
            conditional = True
            holds.append(
                _hold(
                    StressHoldCode.REQUIRED_ADDITIONAL_COST_UNKNOWN,
                    f"'{cost.name}'의 총액과 납부 구간별 금액을 완전히 대조하지 못했습니다.",
                    "총액, 모든 회차별 금액, 납부 구간을 원문 표와 계약서에서 확인하세요.",
                    blocking=True,
                )
            )

    if optional_seen:
        holds.append(
            _hold(
                StressHoldCode.OPTIONAL_COSTS_EXCLUDED,
                "선택 추가비용은 기본 자금 스트레스 계산에 합산하지 않았습니다.",
                "선택할 유상옵션이 있다면 별도 시나리오로 더하세요.",
                blocking=False,
            )
        )
    return amounts, holds, conditional


def _obligations(
    analysis: AnalysisResponse, sale_price_manwon: int
) -> tuple[_Obligations, list[StressHold]]:
    schedule = analysis.payment_schedule
    holds: list[StressHold] = []
    contract = _component_amount(schedule.down_payment, sale_price_manwon)
    interim = _component_amount(schedule.interim_payment, sale_price_manwon)
    balance = _component_amount(schedule.balance_payment, sale_price_manwon)
    costs, cost_holds, conditional = _required_additional_costs(analysis)
    holds.extend(cost_holds)

    missing = []
    for name, value in (("계약금", contract), ("중도금", interim), ("잔금", balance)):
        if value is None:
            missing.append(name)
    if missing:
        holds.append(
            _hold(
                StressHoldCode.PAYMENT_VALUE_UNKNOWN,
                f"{', '.join(missing)} 금액을 확정하지 못해 해당 구간은 계산할 수 없습니다.",
                "검수된 분양가·비율·정액 자료를 확인하세요.",
                blocking=True,
            )
        )

    return (
        _Obligations(
            contract=None if contract is None else contract + costs[PaymentStage.CONTRACT],
            interim=None if interim is None else interim + costs[PaymentStage.INTERIM],
            balance=None if balance is None else balance + costs[PaymentStage.BALANCE],
            conditional=conditional,
        ),
        holds,
    )


def _maximum_interim_bps(analysis: AnalysisResponse, sale_price_manwon: int) -> int | None:
    component = analysis.payment_schedule.interim_payment
    if component.total_ratio is not None:
        return int(
            (Decimal(str(component.total_ratio)) * BPS_DENOMINATOR).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
    if component.total_amount_manwon is not None:
        return _amount_bps(component.total_amount_manwon, sale_price_manwon)
    return None


def _document_cap_bps(analysis: AnalysisResponse, sale_price_manwon: int) -> int | None:
    loan = analysis.interim_loan
    if loan.arrangement_status == LoanArrangementStatus.NOT_AVAILABLE:
        return 0
    if loan.arranged_ratio is not None:
        return int(
            (Decimal(str(loan.arranged_ratio)) * BPS_DENOMINATOR).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
    if loan.arranged_amount_manwon is not None:
        return _amount_bps(loan.arranged_amount_manwon, sale_price_manwon)
    return None


def _settlement_principal(
    analysis: AnalysisResponse,
    interim_loan_amount_manwon: int,
) -> tuple[int, bool, list[StressHold]]:
    if interim_loan_amount_manwon == 0:
        return 0, False, []
    requirement = analysis.interim_loan.settlement_requirement
    if requirement in {
        LoanSettlementRequirement.REPAY_OR_CONVERT_TO_MORTGAGE,
        LoanSettlementRequirement.REPAY_REQUIRED,
        LoanSettlementRequirement.CONVERT_TO_MORTGAGE_REQUIRED,
    }:
        return interim_loan_amount_manwon, False, []
    if requirement in {
        LoanSettlementRequirement.CONTINUE_EXPLICITLY_ALLOWED,
        LoanSettlementRequirement.NOT_APPLICABLE,
    }:
        return 0, False, []
    return (
        interim_loan_amount_manwon,
        True,
        [
            _hold(
                StressHoldCode.SETTLEMENT_TERMS_UNKNOWN,
                "입주 시 중도금 대출 원금 처리 조건이 미확정이라 "
                "상환·대환 필요로 보수 계산했습니다.",
                "잔금대출 전환·재심사·원금 상환 조건을 금융기관에 확인하세요.",
                blocking=True,
            )
        ],
    )


def _interest_holds(analysis: AnalysisResponse) -> list[StressHold]:
    if analysis.interim_loan.interest_type not in {
        InterestType.DEFERRED_INTEREST,
        InterestType.BORROWER_PAYS,
        InterestType.MIXED,
    }:
        return []
    has_known_interest_cost = any(
        cost.type.value == "INTERIM_INTEREST"
        and cost.required is True
        and cost.total_amount_manwon is not None
        for cost in analysis.additional_costs
    )
    if has_known_interest_cost:
        return []
    return [
        _hold(
            StressHoldCode.INTEREST_AMOUNT_UNKNOWN,
            "중도금 이자 금액이 공고문에 확정되지 않아 원금 기준으로만 계산했습니다.",
            "금리·실행일·정산일을 확인해 이자 시나리오를 별도로 계산하세요.",
            blocking=True,
        )
    ]


def _stage_margin(
    *,
    stage: PaymentStage,
    required: int,
    cash: int,
    dedicated_funding: int,
    certainty: FundingCertainty,
) -> StageMargin:
    available = cash + dedicated_funding
    margin = available - required
    return StageMargin(
        stage=stage,
        required_manwon=required,
        dedicated_funding_manwon=dedicated_funding,
        available_manwon=available,
        cash_margin_manwon=margin,
        shortfall_manwon=max(-margin, 0),
        cash_carried_forward_manwon=max(margin, 0),
        certainty=certainty,
    )


def _first_shortfall(
    stage_margins: list[StageMargin], analysis: AnalysisResponse
) -> FirstShortfall | None:
    for item in stage_margins:
        if item.shortfall_manwon <= 0:
            continue
        component = None
        if item.stage == PaymentStage.CONTRACT:
            component = analysis.payment_schedule.down_payment
        elif item.stage == PaymentStage.BALANCE:
            component = analysis.payment_schedule.balance_payment
        # Partial interim financing has no evidence-backed allocation order, so
        # its exact installment and timing must remain unknown.
        return FirstShortfall(
            stage=item.stage,
            installment_number=None,
            due_date=component.due_date if component is not None else None,
            due_month=component.due_month if component is not None else None,
            due_text=component.due_text if component is not None else None,
            shortfall_manwon=item.shortfall_manwon,
            certainty=item.certainty,
        )
    return None


def _recovery_months(shortfall: int, monthly_saving_manwon: int | None) -> int | None:
    if shortfall <= 0 or monthly_saving_manwon is None or monthly_saving_manwon <= 0:
        return None
    return (shortfall + monthly_saving_manwon - 1) // monthly_saving_manwon


def _scenario(
    *,
    analysis: AnalysisResponse,
    obligations: _Obligations,
    cash_manwon: int,
    balance_financing_manwon: int | None,
    ratio_bps: int,
    sale_price_manwon: int,
    monthly_saving_manwon: int | None,
) -> tuple[FundingScenario, list[StressHold]]:
    scheduled_interim = _component_amount(
        analysis.payment_schedule.interim_payment, sale_price_manwon
    )
    loan_amount = _bps_amount(ratio_bps, sale_price_manwon)
    if scheduled_interim is not None:
        # A rounded bps representation of a fixed-amount interim payment can be
        # one manwon above the actual obligation. Financing may never exceed
        # the evidence-backed base interim amount.
        loan_amount = min(loan_amount, scheduled_interim)
    if obligations.contract is None or obligations.interim is None:
        return (
            FundingScenario(
                interim_ratio_bps=ratio_bps,
                interim_loan_amount_manwon=loan_amount,
                status=ScenarioStatus.UNKNOWN,
                first_shortfall=None,
                stage_margins=[],
                worst_margin_manwon=None,
                balance_margin_manwon=None,
                recovery_months_at_first_shortfall=None,
            ),
            [],
        )

    stage_margins: list[StageMargin] = []
    contract = _stage_margin(
        stage=PaymentStage.CONTRACT,
        required=obligations.contract,
        cash=cash_manwon,
        dedicated_funding=0,
        certainty=FundingCertainty.CONFIRMED,
    )
    stage_margins.append(contract)
    interim_certainty = (
        FundingCertainty.CONDITIONAL
        if ratio_bps > 0
        or analysis.interim_loan.arrangement_status
        in {LoanArrangementStatus.PLANNED, LoanArrangementStatus.UNDER_DISCUSSION}
        else FundingCertainty.CONFIRMED
    )
    interim = _stage_margin(
        stage=PaymentStage.INTERIM,
        required=obligations.interim,
        cash=contract.cash_carried_forward_manwon,
        dedicated_funding=loan_amount,
        certainty=interim_certainty,
    )
    stage_margins.append(interim)

    scenario_holds: list[StressHold] = []
    if obligations.balance is not None and balance_financing_manwon is not None:
        settlement, settlement_conditional, settlement_holds = _settlement_principal(
            analysis, loan_amount
        )
        scenario_holds.extend(settlement_holds)
        balance = _stage_margin(
            stage=PaymentStage.BALANCE,
            required=obligations.balance + settlement,
            cash=interim.cash_carried_forward_manwon,
            dedicated_funding=balance_financing_manwon,
            certainty=(
                FundingCertainty.CONDITIONAL
                if settlement_conditional or balance_financing_manwon > 0 or obligations.conditional
                else FundingCertainty.CONFIRMED
            ),
        )
        stage_margins.append(balance)

    first = _first_shortfall(stage_margins, analysis)
    full_information = obligations.balance is not None and balance_financing_manwon is not None
    status = (
        ScenarioStatus.UNKNOWN
        if not full_information or (obligations.conditional and first is None)
        else (ScenarioStatus.SHORTFALL if first is not None else ScenarioStatus.COMPLETE)
    )
    return (
        FundingScenario(
            interim_ratio_bps=ratio_bps,
            interim_loan_amount_manwon=loan_amount,
            status=status,
            first_shortfall=first,
            stage_margins=stage_margins,
            worst_margin_manwon=min(item.cash_margin_manwon for item in stage_margins),
            balance_margin_manwon=(
                stage_margins[-1].cash_margin_manwon
                if stage_margins[-1].stage == PaymentStage.BALANCE
                else None
            ),
            recovery_months_at_first_shortfall=(
                _recovery_months(first.shortfall_manwon, monthly_saving_manwon)
                if first is not None
                else None
            ),
        ),
        scenario_holds,
    )


def _threshold(
    *,
    analysis: AnalysisResponse,
    obligations: _Obligations,
    cash_manwon: int,
    balance_financing_manwon: int | None,
    maximum_bps: int,
    sale_price_manwon: int,
    monthly_saving_manwon: int | None,
    full_completion: bool,
) -> RatioThreshold:
    if obligations.contract is None or obligations.interim is None:
        return RatioThreshold(
            status=ThresholdStatus.UNKNOWN,
            minimum_ratio_bps=None,
            minimum_loan_amount_manwon=None,
            resolution_bps=THRESHOLD_RESOLUTION_BPS,
            limiting_shortfall=None,
        )
    if obligations.conditional:
        return RatioThreshold(
            status=ThresholdStatus.UNKNOWN,
            minimum_ratio_bps=None,
            minimum_loan_amount_manwon=None,
            resolution_bps=THRESHOLD_RESOLUTION_BPS,
            limiting_shortfall=None,
        )
    zero, _ = _scenario(
        analysis=analysis,
        obligations=obligations,
        cash_manwon=cash_manwon,
        balance_financing_manwon=balance_financing_manwon if full_completion else None,
        ratio_bps=0,
        sale_price_manwon=sale_price_manwon,
        monthly_saving_manwon=monthly_saving_manwon,
    )
    if zero.stage_margins and zero.stage_margins[0].shortfall_manwon > 0:
        return RatioThreshold(
            status=ThresholdStatus.PRIOR_STAGE_SHORTFALL,
            minimum_ratio_bps=None,
            minimum_loan_amount_manwon=None,
            resolution_bps=THRESHOLD_RESOLUTION_BPS,
            limiting_shortfall=zero.first_shortfall,
        )
    if full_completion and (obligations.balance is None or balance_financing_manwon is None):
        return RatioThreshold(
            status=ThresholdStatus.UNKNOWN,
            minimum_ratio_bps=None,
            minimum_loan_amount_manwon=None,
            resolution_bps=THRESHOLD_RESOLUTION_BPS,
            limiting_shortfall=None,
        )

    def scenario_at(ratio_bps: int) -> FundingScenario:
        candidate, _ = _scenario(
            analysis=analysis,
            obligations=obligations,
            cash_manwon=cash_manwon,
            balance_financing_manwon=balance_financing_manwon if full_completion else None,
            ratio_bps=ratio_bps,
            sale_price_manwon=sale_price_manwon,
            monthly_saving_manwon=monthly_saving_manwon,
        )
        return candidate

    def succeeds(candidate: FundingScenario) -> bool:
        stages = candidate.stage_margins if full_completion else candidate.stage_margins[:2]
        return bool(stages) and all(item.shortfall_manwon == 0 for item in stages)

    if succeeds(zero):
        return RatioThreshold(
            status=ThresholdStatus.CALCULATED,
            minimum_ratio_bps=0,
            minimum_loan_amount_manwon=zero.interim_loan_amount_manwon,
            resolution_bps=THRESHOLD_RESOLUTION_BPS,
            limiting_shortfall=None,
        )

    upper = scenario_at(maximum_bps)
    if not succeeds(upper):
        return RatioThreshold(
            status=ThresholdStatus.NOT_ACHIEVABLE,
            minimum_ratio_bps=None,
            minimum_loan_amount_manwon=None,
            resolution_bps=THRESHOLD_RESOLUTION_BPS,
            limiting_shortfall=upper.first_shortfall,
        )

    # More interim financing cannot make an already-covered stage fail: when
    # settlement is required the added principal cancels at balance, and when
    # continuation is explicitly allowed it only improves the margin.  Search
    # the first successful integer basis point instead of materializing up to
    # 10,001 Pydantic scenarios.
    low = THRESHOLD_RESOLUTION_BPS
    high = maximum_bps
    while low < high:
        midpoint = (low + high) // 2
        if succeeds(scenario_at(midpoint)):
            high = midpoint
        else:
            low = midpoint + THRESHOLD_RESOLUTION_BPS
    threshold_scenario = scenario_at(low)
    return RatioThreshold(
        status=ThresholdStatus.CALCULATED,
        minimum_ratio_bps=low,
        minimum_loan_amount_manwon=threshold_scenario.interim_loan_amount_manwon,
        resolution_bps=THRESHOLD_RESOLUTION_BPS,
        limiting_shortfall=None,
    )


def _margin(
    *, threshold: RatioThreshold, document_cap_bps: int | None, arrangement_status: str
) -> RatioMargin:
    conditional = arrangement_status not in {
        LoanArrangementStatus.NOT_AVAILABLE.value,
    }
    certainty = FundingCertainty.CONDITIONAL if conditional else FundingCertainty.CONFIRMED
    if threshold.status != ThresholdStatus.CALCULATED or document_cap_bps is None:
        return RatioMargin(
            status=MarginStatus.UNKNOWN,
            required_ratio_bps=threshold.minimum_ratio_bps,
            document_cap_ratio_bps=document_cap_bps,
            margin_bps=None,
            certainty=certainty,
            message="공고문 알선 상한과 중도금 통과 임계비율을 비교할 수 없습니다.",
        )
    margin = document_cap_bps - (threshold.minimum_ratio_bps or 0)
    if margin > 0:
        status = MarginStatus.POSITIVE
        message = f"공고문상 알선 상한이 중도금 통과 임계선보다 {margin / 100:.2f}%p 높습니다."
    elif margin == 0:
        status = MarginStatus.ZERO
        message = "공고문상 알선 상한과 중도금 통과 임계선이 같습니다."
    else:
        status = MarginStatus.NEGATIVE
        message = f"공고문상 알선 상한이 중도금 통과 임계선보다 {abs(margin) / 100:.2f}%p 낮습니다."
    return RatioMargin(
        status=status,
        required_ratio_bps=threshold.minimum_ratio_bps,
        document_cap_ratio_bps=document_cap_bps,
        margin_bps=margin,
        certainty=certainty,
        message=message + " 이 비율은 개인 대출 승인률이 아닙니다.",
    )


def _route_cases(
    *,
    request: FundingStressRequest,
    analysis: AnalysisResponse,
    obligations: _Obligations,
    maximum_bps: int,
    document_cap_bps: int | None,
    interim_threshold: RatioThreshold,
    sale_price_manwon: int,
) -> list[RouteStressCase]:
    base_grid = {0, maximum_bps, *request.interim_ratio_grid_bps}
    if document_cap_bps is not None:
        base_grid.add(document_cap_bps)
    if interim_threshold.minimum_ratio_bps is not None:
        base_grid.add(interim_threshold.minimum_ratio_bps)
    invalid = sorted(value for value in base_grid if value > maximum_bps)
    if invalid:
        raise FundingStressUnavailableError(
            f"중도금 총비율({maximum_bps}bps)을 초과한 시나리오가 있습니다: {invalid}"
        )
    grid = sorted(base_grid)
    cases: list[RouteStressCase] = []

    for route in request.loan_routes:
        if route.status != RouteStatus.OK:
            cases.append(
                RouteStressCase(
                    route_id=route.route_id,
                    product_code=route.product_code,
                    product_name=route.product_name,
                    rule_version=route.rule_version,
                    assumption_set_id=route.assumption_set_id,
                    route_status=route.status,
                    limit_case=None,
                    balance_financing_manwon=None,
                    full_completion_threshold=RatioThreshold(
                        status=ThresholdStatus.UNKNOWN,
                        minimum_ratio_bps=None,
                        minimum_loan_amount_manwon=None,
                        resolution_bps=THRESHOLD_RESOLUTION_BPS,
                        limiting_shortfall=None,
                    ),
                    scenarios=[],
                    holds=[
                        _hold(
                            StressHoldCode.ROUTE_NOT_ELIGIBLE,
                            f"{route.product_name}은 현재 {route.status}이므로 "
                            "자금 경로 계산에 사용하지 않았습니다.",
                            "백엔드 판정에서 OK가 된 경로의 한도로만 재계산하세요.",
                            blocking=True,
                        )
                    ],
                )
            )
            continue

        route_limits: list[tuple[RouteLimitCase, int]] = []
        if route.limit_min_manwon is not None and route.limit_min_manwon != route.limit_max_manwon:
            route_limits.append((RouteLimitCase.CONSERVATIVE_LIMIT, route.limit_min_manwon))
        route_limits.append((RouteLimitCase.MAXIMUM_LIMIT, route.limit_max_manwon or 0))

        for limit_case, balance_limit in route_limits:
            threshold = _threshold(
                analysis=analysis,
                obligations=obligations,
                cash_manwon=request.cash_manwon,
                balance_financing_manwon=balance_limit,
                maximum_bps=maximum_bps,
                sale_price_manwon=sale_price_manwon,
                monthly_saving_manwon=request.monthly_saving_manwon,
                full_completion=True,
            )
            scenario_results: list[FundingScenario] = []
            scenario_holds: list[StressHold] = []
            scenario_grid = set(grid)
            if threshold.minimum_ratio_bps is not None:
                scenario_grid.add(threshold.minimum_ratio_bps)
            for ratio_bps in sorted(scenario_grid):
                result, holds = _scenario(
                    analysis=analysis,
                    obligations=obligations,
                    cash_manwon=request.cash_manwon,
                    balance_financing_manwon=balance_limit,
                    ratio_bps=ratio_bps,
                    sale_price_manwon=sale_price_manwon,
                    monthly_saving_manwon=request.monthly_saving_manwon,
                )
                scenario_results.append(result)
                scenario_holds.extend(holds)
            cases.append(
                RouteStressCase(
                    route_id=route.route_id,
                    product_code=route.product_code,
                    product_name=route.product_name,
                    rule_version=route.rule_version,
                    assumption_set_id=route.assumption_set_id,
                    route_status=route.status,
                    limit_case=limit_case,
                    balance_financing_manwon=balance_limit,
                    full_completion_threshold=threshold,
                    scenarios=scenario_results,
                    holds=_dedupe_holds(scenario_holds),
                )
            )
    return cases


def _input_digest(request: FundingStressRequest, analysis: AnalysisResponse) -> str:
    payload = {
        "request": request.model_dump(mode="json", exclude={"analysis_request": {"pdf_url"}}),
        "source_sha256": analysis.meta.source_sha256,
        "reviewed_at": analysis.reviewed_at.isoformat() if analysis.reviewed_at else None,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def calculate_funding_stress(
    request: FundingStressRequest,
    analysis: AnalysisResponse,
) -> FundingStressResponse:
    if analysis.review_status != ReviewStatus.REVIEWED or analysis.reviewed_at is None:
        raise FundingStressUnavailableError(
            "자금 스트레스 계산은 REVIEWED 공고문 분석만 사용할 수 있습니다."
        )
    if not analysis.validation.passed:
        raise FundingStressUnavailableError(
            "고정식 검증을 통과하지 못한 공고문 분석은 계산할 수 없습니다."
        )
    sale_price = analysis.target_unit.sale_price_manwon
    if sale_price is None or sale_price <= 0:
        raise FundingStressUnavailableError("검수된 선택 주택형의 분양가가 필요합니다.")
    if request.analysis_request.complex_id != analysis.complex_id:
        raise FundingStressUnavailableError("요청 공고번호와 검수본 공고번호가 다릅니다.")
    if request.cash_snapshot_timing != CashSnapshotTiming.PRE_CONTRACT:
        raise FundingStressUnavailableError(
            "보유 현금은 계약금 납부 전 스냅샷으로만 계산할 수 있습니다."
        )
    contract_due_date = analysis.payment_schedule.down_payment.due_date
    if contract_due_date is not None and request.as_of_date > contract_due_date:
        raise FundingStressUnavailableError(
            "as_of_date가 공고문에 확정된 계약금 납부일보다 늦습니다. "
            "계약금 납부 전 보유 현금 스냅샷을 보내세요."
        )

    obligations, calculation_holds = _obligations(analysis, sale_price)
    maximum_bps = _maximum_interim_bps(analysis, sale_price)
    if maximum_bps is None:
        raise FundingStressUnavailableError("중도금 총비율·총액을 확정한 검수본이 필요합니다.")
    document_cap_bps = _document_cap_bps(analysis, sale_price)
    interim_threshold = _threshold(
        analysis=analysis,
        obligations=obligations,
        cash_manwon=request.cash_manwon,
        balance_financing_manwon=None,
        maximum_bps=maximum_bps,
        sale_price_manwon=sale_price,
        monthly_saving_manwon=request.monthly_saving_manwon,
        full_completion=False,
    )
    calculation_holds.extend(_interest_holds(analysis))
    if document_cap_bps is None:
        calculation_holds.append(
            _hold(
                StressHoldCode.PERSONAL_APPROVAL_REQUIRED,
                "공고문에서 중도금 대출 알선 상한을 확정하지 못했습니다.",
                "시행사·취급은행에 분양가 대비 실제 실행 가능 비율을 확인하세요.",
                blocking=True,
            )
        )
    if analysis.interim_loan.arrangement_status in {
        LoanArrangementStatus.PLANNED,
        LoanArrangementStatus.UNDER_DISCUSSION,
        LoanArrangementStatus.BANK_SELECTED,
    }:
        calculation_holds.append(
            _hold(
                StressHoldCode.PERSONAL_APPROVAL_REQUIRED,
                "공고문의 사업장 알선 비율은 개인 대출 승인을 의미하지 않습니다.",
                "소득·기존 대출·보증 요건을 기준으로 실제 실행 비율을 취급은행에 확인하세요.",
                blocking=True,
            )
        )
    if any(hold.reason_code.value == "SELF_FUNDING_SCHEDULE_UNKNOWN" for hold in analysis.holds):
        calculation_holds.append(
            _hold(
                StressHoldCode.SELF_FUNDING_SCHEDULE_UNKNOWN,
                "부분 대출이 어느 중도금 회차를 충당하는지 공고문으로는 확정할 수 없습니다.",
                "회차별 대출 실행액과 자납일을 시행사에 확인하세요.",
                blocking=True,
            )
        )

    route_cases = _route_cases(
        request=request,
        analysis=analysis,
        obligations=obligations,
        maximum_bps=maximum_bps,
        document_cap_bps=document_cap_bps,
        interim_threshold=interim_threshold,
        sale_price_manwon=sale_price,
    )
    comparison = DocumentCapComparison(
        arrangement_status=analysis.interim_loan.arrangement_status.value,
        document_cap_ratio_bps=document_cap_bps,
        personal_approval_confirmed=False,
        interim_continuity=_margin(
            threshold=interim_threshold,
            document_cap_bps=document_cap_bps,
            arrangement_status=analysis.interim_loan.arrangement_status.value,
        ),
    )
    return FundingStressResponse(
        advisory=True,
        calculator_version=CALCULATOR_VERSION,
        calculation_scope="REVIEWED_DOCUMENT_PRE_CONTRACT_CASH_AND_ROUTE_LIMITS",
        input_digest=_input_digest(request, analysis),
        analysis_fingerprint=AnalysisFingerprint(
            complex_id=analysis.complex_id,
            source_sha256=analysis.meta.source_sha256,
            unit_type_id=analysis.target_unit.unit_type_id,
            unit_type_name=analysis.target_unit.unit_type_name,
            sale_price_manwon=sale_price,
            schema_version=analysis.meta.schema_version,
            extractor_version=analysis.meta.extractor_version,
            reviewed_at=analysis.reviewed_at,
        ),
        as_of_date=request.as_of_date,
        savings_policy="RECOVERY_TIME_ONLY_NOT_CASHFLOW",
        monthly_saving_manwon=request.monthly_saving_manwon,
        maximum_interim_ratio_bps=maximum_bps,
        interim_continuity_threshold=interim_threshold,
        document_cap_comparison=comparison,
        route_cases=route_cases,
        holds=_dedupe_holds(calculation_holds),
        assumptions=[
            "중도금 비율은 분양가 대비 bps(100bps=1%p)로 표시합니다.",
            "비율 금액 변환은 만 원 단위 ROUND_HALF_UP을 사용합니다.",
            "대출 경로는 대안이며 서로 합산하지 않습니다.",
            "보유 현금은 계약금 납부 전 스냅샷이며 이미 납부한 금액을 소급 추정하지 않습니다.",
            "월 저축액은 부족액 회복 개월 계산에만 사용하고 현금흐름에 누적하지 않습니다.",
            "선택 추가비용은 제외하고 검수된 필수 추가비용만 합산합니다.",
            "부분 중도금 대출의 회차별 배분이 없으면 정확한 회차·날짜를 생성하지 않습니다.",
            "상환·대환 필요 중도금 원금은 잔금 조달 수요에 포함합니다.",
        ],
    )
