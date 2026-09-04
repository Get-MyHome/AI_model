from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from get_myhome_ai.candidates import CandidatePage
from get_myhome_ai.holds import derive_analysis_status, derive_holds
from get_myhome_ai.models import (
    AdditionalCost,
    AdditionalCostPayment,
    AdditionalCostType,
    AnalysisResponse,
    Evidence,
    ExtractionDraft,
    Installment,
    InterestType,
    LoanArrangementStatus,
    PaymentBasis,
    PaymentComponent,
    PaymentStage,
    ReviewStatus,
)
from get_myhome_ai.normalization import normalize_draft
from get_myhome_ai.pdf_text import PdfPage
from get_myhome_ai.providers.ollama_grounding import reground_review_metadata
from get_myhome_ai.summary import build_analysis_summary
from get_myhome_ai.validation import validate_draft


class ExpandedAuditedRepairError(ValueError):
    """Raised when an audited correction is not proven by its exact locked PDF."""


@dataclass(frozen=True)
class _Due:
    due_date: date | None = None
    due_text: str | None = None


@dataclass(frozen=True)
class _PaymentSpec:
    page: int
    down_won: tuple[int, ...]
    interim_won: tuple[int, ...]
    balance_won: int
    exact_nominal_ratios: bool = True


@dataclass(frozen=True)
class _TargetPolicy:
    unit_name: str
    sale_price_manwon: int
    payment: _PaymentSpec
    balcony_total_manwon: int | None
    balcony_variant_ambiguous: bool = False


@dataclass(frozen=True)
class _DocumentPolicy:
    source_sha256: str
    source_page_count: int
    payment_header_page: int
    payment_header_quote: str
    move_in_page: int
    move_in_quote: str
    move_in_month: str
    down_dues: tuple[_Due, ...]
    interim_dates: tuple[date, ...]
    balance_due_text: str
    loan_page: int
    loan_quote: str
    prepay_quote: str
    interest_note: str
    targets: dict[str, _TargetPolicy]


def _mw(value: int) -> int:
    return value * 10_000


_DATES_0282 = (
    date(2027, 2, 1),
    date(2027, 7, 1),
    date(2028, 1, 3),
    date(2028, 7, 3),
    date(2028, 11, 1),
    date(2029, 3, 2),
)
_DATES_031X = (
    date(2027, 3, 10),
    date(2027, 8, 10),
    date(2028, 2, 10),
    date(2028, 9, 11),
    date(2029, 1, 10),
    date(2029, 5, 10),
)
_DATES_0323 = (
    date(2027, 1, 15),
    date(2027, 5, 17),
    date(2027, 9, 17),
    date(2028, 3, 15),
    date(2028, 7, 18),
    date(2028, 12, 15),
)


def _payment(
    page: int,
    price: int,
    *,
    down: tuple[int, ...],
    interim_each: int,
    balance: int,
    exact_nominal_ratios: bool = True,
) -> _PaymentSpec:
    return _PaymentSpec(
        page=page,
        down_won=tuple(_mw(value) for value in down),
        interim_won=(_mw(interim_each),) * 6,
        balance_won=_mw(balance),
        exact_nominal_ratios=exact_nominal_ratios,
    )


def _payment_won(
    page: int,
    *,
    down_won: tuple[int, ...],
    interim_won: tuple[int, ...],
    balance_won: int,
) -> _PaymentSpec:
    return _PaymentSpec(
        page=page,
        down_won=down_won,
        interim_won=interim_won,
        balance_won=balance_won,
    )


