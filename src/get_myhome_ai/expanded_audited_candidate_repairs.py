from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from get_myhome_ai.audited_candidate_repairs import (
    AuditedRepairError,
    _evidence,
    _normalized,
    _page,
    _remove_evidence,
    _won_values,
)
from get_myhome_ai.models import (
    AdditionalCost,
    AdditionalCostPayment,
    AdditionalCostType,
    AnalysisResponse,
    Evidence,
    Installment,
    PaymentBasis,
    PaymentStage,
)
from get_myhome_ai.pdf_text import PdfPage


@dataclass(frozen=True)
class _CorePaymentSpec:
    row_pages: tuple[int, ...]
    header_page: int
    first_contract_won: int
    first_contract_quote: str
    second_contract_quote: str
    interim_dates: tuple[date, ...]
    interim_dates_quote: str
    balance_due_quote: str
    move_in_page: int
    move_in_month: str
    move_in_quote: str


_CORE_SPECS: dict[str, _CorePaymentSpec] = {
    "2026000331": _CorePaymentSpec(
        row_pages=(7, 8),
        header_page=7,
        first_contract_won=5_000_000,
        first_contract_quote="체결시",
        second_contract_quote="30일 내",
        interim_dates=(
            date(2026, 12, 18),
            date(2027, 3, 18),
            date(2027, 6, 18),
            date(2028, 5, 18),
            date(2028, 8, 18),
            date(2029, 1, 18),
        ),
        interim_dates_quote=("2026-12-18 2027-03-18 2027-06-18 2028-05-18 2028-08-18 2029-01-18"),
        balance_due_quote="입주지정일",
        move_in_page=6,
        move_in_month="2029-08",
        move_in_quote="입주시기 : 2029년 08월",
    ),
    "2026000342": _CorePaymentSpec(
        row_pages=(8,),
        header_page=8,
        first_contract_won=10_000_000,
        first_contract_quote="계약시",
        second_contract_quote="30일 이내",
        interim_dates=(
            date(2027, 1, 11),
            date(2027, 6, 10),
            date(2027, 12, 10),
            date(2028, 9, 11),
            date(2028, 11, 10),
            date(2029, 3, 12),
        ),
        interim_dates_quote=(
            "2027.01.11   2027.06.10   2027.12.10   2028.09.11   2028.11.10   2029.03.12"
        ),
        balance_due_quote="입주지정일",
        move_in_page=6,
        move_in_month="2029-08",
        move_in_quote="입주시기 : 2029년 08월",
    ),
    "2026000354": _CorePaymentSpec(
        row_pages=(7, 8, 9, 10),
        header_page=7,
        first_contract_won=10_000_000,
        first_contract_quote="계약시",
        second_contract_quote="30일 이내",
        interim_dates=(
            date(2027, 10, 28),
            date(2028, 4, 25),
            date(2029, 1, 23),
            date(2030, 1, 31),
            date(2030, 7, 23),
            date(2031, 4, 22),
        ),
        interim_dates_quote=(
            "2027.10.28    2028.04.25    2029.01.23    2030.01.31    2030.07.23    2031.04.22"
        ),
        balance_due_quote="입주지정일",
        move_in_page=5,
        move_in_month="2032-03",
        move_in_quote="입주시기 : 2032년 03월",
    ),
}


EXPANDED_AUDITED_POLICY_DATA: dict[str, dict[str, Any]] = {
    "2026000331": {
        "source_sha256": "010651d169f1d662a27fe619e75489d4682353554dd33d94a382db66a193b5aa",
        "source_page_count": 80,
        "targets": frozenset({("01", "84A", 52_900), ("02", "84B", 52_900), ("03", "99", 71_035)}),
        "not_included_evidence": (
            8,
            "상기 공급금액에는 추가선택품목(유상옵션) 미포함 금액이며",
        ),
        "optional_evidence": (8, "별도의 계약을 통해 선택이 가능합니다"),
    },
    "2026000342": {
        "source_sha256": "ee43d4cb759d7a552ffafbdf3bfe4927646d7f7150feac9ccfaaaf35175be775",
        "source_page_count": 73,
        "targets": frozenset(
            {
                ("01", "84A", 51_800),
                ("02", "84B", 51_460),
                ("03", "115A", 69_810),
                ("04", "129A", 79_600),
            }
        ),
        "not_included_evidence": (48, "발코니 확장 공사비는 공동주택 분양금액과 별도"),
        "optional_evidence": (48, "별도 계약을 체결할 수 있습니다"),
    },
    "2026000354": {
        "source_sha256": "b52cded6e374f1ec024887bd1c70a328d313df8e782323d128bf1e7925d4c520",
        "source_page_count": 79,
        "targets": frozenset(
            {
                ("01", "84A", 163_000),
                ("02", "84B", 149_900),
                ("03", "84C", 159_800),
                ("04", "84D", 159_900),
                ("05", "84E", 163_800),
                ("06", "84F", 159_900),
                ("07", "113A", 224_200),
                ("08", "113B", 224_900),
                ("09", "121", 234_900),
                ("10", "153P", 475_000),
                ("11", "187P", 572_700),
                ("12", "192P", 603_800),
            }
        ),
        "not_included_evidence": (
            10,
            "상기 공급금액은 추가 선택품목(가구, 가전 등 유상 옵션) "
            "금액이 포함되지 아니한 금액이며",
        ),
        "optional_evidence": (10, "추가 선택품목은 계약자가 선택하여 계약하는 사항으로"),
    },
}


