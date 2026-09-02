from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from get_myhome_ai.comparison import ComparisonInputError, compare_reviewed_analyses
from get_myhome_ai.comparison_models import (
    ComparisonStatus,
    ComparisonVerdict,
    FieldComparisonStatus,
)
from get_myhome_ai.models import ReviewStatus
from get_myhome_ai.review import load_result


def _reviewed():
    result = load_result(Path("artifacts/qwen3-8b-12k/2026000372.json"))
    result.review_status = ReviewStatus.REVIEWED
    result.reviewer = "TEST_REVIEWER"
    result.reviewed_at = datetime(2026, 9, 2, tzinfo=UTC)
    return result


def _field(result, path: str):
    return next(item for item in result.comparisons if item.field == path)


def test_comparison_rejects_unreviewed_input() -> None:
    baseline = _reviewed()
    counterpart = deepcopy(baseline)
    counterpart.review_status = ReviewStatus.AUTO_EXTRACTED

    with pytest.raises(ComparisonInputError, match="검수 완료") as exc_info:
        compare_reviewed_analyses(baseline, counterpart)

    assert exc_info.value.code == "NOT_REVIEWED"


def test_same_reviewed_source_is_deterministic() -> None:
    baseline = _reviewed()

    first = compare_reviewed_analyses(baseline, deepcopy(baseline))
    second = compare_reviewed_analyses(baseline, deepcopy(baseline))

    assert first.comparison_status == ComparisonStatus.SAME_SOURCE
    assert first.verdict == ComparisonVerdict.NO_CONFIRMED_DIFFERENCE
    assert first.model_dump_json() == second.model_dump_json()
    arranged = _field(first, "/interim_loan/arranged_ratio")
    assert arranged.status == FieldComparisonStatus.SAME
    assert arranged.baseline_provenance


def test_different_reviewed_source_reports_only_grounded_change() -> None:
    baseline = _reviewed()
    counterpart = deepcopy(baseline)
    counterpart.meta.source_sha256 = "b" * 64
    counterpart.interim_loan.arranged_ratio = 0.5

    result = compare_reviewed_analyses(baseline, counterpart)

    assert result.verdict == ComparisonVerdict.CONFIRMED_DIFFERENCE
    assert _field(result, "/interim_loan/arranged_ratio").status == (
        FieldComparisonStatus.DIFFERENT
    )
    assert result.validation_status == "NOT_VALIDATED_ON_BANK_GUIDANCE"


def test_unknown_counterpart_is_not_reported_as_confirmed_change() -> None:
    baseline = _reviewed()
    counterpart = deepcopy(baseline)
    counterpart.meta.source_sha256 = "c" * 64
    counterpart.interim_loan.arranged_ratio = None

    result = compare_reviewed_analyses(baseline, counterpart)

    assert _field(result, "/interim_loan/arranged_ratio").status == (
        FieldComparisonStatus.BASELINE_ONLY
    )


def test_same_source_with_conflicting_review_is_not_document_change() -> None:
    baseline = _reviewed()
    counterpart = deepcopy(baseline)
    counterpart.interim_loan.arranged_ratio = 0.5

    result = compare_reviewed_analyses(baseline, counterpart)

    assert result.comparison_status == ComparisonStatus.REVIEW_CONFLICT
    assert result.verdict == ComparisonVerdict.INDETERMINATE
