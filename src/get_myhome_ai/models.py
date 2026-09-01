from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

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
    SELF_FUNDING_REQUIRED = "SELF_FUNDING_REQUIRED"
    GUARANTEE_PROVIDER_UNKNOWN = "GUARANTEE_PROVIDER_UNKNOWN"
    INTEREST_TERMS_UNKNOWN = "INTEREST_TERMS_UNKNOWN"
    INDIVIDUAL_REVIEW_REQUIRED = "INDIVIDUAL_REVIEW_REQUIRED"
    BALANCE_CONVERSION_UNCERTAIN = "BALANCE_CONVERSION_UNCERTAIN"
    TERMS_DIFFER_BY_HOUSING_TYPE = "TERMS_DIFFER_BY_HOUSING_TYPE"
    UNIT_SELECTION_REQUIRED = "UNIT_SELECTION_REQUIRED"
    ADDITIONAL_COST_UNKNOWN = "ADDITIONAL_COST_UNKNOWN"
    TABLE_REVIEW_REQUIRED = "TABLE_REVIEW_REQUIRED"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"
    EVIDENCE_MISSING = "EVIDENCE_MISSING"
    PDF_TEXT_UNAVAILABLE = "PDF_TEXT_UNAVAILABLE"


class ExceptionFlag(StrEnum):
    LOAN_MEDIATION_NOT_GUARANTEED = "LOAN_MEDIATION_NOT_GUARANTEED"
    SELF_FUNDING_REQUIRED = "SELF_FUNDING_REQUIRED"
    TERMS_DIFFER_BY_TYPE = "TERMS_DIFFER_BY_TYPE"
    INDIVIDUAL_REVIEW_NOTED = "INDIVIDUAL_REVIEW_NOTED"
    FIXED_AMOUNT_PAYMENT = "FIXED_AMOUNT_PAYMENT"


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
    installments: list[Installment]
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
    bank_names: list[Annotated[str, Field(min_length=1, max_length=100)]]
    guarantee_provider: GuaranteeProvider | None
    interest_type: InterestType
    interest_note: Annotated[str | None, Field(max_length=500)]
    prepay_requirement_ratio: Ratio | None


class AdditionalCost(StrictModel):
    type: AdditionalCostType
    name: Annotated[str, Field(min_length=1, max_length=100)]
    total_amount_manwon: NonNegativeManwon | None
    required: bool | None
    included_in_sale_price: bool | None
    applicable_unit_type: Annotated[str | None, Field(max_length=100)]
    payments: list[AdditionalCostPayment]
    note: Annotated[str | None, Field(max_length=500)]


class ExtractionDraft(StrictModel):
    payment_schedule: PaymentSchedule
    interim_loan: InterimLoan
    additional_costs: list[AdditionalCost]
    evidence: list[Evidence]
    exception_flags: list[ExceptionFlag]


class TargetUnit(StrictModel):
    unit_type_id: str | None
    unit_type_name: str | None
    sale_price_manwon: NonNegativeManwon | None


class Hold(StrictModel):
    reason_code: HoldReasonCode
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
    analysis_summary: str
    holds: list[Hold]
    exception_flags: list[ExceptionFlag]
    evidence: list[Evidence]
    validation: ValidationReport
    meta: AnalysisMeta


class HealthResponse(StrictModel):
    status: str
    version: str


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
