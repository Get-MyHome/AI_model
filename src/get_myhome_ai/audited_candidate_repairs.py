from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from get_myhome_ai.models import (
    AdditionalCost,
    AdditionalCostPayment,
    AdditionalCostType,
    AnalysisResponse,
    Evidence,
    Installment,
    PaymentBasis,
    PaymentStage,
    ValueOrigin,
)
from get_myhome_ai.pdf_text import PdfPage


class AuditedRepairError(ValueError):
    """Raised when a hand-audited repair cannot be proven from the locked PDF."""


@dataclass(frozen=True)
class _Due:
    page: int
    raw_text: str
    due_date: date | None = None
    due_text: str | None = None


@dataclass(frozen=True)
class _PaymentRow:
    page_numbers: tuple[int, ...]
    down_amounts: tuple[int, ...]
    down_dues: tuple[_Due, ...]
    interim_amounts: tuple[int, ...]
    balance_amount: int


def _d(page: int, raw_text: str, due_text: str) -> _Due:
    return _Due(page=page, raw_text=raw_text, due_text=due_text)


def _dated(page: int, raw_text: str, year: int, month: int, day: int) -> _Due:
    return _Due(page=page, raw_text=raw_text, due_date=date(year, month, day))


_PAYMENT_ROWS: dict[tuple[str, str], _PaymentRow] = {
    ("2026000295", "01"): _PaymentRow(
        (7,),
        (500, 1495, 1995),
        (
            _d(7, "계약시", "계약 시"),
            _d(7, "30일 이내", "계약 후 30일 이내"),
            _d(7, "입주지정일", "입주지정일"),
        ),
        (3990,) * 6,
        11970,
    ),
    ("2026000295", "02"): _PaymentRow(
        (8,),
        (500, 1635, 2135),
        (
            _d(7, "계약시", "계약 시"),
            _d(7, "30일 이내", "계약 후 30일 이내"),
            _d(7, "입주지정일", "입주지정일"),
        ),
        (4270,) * 6,
        12810,
    ),
    ("2026000295", "03"): _PaymentRow(
        (8,),
        (500, 1630, 2130),
        (
            _d(7, "계약시", "계약 시"),
            _d(7, "30일 이내", "계약 후 30일 이내"),
            _d(7, "입주지정일", "입주지정일"),
        ),
        (4260,) * 6,
        12780,
    ),
    ("2026000327", "01"): _PaymentRow(
        (6,),
        (1000, 3570),
        (_d(6, "계약시", "계약 시"), _d(6, "1개월 이내", "계약 후 1개월 이내")),
        (4570,) * 6,
        13710,
    ),
    ("2026000327", "02"): _PaymentRow(
        (7,),
        (1000, 4870),
        (_d(6, "계약시", "계약 시"), _d(6, "1개월 이내", "계약 후 1개월 이내")),
        (5870,) * 6,
        17610,
    ),
    ("2026000327", "03"): _PaymentRow(
        (7,),
        (1000, 5460),
        (_d(6, "계약시", "계약 시"), _d(6, "1개월 이내", "계약 후 1개월 이내")),
        (6460,) * 6,
        19380,
    ),
    ("2026000327", "04"): _PaymentRow(
        (7,),
        (1000, 5550),
        (_d(6, "계약시", "계약 시"), _d(6, "1개월 이내", "계약 후 1개월 이내")),
        (6550,) * 6,
        19650,
    ),
    ("2026000358", "03"): _PaymentRow(
        (7,),
        (5000, 13850),
        (_d(6, "계약 시", "계약 시"), _d(6, "15일 이내", "계약 후 15일 이내")),
        (18850,) * 6,
        56550,
    ),
    ("2026000358", "04"): _PaymentRow(
        (7,),
        (5000, 19550),
        (_d(6, "계약 시", "계약 시"), _d(6, "15일 이내", "계약 후 15일 이내")),
        (24550,) * 6,
        73650,
    ),
    ("2026000358", "05"): _PaymentRow(
        (7,),
        (5000, 17950),
        (_d(6, "계약 시", "계약 시"), _d(6, "15일 이내", "계약 후 15일 이내")),
        (22950,) * 6,
        68850,
    ),
    ("2026000382", "05"): _PaymentRow(
        (10,),
        (1000, 4910),
        (_d(9, "계약 시", "계약 시"), _dated(9, "2026.10.07.", 2026, 10, 7)),
        (11820,) * 6,
        41370,
    ),
    ("2026000382", "06"): _PaymentRow(
        (10,),
        (1000, 4615),
        (_d(9, "계약 시", "계약 시"), _dated(9, "2026.10.07.", 2026, 10, 7)),
        (11230,) * 6,
        39305,
    ),
}