_BALCONY_0342: dict[str, int] = {
    "01": 1_045,
    "02": 1_282,
    "03": 1_207,
    "04": 1_167,
}


def _exact_manwon(value_won: int) -> int | None:
    quotient, remainder = divmod(value_won, 10_000)
    return quotient if remainder == 0 else None


def _percentage(value_won: int, percent: int) -> int:
    numerator = value_won * percent
    quotient, remainder = divmod(numerator, 100)
    if remainder:
        raise AuditedRepairError(f"{percent}% 원단위 금액이 정수로 닫히지 않습니다.")
    return quotient


def _contains_subsequence(values: list[int], wanted: list[int]) -> bool:
    return any(values[index : index + len(wanted)] == wanted for index in range(len(values)))


def _won_row_evidence(
    pages: list[PdfPage],
    *,
    field: str,
    page_numbers: tuple[int, ...],
    wanted_won: tuple[int, ...],
    required_text: str | None = None,
) -> Evidence:
    matches: list[tuple[int, str]] = []
    wanted = list(wanted_won)
    for page_number in page_numbers:
        page = _page(pages, page_number)
        for line in page.text.splitlines():
            if _contains_subsequence(_won_values(line), wanted) and (
                required_text is None or _normalized(required_text) in _normalized(line)
            ):
                matches.append((page_number, line.strip()))
    if not matches:
        raise AuditedRepairError(f"{field}: exact 원단위 금액 행을 PDF에서 찾지 못했습니다.")
    if any(not _contains_subsequence(_won_values(raw_text), wanted) for _, raw_text in matches):
        raise AuditedRepairError(f"{field}: 서로 다른 금액 행이 충돌합니다.")
    page_number, raw_text = matches[0]
    return Evidence(field=field, page=page_number, raw_text=raw_text)


