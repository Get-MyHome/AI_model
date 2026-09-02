from __future__ import annotations

import hashlib
from datetime import date

import pytest
from conftest import synthetic_pages

import get_myhome_ai.funding_stress as funding_stress_module
from get_myhome_ai.errors import FundingStressUnavailableError
from get_myhome_ai.funding_stress import _bps_amount, calculate_funding_stress
from get_myhome_ai.models import (
    AnalyzeRequest,
    LoanSettlementRequirement,
    PaymentBasis,
    ReviewStatus,
)
from get_myhome_ai.pdf_text import DownloadedPdf
from get_myhome_ai.pipeline import AnalysisPipeline
from get_myhome_ai.providers.fixture import FixtureExtractor
from get_myhome_ai.review import approve_result
from get_myhome_ai.settings import Settings
from get_myhome_ai.stress_models import (
    CashSnapshotTiming,
    FundingStressRequest,
    LoanRouteSnapshot,
    MarginStatus,
    RouteLimitCase,
    RouteStatus,
    ScenarioStatus,
    StressHoldCode,
    ThresholdStatus,
)


async def _reviewed_result(case):
    content = b"%PDF-funding-stress"

    async def loader(_url, _settings):
        return DownloadedPdf(content=content, sha256=hashlib.sha256(content).hexdigest())

    pipeline = AnalysisPipeline(
        settings=Settings(ai_provider="fixture"),
        provider=FixtureExtractor({case.complex_id: case.expected}),
        url_loader=loader,
        page_extractor=lambda _content, _settings: synthetic_pages(case),
    )
    result = await pipeline.analyze_url(
        AnalyzeRequest(
            complex_id=case.complex_id,
            pdf_url="https://example.com/file.pdf",
            unit_type_id="golden-unit",
            unit_type_name=case.unit_type_name,
            sale_price_manwon=case.sale_price_manwon,
        )
    )
    return approve_result(
        result,
        reviewer="funding-test-reviewer",
        source_sha256=result.meta.source_sha256,
        pages=synthetic_pages(case),
    )


def _route(
    *,
    route_id: str = "bank",
    limit_min: int | None = None,
    limit_max: int = 30_000,
) -> LoanRouteSnapshot:
    return LoanRouteSnapshot(
        route_id=route_id,
        product_code="BANK_MORTGAGE",
        product_name="은행 주택담보대출",
        status=RouteStatus.OK,
        limit_min_manwon=limit_min,
        limit_max_manwon=limit_max,
        rule_version="2026-08-31",
        assumption_set_id="mvp-v1",
    )


def _request(case, *, cash: int, routes=None, grid=None, monthly_saving=100):
    return FundingStressRequest(
        analysis_request=AnalyzeRequest(
            complex_id=case.complex_id,
            pdf_url="https://example.com/file.pdf",
            unit_type_id="golden-unit",
            unit_type_name=case.unit_type_name,
            sale_price_manwon=case.sale_price_manwon,
        ),
        cash_manwon=cash,
        cash_snapshot_timing=CashSnapshotTiming.PRE_CONTRACT,
        monthly_saving_manwon=monthly_saving,
        as_of_date=date(2026, 9, 2),
        loan_routes=routes if routes is not None else [_route()],
        interim_ratio_grid_bps=grid if grid is not None else [],
    )


@pytest.mark.asyncio
async def test_0372_threshold_and_actual_ratio_change_funding_stage(golden_cases) -> None:
    case = golden_cases["2026000372"]
    analysis = await _reviewed_result(case)
    # The real p.6 table is 10/60/30. The older fixture intentionally left the
    # balance ratio unknown; this funding-only fixture supplies the manually
    # checked 30% and the v0.2 settlement fact.
    analysis.payment_schedule.balance_payment.total_ratio = 0.30
    analysis.payment_schedule.balance_payment.basis = PaymentBasis.RATIO
    analysis.interim_loan.settlement_requirement = (
        LoanSettlementRequirement.REPAY_OR_CONVERT_TO_MORTGAGE
    )

    response = calculate_funding_stress(
        _request(case, cash=10_865, grid=[4_000, 6_000]), analysis
    )

    assert response.advisory is True
    assert response.maximum_interim_ratio_bps == 6_000
    assert response.interim_continuity_threshold.status == ThresholdStatus.CALCULATED
    assert response.interim_continuity_threshold.minimum_ratio_bps == 6_000
    assert response.document_cap_comparison.interim_continuity.status == MarginStatus.NEGATIVE
    assert response.document_cap_comparison.interim_continuity.margin_bps == -2_000

    route_case = response.route_cases[0]
    assert route_case.full_completion_threshold.status == ThresholdStatus.NOT_ACHIEVABLE
    by_ratio = {item.interim_ratio_bps: item for item in route_case.scenarios}
    actual = by_ratio[4_000]
    assert actual.status == ScenarioStatus.SHORTFALL
    assert actual.first_shortfall is not None
    assert actual.first_shortfall.stage == "INTERIM"
    assert actual.first_shortfall.installment_number is None
    assert actual.first_shortfall.due_date is None
    assert actual.first_shortfall.due_month is None
    assert actual.first_shortfall.due_text is None
    assert actual.first_shortfall.shortfall_manwon == 21_730
    assert actual.balance_margin_manwon == -46_055
    assert actual.recovery_months_at_first_shortfall == 218

    full_interim = by_ratio[6_000]
    assert full_interim.first_shortfall is not None
    assert full_interim.first_shortfall.stage == "BALANCE"
    assert full_interim.first_shortfall.due_month == "2030-01"
    assert full_interim.first_shortfall.due_text == "입주지정일"
    assert full_interim.first_shortfall.shortfall_manwon == 67_785
    assert full_interim.balance_margin_manwon == -67_785