_BALCONY_0327: dict[str, tuple[int, int, int, int]] = {
    "01": (1400, 140, 280, 980),
    "02": (1700, 170, 340, 1190),
    "03": (1850, 185, 370, 1295),
    "04": (1850, 185, 370, 1295),
}
_AIRCON_0327: dict[str, int | None] = {"01": 610, "02": 620, "03": None, "04": 630}
_BALCONY_0377: dict[str, tuple[int, int, int]] = {
    "01": (1450, 145, 1305),
    "02": (1600, 160, 1440),
    "03": (1800, 180, 1620),
    "04": (2000, 200, 1800),
}


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def _won_values(raw_text: str) -> list[int]:
    return [int(value.replace(",", "")) for value in re.findall(r"\d[\d,]{3,}", raw_text)]


def _page(pages: list[PdfPage], number: int) -> PdfPage:
    page = next((item for item in pages if item.number == number), None)
    if page is None:
        raise AuditedRepairError(f"감사 근거 PDF {number}쪽이 없습니다.")
    return page


def _evidence(pages: list[PdfPage], field: str, page_number: int, quote: str) -> Evidence:
    page = _page(pages, page_number)
    if _normalized(quote) not in _normalized(page.text):
        raise AuditedRepairError(f"{field}: PDF {page_number}쪽에서 감사 근거를 찾지 못했습니다.")
    return Evidence(field=field, page=page_number, raw_text=quote)


def _contains_subsequence(values: list[int], wanted: list[int]) -> bool:
    return any(values[index : index + len(wanted)] == wanted for index in range(len(values)))


def _row_evidence(
    pages: list[PdfPage],
    *,
    field: str,
    page_numbers: tuple[int, ...],
    wanted_manwon: tuple[int, ...],
    required_text: str | None = None,
) -> Evidence:
    wanted_won = [value * 10_000 for value in wanted_manwon]
    matches: list[tuple[int, str]] = []
    for page_number in page_numbers:
        page = _page(pages, page_number)
        for line in page.text.splitlines():
            if _contains_subsequence(_won_values(line), wanted_won) and (
                required_text is None or _normalized(required_text) in _normalized(line)
            ):
                matches.append((page_number, line.strip()))
    if not matches:
        raise AuditedRepairError(f"{field}: exact 금액 행을 PDF에서 찾지 못했습니다.")
    # More than one floor may have the same selected price. This is safe only
    # when every matched row proves exactly the same obligation vector.
    if any(
        not _contains_subsequence(_won_values(raw_text), wanted_won)
        for _page_number, raw_text in matches
    ):
        raise AuditedRepairError(f"{field}: 서로 다른 금액 행이 충돌합니다.")
    page_number, raw_text = matches[0]
    return Evidence(field=field, page=page_number, raw_text=raw_text)


def _remove_evidence(result: AnalysisResponse, prefixes: tuple[str, ...]) -> None:
    result.evidence = [
        item
        for item in result.evidence
        if not any(item.field.startswith(prefix) for prefix in prefixes)
    ]


