from __future__ import annotations

import pytest

from get_myhome_ai.holds import derive_holds
from get_myhome_ai.models import Evidence, HoldReasonCode, ValidationReport
from get_myhome_ai.normalization import normalize_draft

_ALLOCATION_QUOTE = """
본 아파트의 중도금 대출은 이자후불제이며 1~4회차 대출을 받았을 경우
5~6회차 중도금은 계약자가 납부하여야 합니다.
(단, 정부정책 및 금융권 사정 등의 사유로 다소 변경할 수 있음)
""".strip()


def _codes(draft, *, passed: bool) -> set[HoldReasonCode]:
    report = ValidationReport(passed=passed, issues=[], derived_fields=[])
    return {hold.reason_code for hold in derive_holds(draft, report, unit_type_name="59A")}


def test_exact_validated_installment_allocation_suppresses_only_schedule_hold(
    golden_cases,
) -> None:
    draft, _ = normalize_draft(golden_cases["2026000358"].expected)
    draft.evidence.append(
        Evidence(
            field="/interim_loan/self_funding_origin",
            page=38,
            raw_text=_ALLOCATION_QUOTE,
        )
    )

    assert HoldReasonCode.SELF_FUNDING_SCHEDULE_UNKNOWN not in _codes(
        draft,
        passed=True,
    )


@pytest.mark.parametrize(
    ("quote", "passed"),
    [
        (_ALLOCATION_QUOTE.replace("5~6회차", "후반 회차"), True),
        (_ALLOCATION_QUOTE, False),
    ],
)
def test_partial_or_unvalidated_allocation_keeps_schedule_hold(
    golden_cases,
    quote: str,
    passed: bool,
) -> None:
    draft, _ = normalize_draft(golden_cases["2026000358"].expected)
    draft.evidence.append(
        Evidence(
            field="/interim_loan/self_funding_origin",
            page=38,
            raw_text=quote,
        )
    )

    assert HoldReasonCode.SELF_FUNDING_SCHEDULE_UNKNOWN in _codes(
        draft,
        passed=passed,
    )