_TARGETS_0282 = {
    "01": _TargetPolicy(
        "38A", 27_100, _payment(6, 27_100, down=(1000, 355), interim_each=2710, balance=9485), None
    ),
    "02": _TargetPolicy(
        "46A",
        33_990,
        _payment_won(
            6,
            down_won=(_mw(1000), 6_995_000),
            interim_won=(_mw(3399),) * 6,
            balance_won=118_965_000,
        ),
        1100,
    ),
    "03": _TargetPolicy(
        "59A",
        43_600,
        _payment(6, 43_600, down=(1000, 1180), interim_each=4360, balance=15_260),
        1410,
    ),
    "04": _TargetPolicy(
        "74A",
        53_080,
        _payment(7, 53_080, down=(1000, 1654), interim_each=5308, balance=18_578),
        1740,
    ),
    "05": _TargetPolicy(
        "74B",
        52_080,
        _payment(7, 52_080, down=(1000, 1604), interim_each=5208, balance=18_228),
        1740,
    ),
    "06": _TargetPolicy(
        "84A",
        61_150,
        _payment_won(
            7,
            down_won=(_mw(1000), 20_575_000),
            interim_won=(_mw(6115),) * 6,
            balance_won=214_025_000,
        ),
        2000,
    ),
    "07": _TargetPolicy(
        "84B",
        58_100,
        _payment(7, 58_100, down=(1000, 1905), interim_each=5810, balance=20_335),
        2000,
    ),
    "08": _TargetPolicy(
        "84C",
        60_840,
        _payment(8, 60_840, down=(1000, 2042), interim_each=6084, balance=21_294),
        2000,
    ),
    "09": _TargetPolicy(
        "101A",
        72_080,
        _payment(8, 72_080, down=(1000, 2604), interim_each=7208, balance=25_228),
        2360,
    ),
    "10": _TargetPolicy(
        "101B",
        71_320,
        _payment(8, 71_320, down=(1000, 2566), interim_each=7132, balance=24_962),
        2360,
    ),
    "11": _TargetPolicy(
        "136A",
        150_340,
        _payment(8, 150_340, down=(1000, 6517), interim_each=15_034, balance=52_619),
        3220,
    ),
    "12": _TargetPolicy(
        "136B",
        150_050,
        _payment_won(
            8,
            down_won=(_mw(1000), 65_025_000),
            interim_won=(_mw(15_005),) * 6,
            balance_won=525_175_000,
        ),
        3220,
    ),
}


