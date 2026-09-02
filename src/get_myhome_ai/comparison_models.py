from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from get_myhome_ai.models import StrictModel, ValueOrigin


class ComparisonStatus(StrEnum):
    READY = "READY"
    PARTIAL = "PARTIAL"
    SAME_SOURCE = "SAME_SOURCE"
    REVIEW_CONFLICT = "REVIEW_CONFLICT"


class ComparisonVerdict(StrEnum):
    CONFIRMED_DIFFERENCE = "CONFIRMED_DIFFERENCE"
    NO_CONFIRMED_DIFFERENCE = "NO_CONFIRMED_DIFFERENCE"
    INDETERMINATE = "INDETERMINATE"


class FieldComparisonStatus(StrEnum):
    SAME = "SAME"
    DIFFERENT = "DIFFERENT"
    BASELINE_ONLY = "BASELINE_ONLY"
    COUNTERPART_ONLY = "COUNTERPART_ONLY"
    UNAVAILABLE_BOTH = "UNAVAILABLE_BOTH"
    PROVENANCE_UNRESOLVED = "PROVENANCE_UNRESOLVED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class DocumentRef(StrictModel):
    complex_id: str
    unit_type_id: str | None
    unit_type_name: str | None
    sale_price_manwon: int | None
    source_sha256: str
    schema_version: str
    extractor_version: str


class FieldProvenance(StrictModel):
    source_sha256: str
    requested_field: str
    supporting_field: str
    origin: ValueOrigin
    page: int
    raw_text: str
    derived_from: list[str] = Field(default_factory=list)


class FieldComparison(StrictModel):
    field: str
    status: FieldComparisonStatus
    baseline_value: Any = None
    counterpart_value: Any = None
    baseline_provenance: list[FieldProvenance] = Field(default_factory=list)
    counterpart_provenance: list[FieldProvenance] = Field(default_factory=list)


class ComparisonCounts(StrictModel):
    same: int
    different: int
    unresolved: int


class ComparisonResponse(StrictModel):
    schema_version: Literal["comparison-v0.1"] = "comparison-v0.1"
    comparator_version: Literal["0.1.0"] = "0.1.0"
    validation_status: Literal["NOT_VALIDATED_ON_BANK_GUIDANCE"] = (
        "NOT_VALIDATED_ON_BANK_GUIDANCE"
    )
    comparison_status: ComparisonStatus
    verdict: ComparisonVerdict
    baseline: DocumentRef
    counterpart: DocumentRef
    comparisons: list[FieldComparison]
    counts: ComparisonCounts