@pytest.mark.asyncio
async def test_route_min_and_max_are_independent_not_summed(golden_cases) -> None:
    case = golden_cases["2026000372"]
    analysis = await _reviewed_result(case)
    analysis.payment_schedule.balance_payment.total_ratio = 0.30
    analysis.payment_schedule.balance_payment.basis = PaymentBasis.RATIO
    analysis.interim_loan.settlement_requirement = LoanSettlementRequirement.REPAY_REQUIRED

    response = calculate_funding_stress(
        _request(case, cash=10_865, routes=[_route(limit_min=25_000, limit_max=30_000)]),
        analysis,
    )

    assert len(response.route_cases) == 2
    assert {item.limit_case for item in response.route_cases} == {
        RouteLimitCase.CONSERVATIVE_LIMIT,
        RouteLimitCase.MAXIMUM_LIMIT,
    }
    assert {item.balance_financing_manwon for item in response.route_cases} == {25_000, 30_000}
    assert all(item.balance_financing_manwon != 55_000 for item in response.route_cases)
    assert {item.rule_version for item in response.route_cases} == {"2026-08-31"}
    assert {item.assumption_set_id for item in response.route_cases} == {"mvp-v1"}


@pytest.mark.asyncio
async def test_threshold_search_is_bounded_not_one_call_per_basis_point(
    golden_cases, monkeypatch
) -> None:
    case = golden_cases["2026000372"]
    analysis = await _reviewed_result(case)
    analysis.payment_schedule.balance_payment.total_ratio = 0.30
    analysis.payment_schedule.balance_payment.basis = PaymentBasis.RATIO
    analysis.interim_loan.settlement_requirement = LoanSettlementRequirement.REPAY_REQUIRED
    original = funding_stress_module._scenario
    calls = 0

    def counted_scenario(**kwargs):
        nonlocal calls
        calls += 1
        return original(**kwargs)

    monkeypatch.setattr(funding_stress_module, "_scenario", counted_scenario)
    calculate_funding_stress(_request(case, cash=10_865), analysis)

    assert calls < 100


@pytest.mark.asyncio
async def test_fixed_amount_interim_never_overfinances_rounding(golden_cases) -> None:
    case = golden_cases["2026000376"]
    analysis = await _reviewed_result(case)
    analysis.interim_loan.settlement_requirement = LoanSettlementRequirement.NOT_APPLICABLE
    request = _request(
        case,
        cash=3_395,
        routes=[_route(limit_max=44_505)],
        monthly_saving=None,
    )

    response = calculate_funding_stress(request, analysis)

    assert response.maximum_interim_ratio_bps == 209
    assert response.document_cap_comparison.document_cap_ratio_bps == 0
    assert response.interim_continuity_threshold.minimum_ratio_bps == 0
    max_ratio_case = next(
        item
        for item in response.route_cases[0].scenarios
        if item.interim_ratio_bps == response.maximum_interim_ratio_bps
    )
    assert max_ratio_case.interim_loan_amount_manwon == 1_000


@pytest.mark.asyncio
async def test_unknown_settlement_and_interest_are_conditional_holds(golden_cases) -> None:
    case = golden_cases["2026000358"]
    analysis = await _reviewed_result(case)
    response = calculate_funding_stress(_request(case, cash=30_000), analysis)

    codes = {hold.code for hold in response.holds}
    route_codes = {hold.code for item in response.route_cases for hold in item.holds}
    assert StressHoldCode.INTEREST_AMOUNT_UNKNOWN in codes
    assert StressHoldCode.SETTLEMENT_TERMS_UNKNOWN in route_codes


@pytest.mark.asyncio
async def test_required_cost_with_unknown_allocation_makes_results_unknown(golden_cases) -> None:
    case = golden_cases["2026000358"]
    analysis = await _reviewed_result(case)
    cost = analysis.additional_costs[0]
    cost.required = True
    cost.payments[-1].amount_manwon = None

    response = calculate_funding_stress(
        _request(case, cash=200_000, routes=[_route(limit_max=200_000)]), analysis
    )

    assert response.interim_continuity_threshold.status == ThresholdStatus.UNKNOWN
    assert all(
        scenario.status == ScenarioStatus.UNKNOWN
        for route_case in response.route_cases
        for scenario in route_case.scenarios
    )
    affected = [
        hold
        for hold in response.holds
        if hold.code == StressHoldCode.REQUIRED_ADDITIONAL_COST_UNKNOWN
    ]
    assert affected
    assert all(hold.blocking for hold in affected)
    assert any("발코니 확장비" in hold.message for hold in affected)


