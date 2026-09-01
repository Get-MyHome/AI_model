from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from get_myhome_ai.models import AnalysisResponse, ExtractionDraft


@dataclass(frozen=True)
class CaseEvaluation:
    complex_id: str
    exact_match: bool
    matched_fields: int
    total_fields: int
    validation_passed: bool
    evidence_error_count: int


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        flattened: dict[str, Any] = {}
        for key, item in value.items():
            flattened.update(_flatten(item, f"{prefix}/{key}"))
        return flattened
    if isinstance(value, list):
        flattened = {}
        for index, item in enumerate(value):
            flattened.update(_flatten(item, f"{prefix}/{index}"))
        if not value:
            flattened[prefix] = []
        return flattened
    return {prefix: value}


def evaluate_case(
    result: AnalysisResponse,
    expected: ExtractionDraft,
) -> CaseEvaluation:
    actual_draft = ExtractionDraft(
        payment_schedule=result.payment_schedule,
        interim_loan=result.interim_loan,
        additional_costs=result.additional_costs,
        evidence=result.evidence,
        exception_flags=result.exception_flags,
    )
    actual = _flatten(actual_draft.model_dump(mode="json"))
    normalized_expected = expected.model_copy(deep=True)
    # Normalization may intentionally derive values that are absent from a human fixture.
    from get_myhome_ai.normalization import normalize_draft

    normalized_expected, _ = normalize_draft(normalized_expected)
    wanted = _flatten(normalized_expected.model_dump(mode="json"))
    keys = sorted(set(actual) | set(wanted))
    matches = sum(actual.get(key) == wanted.get(key) for key in keys)
    evidence_errors = sum(issue.code.startswith("EVIDENCE_") for issue in result.validation.issues)
    return CaseEvaluation(
        complex_id=result.complex_id,
        exact_match=matches == len(keys),
        matched_fields=matches,
        total_fields=len(keys),
        validation_passed=result.validation.passed,
        evidence_error_count=evidence_errors,
    )


def summarize_evaluations(cases: list[CaseEvaluation], provider_name: str) -> dict[str, Any]:
    total_fields = sum(case.total_fields for case in cases)
    matched_fields = sum(case.matched_fields for case in cases)
    return {
        "provider": provider_name,
        "scope": "golden_set_replay" if provider_name == "fixture" else "model_evaluation",
        "document_count": len(cases),
        "exact_match_documents": sum(case.exact_match for case in cases),
        "field_match_rate": matched_fields / total_fields if total_fields else 0.0,
        "validation_pass_documents": sum(case.validation_passed for case in cases),
        "evidence_error_count": sum(case.evidence_error_count for case in cases),
        "claim_limit": (
            "Fixture replay verifies pipeline, validation, and evidence wiring; "
            "it is not a live-model accuracy measurement."
            if provider_name == "fixture"
            else "Metrics apply only to the labeled documents in this run."
        ),
        "cases": [case.__dict__ for case in cases],
    }
