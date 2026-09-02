from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import Field, model_validator

from get_myhome_ai.models import AnalyzeRequest, PaymentStage, StrictModel

NonNegativeManwon = Annotated[int, Field(ge=0)]
SignedManwon = int
BasisPoints = Annotated[int, Field(ge=0, le=10_000)]


class RouteStatus(StrEnum):
    OK = "OK"
    HOLD = "HOLD"
    BLOCK = "BLOCK"


class RouteLimitCase(StrEnum):
    CONSERVATIVE_LIMIT = "CONSERVATIVE_LIMIT"
    MAXIMUM_LIMIT = "MAXIMUM_LIMIT"


class ThresholdStatus(StrEnum):
    CALCULATED = "CALCULATED"
    PRIOR_STAGE_SHORTFALL = "PRIOR_STAGE_SHORTFALL"
    NOT_ACHIEVABLE = "NOT_ACHIEVABLE"
    UNKNOWN = "UNKNOWN"


class ScenarioStatus(StrEnum):
    COMPLETE = "COMPLETE"
    SHORTFALL = "SHORTFALL"
    UNKNOWN = "UNKNOWN"
    NOT_CALCULATED = "NOT_CALCULATED"


class FundingCertainty(StrEnum):
    CONFIRMED = "CONFIRMED"
    CONDITIONAL = "CONDITIONAL"


class CashSnapshotTiming(StrEnum):
    PRE_CONTRACT = "PRE_CONTRACT"


class MarginStatus(StrEnum):
    POSITIVE = "POSITIVE"
    ZERO = "ZERO"
    NEGATIVE = "NEGATIVE"
    UNKNOWN = "UNKNOWN"


class StressHoldCode(StrEnum):
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    SALE_PRICE_REQUIRED = "SALE_PRICE_REQUIRED"
    PAYMENT_VALUE_UNKNOWN = "PAYMENT_VALUE_UNKNOWN"
    BALANCE_FINANCING_UNKNOWN = "BALANCE_FINANCING_UNKNOWN"
    SETTLEMENT_TERMS_UNKNOWN = "SETTLEMENT_TERMS_UNKNOWN"
    REQUIRED_ADDITIONAL_COST_UNKNOWN = "REQUIRED_ADDITIONAL_COST_UNKNOWN"
    ADDITIONAL_COST_INCLUSION_UNKNOWN = "ADDITIONAL_COST_INCLUSION_UNKNOWN"
    ADDITIONAL_COST_APPLICABILITY_UNKNOWN = "ADDITIONAL_COST_APPLICABILITY_UNKNOWN"
    OPTIONAL_COSTS_EXCLUDED = "OPTIONAL_COSTS_EXCLUDED"
    INTEREST_AMOUNT_UNKNOWN = "INTEREST_AMOUNT_UNKNOWN"
    PERSONAL_APPROVAL_REQUIRED = "PERSONAL_APPROVAL_REQUIRED"
    SELF_FUNDING_SCHEDULE_UNKNOWN = "SELF_FUNDING_SCHEDULE_UNKNOWN"
    ROUTE_NOT_ELIGIBLE = "ROUTE_NOT_ELIGIBLE"