def _targets_031x(*, include_84d: bool) -> dict[str, _TargetPolicy]:
    rows = {
        "01": ("75", 73_380, 1770),
        "02": ("84A", 78_300, 1990),
        "03": ("84B", 78_900, 2040),
        "04": ("84C", 79_400, 2500),
        "05": ("84D", 79_300, 2470),
        "06": ("102", 90_710, 2690),
        "07": ("124", 106_300, 3100),
        "08": ("166P", 207_730, 4090),
    }
    if not include_84d:
        rows = {
            "01": rows["01"],
            "02": rows["02"],
            "03": rows["03"],
            "04": rows["04"],
            "05": rows["06"],
            "06": rows["07"],
            "07": rows["08"],
        }
    return {
        unit_id: _TargetPolicy(
            unit_name,
            price,
            _payment(
                7,
                price,
                down=(1000, price // 10 - 1000),
                interim_each=price // 10,
                balance=price * 3 // 10,
            ),
            balcony,
        )
        for unit_id, (unit_name, price, balcony) in rows.items()
    }


_TARGETS_0323 = {
    "01": _TargetPolicy(
        "45", 29_500, _payment(7, 29_500, down=(2950,), interim_each=2950, balance=8850), 345
    ),
    "02": _TargetPolicy(
        "59",
        43_550,
        _payment(
            8,
            43_550,
            down=(4355,),
            interim_each=4350,
            balance=13_095,
            exact_nominal_ratios=False,
        ),
        763,
    ),
    "03": _TargetPolicy(
        "84A",
        64_560,
        _payment(
            8,
            64_560,
            down=(6456,),
            interim_each=6450,
            balance=19_404,
            exact_nominal_ratios=False,
        ),
        682,
    ),
    "04": _TargetPolicy(
        "84B",
        64_560,
        _payment(
            8,
            64_560,
            down=(6456,),
            interim_each=6450,
            balance=19_404,
            exact_nominal_ratios=False,
        ),
        686,
    ),
    "05": _TargetPolicy(
        "84C",
        63_600,
        _payment(8, 63_600, down=(6360,), interim_each=6360, balance=19_080),
        None,
        True,
    ),
    "06": _TargetPolicy(
        "84D", 62_500, _payment(8, 62_500, down=(6250,), interim_each=6250, balance=18_750), 1122
    ),
    "07": _TargetPolicy(
        "84E",
        61_570,
        _payment(
            9,
            61_570,
            down=(6157,),
            interim_each=6150,
            balance=18_513,
            exact_nominal_ratios=False,
        ),
        1179,
    ),
    "08": _TargetPolicy(
        "84F",
        64_560,
        _payment(
            9,
            64_560,
            down=(6456,),
            interim_each=6450,
            balance=19_404,
            exact_nominal_ratios=False,
        ),
        711,
    ),
}


_POLICIES = {
    "2026000282": _DocumentPolicy(
        source_sha256="a0a199d1a63c68d8a9439483decac6f7f0af7a95e4c86eaeab18dbe2009ca9dd",
        source_page_count=75,
        payment_header_page=6,
        payment_header_quote=(
            "계약일로부터 (계약시) 30일이내 2027-02-01 2027-07-01 2028-01-03 "
            "2028-07-03 2028-11-01 2029-03-02"
        ),
        move_in_page=5,
        move_in_quote="입주시기 : 2029년 08월 예정",
        move_in_month="2029-08",
        down_dues=(_Due(due_text="계약 시"), _Due(due_text="계약일로부터 30일 이내")),
        interim_dates=_DATES_0282,
        balance_due_text="입주지정일",
        loan_page=39,
        loan_quote=(
            "중도금 대출에 대한 이자는 “중도금 무이자” 조건으로 "
            "전체 공급대금의 60% 범위 내에서 시행할 예정"
        ),
        prepay_quote="분양대금의 총 5% 완납 후",
        interest_note="무이자 표기이나 사업주체 대납이자를 입주 시 일시 납부",
        targets=_TARGETS_0282,
    ),
    "2026000315": _DocumentPolicy(
        source_sha256="987b7d52d38d5b981f28275b318d289ff5580f9f57f957685c3497d44293df35",
        source_page_count=63,
        payment_header_page=7,
        payment_header_quote=(
            "계약 시 계약 후 30일 이내 2027.03.10 2027.08.10 2028.02.10 "
            "2028.09.11 2029.01.10 2029.05.10"
        ),
        move_in_page=6,
        move_in_quote="입주시기 : 2029년 09월 예정",
        move_in_month="2029-09",
        down_dues=(_Due(due_text="계약 시"), _Due(due_text="계약 후 30일 이내")),
        interim_dates=_DATES_031X,
        balance_due_text="입주지정기간",
        loan_page=36,
        loan_quote=(
            "중도금 대출에 대한 이자는 “중도금 대출 이자후불제” 조건으로 "
            "융자 알선을 시행할 예정이며, 총 공급대금의 60% 범위 내"
        ),
        prepay_quote="분양대금의 10%(1차, 2차 계약금) 완납 이후 중도금 대출이 가능",
        interest_note="중도금 대출 이자후불제",
        targets=_targets_031x(include_84d=True),
    ),
    "2026000316": _DocumentPolicy(
        source_sha256="0d0abe61cbc0e21de5264309548607e8d4563f1dfa216c839ef25bba415c9e0d",
        source_page_count=63,
        payment_header_page=7,
        payment_header_quote=(
            "계약 시 계약 후 30일 이내 2027.03.10 2027.08.10 2028.02.10 "
            "2028.09.11 2029.01.10 2029.05.10"
        ),
        move_in_page=6,
        move_in_quote="입주시기 : 2029년 09월 예정",
        move_in_month="2029-09",
        down_dues=(_Due(due_text="계약 시"), _Due(due_text="계약 후 30일 이내")),
        interim_dates=_DATES_031X,
        balance_due_text="입주지정기간",
        loan_page=36,
        loan_quote=(
            "중도금 대출에 대한 이자는 “중도금 대출 이자후불제” 조건으로 "
            "융자 알선을 시행할 예정이며, 총 공급대금의 60% 범위 내"
        ),
        prepay_quote="분양대금의 10%(1차, 2차 계약금) 완납 이후 중도금 대출이 가능",
        interest_note="중도금 대출 이자후불제",
        targets=_targets_031x(include_84d=False),
    ),
    "2026000323": _DocumentPolicy(
        source_sha256="047136ffa1909cfde73fda47a55e6d1a9ad56819fc57a76397ecfc48785b83e1",
        source_page_count=77,
        payment_header_page=7,
        payment_header_quote=(
            "계약 시 2027.01.15. 2027.05.17. 2027.09.17. 2028.03.15. "
            "2028.07.18. 2028.12.15. 입주 시"
        ),
        move_in_page=6,
        move_in_quote="입주시기 : 2029년 3월 예정",
        move_in_month="2029-03",
        down_dues=(_Due(due_text="계약 시"),),
        interim_dates=_DATES_0323,
        balance_due_text="입주 시",
        loan_page=39,
        loan_quote=(
            "중도금대출 알선조건은 ‘중도금 대출 이자후불제’ 조건이며, 총 공급대금에 60%범위 내에서"  # noqa: RUF001
        ),
        prepay_quote="공급대금의 10% 이상 납부 이후 대출이 가능",
        interest_note="중도금 대출 이자후불제",
        targets=_TARGETS_0323,
    ),
}

EXPANDED_AUDITED_TARGET_COUNT_A = sum(len(policy.targets) for policy in _POLICIES.values())


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def _won_values(value: str) -> list[int]:
    return [int(item.replace(",", "")) for item in re.findall(r"\d[\d,]{3,}", value)]


def _contains_subsequence(values: list[int], wanted: list[int]) -> bool:
    return any(values[index : index + len(wanted)] == wanted for index in range(len(values)))


def _page(pages: list[PdfPage], number: int) -> PdfPage:
    matches = [page for page in pages if page.number == number]
    if len(matches) != 1:
        raise ExpandedAuditedRepairError(f"PDF {number}쪽을 유일하게 확인할 수 없습니다.")
    return matches[0]


def _evidence(pages: list[PdfPage], *, field: str, page: int, quote: str) -> Evidence:
    source = _page(pages, page)
    if _normalized(quote) not in _normalized(source.text):
        raise ExpandedAuditedRepairError(
            f"{field}: PDF {page}쪽에서 감사 근거를 확인하지 못했습니다."
        )
    return Evidence(field=field, page=page, raw_text=quote)


def _row_evidence(
    pages: list[PdfPage],
    *,
    field: str,
    page: int,
    wanted_won: tuple[int, ...],
) -> Evidence:
    matches = [
        line.strip()
        for line in _page(pages, page).text.splitlines()
        if _contains_subsequence(_won_values(line), list(wanted_won))
    ]
    if not matches:
        raise ExpandedAuditedRepairError(f"{field}: PDF {page}쪽의 exact 금액 행이 없습니다.")
    if any(not _contains_subsequence(_won_values(line), list(wanted_won)) for line in matches):
        raise ExpandedAuditedRepairError(f"{field}: exact 금액 행이 충돌합니다.")
    return Evidence(field=field, page=page, raw_text=matches[0])


def _exact_manwon(value_won: int) -> int | None:
    return value_won // 10_000 if value_won % 10_000 == 0 else None


def _basis(*, ratio: float | None, amount: int | None) -> PaymentBasis:
    if ratio is not None and amount is not None:
        return PaymentBasis.MIXED
    if ratio is not None:
        return PaymentBasis.RATIO
    if amount is not None:
        return PaymentBasis.FIXED_AMOUNT
    return PaymentBasis.UNKNOWN


def _component(
    *,
    values_won: tuple[int, ...],
    price_won: int,
    dues: tuple[_Due, ...],
    keep_ratios: bool,
    due_month: str | None = None,
    due_text: str | None = None,
) -> PaymentComponent:
    if len(values_won) != len(dues):
        raise AssertionError("감사 금액과 납부시점 수가 다릅니다.")
    total_won = sum(values_won)
    total_ratio = total_won / price_won if keep_ratios else None
    total_amount = _exact_manwon(total_won)
    return PaymentComponent(
        total_ratio=total_ratio,
        total_amount_manwon=total_amount,
        basis=_basis(ratio=total_ratio, amount=total_amount),
        installments=[
            Installment(
                number=index,
                ratio=value / price_won if keep_ratios else None,
                amount_manwon=_exact_manwon(value),
                due_date=due.due_date,
                due_text=due.due_text,
            )
            for index, (value, due) in enumerate(zip(values_won, dues, strict=True), start=1)
        ],
        due_date=None,
        due_month=due_month,
        due_text=due_text,
    )


def _policy_for(
    result: AnalysisResponse, pages: list[PdfPage]
) -> tuple[_DocumentPolicy, _TargetPolicy] | None:
    policy = _POLICIES.get(result.complex_id)
    if policy is None:
        return None
    if result.review_status == ReviewStatus.REVIEWED or result.reviewer or result.reviewed_at:
        raise ExpandedAuditedRepairError("REVIEWED 결과는 자동 교정 입력으로 사용할 수 없습니다.")
    target = policy.targets.get(result.target_unit.unit_type_id or "")
    if target is None or (
        result.target_unit.unit_type_name != target.unit_name
        or result.target_unit.sale_price_manwon != target.sale_price_manwon
    ):
        raise ExpandedAuditedRepairError(
            "감사 대상 complex의 exact unit/name/price tuple과 다릅니다."
        )
    if (
        result.meta.source_sha256 != policy.source_sha256
        or result.meta.source_page_count != policy.source_page_count
        or len(pages) != policy.source_page_count
        or {page.number for page in pages} != set(range(1, policy.source_page_count + 1))
    ):
        raise ExpandedAuditedRepairError("감사한 PDF source lock과 다릅니다.")
    return policy, target


def _replace_evidence(result: AnalysisResponse) -> None:
    prefixes = ("/payment_schedule/", "/interim_loan/", "/additional_costs/", "/exception_flags")
    result.evidence = [
        item
        for item in result.evidence
        if not any(item.field.startswith(prefix) for prefix in prefixes)
    ]
    result.risk_clauses = []


def _repair_payments(
    result: AnalysisResponse,
    pages: list[PdfPage],
    policy: _DocumentPolicy,
    target: _TargetPolicy,
) -> None:
    spec = target.payment
    price_won = _mw(target.sale_price_manwon)
    wanted = (price_won, *spec.down_won, *spec.interim_won, spec.balance_won)
    row = _row_evidence(
        pages,
        field="/payment_schedule/down_payment",
        page=spec.page,
        wanted_won=wanted,
    )
    header = _evidence(
        pages,
        field="/payment_schedule/interim_payment/installments",
        page=policy.payment_header_page if result.complex_id != "2026000323" else spec.page,
        quote=policy.payment_header_quote,
    )
    move_in = _evidence(
        pages,
        field="/payment_schedule/balance_payment/due_month",
        page=policy.move_in_page,
        quote=policy.move_in_quote,
    )
    interim_dues = tuple(_Due(due_date=value) for value in policy.interim_dates)
    balance_dues = (_Due(due_text=policy.balance_due_text),)
    schedule = result.payment_schedule
    schedule.down_payment = _component(
        values_won=spec.down_won,
        price_won=price_won,
        dues=policy.down_dues,
        keep_ratios=True,
    )
    schedule.interim_payment = _component(
        values_won=spec.interim_won,
        price_won=price_won,
        dues=interim_dues,
        keep_ratios=spec.exact_nominal_ratios,
    )
    schedule.balance_payment = _component(
        values_won=(spec.balance_won,),
        price_won=price_won,
        dues=balance_dues,
        keep_ratios=spec.exact_nominal_ratios,
        due_month=policy.move_in_month,
        due_text=policy.balance_due_text,
    )
    for field in (
        "/payment_schedule/down_payment",
        "/payment_schedule/interim_payment",
        "/payment_schedule/balance_payment",
    ):
        result.evidence.append(row.model_copy(update={"field": field}))
    for index, _due in enumerate(policy.down_dues):
        result.evidence.append(
            _evidence(
                pages,
                field=f"/payment_schedule/down_payment/installments/{index}/due_text",
                page=policy.payment_header_page if result.complex_id != "2026000323" else spec.page,
                quote=policy.payment_header_quote,
            )
        )
    result.evidence.append(header)
    for index in range(len(policy.interim_dates)):
        result.evidence.append(
            header.model_copy(
                update={"field": f"/payment_schedule/interim_payment/installments/{index}/due_date"}
            )
        )
    result.evidence.extend(
        [
            header.model_copy(update={"field": "/payment_schedule/balance_payment/due_text"}),
            move_in,
        ]
    )


def _repair_loan(
    result: AnalysisResponse,
    pages: list[PdfPage],
    policy: _DocumentPolicy,
) -> None:
    loan = result.interim_loan
    loan.arrangement_status = LoanArrangementStatus.PLANNED
    loan.arranged_ratio = 0.60
    loan.arranged_amount_manwon = None
    loan.self_funding_ratio = None
    loan.self_funding_amount_manwon = None
    loan.self_funding_origin = None
    loan.bank_names = []
    loan.guarantee_provider = None
    loan.interest_type = InterestType.DEFERRED_INTEREST
    loan.interest_note = policy.interest_note
    loan.prepay_requirement_ratio = 0.05 if result.complex_id == "2026000282" else 0.10
    arrangement = _evidence(
        pages,
        field="/interim_loan/arrangement_status",
        page=policy.loan_page,
        quote=policy.loan_quote,
    )
    prepay = _evidence(
        pages,
        field="/interim_loan/prepay_requirement_ratio",
        page=policy.loan_page,
        quote=policy.prepay_quote,
    )
    result.evidence.extend(
        [
            arrangement,
            arrangement.model_copy(update={"field": "/interim_loan/arranged_ratio"}),
            arrangement.model_copy(update={"field": "/interim_loan/interest_type"}),
            arrangement.model_copy(update={"field": "/interim_loan/interest_note"}),
            prepay,
        ]
    )


def _repair_0282_cost(
    result: AnalysisResponse, pages: list[PdfPage], target: _TargetPolicy
) -> None:
    if target.balcony_total_manwon is None:
        result.additional_costs = [
            AdditionalCost(
                type=AdditionalCostType.BALCONY_EXTENSION,
                name="발코니 확장 공사비",
                total_amount_manwon=None,
                required=None,
                included_in_sale_price=False,
                applicable_unit_type=None,
                payments=[],
                note="선택 주택형 금액 미기재 · 전체 유상옵션 범위 별도 확인",
            )
        ]
        title = _evidence(
            pages,
            field="/additional_costs/0",
            page=42,
            quote="■ 발코니 확장 공사 금액",
        )
        result.evidence.extend(
            [
                title,
                title.model_copy(update={"field": "/additional_costs/0/included_in_sale_price"}),
                title.model_copy(update={"field": "/additional_costs/0/note"}),
            ]
        )
        return
    raw_names = {
        "02": "46.9070A",
        "03": "59.9783A",
        "04": "74.9730A",
        "05": "74.9705B",
        "06": "84.9692A",
        "07": "84.9734B",
        "08": "84.9704C",
        "09": "101.9695A",
        "10": "101.9772B",
        "11": "136.5534A",
        "12": "136.9291B",
    }
    unit_id = result.target_unit.unit_type_id or ""
    total = target.balcony_total_manwon
    contract = total // 10
    balance = total - contract
    raw_row = f"{raw_names[unit_id]} {_mw(total):,} {_mw(contract):,} {_mw(balance):,}"
    result.additional_costs = [
        AdditionalCost(
            type=AdditionalCostType.BALCONY_EXTENSION,
            name="발코니 확장 공사비",
            total_amount_manwon=total,
            required=False,
            included_in_sale_price=False,
            applicable_unit_type=target.unit_name,
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
                    due_text="입주지정일",
                ),
            ],
            note="선택사항 · 분양가 미포함; 전체 유상옵션 범위는 별도 확인",
        )
    ]
    row = _evidence(pages, field="/additional_costs/0", page=42, quote=raw_row)
    payment_header = _evidence(
        pages,
        field="/additional_costs/0/payments",
        page=42,
        quote="계약금(10%) 잔금(90%) 주택형 발코니 확장비 비고 계약시 입주지정일",
    )
    optional = _evidence(
        pages,
        field="/additional_costs/0/required",
        page=42,
        quote="비확장형 선택세대",
    )
    excluded = _evidence(
        pages,
        field="/additional_costs/0/included_in_sale_price",
        page=42,
        quote="확장비용은 분양가와 별도로",
    )
    result.evidence.extend(
        [
            row,
            payment_header,
            optional,
            excluded,
            row.model_copy(update={"field": "/additional_costs/0/applicable_unit_type"}),
            excluded.model_copy(update={"field": "/additional_costs/0/note"}),
        ]
    )


