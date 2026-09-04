from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

import get_myhome_ai.funding_stress as funding_stress_module
from get_myhome_ai.expanded_audited_repairs_b import (
    _POLICIES,
    EXPANDED_AUDITED_TARGET_COUNT_B,
    ExpandedAuditedRepairBError,
    repair_expanded_audited_candidate_b,
)
from get_myhome_ai.funding_stress import calculate_funding_stress
from get_myhome_ai.models import (
    AdditionalCostType,
    AnalysisResponse,
    AnalyzeRequest,
    HoldReasonCode,
    InterestType,
    LoanArrangementStatus,
    LoanSettlementRequirement,
    ReviewStatus,
)
from get_myhome_ai.pdf_text import DownloadedPdf, PdfPage, extract_pdf_pages, load_pdf_from_path
from get_myhome_ai.pipeline import AnalysisPipeline
from get_myhome_ai.providers.fixture import FixtureExtractor
from get_myhome_ai.settings import Settings
from get_myhome_ai.stress_models import (
    CashSnapshotTiming,
    FundingStressRequest,
    LoanRouteSnapshot,
    RouteStatus,
    ScenarioStatus,
    StressHoldCode,
    ThresholdStatus,
)

CASES = [
    (complex_id, unit_type_id)
    for complex_id, policy in _POLICIES.items()
    for unit_type_id in policy.targets
]


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


@pytest.fixture(scope="module")
def source_pages(golden_pdf_dir: Path) -> dict[str, list[PdfPage]]:
    settings = Settings(ai_provider="fixture")
    loaded: dict[str, list[PdfPage]] = {}
    for complex_id, policy in _POLICIES.items():
        path = next(golden_pdf_dir.glob(f"{complex_id}_*.pdf"))
        document = load_pdf_from_path(str(path), settings)
        pages = extract_pdf_pages(document.content, settings)
        assert document.sha256 == policy.source_sha256
        assert len(pages) == policy.source_page_count
        loaded[complex_id] = pages
    return loaded


@pytest.fixture(scope="module")
def seed_result(golden_cases) -> AnalysisResponse:
    case = golden_cases["2026000372"]
    content = b"%PDF-expanded-audit-b-seed"

    async def loader(_url, _settings):
        return DownloadedPdf(content=content, sha256=hashlib.sha256(content).hexdigest())

    pipeline = AnalysisPipeline(
        settings=Settings(ai_provider="fixture"),
        provider=FixtureExtractor({case.complex_id: case.expected}),
        url_loader=loader,
        page_extractor=lambda _content, _settings: [
            PdfPage(number=number, text="seed") for number in range(1, 53)
        ],
    )
    return asyncio.run(
        pipeline.analyze_url(
            AnalyzeRequest(
                complex_id=case.complex_id,
                pdf_url="https://example.com/announcement.pdf",
                unit_type_id="01",
                unit_type_name="059.9883A",
                sale_price_manwon=case.sale_price_manwon,
            )
        )
    )


def _candidate(seed: AnalysisResponse, complex_id: str, unit_type_id: str) -> AnalysisResponse:
    policy = _POLICIES[complex_id]
    target = policy.targets[unit_type_id]
    result = seed.model_copy(deep=True)
    result.complex_id = complex_id
    result.target_unit.unit_type_id = unit_type_id
    result.target_unit.unit_type_name = target.unit_name
    result.target_unit.sale_price_manwon = target.sale_price_manwon
    result.meta.source_sha256 = policy.source_sha256
    result.meta.source_page_count = policy.source_page_count
    result.review_status = ReviewStatus.AUTO_EXTRACTED
    result.reviewer = None
    result.reviewed_at = None
    return result


def _repair(
    seed: AnalysisResponse,
    source_pages: dict[str, list[PdfPage]],
    complex_id: str,
    unit_type_id: str,
) -> AnalysisResponse:
    return repair_expanded_audited_candidate_b(
        _candidate(seed, complex_id, unit_type_id),
        pages=source_pages[complex_id],
    )