def _repair_core_payment_schedule(
    result: AnalysisResponse,
    pages: list[PdfPage],
    spec: _CorePaymentSpec,
) -> None:
    sale_price_manwon = result.target_unit.sale_price_manwon
    if sale_price_manwon is None:
        raise AuditedRepairError("선택 분양가 없이 exact 납부표를 교정할 수 없습니다.")
    sale_won = sale_price_manwon * 10_000
    down_total_won = _percentage(sale_won, 5)
    if spec.first_contract_won >= down_total_won:
        raise AuditedRepairError("계약 체결 시 금액이 계약금 총액 이상입니다.")
    second_contract_won = down_total_won - spec.first_contract_won
    interim_each_won = _percentage(sale_won, 10)
    balance_won = _percentage(sale_won, 35)
    obligation_vector = (
        sale_won,
        spec.first_contract_won,
        second_contract_won,
        *((interim_each_won,) * 6),
        balance_won,
    )
    row = _won_row_evidence(
        pages,
        field="/payment_schedule/down_payment",
        page_numbers=spec.row_pages,
        wanted_won=obligation_vector,
    )

    schedule = result.payment_schedule
    down = schedule.down_payment
    down.total_ratio = 0.05
    down.total_amount_manwon = _exact_manwon(down_total_won)
    down.basis = PaymentBasis.MIXED if down.total_amount_manwon is not None else PaymentBasis.RATIO
    down.due_date = None
    down.due_month = None
    down.due_text = None
    down.installments = [
        Installment(
            number=1,
            ratio=spec.first_contract_won / sale_won,
            amount_manwon=_exact_manwon(spec.first_contract_won),
            due_date=None,
            due_text="계약 시",
        ),
        Installment(
            number=2,
            ratio=second_contract_won / sale_won,
            amount_manwon=_exact_manwon(second_contract_won),
            due_date=None,
            due_text="계약 후 30일 이내",
        ),
    ]

    interim = schedule.interim_payment
    interim.total_ratio = 0.60
    interim.total_amount_manwon = _exact_manwon(interim_each_won * 6)
    interim.basis = PaymentBasis.MIXED
    interim.due_date = None
    interim.due_month = None
    interim.due_text = None
    interim.installments = [
        Installment(
            number=index,
            ratio=0.10,
            amount_manwon=_exact_manwon(interim_each_won),
            due_date=due_date,
            due_text=None,
        )
        for index, due_date in enumerate(spec.interim_dates, start=1)
    ]

    balance = schedule.balance_payment
    balance.total_ratio = 0.35
    balance.total_amount_manwon = _exact_manwon(balance_won)
    balance.basis = (
        PaymentBasis.MIXED if balance.total_amount_manwon is not None else PaymentBasis.RATIO
    )
    balance.due_date = None
    balance.due_month = spec.move_in_month
    balance.due_text = "입주지정일"
    balance.installments = []

    _remove_evidence(result, ("/payment_schedule/",))
    for field in (
        "/payment_schedule/down_payment",
        "/payment_schedule/interim_payment",
        "/payment_schedule/balance_payment",
    ):
        result.evidence.append(row.model_copy(update={"field": field}))
    result.evidence.extend(
        [
            _evidence(
                pages,
                "/payment_schedule/down_payment/installments/0/due_text",
                spec.header_page,
                spec.first_contract_quote,
            ),
            _evidence(
                pages,
                "/payment_schedule/down_payment/installments/1/due_text",
                spec.header_page,
                spec.second_contract_quote,
            ),
            _evidence(
                pages,
                "/payment_schedule/interim_payment/installments",
                spec.header_page,
                spec.interim_dates_quote,
            ),
            _evidence(
                pages,
                "/payment_schedule/balance_payment/due_text",
                spec.header_page,
                spec.balance_due_quote,
            ),
            _evidence(
                pages,
                "/payment_schedule/balance_payment/due_month",
                spec.move_in_page,
                spec.move_in_quote,
            ),
        ]
    )


def _repair_0331_cost_scope(result: AnalysisResponse, pages: list[PdfPage]) -> None:
    unit_name = result.target_unit.unit_type_name
    if unit_name is None:
        raise AuditedRepairError("2026000331 선택 주택형이 없습니다.")
    free_quote = (
        "본 아파트는 계약자가 발코니 확장을 선택시 확장 공사를 무상으로 제공하오니 "
        "청약 및 계약 전 이를 반드시 확인하기 바랍니다."
    )
    _evidence(pages, "/exception_flags", 53, "현관 중문 옵션")
    result.additional_costs = [
        AdditionalCost(
            type=AdditionalCostType.BALCONY_EXTENSION,
            name="발코니 확장(선택 시 무상)",
            total_amount_manwon=None,
            required=False,
            included_in_sale_price=True,
            applicable_unit_type=unit_name,
            payments=[],
            note="선택 시 무상 · 부분 확장 선택 불가; 현관 중문 등 유상옵션은 미선택 상태",
        )
    ]
    _remove_evidence(result, ("/additional_costs/",))
    result.evidence.append(_evidence(pages, "/additional_costs/0", 52, free_quote))


