from __future__ import annotations

import re
import unicodedata
from typing import Any

from get_myhome_ai.comparison_models import (
    ComparisonCounts,
    ComparisonResponse,
    ComparisonStatus,
    ComparisonVerdict,
    DocumentRef,
    FieldComparison,
    FieldComparisonStatus,
    FieldProvenance,
)
from get_myhome_ai.models import AnalysisResponse, Evidence, ReviewStatus, ValueOrigin
from get_myhome_ai.normalization import normalize_unit_type_name

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SUPPORTED_SCHEMA_VERSION = "v0.3"

SCALAR_FIELDS = (
    "/payment_schedule/down_payment/total_ratio",
    "/payment_schedule/down_payment/total_amount_manwon",
    "/payment_schedule/interim_payment/total_ratio",
    "/payment_schedule/interim_payment/total_amount_manwon",
    "/payment_schedule/interim_payment/installments",
    "/payment_schedule/balance_payment/total_ratio",
    "/payment_schedule/balance_payment/total_amount_manwon",
    "/payment_schedule/balance_payment/due_date",
    "/payment_schedule/balance_payment/due_month",
    "/interim_loan/arrangement_status",
    "/interim_loan/arranged_ratio",
    "/interim_loan/arranged_amount_manwon",
    "/interim_loan/self_funding_ratio",
    "/interim_loan/self_funding_amount_manwon",
    "/interim_loan/bank_names",
    "/interim_loan/guarantee_provider",
    "/interim_loan/interest_type",
    "/interim_loan/prepay_requirement_ratio",
    "/interim_loan/settlement_requirement",
)

DERIVED_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "/payment_schedule/balance_payment/total_ratio": (
        "/payment_schedule/down_payment/total_ratio",
        "/payment_schedule/interim_payment/total_ratio",
    ),
    "/interim_loan/self_funding_ratio": (
        "/payment_schedule/interim_payment/total_ratio",
        "/interim_loan/arranged_ratio",
    ),
    "/interim_loan/self_funding_amount_manwon": (
        "/payment_schedule/interim_payment/total_amount_manwon",
        "/interim_loan/arranged_amount_manwon",
    ),
    "/interim_loan/arranged_ratio": ("/interim_loan/arrangement_status",),
    "/interim_loan/arranged_amount_manwon": ("/interim_loan/arrangement_status",),
}


class ComparisonInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _pointer_value(document: dict[str, Any], pointer: str) -> Any:
    current: Any = document
    for token in pointer.removeprefix("/").split("/"):
        if isinstance(current, dict):
            current = current[token]
        elif isinstance(current, list):
            current = current[int(token)]
        else:
            raise KeyError(pointer)
    return current


def _unknown(path: str, value: Any) -> bool:
    if value is None:
        return True
    if path == "/interim_loan/bank_names" and value == []:
        return True
    return isinstance(value, str) and value in {"UNKNOWN", "NOT_STATED"}


def _normalized_banks(value: list[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                re.sub(r"\s+", "", unicodedata.normalize("NFKC", item)).casefold()
                for item in value
            }
        )
    )


def _supporting_evidence(result: AnalysisResponse, path: str) -> Evidence | None:
    exact = next((item for item in result.evidence if item.field == path), None)
    if exact is not None:
        return exact
    allowed_parents = (
        "/payment_schedule/down_payment",
        "/payment_schedule/interim_payment",
        "/payment_schedule/balance_payment",
        "/payment_schedule/interim_payment/installments",
    )
    return next(
        (
            item
            for item in result.evidence
            if item.field in allowed_parents and path.startswith(f"{item.field}/")
        ),
        None,
    )


def _provenance(result: AnalysisResponse, path: str) -> list[FieldProvenance]:
    if path in result.validation.derived_fields:
        dependencies = DERIVED_DEPENDENCIES.get(path)
        if dependencies is None:
            return []
        provenances: list[FieldProvenance] = []
        for dependency in dependencies:
            evidence = _supporting_evidence(result, dependency)
            if evidence is None:
                return []
            provenances.append(
                FieldProvenance(
                    source_sha256=result.meta.source_sha256,
                    requested_field=path,
                    supporting_field=dependency,
                    origin=ValueOrigin.DERIVED,
                    page=evidence.page,
                    raw_text=evidence.raw_text,
                    derived_from=list(dependencies),
                )
            )
        return provenances

    evidence = _supporting_evidence(result, path)
    if evidence is None:
        return []
    return [
        FieldProvenance(
            source_sha256=result.meta.source_sha256,
            requested_field=path,
            supporting_field=evidence.field,
            origin=ValueOrigin.EXTRACTED,
            page=evidence.page,
            raw_text=evidence.raw_text,
        )
    ]


def _validate_input(result: AnalysisResponse, label: str) -> None:
    if result.review_status != ReviewStatus.REVIEWED:
        raise ComparisonInputError(
            "NOT_REVIEWED", f"{label} 분석 결과가 검수 완료 상태가 아닙니다."
        )
    if not result.reviewer or result.reviewed_at is None:
        raise ComparisonInputError(
            "REVIEW_METADATA_MISSING", f"{label} 분석 결과의 검수 메타데이터가 없습니다."
        )
    if not result.validation.passed:
        raise ComparisonInputError(
            "VALIDATION_FAILED", f"{label} 분석 결과가 자동 검증을 통과하지 못했습니다."
        )
    if result.meta.schema_version != SUPPORTED_SCHEMA_VERSION:
        raise ComparisonInputError(
            "UNSUPPORTED_SCHEMA", f"{label} 분석 결과의 스키마를 지원하지 않습니다."
        )
    if not SHA256_PATTERN.fullmatch(result.meta.source_sha256):
        raise ComparisonInputError(
            "INVALID_SOURCE_DIGEST", f"{label} 분석 결과의 원문 식별자가 유효하지 않습니다."
        )