@pytest.mark.parametrize(("complex_id", "unit_type_id"), CASES)
def test_all_39_tuples_are_source_locked_without_granting_review(
    seed_result: AnalysisResponse,
    source_pages: dict[str, list[PdfPage]],
    complex_id: str,
    unit_type_id: str,
) -> None:
    candidate = _candidate(seed_result, complex_id, unit_type_id)
    policy = _POLICIES[complex_id]
    target = policy.targets[unit_type_id]

    corrected = repair_expanded_audited_candidate_b(
        candidate,
        pages=source_pages[complex_id],
    )

    assert corrected is not candidate
    assert corrected.review_status in {ReviewStatus.AUTO_EXTRACTED, ReviewStatus.NEEDS_REVIEW}
    assert corrected.review_status != ReviewStatus.REVIEWED
    assert corrected.reviewer is None
    assert corrected.reviewed_at is None
    assert corrected.validation.passed is True
    assert len(corrected.additional_costs) == 1
    assert corrected.additional_costs[0].type == AdditionalCostType.BALCONY_EXTENSION
    assert corrected.additional_costs[0].total_amount_manwon == (
        target.balcony_total_won // 10_000
        if target.balcony_total_won is not None and target.balcony_total_won % 10_000 == 0
        else None
    )

    expected_parts = (
        (corrected.payment_schedule.down_payment, target.payment.down_won),
        (corrected.payment_schedule.interim_payment, target.payment.interim_won),
        (corrected.payment_schedule.balance_payment, (target.payment.balance_won,)),
    )
    for component, exact_values in expected_parts:
        assert [item.amount_manwon for item in component.installments] == [
            value // 10_000 if value % 10_000 == 0 else None for value in exact_values
        ]
        total = sum(exact_values)
        assert component.total_amount_manwon == (total // 10_000 if total % 10_000 == 0 else None)
        assert component.total_ratio == pytest.approx(total / (target.sale_price_manwon * 10_000))

    pages_by_number = {page.number: page for page in source_pages[complex_id]}
    for evidence in corrected.evidence:
        assert _normalized(evidence.raw_text) in _normalized(pages_by_number[evidence.page].text)
    for clause in corrected.risk_clauses:
        for evidence in clause.evidence:
            assert _normalized(evidence.raw_text) in _normalized(
                pages_by_number[evidence.page].text
            )


def test_target_count_and_fail_closed_boundaries(
    seed_result: AnalysisResponse,
    source_pages: dict[str, list[PdfPage]],
) -> None:
    assert EXPANDED_AUDITED_TARGET_COUNT_B == 39

    candidate = _candidate(seed_result, "2026000367", "01")
    candidate.meta.source_sha256 = "0" * 64
    with pytest.raises(ExpandedAuditedRepairBError, match="source lock"):
        repair_expanded_audited_candidate_b(candidate, pages=source_pages["2026000367"])

    candidate = _candidate(seed_result, "2026000367", "01")
    candidate.target_unit.sale_price_manwon += 1
    with pytest.raises(ExpandedAuditedRepairBError, match="exact unit"):
        repair_expanded_audited_candidate_b(candidate, pages=source_pages["2026000367"])

    candidate = _candidate(seed_result, "2026000367", "01")
    candidate.review_status = ReviewStatus.REVIEWED
    candidate.reviewer = "somebody"
    candidate.reviewed_at = datetime.now(UTC)
    with pytest.raises(ExpandedAuditedRepairBError, match="REVIEWED"):
        repair_expanded_audited_candidate_b(candidate, pages=source_pages["2026000367"])

    unrelated = seed_result.model_copy(deep=True)
    assert repair_expanded_audited_candidate_b(unrelated, pages=[]) is unrelated


def test_tampered_exact_payment_row_is_rejected(
    seed_result: AnalysisResponse,
    source_pages: dict[str, list[PdfPage]],
) -> None:
    candidate = _candidate(seed_result, "2026000374", "01")
    pages = list(source_pages["2026000374"])
    source = pages[7]
    pages[7] = PdfPage(
        number=source.number,
        text=source.text.replace("124,000,000", "123,000,000"),
    )
    with pytest.raises(ExpandedAuditedRepairBError, match="exact unit/금액 행"):
        repair_expanded_audited_candidate_b(candidate, pages=pages)


def test_optional_costs_are_canonical_and_never_rounded(
    seed_result: AnalysisResponse,
    source_pages: dict[str, list[PdfPage]],
) -> None:
    ambiguous = _repair(seed_result, source_pages, "2026000367", "01")
    assert ambiguous.additional_costs[0].total_amount_manwon is None
    assert ambiguous.additional_costs[0].payments == []
    assert "층 미지정" in (ambiguous.additional_costs[0].note or "")

    exact_split = _repair(seed_result, source_pages, "2026000367", "04")
    assert exact_split.additional_costs[0].total_amount_manwon == 1_149
    assert [item.amount_manwon for item in exact_split.additional_costs[0].payments] == [
        200,
        949,
    ]

    sub_manwon_splits = _repair(seed_result, source_pages, "2026000371", "01")
    assert sub_manwon_splits.additional_costs[0].total_amount_manwon == 209
    assert sub_manwon_splits.additional_costs[0].payments == []

    exact_0374 = _repair(seed_result, source_pages, "2026000374", "08")
    assert [item.amount_manwon for item in exact_0374.additional_costs[0].payments] == [
        205,
        205,
        1_640,
    ]
    abstained_0374 = _repair(seed_result, source_pages, "2026000374", "09")
    assert abstained_0374.additional_costs[0].total_amount_manwon == 2_068
    assert abstained_0374.additional_costs[0].payments == []

    for complex_id, unit_type_id in CASES:
        corrected = _repair(seed_result, source_pages, complex_id, unit_type_id)
        assert len(corrected.additional_costs) == 1
        assert corrected.additional_costs[0].type == AdditionalCostType.BALCONY_EXTENSION


@pytest.mark.parametrize(("unit_type_id", "expected_name"), [("05", "51E"), ("09", "59D")])
def test_0374_visual_aliases_are_locked_to_the_correct_option_rows(
    seed_result: AnalysisResponse,
    source_pages: dict[str, list[PdfPage]],
    unit_type_id: str,
    expected_name: str,
) -> None:
    corrected = _repair(seed_result, source_pages, "2026000374", unit_type_id)
    assert corrected.target_unit.unit_type_name == expected_name
    assert corrected.additional_costs[0].applicable_unit_type == expected_name
    fields = {
        item.field: item.raw_text
        for item in corrected.evidence
        if item.field == "/additional_costs/0/applicable_unit_type"
    }
    assert "⇒" in fields["/additional_costs/0/applicable_unit_type"]


def test_document_loan_semantics_and_allocation_uncertainty(
    seed_result: AnalysisResponse,
    source_pages: dict[str, list[PdfPage]],
) -> None:
    for complex_id in ("2026000367", "2026000371", "2026000383"):
        corrected = _repair(seed_result, source_pages, complex_id, "01")
        assert corrected.interim_loan.arrangement_status == LoanArrangementStatus.PLANNED
        assert corrected.interim_loan.arranged_ratio == pytest.approx(0.60)
        assert corrected.interim_loan.interest_type == InterestType.DEFERRED_INTEREST
        assert (
            corrected.interim_loan.settlement_requirement
            == LoanSettlementRequirement.REPAY_OR_CONVERT_TO_MORTGAGE
        )

    uncertain = _repair(seed_result, source_pages, "2026000374", "01")
    assert uncertain.interim_loan.arranged_ratio == pytest.approx(0.40)
    assert uncertain.interim_loan.self_funding_ratio == pytest.approx(0.20)
    assert HoldReasonCode.SELF_FUNDING_SCHEDULE_UNKNOWN in {
        hold.reason_code for hold in uncertain.holds
    }

    unavailable = _repair(seed_result, source_pages, "2026000376", "01")
    assert unavailable.interim_loan.arrangement_status == LoanArrangementStatus.NOT_AVAILABLE
    assert unavailable.interim_loan.arranged_ratio == pytest.approx(0.0)
    assert unavailable.interim_loan.interest_type == InterestType.NOT_APPLICABLE
    assert (
        unavailable.interim_loan.settlement_requirement == LoanSettlementRequirement.NOT_APPLICABLE
    )
    assert unavailable.additional_costs[0].required is True
    assert unavailable.additional_costs[0].included_in_sale_price is True
    assert unavailable.additional_costs[0].total_amount_manwon is None


def _mark_test_only_reviewed(result: AnalysisResponse) -> AnalysisResponse:
    reviewed = result.model_copy(deep=True)
    reviewed.review_status = ReviewStatus.REVIEWED
    reviewed.reviewer = "test-only-human-approval"
    reviewed.reviewed_at = datetime(2026, 9, 4, tzinfo=UTC)
    return reviewed


def _stress_request(result: AnalysisResponse, *, cash: int = 0) -> FundingStressRequest:
    return FundingStressRequest(
        analysis_request=AnalyzeRequest(
            complex_id=result.complex_id,
            pdf_url="https://example.com/audited.pdf",
            unit_type_id=result.target_unit.unit_type_id,
            unit_type_name=result.target_unit.unit_type_name,
            sale_price_manwon=result.target_unit.sale_price_manwon,
        ),
        cash_manwon=cash,
        cash_snapshot_timing=CashSnapshotTiming.PRE_CONTRACT,
        monthly_saving_manwon=None,
        as_of_date=date(2026, 9, 4),
        loan_routes=[
            LoanRouteSnapshot(
                route_id="bank",
                product_code="BANK_MORTGAGE",
                product_name="은행 주택담보대출",
                status=RouteStatus.OK,
                limit_min_manwon=None,
                limit_max_manwon=200_000,
                rule_version="test",
                assumption_set_id="test",
            )
        ],
        interim_ratio_grid_bps=([] if result.complex_id == "2026000376" else [4_000, 6_000]),
    )


def test_0374_funding_stress_preserves_unknown_installment_and_date(
    seed_result: AnalysisResponse,
    source_pages: dict[str, list[PdfPage]],
) -> None:
    analysis = _mark_test_only_reviewed(_repair(seed_result, source_pages, "2026000374", "01"))
    response = calculate_funding_stress(_stress_request(analysis, cash=12_400), analysis)

    assert StressHoldCode.SELF_FUNDING_SCHEDULE_UNKNOWN in {hold.code for hold in response.holds}
    scenario = next(
        item for item in response.route_cases[0].scenarios if item.interim_ratio_bps == 4_000
    )
    assert scenario.status == ScenarioStatus.SHORTFALL
    assert scenario.first_shortfall is not None
    assert scenario.first_shortfall.stage == "INTERIM"
    assert scenario.first_shortfall.installment_number is None
    assert scenario.first_shortfall.due_date is None
    assert scenario.first_shortfall.due_month is None
    assert scenario.first_shortfall.due_text is None


def test_0376_half_manwon_obligations_abstain_instead_of_rounding(
    seed_result: AnalysisResponse,
    source_pages: dict[str, list[PdfPage]],
) -> None:
    analysis = _repair(seed_result, source_pages, "2026000376", "02")
    assert analysis.payment_schedule.down_payment.total_amount_manwon is None
    assert analysis.payment_schedule.down_payment.installments[1].amount_manwon is None
    assert analysis.payment_schedule.balance_payment.total_amount_manwon is None
    assert analysis.payment_schedule.balance_payment.installments[0].amount_manwon is None

    obligations, holds = funding_stress_module._obligations(
        _mark_test_only_reviewed(analysis),
        analysis.target_unit.sale_price_manwon or 0,
    )
    assert obligations.contract is None
    assert obligations.balance is None
    assert any(hold.code == StressHoldCode.PAYMENT_VALUE_UNKNOWN for hold in holds)

    reviewed = _mark_test_only_reviewed(analysis)
    response = calculate_funding_stress(_stress_request(reviewed), reviewed)
    assert response.interim_continuity_threshold.status == ThresholdStatus.UNKNOWN
    assert StressHoldCode.PAYMENT_VALUE_UNKNOWN in {hold.code for hold in response.holds}
