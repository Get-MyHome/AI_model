from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator

Ratio = Annotated[float, Field(ge=0.0, le=1.0)]
NonNegativeManwon = Annotated[int, Field(ge=0)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class AnalysisStatus(StrEnum):
    READY = "READY"
    PARTIAL = "PARTIAL"
    HOLD = "HOLD"


class ReviewStatus(StrEnum):
    AUTO_EXTRACTED = "AUTO_EXTRACTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REVIEWED = "REVIEWED"


class PaymentBasis(StrEnum):
    RATIO = "RATIO"
    FIXED_AMOUNT = "FIXED_AMOUNT"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class LoanArrangementStatus(StrEnum):
    NOT_STATED = "NOT_STATED"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    PLANNED = "PLANNED"
    UNDER_DISCUSSION = "UNDER_DISCUSSION"
    BANK_SELECTED = "BANK_SELECTED"


class InterestType(StrEnum):
    INTEREST_FREE = "INTEREST_FREE"
    DEFERRED_INTEREST = "DEFERRED_INTEREST"
    BORROWER_PAYS = "BORROWER_PAYS"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class LoanSettlementRequirement(StrEnum):
    REPAY_OR_CONVERT_TO_MORTGAGE = "REPAY_OR_CONVERT_TO_MORTGAGE"
    REPAY_REQUIRED = "REPAY_REQUIRED"
    CONVERT_TO_MORTGAGE_REQUIRED = "CONVERT_TO_MORTGAGE_REQUIRED"
    CONTINUE_EXPLICITLY_ALLOWED = "CONTINUE_EXPLICITLY_ALLOWED"
    NOT_STATED = "NOT_STATED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class GuaranteeProvider(StrEnum):
    HF = "HF"
    HUG = "HUG"


class PaymentStage(StrEnum):
    CONTRACT = "CONTRACT"
    INTERIM = "INTERIM"
    BALANCE = "BALANCE"
    MOVE_IN = "MOVE_IN"
    UNKNOWN = "UNKNOWN"


class AdditionalCostType(StrEnum):
    BALCONY_EXTENSION = "BALCONY_EXTENSION"
    PAID_OPTION = "PAID_OPTION"
    SYSTEM_AIR_CONDITIONER = "SYSTEM_AIR_CONDITIONER"
    INTERIM_INTEREST = "INTERIM_INTEREST"
    OTHER = "OTHER"


class HoldReasonCode(StrEnum):
    DOWN_PAYMENT_MISSING = "DOWN_PAYMENT_MISSING"
    INTERIM_PAYMENT_MISSING = "INTERIM_PAYMENT_MISSING"
    BALANCE_PAYMENT_MISSING = "BALANCE_PAYMENT_MISSING"
    INTERIM_SCHEDULE_MISSING = "INTERIM_SCHEDULE_MISSING"
    INTERIM_LOAN_RATIO_MISSING = "INTERIM_LOAN_RATIO_MISSING"
    BANK_NOT_DISCLOSED = "BANK_NOT_DISCLOSED"
    LOAN_ARRANGEMENT_ONLY = "LOAN_ARRANGEMENT_ONLY"
    SELF_FUNDING_SCHEDULE_UNKNOWN = "SELF_FUNDING_SCHEDULE_UNKNOWN"
    # Kept for backward-compatible parsing of v0.2 artifacts. New responses use
    # SELF_FUNDING_SCHEDULE_UNKNOWN; the known self-funding amount itself is a
    # risk factor, not an uncertainty.
    SELF_FUNDING_REQUIRED = "SELF_FUNDING_REQUIRED"
    GUARANTEE_PROVIDER_UNKNOWN = "GUARANTEE_PROVIDER_UNKNOWN"
    INTEREST_TERMS_UNKNOWN = "INTEREST_TERMS_UNKNOWN"
    INDIVIDUAL_REVIEW_REQUIRED = "INDIVIDUAL_REVIEW_REQUIRED"
    BALANCE_CONVERSION_UNCERTAIN = "BALANCE_CONVERSION_UNCERTAIN"
    TERMS_DIFFER_BY_HOUSING_TYPE = "TERMS_DIFFER_BY_HOUSING_TYPE"
    UNIT_SELECTION_REQUIRED = "UNIT_SELECTION_REQUIRED"
    ADDITIONAL_COST_UNKNOWN = "ADDITIONAL_COST_UNKNOWN"
    ADDITIONAL_COST_SCOPE_LIMITED = "ADDITIONAL_COST_SCOPE_LIMITED"
    TABLE_REVIEW_REQUIRED = "TABLE_REVIEW_REQUIRED"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"
    EVIDENCE_MISSING = "EVIDENCE_MISSING"
    PDF_TEXT_UNAVAILABLE = "PDF_TEXT_UNAVAILABLE"


class HoldKind(StrEnum):
    DOCUMENT_UNCERTAINTY = "DOCUMENT_UNCERTAINTY"
    PERSONAL_REVIEW = "PERSONAL_REVIEW"


class ExceptionFlag(StrEnum):
    LOAN_MEDIATION_NOT_GUARANTEED = "LOAN_MEDIATION_NOT_GUARANTEED"
    SELF_FUNDING_REQUIRED = "SELF_FUNDING_REQUIRED"
    TERMS_DIFFER_BY_TYPE = "TERMS_DIFFER_BY_TYPE"
    INDIVIDUAL_REVIEW_NOTED = "INDIVIDUAL_REVIEW_NOTED"
    FIXED_AMOUNT_PAYMENT = "FIXED_AMOUNT_PAYMENT"
    ADDITIONAL_COST_SCOPE_LIMITED = "ADDITIONAL_COST_SCOPE_LIMITED"


class RiskClauseCode(StrEnum):
    LOAN_MEDIATION_NOT_GUARANTEED = "LOAN_MEDIATION_NOT_GUARANTEED"
    INDIVIDUAL_REVIEW_REQUIRED = "INDIVIDUAL_REVIEW_REQUIRED"
    SELF_FUNDING_REQUIRED = "SELF_FUNDING_REQUIRED"
    INTEREST_PAYMENT_RISK = "INTEREST_PAYMENT_RISK"
    LOAN_NOT_AVAILABLE = "LOAN_NOT_AVAILABLE"
    TERMS_DIFFER_BY_HOUSING_TYPE = "TERMS_DIFFER_BY_HOUSING_TYPE"


class IssueSeverity(StrEnum):
    WARNING = "WARNING"
    ERROR = "ERROR"


class ValueOrigin(StrEnum):
    EXTRACTED = "EXTRACTED"
    DERIVED = "DERIVED"


class AnalyzeRequest(StrictModel):
    complex_id: Annotated[str, Field(min_length=1, max_length=100)]
    pdf_url: AnyHttpUrl
    unit_type_id: Annotated[str | None, Field(max_length=100)] = None
    unit_type_name: Annotated[str | None, Field(max_length=100)] = None
    sale_price_manwon: NonNegativeManwon | None = None

    @model_validator(mode="after")
    def target_fields_are_complete(self) -> AnalyzeRequest:
        target = (self.unit_type_id, self.unit_type_name, self.sale_price_manwon)
        if any(value is not None for value in target) and not all(
            value is not None for value in target
        ):
            raise ValueError(
                "주택형 분석 시 unit_type_id, unit_type_name, "
                "sale_price_manwon을 모두 보내야 합니다."
            )
        return self


class Evidence(StrictModel):
    field: Annotated[str, Field(min_length=1, max_length=200)]
    page: Annotated[int, Field(ge=1)]
    raw_text: Annotated[str, Field(min_length=1, max_length=1000)]


class Installment(StrictModel):
    number: Annotated[int, Field(ge=1)]
    ratio: Ratio | None
    amount_manwon: NonNegativeManwon | None
    due_date: date | None
    due_text: Annotated[str | None, Field(max_length=200)]


class AdditionalCostPayment(StrictModel):
    number: Annotated[int, Field(ge=1)]
    stage: PaymentStage
    amount_manwon: NonNegativeManwon | None
    due_date: date | None
    due_text: Annotated[str | None, Field(max_length=200)]


class PaymentComponent(StrictModel):
    total_ratio: Ratio | None
    total_amount_manwon: NonNegativeManwon | None
    basis: PaymentBasis
    installments: Annotated[list[Installment], Field(max_length=20)]
    due_date: date | None
    due_month: Annotated[str | None, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
    due_text: Annotated[str | None, Field(max_length=200)]


class PaymentSchedule(StrictModel):
    down_payment: PaymentComponent
    interim_payment: PaymentComponent
    balance_payment: PaymentComponent


class InterimLoan(StrictModel):
    arrangement_status: LoanArrangementStatus
    arranged_ratio: Ratio | None
    arranged_amount_manwon: NonNegativeManwon | None
    self_funding_ratio: Ratio | None
    self_funding_amount_manwon: NonNegativeManwon | None
    self_funding_origin: ValueOrigin | None
    bank_names: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=100)]], Field(max_length=10)
    ]
    guarantee_provider: GuaranteeProvider | None
    interest_type: InterestType
    interest_note: Annotated[str | None, Field(max_length=500)]
    prepay_requirement_ratio: Ratio | None
    settlement_requirement: LoanSettlementRequirement = LoanSettlementRequirement.NOT_STATED
    settlement_deadline_text: Annotated[str | None, Field(max_length=300)] = None
    extension_contingency_disclosed: bool | None = None