def _repair_031x_cost(
    result: AnalysisResponse,
    pages: list[PdfPage],
    target: _TargetPolicy,
) -> None:
    is_0315 = result.complex_id == "2026000315"
    units = (
        ("75", "84A", "84B", "84C", "84D", "102", "124", "166P")
        if is_0315
        else (
            "75",
            "84A",
            "84B",
            "84C",
            "102",
            "124",
            "166P",
        )
    )
    totals = (
        (1770, 1990, 2040, 2500, 2470, 2690, 3100, 4090)
        if is_0315
        else (
            1770,
            1990,
            2040,
            2500,
            2690,
            3100,
            4090,
        )
    )
    index = units.index(target.unit_name)
    total = totals[index]
    if total != target.balcony_total_manwon:
        raise AssertionError("감사 발코니 금액 매핑이 충돌합니다.")
    contract = total // 10
    interim = contract
    balance = total - contract - interim
    header_quote = "구분 (약식표기) " + " ".join(units)
    total_quote = "발코니 확장 금액 " + " ".join(f"{_mw(value):,}" for value in totals)
    payment_quote = " ".join(
        [
            "계약금 (10%) 계약 시 " + " ".join(f"{_mw(value // 10):,}" for value in totals),
            "중도금 (10%) 2027.03.10 " + " ".join(f"{_mw(value // 10):,}" for value in totals),
            "잔 금 (80%) 입주지정기간 "
            + " ".join(f"{_mw(value - 2 * (value // 10)):,}" for value in totals),
        ]
    )
    result.additional_costs = [
        AdditionalCost(
            type=AdditionalCostType.BALCONY_EXTENSION,
            name="발코니 확장 대금",
            total_amount_manwon=total,
            required=False,
            included_in_sale_price=False,
            applicable_unit_type=target.unit_name,
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
                    stage=PaymentStage.INTERIM,
                    amount_manwon=interim,
                    due_date=date(2027, 3, 10),
                    due_text=None,
                ),
                AdditionalCostPayment(
                    number=3,
                    stage=PaymentStage.BALANCE,
                    amount_manwon=balance,
                    due_date=None,
                    due_text="입주지정기간",
                ),
            ],
            note="선택사항 · 분양가 미포함; 전체 유상옵션 범위는 별도 확인",
        )
    ]
    header = _evidence(
        pages, field="/additional_costs/0/applicable_unit_type", page=42, quote=header_quote
    )
    row = _evidence(pages, field="/additional_costs/0", page=42, quote=total_quote)
    payment = _evidence(pages, field="/additional_costs/0/payments", page=42, quote=payment_quote)
    optional = _evidence(
        pages,
        field="/additional_costs/0/required",
        page=43,
        quote="발코니 확장을 선택하지 않을 경우",
    )
    excluded = _evidence(
        pages,
        field="/additional_costs/0/included_in_sale_price",
        page=43,
        quote="발코니 확장은 세대별로 택하여 계약하는 별도 계약 품목으로 분양가에는 미포함",
    )
    result.evidence.extend(
        [
            row,
            header,
            payment,
            optional,
            excluded,
            excluded.model_copy(update={"field": "/additional_costs/0/note"}),
        ]
    )
    # This source table is transposed: unit names, totals, and each payment
    # stage occupy separate rows. Keep exact leaf anchors because the generic
    # row-based re-grounding intentionally cannot infer that layout.
    for index, cost_payment in enumerate(result.additional_costs[0].payments):
        timing_field = "due_date" if cost_payment.due_date is not None else "due_text"
        result.evidence.extend(
            [
                payment.model_copy(
                    update={"field": f"/additional_costs/0/payments/{index}/stage"}
                ),
                payment.model_copy(
                    update={"field": f"/additional_costs/0/payments/{index}/{timing_field}"}
                ),
            ]
        )