def _repair_payment_row(
    result: AnalysisResponse,
    pages: list[PdfPage],
    spec: _PaymentRow,
) -> None:
    sale_price = result.target_unit.sale_price_manwon
    if sale_price is None:
        raise AuditedRepairError("선택 분양가 없이 exact 납부행을 교정할 수 없습니다.")
    if len(spec.down_amounts) != len(spec.down_dues):
        raise AssertionError("감사 계약금 금액과 납부시점 수가 다릅니다.")
    interim = result.payment_schedule.interim_payment
    if len(interim.installments) != len(spec.interim_amounts) or any(
        item.due_date is None and item.due_text is None for item in interim.installments
    ):
        raise AuditedRepairError("검증된 중도금 납부일정과 자동 초안의 회차가 다릅니다.")

    row_values = (
        sale_price,
        *spec.down_amounts,
        *spec.interim_amounts,
        spec.balance_amount,
    )
    row = _row_evidence(
        pages,
        field="/payment_schedule/down_payment",
        page_numbers=spec.page_numbers,
        wanted_manwon=row_values,
    )
    # Replace only the exact amount-row evidence and the rebuilt down-payment
    # installment evidence.  The original, source-checked interim due dates and
    # balance due metadata must survive this repair.
    replaced_fields = {
        "/payment_schedule/down_payment",
        "/payment_schedule/interim_payment",
        "/payment_schedule/balance_payment",
    }
    result.evidence = [
        item
        for item in result.evidence
        if item.field not in replaced_fields
        and not item.field.startswith("/payment_schedule/down_payment/installments/")
    ]
    schedule = result.payment_schedule
    schedule.down_payment.total_amount_manwon = sum(spec.down_amounts)
    schedule.down_payment.total_ratio = sum(spec.down_amounts) / sale_price
    schedule.down_payment.basis = PaymentBasis.MIXED
    schedule.down_payment.due_date = None
    schedule.down_payment.due_month = None
    schedule.down_payment.due_text = None
    schedule.down_payment.installments = [
        Installment(
            number=index,
            ratio=None,
            amount_manwon=amount,
            due_date=due.due_date,
            due_text=due.due_text,
        )
        for index, (amount, due) in enumerate(
            zip(spec.down_amounts, spec.down_dues, strict=True), start=1
        )
    ]
    interim.total_amount_manwon = sum(spec.interim_amounts)
    interim.total_ratio = sum(spec.interim_amounts) / sale_price
    interim.basis = PaymentBasis.MIXED
    for installment, amount in zip(interim.installments, spec.interim_amounts, strict=True):
        installment.amount_manwon = amount
        installment.ratio = amount / sale_price
    balance = schedule.balance_payment
    balance.total_amount_manwon = spec.balance_amount
    balance.total_ratio = spec.balance_amount / sale_price
    balance.basis = PaymentBasis.MIXED

    for field in (
        "/payment_schedule/down_payment",
        "/payment_schedule/interim_payment",
        "/payment_schedule/balance_payment",
    ):
        result.evidence.append(row.model_copy(update={"field": field}))
    for index, due in enumerate(spec.down_dues):
        due_field = "due_date" if due.due_date is not None else "due_text"
        result.evidence.append(
            _evidence(
                pages,
                f"/payment_schedule/down_payment/installments/{index}/{due_field}",
                due.page,
                due.raw_text,
            )
        )


def _repair_0291_due(result: AnalysisResponse, pages: list[PdfPage]) -> None:
    installments = result.payment_schedule.down_payment.installments
    if len(installments) != 2:
        raise AuditedRepairError("2026000291 계약금 2회차 구조가 아닙니다.")
    installments[1].due_date = None
    installments[1].due_text = "계약 후 1개월 이내"
    result.evidence.append(
        _evidence(
            pages,
            "/payment_schedule/down_payment/installments/1/due_text",
            8,
            "1개월이내",
        )
    )


def _repair_0382_due_dates(result: AnalysisResponse, pages: list[PdfPage]) -> None:
    installments = result.payment_schedule.down_payment.installments
    if len(installments) != 2:
        raise AuditedRepairError("2026000382 계약금 2회차 구조가 아닙니다.")
    installments[1].due_date = date(2026, 10, 7)
    installments[1].due_text = None
    result.evidence.append(
        _evidence(
            pages,
            "/payment_schedule/down_payment/installments/1/due_date",
            9,
            "2026.10.07.",
        )
    )


