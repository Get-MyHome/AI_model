from __future__ import annotations

import asyncio
from datetime import date

import pytest
from test_review_candidate_correction import _automatic_result

import get_myhome_ai.audited_candidate_repairs as repairs
import get_myhome_ai.review_candidate_correction as correction
from get_myhome_ai.models import AdditionalCostType, Installment, ValueOrigin
from get_myhome_ai.pdf_text import PdfPage


def test_current_audit_policy_is_143_source_audited_candidates() -> None:
    assert correction.AUDITED_CANDIDATE_COUNT == 143
    assert "2026000356" not in correction._AUDITED_DOCUMENTS
    assert "2026000364" in correction._AUDITED_DOCUMENTS
    assert ("03", "74", 85_700) in correction._AUDITED_DOCUMENTS["2026000377"].targets


def test_exact_payment_repair_replaces_amounts_but_keeps_interim_dates(golden_cases) -> None:
    result, _pages = asyncio.run(_automatic_result(golden_cases["2026000372"]))
    result.complex_id = "2026000295"
    result.target_unit.unit_type_id = "02"
    result.target_unit.unit_type_name = "84A"
    result.target_unit.sale_price_manwon = 42_700
    original_dates = [
        item.due_date for item in result.payment_schedule.interim_payment.installments
    ]
    pages = [
        PdfPage(
            number=7,
            text=(
                "계약시 30일 이내 입주지정일\n"
                "84A 427,000,000 5,000,000 16,350,000 21,350,000 "
                "42,700,000 42,700,000 42,700,000 42,700,000 42,700,000 "
                "42,700,000 128,100,000"
            ),
        ),
        PdfPage(
            number=8,
            text=(
                "84A 427,000,000 5,000,000 16,350,000 21,350,000 "
                "42,700,000 42,700,000 42,700,000 42,700,000 42,700,000 "
                "42,700,000 128,100,000"
            ),
        ),
    ]

    repairs._repair_payment_row(result, pages, repairs._PAYMENT_ROWS[("2026000295", "02")])

    schedule = result.payment_schedule
    assert [item.amount_manwon for item in schedule.down_payment.installments] == [500, 1635, 2135]
    assert schedule.interim_payment.total_amount_manwon == 25_620
    assert [item.amount_manwon for item in schedule.interim_payment.installments] == [4270] * 6
    assert [item.due_date for item in schedule.interim_payment.installments] == original_dates
    assert schedule.balance_payment.total_amount_manwon == 12_810


def test_0327_repairs_mandatory_options_and_anchors_duplicate_amount_row_to_unit(
    golden_cases,
) -> None:
    result, _pages = asyncio.run(_automatic_result(golden_cases["2026000372"]))
    result.complex_id = "2026000327"
    result.target_unit.unit_type_id = "04"
    result.target_unit.unit_type_name = "84B"
    pages = [
        PdfPage(
            number=7,
            text=(
                "일부 유상옵션이 설치되어 있으므로 유상옵션이 설치된 상태로 공급받아야 하며, "
                "유상옵션 대금은 분양대금과 별도로 납부하여야 합니다.\n"
                "상기 공급금액에는 발코니 확장 비용이 미포함되어 있습니다\n"
                "106-2101 84B 6,300,000 6,300,000"
            ),
        ),
        PdfPage(
            number=22,
            text=(
                "계약시(10%) 중도금(20%) 잔금(70%) 약식표기 공급금액 계약시 "
                "2026.09.30. 입주지정일\n"
                "84A 18,500,000 1,850,000 3,700,000 12,950,000\n"
                "84B 18,500,000 1,850,000 3,700,000 12,950,000\n"
                "기존 조합원이 발코니 확장을 선택하여 계약체결된 건이므로 금회 본 아파트를 "
                "계약하시는 분은 반드시 발코니 확장을 계약하는 조건으로만 청약이 가능합니다."
            ),
        ),
    ]

    repairs._repair_0327_costs(result, pages)

    balcony, aircon = result.additional_costs
    assert balcony.required is True
    assert balcony.total_amount_manwon == 1850
    assert [item.amount_manwon for item in balcony.payments] == [185, 370, 1295]
    assert aircon.type == AdditionalCostType.SYSTEM_AIR_CONDITIONER
    assert aircon.required is True
    assert aircon.total_amount_manwon == 630
    row = next(item for item in result.evidence if item.field == "/additional_costs/0")
    assert row.raw_text.startswith("84B")
    assert result.interim_loan.interest_note is None


def test_0358_marks_direct_twenty_percent_as_source_extracted(golden_cases) -> None:
    result, _pages = asyncio.run(_automatic_result(golden_cases["2026000372"]))
    result.complex_id = "2026000358"
    quote = (
        "총 공급대금의 60% 중 총 공급대금의 40% 범위 내에서 대출 알선이 가능하며 "
        "나머지 총 공급대금의 20%는 계약자가 직접 납부"
    )

    repairs._repair_0358_direct_funding(result, [PdfPage(number=37, text=quote)])

    assert result.interim_loan.arranged_ratio == pytest.approx(0.4)
    assert result.interim_loan.self_funding_ratio == pytest.approx(0.2)
    assert result.interim_loan.self_funding_origin == ValueOrigin.EXTRACTED


def test_0377_unit03_replaces_representative_option_with_exact_balcony(golden_cases) -> None:
    result, _pages = asyncio.run(_automatic_result(golden_cases["2026000372"]))
    result.complex_id = "2026000377"
    result.target_unit.unit_type_id = "03"
    result.target_unit.unit_type_name = "74"
    pages = [
        PdfPage(
            number=9,
            text=(
                "상기 공급금액에는 발코니 확장 비용, 추가선택 품목 비용이 미포함 되었으며 "
                "주택 분양계약 체결 시 별도계약을 통해 선택이 가능합니다"
            ),
        ),
        PdfPage(number=45, text="74 18,000,000 1,800,000 16,200,000"),
    ]

    repairs._repair_0377_cost(result, pages)

    assert len(result.additional_costs) == 1
    cost = result.additional_costs[0]
    assert cost.type == AdditionalCostType.BALCONY_EXTENSION
    assert cost.name == "발코니 확장"
    assert cost.total_amount_manwon == 1800
    assert cost.payments[1].due_text == "입주지정기간"


def test_0382_sets_source_proven_second_contract_date(golden_cases) -> None:
    result, _pages = asyncio.run(_automatic_result(golden_cases["2026000372"]))
    result.complex_id = "2026000382"
    result.payment_schedule.down_payment.installments = [
        Installment(number=1, ratio=None, amount_manwon=1000, due_date=None, due_text="계약 시"),
        Installment(
            number=2, ratio=None, amount_manwon=4910, due_date=None, due_text="기존 오인식"
        ),
    ]

    repairs._repair_0382_due_dates(result, [PdfPage(number=9, text="2026.10.07.")])

    assert result.payment_schedule.down_payment.installments[1].due_date == date(2026, 10, 7)
    assert result.payment_schedule.down_payment.installments[1].due_text is None