def _repair_0323_cost(
    result: AnalysisResponse,
    pages: list[PdfPage],
    target: _TargetPolicy,
) -> None:
    units = ("45", "59", "84A", "84B", "84C-1", "84C-2", "84C-3", "84D", "84E-1", "84E-2", "84F")
    totals = (345, 763, 682, 686, 769, 769, 713, 1122, 1179, 1179, 711)
    header_quote = "약식표기 " + " ".join(units)
    total_quote = " ".join(f"{_mw(value):,}" for value in totals)
    if target.balcony_variant_ambiguous:
        amount = None
        note = "84C 세부형 미선택: 84C-1/2 769만원, 84C-3 713만원 · 납부일정 미기재"
    else:
        amount = target.balcony_total_manwon
        note = "선택사항 · 분양가 미포함 · 납부일정 미기재; 전체 유상옵션 범위 별도 확인"
    result.additional_costs = [
        AdditionalCost(
            type=AdditionalCostType.BALCONY_EXTENSION,
            name="발코니 확장",
            total_amount_manwon=amount,
            required=False,
            included_in_sale_price=False,
            applicable_unit_type=target.unit_name,
            payments=[],
            note=note,
        )
    ]
    row = _evidence(pages, field="/additional_costs/0", page=43, quote=total_quote)
    title = _evidence(
        pages,
        field="/additional_costs/0/type",
        page=43,
        quote="■ 추가선택품목(발코니 확장)",
    )
    header = _evidence(
        pages,
        field="/additional_costs/0/applicable_unit_type",
        page=43,
        quote=header_quote,
    )
    optional = _evidence(
        pages,
        field="/additional_costs/0/required",
        page=43,
        quote="발코니 확장공사 여부를 선택하여",
    )
    excluded = _evidence(
        pages,
        field="/additional_costs/0/included_in_sale_price",
        page=9,
        quote=("상기 공급금액은 발코니 확장비용 및 추가선택품목(유상옵션)이 포함되지 않은 가격"),
    )
    result.evidence.extend(
        [
            row,
            title,
            title.model_copy(update={"field": "/additional_costs/0/name"}),
            header,
            optional,
            excluded,
            row.model_copy(update={"field": "/additional_costs/0/note"}),
        ]
    )