def _repair_0358_direct_funding(result: AnalysisResponse, pages: list[PdfPage]) -> None:
    quote = (
        "총 공급대금의 60% 중 총 공급대금의 40% 범위 내에서 대출 알선이 가능하며 "
        "나머지 총 공급대금의 20%는 계약자가 직접 납부"
    )
    loan = result.interim_loan
    loan.arranged_ratio = 0.4
    loan.self_funding_ratio = 0.2
    loan.self_funding_origin = ValueOrigin.EXTRACTED
    _remove_evidence(result, ("/interim_loan/self_funding_ratio",))
    result.evidence.append(_evidence(pages, "/interim_loan/self_funding_ratio", 37, quote))


def _repair_0327_costs(result: AnalysisResponse, pages: list[PdfPage]) -> None:
    unit_id = result.target_unit.unit_type_id or ""
    unit_name = result.target_unit.unit_type_name
    if unit_name is None or unit_id not in _BALCONY_0327:
        raise AuditedRepairError("2026000327 감사 대상 주택형이 아닙니다.")
    total, contract, interim, balance = _BALCONY_0327[unit_id]
    mandatory_quote = (
        "기존 조합원이 발코니 확장을 선택하여 계약체결된 건이므로 금회 본 아파트를 "
        "계약하시는 분은 반드시 발코니 확장을 계약하는 조건으로만 청약이 가능합니다."
    )
    not_included = "상기 공급금액에는 발코니 확장 비용이 미포함되어 있습니다"
    balcony = AdditionalCost(
        type=AdditionalCostType.BALCONY_EXTENSION,
        name="발코니 확장 공사비",
        total_amount_manwon=total,
        required=True,
        included_in_sale_price=False,
        applicable_unit_type=unit_name,
        payments=[
            AdditionalCostPayment(
                number=1,
                stage=PaymentStage.CONTRACT,
                amount_manwon=contract,
                due_date=None,
                due_text="계약시",
            ),
            AdditionalCostPayment(
                number=2,
                stage=PaymentStage.INTERIM,
                amount_manwon=interim,
                due_date=date(2026, 9, 30),
                due_text=None,
            ),
            AdditionalCostPayment(
                number=3,
                stage=PaymentStage.BALANCE,
                amount_manwon=balance,
                due_date=None,
                due_text="입주지정일",
            ),
        ],
        note="필수 계약 · 기존 계약 승계 · 공급금액 미포함",
    )
    costs = [balcony]
    aircon_amount = _AIRCON_0327[unit_id]
    if aircon_amount is not None:
        costs.append(
            AdditionalCost(
                type=AdditionalCostType.SYSTEM_AIR_CONDITIONER,
                name="기설치 시스템 에어컨",
                total_amount_manwon=aircon_amount,
                required=True,
                included_in_sale_price=False,
                applicable_unit_type=unit_name,
                payments=[],
                note="기설치 유상옵션 · 기존 계약 승계 · 납부시점 별도 확인",
            )
        )
    result.additional_costs = costs
    _remove_evidence(result, ("/additional_costs/",))
    balcony_row = _row_evidence(
        pages,
        field="/additional_costs/0",
        page_numbers=(22,),
        wanted_manwon=(total, contract, interim, balance),
        required_text=unit_name,
    )
    result.evidence.extend(
        [
            balcony_row,
            _evidence(
                pages,
                "/additional_costs/0/payments",
                22,
                "계약시(10%) 중도금(20%) 잔금(70%) 약식표기 공급금액 계약시 2026.09.30. 입주지정일",
            ),
            _evidence(pages, "/additional_costs/0/required", 22, mandatory_quote),
            _evidence(pages, "/additional_costs/0/included_in_sale_price", 7, not_included),
            _evidence(pages, "/additional_costs/0/note", 22, mandatory_quote),
        ]
    )
    if aircon_amount is not None:
        installed_quote = (
            "일부 유상옵션이 설치되어 있으므로 유상옵션이 설치된 상태로 공급받아야 하며, "
            "유상옵션 대금은 분양대금과 별도로 납부하여야 합니다."
        )
        result.evidence.extend(
            [
                _row_evidence(
                    pages,
                    field="/additional_costs/1",
                    page_numbers=(7,),
                    wanted_manwon=(aircon_amount, aircon_amount),
                    required_text=unit_name,
                ),
                _evidence(pages, "/additional_costs/1/required", 7, installed_quote),
                _evidence(
                    pages,
                    "/additional_costs/1/included_in_sale_price",
                    7,
                    installed_quote,
                ),
                _evidence(pages, "/additional_costs/1/note", 7, installed_quote),
            ]
        )
    result.interim_loan.interest_note = None
    _remove_evidence(result, ("/interim_loan/interest_note",))