@pytest.mark.asyncio
async def test_unknown_cost_inclusion_and_applicability_are_blocking(golden_cases) -> None:
    case = golden_cases["2026000358"]
    analysis = await _reviewed_result(case)
    cost = analysis.additional_costs[0]
    cost.required = True
    cost.included_in_sale_price = None
    cost.applicable_unit_type = None

    response = calculate_funding_stress(
        _request(case, cash=200_000, routes=[_route(limit_max=200_000)]), analysis
    )

    holds_by_code = {hold.code: hold for hold in response.holds}
    assert holds_by_code[StressHoldCode.ADDITIONAL_COST_INCLUSION_UNKNOWN].blocking is True
    assert holds_by_code[StressHoldCode.ADDITIONAL_COST_APPLICABILITY_UNKNOWN].blocking is True
    assert response.interim_continuity_threshold.status == ThresholdStatus.UNKNOWN


@pytest.mark.asyncio
async def test_cost_for_another_unit_type_is_not_added(golden_cases) -> None:
    case = golden_cases["2026000358"]
    analysis = await _reviewed_result(case)
    cost = analysis.additional_costs[0]
    cost.required = True
    cost.applicable_unit_type = "84B"

    response = calculate_funding_stress(
        _request(case, cash=200_000, routes=[_route(limit_max=200_000)]), analysis
    )

    assert response.interim_continuity_threshold.status == ThresholdStatus.CALCULATED
    assert all(
        hold.code != StressHoldCode.ADDITIONAL_COST_APPLICABILITY_UNKNOWN
        for hold in response.holds
    )


@pytest.mark.asyncio
async def test_holds_preserve_every_affected_cost_name(golden_cases) -> None:
    case = golden_cases["2026000358"]
    analysis = await _reviewed_result(case)
    template = analysis.additional_costs[0]
    first = template.model_copy(deep=True)
    first.name = "필수비용 A"
    first.required = True
    first.total_amount_manwon = 100
    first.payments = []
    second = first.model_copy(deep=True)
    second.name = "필수비용 B"
    analysis.additional_costs = [first, second]

    response = calculate_funding_stress(
        _request(case, cash=200_000, routes=[_route(limit_max=200_000)]), analysis
    )

    messages = [
        hold.message
        for hold in response.holds
        if hold.code == StressHoldCode.REQUIRED_ADDITIONAL_COST_UNKNOWN
    ]
    assert len(messages) == 2
    assert any("필수비용 A" in message for message in messages)
    assert any("필수비용 B" in message for message in messages)


@pytest.mark.asyncio
async def test_contract_shortfall_carries_source_timing(golden_cases) -> None:
    case = golden_cases["2026000358"]
    analysis = await _reviewed_result(case)
    analysis.payment_schedule.down_payment.due_date = date(2026, 10, 1)

    response = calculate_funding_stress(_request(case, cash=0), analysis)

    first = response.route_cases[0].scenarios[0].first_shortfall
    assert first is not None
    assert first.stage == "CONTRACT"
    assert first.due_date == date(2026, 10, 1)
    assert first.due_text == "계약 시 및 계약 후 15일 이내"


@pytest.mark.asyncio
async def test_post_contract_cash_snapshot_is_rejected(golden_cases) -> None:
    case = golden_cases["2026000358"]
    analysis = await _reviewed_result(case)
    analysis.payment_schedule.down_payment.due_date = date(2026, 9, 1)

    with pytest.raises(FundingStressUnavailableError, match="계약금 납부일보다 늦"):
        calculate_funding_stress(_request(case, cash=30_000), analysis)


def test_duplicate_route_ids_are_rejected(golden_cases) -> None:
    case = golden_cases["2026000358"]

    with pytest.raises(ValueError, match="route_id"):
        _request(case, cash=30_000, routes=[_route(route_id="same"), _route(route_id="same")])


@pytest.mark.asyncio
async def test_auto_extracted_analysis_is_rejected(golden_cases) -> None:
    case = golden_cases["2026000358"]
    analysis = await _reviewed_result(case)
    analysis.review_status = ReviewStatus.AUTO_EXTRACTED
    analysis.reviewer = None
    analysis.reviewed_at = None

    with pytest.raises(FundingStressUnavailableError, match="REVIEWED"):
        calculate_funding_stress(_request(case, cash=30_000), analysis)


@pytest.mark.asyncio
async def test_ratio_grid_cannot_exceed_interim_obligation(golden_cases) -> None:
    case = golden_cases["2026000358"]
    analysis = await _reviewed_result(case)

    with pytest.raises(FundingStressUnavailableError, match="초과"):
        calculate_funding_stress(_request(case, cash=30_000, grid=[6_001]), analysis)


def test_bps_amount_uses_half_up() -> None:
    assert _bps_amount(1_000, 5) == 1