def _repair_0342_balcony(result: AnalysisResponse, pages: list[PdfPage]) -> None:
    unit_id = result.target_unit.unit_type_id or ""
    unit_name = result.target_unit.unit_type_name
    if unit_name is None or unit_id not in _BALCONY_0342:
        raise AuditedRepairError("2026000342 감사 대상 주택형이 아닙니다.")
    total = _BALCONY_0342[unit_id]
    row = _won_row_evidence(
        pages,
        field="/additional_costs/0",
        page_numbers=(48,),
        wanted_won=(total * 10_000,),
        required_text=unit_name,
    )
    row_values = _won_values(row.raw_text)
    if len(row_values) != 4 or row_values[0] != total * 10_000:
        raise AuditedRepairError("발코니 총액과 10/20/70 분납행이 exact 행으로 닫히지 않습니다.")
    if all(value % 10_000 == 0 for value in row_values[1:]):
        raise AuditedRepairError("반만원 단위 분납 보류 대상이 아닙니다.")

    result.additional_costs = [
        AdditionalCost(
            type=AdditionalCostType.BALCONY_EXTENSION,
            name="발코니 확장 공사비",
            total_amount_manwon=total,
            required=False,
            included_in_sale_price=False,
            applicable_unit_type=unit_name,
            payments=[
                AdditionalCostPayment(
                    number=1,
                    stage=PaymentStage.CONTRACT,
                    amount_manwon=None,
                    due_date=None,
                    due_text="계약시",
                ),
                AdditionalCostPayment(
                    number=2,
                    stage=PaymentStage.INTERIM,
                    amount_manwon=None,
                    due_date=date(2027, 1, 11),
                    due_text=None,
                ),
                AdditionalCostPayment(
                    number=3,
                    stage=PaymentStage.BALANCE,
                    amount_manwon=None,
                    due_date=None,
                    due_text="입주지정일",
                ),
            ],
            note="선택사항 · 공급금액 미포함; 분납액은 반만원 단위로 현 스키마에서 금액 계산 보류",
        )
    ]
    _remove_evidence(result, ("/additional_costs/",))
    result.evidence.extend(
        [
            row,
            _evidence(
                pages,
                "/additional_costs/0/required",
                48,
                "별도 계약을 체결할 수 있습니다",
            ),
            _evidence(
                pages,
                "/additional_costs/0/included_in_sale_price",
                48,
                "발코니 확장 공사비는 공동주택 분양금액과 별도",
            ),
            _evidence(
                pages,
                "/additional_costs/0/note",
                48,
                "계약금(10%)                  중도금(20%)          잔금(70%)",
            ),
            _evidence(
                pages,
                "/additional_costs/0/payments",
                48,
                "계약금(10%)                  중도금(20%)          잔금(70%)",
            ),
        ]
    )


def _repair_0354_included_balcony(result: AnalysisResponse, pages: list[PdfPage]) -> None:
    unit_name = result.target_unit.unit_type_name
    if unit_name is None:
        raise AuditedRepairError("2026000354 선택 주택형이 없습니다.")
    free_quote = (
        "상기 아파트는 전세대 발코니 확장형으로 발코니 확장공사가 무상으로 시공되며, "
        "비확장형으로 선택이 불가합니다."
    )
    scope_quote = (
        "상기 공급금액은 추가 선택품목(가구, 가전 등 유상 옵션) 금액이 포함되지 아니한 금액이며, "
        "추가 선택품목은 계약자가 선택하여 계약하는 사항으로, 별도의 계약을 통해 선택이 가능합니다."
    )
    result.additional_costs = [
        AdditionalCost(
            type=AdditionalCostType.BALCONY_EXTENSION,
            name="발코니 확장(무상 시공)",
            total_amount_manwon=None,
            required=True,
            included_in_sale_price=True,
            applicable_unit_type=unit_name,
            payments=[],
            note="전 세대 무상 시공 · 비확장형 선택 불가; 선택 유상옵션은 미선택 상태",
        )
    ]
    _remove_evidence(result, ("/additional_costs/",))
    result.evidence.extend(
        [
            _evidence(pages, "/additional_costs/0", 10, free_quote),
            _evidence(pages, "/exception_flags", 10, scope_quote),
        ]
    )


def apply_expanded_audited_repairs(
    result: AnalysisResponse,
    *,
    pages: list[PdfPage],
) -> tuple[list[str], bool]:
    """Apply source-locked repairs for the expanded 0331/0342/0354 set."""

    spec = _CORE_SPECS.get(result.complex_id)
    if spec is None:
        return [], False
    _repair_core_payment_schedule(result, pages, spec)
    actions = ["GROUND_EXACT_5_60_35_PAYMENT_ROW"]
    if result.complex_id == "2026000331":
        _repair_0331_cost_scope(result, pages)
        actions.append("RECORD_OPTIONAL_FREE_BALCONY_EXCLUDE_UNSELECTED_OPTIONS")
    elif result.complex_id == "2026000342":
        _repair_0342_balcony(result, pages)
        actions.append("PRESERVE_BALCONY_TOTAL_ABSTAIN_SUB_MANWON_INSTALLMENTS")
    else:
        _repair_0354_included_balcony(result, pages)
        actions.append("RECORD_INCLUDED_FREE_BALCONY_EXCLUDE_UNSELECTED_OPTIONS")
    return actions, True