def _document_ref(result: AnalysisResponse) -> DocumentRef:
    return DocumentRef(
        complex_id=result.complex_id,
        unit_type_id=result.target_unit.unit_type_id,
        unit_type_name=result.target_unit.unit_type_name,
        sale_price_manwon=result.target_unit.sale_price_manwon,
        source_sha256=result.meta.source_sha256,
        schema_version=result.meta.schema_version,
        extractor_version=result.meta.extractor_version,
    )


def compare_reviewed_analyses(
    baseline: AnalysisResponse,
    counterpart: AnalysisResponse,
) -> ComparisonResponse:
    """Compare only source-grounded fields from two trusted reviewed artifacts.

    This function is deliberately not exposed as a public endpoint. Bank-guide
    extraction has not yet been validated, so callers must load both inputs
    from the server-controlled reviewed artifact store.
    """

    _validate_input(baseline, "baseline")
    _validate_input(counterpart, "counterpart")
    if baseline.complex_id != counterpart.complex_id:
        raise ComparisonInputError("COMPLEX_MISMATCH", "서로 다른 공고는 비교할 수 없습니다.")
    baseline_target = (
        baseline.target_unit.unit_type_id,
        normalize_unit_type_name(baseline.target_unit.unit_type_name),
        baseline.target_unit.sale_price_manwon,
    )
    counterpart_target = (
        counterpart.target_unit.unit_type_id,
        normalize_unit_type_name(counterpart.target_unit.unit_type_name),
        counterpart.target_unit.sale_price_manwon,
    )
    if baseline_target != counterpart_target:
        raise ComparisonInputError(
            "TARGET_MISMATCH", "서로 다른 주택형·분양가는 비교할 수 없습니다."
        )

    baseline_document = baseline.model_dump(mode="json")
    counterpart_document = counterpart.model_dump(mode="json")
    comparisons: list[FieldComparison] = []
    for path in SCALAR_FIELDS:
        baseline_value = _pointer_value(baseline_document, path)
        counterpart_value = _pointer_value(counterpart_document, path)
        baseline_unknown = _unknown(path, baseline_value)
        counterpart_unknown = _unknown(path, counterpart_value)
        baseline_provenance = [] if baseline_unknown else _provenance(baseline, path)
        counterpart_provenance = [] if counterpart_unknown else _provenance(counterpart, path)

        if baseline_unknown and counterpart_unknown:
            status = FieldComparisonStatus.UNAVAILABLE_BOTH
        elif counterpart_unknown:
            status = FieldComparisonStatus.BASELINE_ONLY
        elif baseline_unknown:
            status = FieldComparisonStatus.COUNTERPART_ONLY
        elif not baseline_provenance or not counterpart_provenance:
            status = FieldComparisonStatus.PROVENANCE_UNRESOLVED
        elif path == "/interim_loan/bank_names":
            status = (
                FieldComparisonStatus.SAME
                if _normalized_banks(baseline_value) == _normalized_banks(counterpart_value)
                else FieldComparisonStatus.REVIEW_REQUIRED
            )
        else:
            status = (
                FieldComparisonStatus.SAME
                if baseline_value == counterpart_value
                else FieldComparisonStatus.DIFFERENT
            )
        comparisons.append(
            FieldComparison(
                field=path,
                status=status,
                baseline_value=baseline_value,
                counterpart_value=counterpart_value,
                baseline_provenance=baseline_provenance,
                counterpart_provenance=counterpart_provenance,
            )
        )

    same_count = sum(item.status == FieldComparisonStatus.SAME for item in comparisons)
    different_count = sum(item.status == FieldComparisonStatus.DIFFERENT for item in comparisons)
    unresolved_count = len(comparisons) - same_count - different_count
    same_source = baseline.meta.source_sha256 == counterpart.meta.source_sha256
    if same_source and different_count:
        comparison_status = ComparisonStatus.REVIEW_CONFLICT
        verdict = ComparisonVerdict.INDETERMINATE
    elif same_source:
        comparison_status = ComparisonStatus.SAME_SOURCE
        verdict = (
            ComparisonVerdict.NO_CONFIRMED_DIFFERENCE
            if same_count
            else ComparisonVerdict.INDETERMINATE
        )
    else:
        comparison_status = (
            ComparisonStatus.PARTIAL if unresolved_count else ComparisonStatus.READY
        )
        if different_count:
            verdict = ComparisonVerdict.CONFIRMED_DIFFERENCE
        elif same_count:
            verdict = ComparisonVerdict.NO_CONFIRMED_DIFFERENCE
        else:
            verdict = ComparisonVerdict.INDETERMINATE

    return ComparisonResponse(
        comparison_status=comparison_status,
        verdict=verdict,
        baseline=_document_ref(baseline),
        counterpart=_document_ref(counterpart),
        comparisons=comparisons,
        counts=ComparisonCounts(
            same=same_count,
            different=different_count,
            unresolved=unresolved_count,
        ),
    )