class RiskClause(StrictModel):
    code: RiskClauseCode
    impact_stage: PaymentStage
    origin: ValueOrigin
    message: Annotated[str, Field(min_length=1, max_length=300)]
    next_action: Annotated[str, Field(min_length=1, max_length=300)]
    evidence: Annotated[list[Evidence], Field(min_length=1, max_length=3)]


class AdditionalCost(StrictModel):
    type: AdditionalCostType
    name: Annotated[str, Field(min_length=1, max_length=100)]
    total_amount_manwon: NonNegativeManwon | None
    required: bool | None
    included_in_sale_price: bool | None
    applicable_unit_type: Annotated[str | None, Field(max_length=100)]
    payments: Annotated[list[AdditionalCostPayment], Field(max_length=20)]
    note: Annotated[str | None, Field(max_length=500)]


class ExtractionDraft(StrictModel):
    payment_schedule: PaymentSchedule
    interim_loan: InterimLoan
    additional_costs: Annotated[list[AdditionalCost], Field(max_length=20)]
    risk_clauses: Annotated[list[RiskClause], Field(default_factory=list, max_length=20)]
    evidence: Annotated[list[Evidence], Field(max_length=100)]
    exception_flags: Annotated[list[ExceptionFlag], Field(max_length=10)]


class TargetUnit(StrictModel):
    unit_type_id: str | None
    unit_type_name: str | None
    sale_price_manwon: NonNegativeManwon | None