def _repair_costs(
    result: AnalysisResponse,
    pages: list[PdfPage],
    target: _TargetPolicy,
) -> None:
    if result.complex_id == "2026000282":
        _repair_0282_cost(result, pages, target)
    elif result.complex_id in {"2026000315", "2026000316"}:
        _repair_031x_cost(result, pages, target)
    else:
        _repair_0323_cost(result, pages, target)


def _finalize_without_approval(result: AnalysisResponse, pages: list[PdfPage]) -> AnalysisResponse:
    cost_payment_evidence = [
        item
        for item in result.evidence
        if re.fullmatch(r"/additional_costs/\d+/payments", item.field)
    ]
    draft = ExtractionDraft(
        payment_schedule=result.payment_schedule,
        interim_loan=result.interim_loan,
        additional_costs=result.additional_costs,
        risk_clauses=result.risk_clauses,
        evidence=result.evidence,
        exception_flags=result.exception_flags,
    )
    source_pages = [
        CandidatePage(number=page.number, text=page.text, score=0, categories=frozenset())
        for page in pages
    ]
    grounded = reground_review_metadata(
        draft,
        pages=source_pages,
        unit_type_name=result.target_unit.unit_type_name,
    )
    existing = {(item.field, item.page, item.raw_text) for item in grounded.evidence}
    grounded.evidence.extend(
        item
        for item in cost_payment_evidence
        if (item.field, item.page, item.raw_text) not in existing
    )
    normalized, derived_fields = normalize_draft(grounded)
    validation = validate_draft(
        normalized,
        pages=pages,
        derived_fields=derived_fields,
        sale_price_manwon=result.target_unit.sale_price_manwon,
    )
    holds = derive_holds(
        normalized,
        validation,
        unit_type_name=result.target_unit.unit_type_name,
        text_available=sum(len(page.text.strip()) for page in pages) >= 100,
    )
    result.payment_schedule = normalized.payment_schedule
    result.interim_loan = normalized.interim_loan
    result.additional_costs = normalized.additional_costs
    result.risk_clauses = normalized.risk_clauses
    result.exception_flags = normalized.exception_flags
    result.evidence = normalized.evidence
    result.validation = validation
    result.holds = holds
    result.analysis_status = derive_analysis_status(validation, holds)
    result.analysis_summary = build_analysis_summary(normalized)
    result.review_status = (
        ReviewStatus.AUTO_EXTRACTED if validation.passed else ReviewStatus.NEEDS_REVIEW
    )
    result.reviewer = None
    result.reviewed_at = None
    return result


def repair_expanded_audited_candidate_a(
    result: AnalysisResponse,
    *,
    pages: list[PdfPage],
) -> AnalysisResponse:
    """Return a source-proven draft correction without ever granting REVIEWED.

    Announcements outside this module's exact audit set are returned unchanged.
    A known complex with a different unit, price, source hash, page count, or
    altered evidence page fails closed instead of falling back to guessed data.
    """

    matched = _policy_for(result, pages)
    if matched is None:
        return result
    policy, target = matched
    corrected = result.model_copy(deep=True)
    _replace_evidence(corrected)
    _repair_payments(corrected, pages, policy, target)
    _repair_loan(corrected, pages, policy)
    _repair_costs(corrected, pages, target)
    return _finalize_without_approval(corrected, pages)