class LoanRouteSnapshot(StrictModel):
    route_id: Annotated[str, Field(min_length=1, max_length=100)]
    product_code: Annotated[str, Field(min_length=1, max_length=100)]
    product_name: Annotated[str, Field(min_length=1, max_length=200)]
    status: RouteStatus
    limit_min_manwon: NonNegativeManwon | None = None
    limit_max_manwon: NonNegativeManwon | None = None
    rule_version: Annotated[str, Field(min_length=1, max_length=100)]
    assumption_set_id: Annotated[str, Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def limits_match_status(self) -> LoanRouteSnapshot:
        if (
            self.limit_min_manwon is not None
            and self.limit_max_manwon is not None
            and self.limit_min_manwon > self.limit_max_manwon
        ):
            raise ValueError("limit_min_manwon은 limit_max_manwon보다 클 수 없습니다.")
        if self.status == RouteStatus.OK and self.limit_max_manwon is None:
            raise ValueError("status=OK인 경로에는 limit_max_manwon이 필요합니다.")
        if self.status != RouteStatus.OK and (
            self.limit_min_manwon is not None or self.limit_max_manwon is not None
        ):
            raise ValueError("HOLD/BLOCK 경로에는 확정 한도를 보내지 않습니다.")
        return self


class FundingStressRequest(StrictModel):
    analysis_request: AnalyzeRequest
    cash_manwon: NonNegativeManwon
    cash_snapshot_timing: CashSnapshotTiming
    monthly_saving_manwon: NonNegativeManwon | None = None
    as_of_date: date
    loan_routes: Annotated[list[LoanRouteSnapshot], Field(max_length=10)]
    interim_ratio_grid_bps: Annotated[
        list[BasisPoints], Field(default_factory=list, max_length=101)
    ]

    @model_validator(mode="after")
    def route_ids_are_unique(self) -> FundingStressRequest:
        route_ids = [route.route_id for route in self.loan_routes]
        duplicates = sorted(
            route_id for route_id in set(route_ids) if route_ids.count(route_id) > 1
        )
        if duplicates:
            raise ValueError(f"route_id는 요청 내에서 고유해야 합니다: {duplicates}")
        return self


class AnalysisFingerprint(StrictModel):
    complex_id: str
    source_sha256: str
    unit_type_id: str | None
    unit_type_name: str | None
    sale_price_manwon: NonNegativeManwon
    schema_version: str
    extractor_version: str
    reviewed_at: datetime


class StressHold(StrictModel):
    code: StressHoldCode
    blocking: bool
    message: str
    next_action: str


class FirstShortfall(StrictModel):
    stage: PaymentStage
    installment_number: int | None
    due_date: date | None
    due_month: str | None
    due_text: str | None
    shortfall_manwon: NonNegativeManwon
    certainty: FundingCertainty


class StageMargin(StrictModel):
    stage: PaymentStage
    required_manwon: NonNegativeManwon
    dedicated_funding_manwon: NonNegativeManwon
    available_manwon: NonNegativeManwon
    cash_margin_manwon: SignedManwon
    shortfall_manwon: NonNegativeManwon
    cash_carried_forward_manwon: NonNegativeManwon
    certainty: FundingCertainty


class FundingScenario(StrictModel):
    interim_ratio_bps: BasisPoints
    interim_loan_amount_manwon: NonNegativeManwon
    status: ScenarioStatus
    first_shortfall: FirstShortfall | None
    stage_margins: list[StageMargin]
    worst_margin_manwon: SignedManwon | None
    balance_margin_manwon: SignedManwon | None
    recovery_months_at_first_shortfall: NonNegativeManwon | None


class RatioThreshold(StrictModel):
    status: ThresholdStatus
    minimum_ratio_bps: BasisPoints | None
    minimum_loan_amount_manwon: NonNegativeManwon | None
    resolution_bps: Annotated[int, Field(ge=1)]
    limiting_shortfall: FirstShortfall | None


class RatioMargin(StrictModel):
    status: MarginStatus
    required_ratio_bps: BasisPoints | None
    document_cap_ratio_bps: BasisPoints | None
    margin_bps: int | None
    certainty: FundingCertainty
    message: str


class DocumentCapComparison(StrictModel):
    arrangement_status: str
    document_cap_ratio_bps: BasisPoints | None
    personal_approval_confirmed: bool
    interim_continuity: RatioMargin


class RouteStressCase(StrictModel):
    route_id: str
    product_code: str
    product_name: str
    rule_version: str
    assumption_set_id: str
    route_status: RouteStatus
    limit_case: RouteLimitCase | None
    balance_financing_manwon: NonNegativeManwon | None
    full_completion_threshold: RatioThreshold
    scenarios: list[FundingScenario]
    holds: list[StressHold]


class FundingStressResponse(StrictModel):
    advisory: bool
    calculator_version: str
    calculation_scope: str
    input_digest: str
    analysis_fingerprint: AnalysisFingerprint
    as_of_date: date
    savings_policy: str
    monthly_saving_manwon: NonNegativeManwon | None
    maximum_interim_ratio_bps: BasisPoints
    interim_continuity_threshold: RatioThreshold
    document_cap_comparison: DocumentCapComparison
    route_cases: list[RouteStressCase]
    holds: list[StressHold]
    assumptions: list[str]