def _repair_0377_cost(result: AnalysisResponse, pages: list[PdfPage]) -> None:
    unit_id = result.target_unit.unit_type_id or ""
    unit_name = result.target_unit.unit_type_name
    if unit_name is None or unit_id not in _BALCONY_0377:
        raise AuditedRepairError("2026000377 감사 대상 주택형이 아닙니다.")
    total, contract, balance = _BALCONY_0377[unit_id]
    result.additional_costs = [
        AdditionalCost(
            type=AdditionalCostType.BALCONY_EXTENSION,
            name="발코니 확장",
            total_amount_manwon=total,
            required=False,
            included_in_sale_price=False,
            applicable_unit_type=unit_name,
            payments=[
                AdditionalCostPayment(
                    number=1,
                    stage=PaymentStage.CONTRACT,
                    amount_manwon=contract,
                    due_date=None,
                    due_text="계약 시",
                ),
                AdditionalCostPayment(
                    number=2,
                    stage=PaymentStage.BALANCE,
                    amount_manwon=balance,
                    due_date=None,
                    due_text="입주지정기간",
                ),
            ],
            note="선택사항 · 공급금액 미포함; 전체 유상옵션 범위는 별도 확인",
        )
    ]
    _remove_evidence(result, ("/additional_costs/",))
    not_included = "상기 공급금액에는 발코니 확장 비용, 추가선택 품목 비용이 미포함 되었으며"
    optional = "주택 분양계약 체결 시 별도계약을 통해 선택이 가능합니다"
    result.evidence.extend(
        [
            _row_evidence(
                pages,
                field="/additional_costs/0",
                page_numbers=(45,),
                wanted_manwon=(total, contract, balance),
                required_text=unit_name,
            ),
            _evidence(pages, "/additional_costs/0/required", 9, optional),
            _evidence(pages, "/additional_costs/0/included_in_sale_price", 9, not_included),
            _evidence(pages, "/additional_costs/0/note", 9, optional),
        ]
    )


def apply_audited_repairs(
    result: AnalysisResponse,
    *,
    pages: list[PdfPage],
) -> tuple[list[str], bool]:
    """Apply only source-proven corrections; never grants REVIEWED status.

    Returns ``(actions, handles_additional_costs)``. The latter tells the
    caller not to apply the older balcony-only filter to complex 0327/0377.
    """

    actions: list[str] = []
    unit_id = result.target_unit.unit_type_id or ""
    payment_spec = _PAYMENT_ROWS.get((result.complex_id, unit_id))
    if payment_spec is not None:
        _repair_payment_row(result, pages, payment_spec)
        actions.append("GROUND_EXACT_PAYMENT_ROW")
    if result.complex_id == "2026000291":
        _repair_0291_due(result, pages)
        actions.append("CORRECT_SECOND_DOWN_PAYMENT_DUE")
    if result.complex_id == "2026000382":
        _repair_0382_due_dates(result, pages)
        actions.append("CORRECT_SECOND_DOWN_PAYMENT_DATE")
    if result.complex_id == "2026000358":
        _repair_0358_direct_funding(result, pages)
        actions.append("GROUND_EXPLICIT_DIRECT_20_PERCENT")
    if result.complex_id == "2026000327":
        _repair_0327_costs(result, pages)
        actions.append("GROUND_MANDATORY_INHERITED_OPTIONS")
        return actions, True
    if result.complex_id == "2026000377":
        _repair_0377_cost(result, pages)
        actions.append("REPLACE_MISCLASSIFIED_COST_WITH_BALCONY")
        return actions, True
    return actions, False