class Hold(StrictModel):
    reason_code: HoldReasonCode
    kind: HoldKind
    blocking: bool
    message: str
    next_action: str


class ValidationIssue(StrictModel):
    severity: IssueSeverity
    code: str
    field: str | None
    message: str


class ValidationReport(StrictModel):
    passed: bool
    issues: list[ValidationIssue]
    derived_fields: list[str]


class AnalysisMeta(StrictModel):
    schema_version: str
    extractor_version: str
    prompt_version: str
    provider: str
    model: str | None
    source_sha256: str
    source_page_count: int
    candidate_pages: list[int]
    analyzed_at: datetime


class AnalysisResponse(StrictModel):
    complex_id: str
    analysis_status: AnalysisStatus
    review_status: ReviewStatus
    reviewer: str | None
    reviewed_at: datetime | None
    target_unit: TargetUnit
    payment_schedule: PaymentSchedule
    interim_loan: InterimLoan
    additional_costs: list[AdditionalCost]
    risk_clauses: list[RiskClause] = Field(default_factory=list)
    analysis_summary: str
    holds: list[Hold]
    exception_flags: list[ExceptionFlag]
    evidence: list[Evidence]
    validation: ValidationReport
    meta: AnalysisMeta


class HealthResponse(StrictModel):
    status: str
    version: str
    source_fingerprint_algorithm: str
    source_fingerprint_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ReadinessResponse(StrictModel):
    ready: bool
    provider: str
    checks: dict[str, bool]


class ErrorDetail(StrictModel):
    code: str
    message: str
    retryable: bool


class ErrorResponse(StrictModel):
    error: ErrorDetail
