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
    ExceptionFlag,
    ExtractionDraft,
    Installment,
    InterestType,
    LoanArrangementStatus,
    LoanSettlementRequirement,
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


class ExpandedAuditedRepairBError(ValueError):
    """Raised when a correction is not proven by the exact audited PDF."""


@dataclass(frozen=True)
class _Due:
    due_date: date | None = None
    due_text: str | None = None


@dataclass(frozen=True)
class _PaymentSpec:
    page: int
    row_prefix: str
    down_won: tuple[int, ...]
    interim_won: tuple[int, ...]
    balance_won: int


@dataclass(frozen=True)
class _TargetPolicy:
    unit_name: str
    sale_price_manwon: int
    payment: _PaymentSpec
    balcony_total_won: int | None
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
    interim_dues: tuple[_Due, ...]
    balance_due_text: str
    loan_status: LoanArrangementStatus
    loan_page: int
    loan_quote: str
    arranged_ratio: float | None
    prepay_ratio: float | None
    prepay_quote: str | None
    targets: dict[str, _TargetPolicy]


def _mw(value: int) -> int:
    return value * 10_000


def _payment(
    page: int,
    row_prefix: str,
    *,
    down: tuple[int, ...],
    interim_each: int,
    balance: int,
) -> _PaymentSpec:
    return _PaymentSpec(
        page=page,
        row_prefix=row_prefix,
        down_won=tuple(_mw(value) for value in down),
        interim_won=(_mw(interim_each),) * 6,
        balance_won=_mw(balance),
    )


_DATES_0367 = tuple(
    _Due(due_date=value)
    for value in (
        date(2027, 6, 17),
        date(2028, 2, 17),
        date(2028, 11, 17),
        date(2029, 7, 18),
        date(2029, 12, 17),
        date(2030, 5, 17),
    )
)
_DATES_0371 = tuple(
    _Due(due_date=value)
    for value in (
        date(2027, 1, 25),
        date(2027, 5, 25),
        date(2027, 9, 27),
        date(2028, 1, 25),
        date(2028, 5, 25),
        date(2028, 9, 25),
    )
)
_DATES_0374 = tuple(
    _Due(due_date=value)
    for value in (
        date(2026, 10, 30),
        date(2027, 1, 29),
        date(2027, 6, 30),
        date(2028, 1, 31),
        date(2028, 6, 30),
        date(2028, 12, 29),
    )
)
_DATES_0383 = tuple(
    _Due(due_date=value)
    for value in (
        date(2026, 12, 10),
        date(2027, 4, 12),
        date(2027, 9, 10),
        date(2028, 5, 10),
        date(2028, 10, 10),
        date(2029, 3, 12),
    )
)


_TARGETS_0367 = {
    "01": _TargetPolicy(
        "84A",
        77_300,
        _payment(
            7,
            "30층 이상 90 334,245,200",
            down=(1000, 6730),
            interim_each=7730,
            balance=23_190,
        ),
        None,
        True,
    ),
    "02": _TargetPolicy(
        "84B",
        76_800,
        _payment(
            7,
            "30층 이상 3 332,083,200",
            down=(1000, 6680),
            interim_each=7680,
            balance=23_040,
        ),
        12_722_000,
    ),
    "03": _TargetPolicy(
        "84C",
        74_200,
        _payment(
            7,
            "30층 이상 117 320,840,800",
            down=(1000, 6420),
            interim_each=7420,
            balance=22_260,
        ),
        9_576_000,
    ),
    "04": _TargetPolicy(
        "84D",
        73_700,
        _payment(
            7,
            "30층 이상 36 318,678,800",
            down=(1000, 6370),
            interim_each=7370,
            balance=22_110,
        ),
        11_490_000,
    ),
    "05": _TargetPolicy(
        "84E",
        75_200,
        _payment(
            8,
            "30층 이상 12 325,164,800",
            down=(1000, 6520),
            interim_each=7520,
            balance=22_560,
        ),
        11_265_000,
    ),
    "06": _TargetPolicy(
        "84F",
        74_200,
        _payment(
            8,
            "30층 이상 8 320,840,800",
            down=(1000, 6420),
            interim_each=7420,
            balance=22_260,
        ),
        9_189_000,
    ),
    "07": _TargetPolicy(
        "106A",
        95_100,
        _payment(
            8,
            "30층 이상 32 389,139,690",
            down=(1000, 8510),
            interim_each=9510,
            balance=28_530,
        ),
        9_794_000,
    ),
    "08": _TargetPolicy(
        "106B",
        95_000,
        _payment(
            9,
            "30층 이상 16 388,730,500",
            down=(1000, 8500),
            interim_each=9500,
            balance=28_500,
        ),
        13_176_000,
    ),
    "09": _TargetPolicy(
        "122",
        118_600,
        _payment(
            9,
            "30층 이상 24 485,299,340",
            down=(1000, 10_860),
            interim_each=11_860,
            balance=35_580,
        ),
        11_039_000,
    ),
    "10": _TargetPolicy(
        "180A",
        299_600,
        _payment(
            9,
            "180A 30층 이상",
            down=(1000, 28_960),
            interim_each=29_960,
            balance=89_880,
        ),
        18_871_000,
    ),
    "11": _TargetPolicy(
        "180B",
        292_200,
        _payment(
            9,
            "180B 30층 이상",
            down=(1000, 28_220),
            interim_each=29_220,
            balance=87_660,
        ),
        21_161_000,
    ),
    "12": _TargetPolicy(
        "180C",
        299_900,
        _payment(
            9,
            "180C 103동 1호 30층 이상",
            down=(1000, 28_990),
            interim_each=29_990,
            balance=89_970,
        ),
        18_760_000,
    ),
}


def _targets_0371() -> dict[str, _TargetPolicy]:
    rows = {
        "01": ("59A", 41_600, "10~20층 22 100,376,000 315,624,000", 209),
        "02": ("59B", 40_800, "10-19층 40 100,307,000 307,693,000", 242),
        "03": ("59C", 39_700, "10-19층 20 99,646,310 297,353,690", 251),
        "04": ("71A", 47_800, "10-20층 22 119,384,000 358,616,000", 238),
        "05": ("71B", 47_600, "10-20층 44 120,275,000 355,725,000", 313),
        "06": ("84A", 53_400, "10-20층 22 141,939,000 392,061,000", 239),
    }
    return {
        unit_id: _TargetPolicy(
            unit_name,
            price,
            _payment(
                9 if unit_id in {"01", "02"} else 10,
                prefix,
                down=(500, price // 20 - 500),
                interim_each=price // 10,
                balance=price * 35 // 100,
            ),
            _mw(cost),
        )
        for unit_id, (unit_name, price, prefix, cost) in rows.items()
    }


def _targets_0374() -> dict[str, _TargetPolicy]:
    rows = {
        "01": ("51A", 124_000, 8, "051.9981A 1 51A 103동1호", 1788),
        "02": ("51B", 125_000, 8, "21~30층 1 821,250,000 428,750,000", 1794),
        "03": ("51C", 122_000, 9, "11~20층 1 801,540,000 418,460,000", 1776),
        "04": ("51D", 124_000, 9, "11~20층 2 814,680,000 425,320,000", 1796),
        "05": ("51E", 118_000, 9, "051.9953E 1 51A1 103동2호", 1787),
        "06": ("59A", 145_000, 9, "21~30층 1 952,650,000 497,350,000", 2048),
        "07": ("59B", 144_000, 9, "21~30층 3 946,080,000 493,920,000", 2059),
        "08": ("59C", 144_000, 9, "113동3호 11~20층 1 946,080,000", 2050),
        "09": ("59D", 145_000, 9, "11~20층 1 952,650,000 497,350,000", 2068),
        "10": ("74A", 170_000, 9, "31층 이상 1 1,116,900,000 583,100,000", 2581),
        "11": ("74B", 168_000, 9, "31층 이상 1 1,103,760,000 576,240,000", 2551),
        "12": ("74C", 165_000, 10, "111동1호 11~20층 1 1,084,050,000", 2539),
        "13": ("84A", 184_000, 10, "21~30층 2 1,208,880,000 631,120,000", 2879),
    }
    return {
        unit_id: _TargetPolicy(
            unit_name,
            price,
            _payment(
                page,
                prefix,
                down=(price // 10,),
                interim_each=price // 10,
                balance=price * 3 // 10,
            ),
            _mw(cost),
        )
        for unit_id, (unit_name, price, page, prefix, cost) in rows.items()
    }


_TARGETS_0376 = {
    "01": _TargetPolicy(
        "84A",
        58_660,
        _PaymentSpec(
            page=6,
            row_prefix="40층 이상 10 211,176,000 375,424,000",
            down_won=(1_000_000, 28_330_000),
            interim_won=(10_000_000,),
            balance_won=547_270_000,
        ),
        None,
    ),
    "02": _TargetPolicy(
        "84B",
        59_730,
        _PaymentSpec(
            page=6,
            row_prefix="40층 이상 40 215,028,000 382,272,000",
            down_won=(1_000_000, 28_865_000),
            interim_won=(10_000_000,),
            balance_won=557_435_000,
        ),
        None,
    ),
}


def _targets_0383() -> dict[str, _TargetPolicy]:
    rows = {
        "01": ("59A", 42_000, 8, "35층 4 70,140,000 349,860,000", 1450),
        "02": ("59B", 41_300, 8, "21층 1 68,971,000 344,029,000", 1450),
        "03": ("72A", 49_000, 8, "35층 1 81,830,000 408,170,000", 1680),
        "04": ("72B", 48_600, 9, "35층 1 81,162,000 404,838,000", 1680),
        "05": ("84A", 57_700, 9, "106동 1,2,3,5호 35층 8 96,359,000", 1850),
        "06": ("84B", 57_100, 9, "35층 3 95,357,000 475,643,000", 1850),
    }
    return {
        unit_id: _TargetPolicy(
            unit_name,
            price,
            _payment(
                page,
                prefix,
                down=(1000, price // 20 - 1000),
                interim_each=price // 10,
                balance=price * 35 // 100,
            ),
            _mw(cost),
        )
        for unit_id, (unit_name, price, page, prefix, cost) in rows.items()
    }


_POLICIES = {
    "2026000367": _DocumentPolicy(
        source_sha256="5955b503285b166bf717d0c68b7dec43805b1764096e2ab50fd570525c43e90a",
        source_page_count=79,
        payment_header_page=6,
        payment_header_quote=(
            "계약 시 1개월 내 2027.06.17 2028.02.17 2028.11.17 "
            "2029.07.18 2029.12.17 2030.05.17 입주지정일"
        ),
        move_in_page=5,
        move_in_quote="입주시기 : 2031년 01월 예정",
        move_in_month="2031-01",
        down_dues=(_Due(due_text="계약 시"), _Due(due_text="계약 후 1개월 이내")),
        interim_dues=_DATES_0367,
        balance_due_text="입주지정일",
        loan_status=LoanArrangementStatus.PLANNED,
        loan_page=40,
        loan_quote=(
            "본 아파트의 중도금 대출에 대한 이자는 “중도금 이자후불제” 조건이며, "
            "총 공급대금의 60% 범위 내에서 사업주체가 지정하는 대출취급기관에서 "
            "중도금 융자알선을 시행할 예정입니다."
        ),
        arranged_ratio=0.60,
        prepay_ratio=0.10,
        prepay_quote="계약금 10% 완납 이후 중도금 대출",
        targets=_TARGETS_0367,
    ),
    "2026000371": _DocumentPolicy(
        source_sha256="96814f43b9bddfe384386729db23b1052d677919cb1fbd5222df9bec361e0f3a",
        source_page_count=78,
        payment_header_page=9,
        payment_header_quote=(
            "계약 시 계약 후 2027.01.25. 2027.05.25. 2027.09.27. "
            "2028.01.25. 2028.05.25. 2028.09.25. 30일 이내"
        ),
        move_in_page=7,
        move_in_quote="입주시기 : 2029년 01월 예정",
        move_in_month="2029-01",
        down_dues=(_Due(due_text="계약 시"), _Due(due_text="계약 후 30일 이내")),
        interim_dues=_DATES_0371,
        balance_due_text="입주 지정일",
        loan_status=LoanArrangementStatus.PLANNED,
        loan_page=45,
        loan_quote=(
            "본 아파트의 중도금 대출에 대한 이자는 “중도금 이자후불제” 조건이며, "
            "총 공급대금의 60% 범위 내에서 사업주체가 지정하는 대출취급기관에서 "
            "중도금 융자알선을 시행할 예정입니다."
        ),
        arranged_ratio=0.60,
        prepay_ratio=0.05,
        prepay_quote="분양대금의 총 5% 완납 후",
        targets=_targets_0371(),
    ),
    "2026000374": _DocumentPolicy(
        source_sha256="a67a4e21c91ba8645ac735e761c391a39db159d32a23e58a951f9e170ed272ab",
        source_page_count=74,
        payment_header_page=8,
        payment_header_quote=("2026.10.30 2027.01.29 2027.06.30 2028.01.31 2028.06.30 2028.12.29"),
        move_in_page=7,
        move_in_quote="입주시기 : 2029년 07월 예정",
        move_in_month="2029-07",
        down_dues=(_Due(due_text="계약시"),),
        interim_dues=_DATES_0374,
        balance_due_text="입주시",
        loan_status=LoanArrangementStatus.PLANNED,
        loan_page=47,
        loan_quote=(
            "본 아파트의 중도금 대출에 대한 이자는 중도금 이자후불제 조건이며 "
            "총 공급금액 40% 범위 내에서 중도금 융자 알선을 시행할 예정"
        ),
        arranged_ratio=0.40,
        prepay_ratio=0.10,
        prepay_quote="분양대금의 10% 완납 시 중도금 대출",
        targets=_targets_0374(),
    ),
    "2026000376": _DocumentPolicy(
        source_sha256="1bb708d6b2359117621a83c7a6211706a4b1b7a71f633eac4b350bee1b333aa1",
        source_page_count=48,
        payment_header_page=6,
        payment_header_quote="2026-11-17",
        move_in_page=5,
        move_in_quote="입주시기 : 2027년 02월 예정",
        move_in_month="2027-02",
        down_dues=(_Due(due_text="계약시"), _Due(due_text="계약 후 7일 이내")),
        interim_dues=(_Due(due_date=date(2026, 11, 17)),),
        balance_due_text="입주지정일",
        loan_status=LoanArrangementStatus.NOT_AVAILABLE,
        loan_page=6,
        loan_quote=(
            "본 아파트는 중도금대출이 불가하며 계약자는 본인 책임하에 "
            "공급금액을 조달하여 납부일정에 맞춰 납부하여야 하며"
        ),
        arranged_ratio=None,
        prepay_ratio=None,
        prepay_quote=None,
        targets=_TARGETS_0376,
    ),
    "2026000383": _DocumentPolicy(
        source_sha256="4464ac6ae6059ce324d15c51a5c6bb5cd00ad7e63614531f2a3483186a5f710e",
        source_page_count=78,
        payment_header_page=8,
        payment_header_quote=(
            "계약 시 계약 후 2026-12-10 2027-04-12 2027-09-10 "
            "2028-05-10 2028-10-10 2029-03-12 실입주일 중 30일 이내 빠른 날"
        ),
        move_in_page=6,
        move_in_quote="입주시기 : 2029년 09월 예정",
        move_in_month="2029-09",
        down_dues=(_Due(due_text="계약 시"), _Due(due_text="계약 후 30일 이내")),
        interim_dues=_DATES_0383,
        balance_due_text="입주지정기간 만료일 또는 실입주일 중 빠른 날",
        loan_status=LoanArrangementStatus.PLANNED,
        loan_page=38,
        loan_quote=(
            "본 아파트의 중도금 대출에 대한 이자는 중도금 이자후불제 조건이며, "
            "총 공급대금의 60% 범위 내에서 사업주체가 지정하는 대출취급기관에서 "
            "중도금 융자알선을 시행할 예정입니다."
        ),
        arranged_ratio=0.60,
        prepay_ratio=0.05,
        prepay_quote="공급대금의 5% 이상 납부 이후 중도금 대출이 가능",
        targets=_targets_0383(),
    ),
}

EXPANDED_AUDITED_TARGET_COUNT_B = sum(len(policy.targets) for policy in _POLICIES.values())


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def _won_values(value: str) -> list[int]:
    return [int(item.replace(",", "")) for item in re.findall(r"\d[\d,]{3,}", value)]


def _contains_subsequence(values: list[int], wanted: list[int]) -> bool:
    return any(values[index : index + len(wanted)] == wanted for index in range(len(values)))


def _page(pages: list[PdfPage], number: int) -> PdfPage:
    matches = [page for page in pages if page.number == number]
    if len(matches) != 1:
        raise ExpandedAuditedRepairBError(f"PDF {number}쪽을 유일하게 확인할 수 없습니다.")
    return matches[0]


def _evidence(pages: list[PdfPage], *, field: str, page: int, quote: str) -> Evidence:
    source = _page(pages, page)
    if _normalized(quote) not in _normalized(source.text):
        raise ExpandedAuditedRepairBError(
            f"{field}: PDF {page}쪽에서 감사 근거를 확인하지 못했습니다."
        )
    return Evidence(field=field, page=page, raw_text=quote)


def _row_evidence(
    pages: list[PdfPage],
    *,
    field: str,
    page: int,
    prefix: str,
    wanted_won: tuple[int, ...],
) -> Evidence:
    matches = [
        line.strip()
        for line in _page(pages, page).text.splitlines()
        if _normalized(prefix) in _normalized(line)
        and _contains_subsequence(_won_values(line), list(wanted_won))
    ]
    unique = list(dict.fromkeys(matches))
    if len(unique) != 1:
        raise ExpandedAuditedRepairBError(
            f"{field}: PDF {page}쪽의 exact unit/금액 행을 유일하게 확인할 수 없습니다."
        )
    return Evidence(field=field, page=page, raw_text=unique[0])


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
    due_month: str | None = None,
    due_text: str | None = None,
) -> PaymentComponent:
    if len(values_won) != len(dues):
        raise AssertionError("감사 금액과 납부시점 수가 다릅니다.")
    total_won = sum(values_won)
    total_ratio = total_won / price_won
    total_amount = _exact_manwon(total_won)
    return PaymentComponent(
        total_ratio=total_ratio,
        total_amount_manwon=total_amount,
        basis=_basis(ratio=total_ratio, amount=total_amount),
        installments=[
            Installment(
                number=index,
                ratio=value / price_won,
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
        raise ExpandedAuditedRepairBError("REVIEWED 결과는 자동 교정 입력으로 사용할 수 없습니다.")
    target = policy.targets.get(result.target_unit.unit_type_id or "")
    if target is None or (
        result.target_unit.unit_type_name != target.unit_name
        or result.target_unit.sale_price_manwon != target.sale_price_manwon
    ):
        raise ExpandedAuditedRepairBError(
            "감사 대상 complex의 exact unit/name/price tuple과 다릅니다."
        )
    if (
        result.meta.source_sha256 != policy.source_sha256
        or result.meta.source_page_count != policy.source_page_count
        or len(pages) != policy.source_page_count
        or {page.number for page in pages} != set(range(1, policy.source_page_count + 1))
    ):
        raise ExpandedAuditedRepairBError("감사한 PDF source lock과 다릅니다.")
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
        prefix=spec.row_prefix,
        wanted_won=wanted,
    )
    header = _evidence(
        pages,
        field="/payment_schedule/interim_payment/installments",
        page=policy.payment_header_page,
        quote=policy.payment_header_quote,
    )
    move_in = _evidence(
        pages,
        field="/payment_schedule/balance_payment/due_month",
        page=policy.move_in_page,
        quote=policy.move_in_quote,
    )
    schedule = result.payment_schedule
    schedule.down_payment = _component(
        values_won=spec.down_won,
        price_won=price_won,
        dues=policy.down_dues,
    )
    schedule.interim_payment = _component(
        values_won=spec.interim_won,
        price_won=price_won,
        dues=policy.interim_dues,
    )
    schedule.balance_payment = _component(
        values_won=(spec.balance_won,),
        price_won=price_won,
        dues=(_Due(due_text=policy.balance_due_text),),
        due_month=policy.move_in_month,
        due_text=policy.balance_due_text,
    )
    for field in (
        "/payment_schedule/down_payment",
        "/payment_schedule/interim_payment",
        "/payment_schedule/balance_payment",
    ):
        result.evidence.append(row.model_copy(update={"field": field}))
    down_due_quotes = {
        "2026000367": ("계약 시", "1개월 내"),
        "2026000371": ("계약 시", "30일 이내"),
        "2026000374": ("계약시",),
        "2026000376": ("계약시", "계약 후 7일 이내"),
        "2026000383": ("계약 시", "30일 이내"),
    }[result.complex_id]
    for index, quote in enumerate(down_due_quotes):
        result.evidence.append(
            _evidence(
                pages,
                field=f"/payment_schedule/down_payment/installments/{index}/due_text",
                page=policy.payment_header_page,
                quote=quote,
            )
        )
    for index in range(len(policy.interim_dues)):
        result.evidence.append(
            header.model_copy(
                update={"field": f"/payment_schedule/interim_payment/installments/{index}/due_date"}
            )
        )
    balance_due = (
        header.model_copy(update={"field": "/payment_schedule/balance_payment/due_text"})
        if result.complex_id == "2026000383"
        else _evidence(
            pages,
            field="/payment_schedule/balance_payment/due_text",
            page=policy.payment_header_page,
            quote=policy.balance_due_text,
        )
    )
    result.evidence.extend([header, balance_due, move_in])


def _repair_loan(
    result: AnalysisResponse,
    pages: list[PdfPage],
    policy: _DocumentPolicy,
) -> None:
    loan = result.interim_loan
    loan.arrangement_status = policy.loan_status
    loan.arranged_ratio = policy.arranged_ratio
    loan.arranged_amount_manwon = None
    loan.self_funding_ratio = None
    loan.self_funding_amount_manwon = None
    loan.self_funding_origin = None
    loan.bank_names = []
    loan.guarantee_provider = None
    loan.interest_type = (
        InterestType.NOT_APPLICABLE
        if policy.loan_status == LoanArrangementStatus.NOT_AVAILABLE
        else InterestType.DEFERRED_INTEREST
    )
    loan.interest_note = (
        None if policy.loan_status == LoanArrangementStatus.NOT_AVAILABLE else "중도금 이자후불제"
    )
    loan.prepay_requirement_ratio = policy.prepay_ratio
    arrangement = _evidence(
        pages,
        field="/interim_loan/arrangement_status",
        page=policy.loan_page,
        quote=policy.loan_quote,
    )
    result.evidence.append(arrangement)
    if policy.arranged_ratio is not None:
        result.evidence.extend(
            [
                arrangement.model_copy(update={"field": "/interim_loan/arranged_ratio"}),
                arrangement.model_copy(update={"field": "/interim_loan/interest_type"}),
                arrangement.model_copy(update={"field": "/interim_loan/interest_note"}),
            ]
        )
    prepay_pages = {
        "2026000367": 41,
        "2026000371": 11,
        "2026000374": 10,
        "2026000383": 38,
    }
    if policy.prepay_quote is not None:
        result.evidence.append(
            _evidence(
                pages,
                field="/interim_loan/prepay_requirement_ratio",
                page=prepay_pages[result.complex_id],
                quote=policy.prepay_quote,
            )
        )

    settlement_sources = {
        "2026000367": (
            41,
            "계약자는 입주 전까지 대출금을 상환하거나 담보대출로 전환",
            "입주 전",
        ),
        "2026000371": (
            46,
            "계약자는 입주 전까지 대출금을 상환하거나 담보대출로 전환",
            "입주 전",
        ),
        "2026000374": (
            47,
            "계약자는 입주 전까지 중도금 대출금을 상환하거나 담보대출로 전환",
            "입주 전",
        ),
        "2026000383": (
            39,
            "계약자는 입주증 발급일 또는 입주지정기간 종료일 중 빠른날까지 "
            "대출금을 상환하거나 담보대출로 전환",
            "입주증 발급일 또는 입주지정기간 종료일 중 빠른 날까지",
        ),
    }
    extension_sources = {
        "2026000367": (41, "대출기간 만료 시(준공 후 미입주 등) 금융기관의 대출기간 연장"),
        "2026000371": (46, "대출기간 만료 시(준공 후 미입주 등) 금융기관의 대출기간 연장"),
        "2026000374": (47, "대출기간 만료 시(준공 후 미입주 등) 금융기관의 대출기간 연장"),
        "2026000383": (39, "대출기간 만료 시(준공 후 미입주 등) 금융기관의 대출기간 연장"),
    }
    if policy.loan_status == LoanArrangementStatus.NOT_AVAILABLE:
        loan.settlement_requirement = LoanSettlementRequirement.NOT_APPLICABLE
        loan.settlement_deadline_text = None
        loan.extension_contingency_disclosed = None
        result.evidence.append(
            arrangement.model_copy(update={"field": "/interim_loan/settlement_requirement"})
        )
    else:
        settlement_page, settlement_quote, deadline = settlement_sources[result.complex_id]
        loan.settlement_requirement = LoanSettlementRequirement.REPAY_OR_CONVERT_TO_MORTGAGE
        loan.settlement_deadline_text = deadline
        loan.extension_contingency_disclosed = True
        settlement = _evidence(
            pages,
            field="/interim_loan/settlement_requirement",
            page=settlement_page,
            quote=settlement_quote,
        )
        result.evidence.extend(
            [
                settlement,
                settlement.model_copy(update={"field": "/interim_loan/settlement_deadline_text"}),
            ]
        )
        extension_page, extension_quote = extension_sources[result.complex_id]
        result.evidence.append(
            _evidence(
                pages,
                field="/interim_loan/extension_contingency_disclosed",
                page=extension_page,
                quote=extension_quote,
            )
        )


_COST_HEADER_0367 = (
    "84A \ud488목 84B 84C 84D 84E 84F 106A 106B 122 180A 180B 180C 2\uce35~37\uce35 38\uce35"
)
_COST_ROW_0367 = (
    "\ubc1c\ucf54\ub2c8 \ud655\uc7a5 9,856,000 10,575,000 12,722,000 9,576,000 11,490,000 "
    "11,265,000 9,189,000 9,794,000 13,176,000 11,039,000 18,871,000 "
    "21,161,000 18,760,000"
)
_COST_ROWS_0371 = {
    "01": "59A 2,090,000 209,000 1,881,000 -",
    "02": "59B 2,420,000 242,000 2,178,000 -",
    "03": "59C 2,510,000 251,000 2,259,000 -",
    "04": "71A 2,380,000 238,000 2,142,000 -",
    "05": "71B 3,130,000 313,000 2,817,000 -",
    "06": "84A 2,390,000 239,000 2,151,000 -",
}
_COST_ROWS_0374 = {
    "01": "51A 17,880,000 1,788,000 1,788,000 14,304,000",
    "02": "51B 17,940,000 1,794,000 1,794,000 14,352,000",
    "03": "51C 17,760,000 1,776,000 1,776,000 14,208,000",
    "04": "51D 17,960,000 1,796,000 1,796,000 14,368,000",
    "05": "51A1 17,870,000 1,787,000 1,787,000 14,296,000",
    "06": "59A 20,480,000 2,048,000 2,048,000 16,384,000",
    "07": "59B 20,590,000 2,059,000 2,059,000 16,472,000",
    "08": "59C 20,500,000 2,050,000 2,050,000 16,400,000",
    "09": "59A1 20,680,000 2,068,000 2,068,000 16,544,000",
    "10": "74A 25,810,000 2,581,000 2,581,000 20,648,000",
    "11": "74B 25,510,000 2,551,000 2,551,000 20,408,000",
    "12": "74C 25,390,000 2,539,000 2,539,000 20,312,000",
    "13": "84A 28,790,000 2,879,000 2,879,000 23,032,000",
}
_COST_ROWS_0383 = {
    "01": "59A 14,500,000 1,450,000 13,050,000 59B",
    "02": "59A 14,500,000 1,450,000 13,050,000 59B",
    "03": "72A 16,800,000 1,680,000 15,120,000 72B",
    "04": "72A 16,800,000 1,680,000 15,120,000 72B",
    "05": "84A 18,500,000 1,850,000 16,650,000 84B",
    "06": "84A 18,500,000 1,850,000 16,650,000 84B",
}


def _cost_payment(
    number: int,
    stage: PaymentStage,
    amount_won: int,
    *,
    due_date: date | None = None,
    due_text: str | None = None,
) -> AdditionalCostPayment:
    amount = _exact_manwon(amount_won)
    if amount is None:
        raise AssertionError("소수 만원 추가비용을 반올림하면 안 됩니다.")
    return AdditionalCostPayment(
        number=number,
        stage=stage,
        amount_manwon=amount,
        due_date=due_date,
        due_text=due_text,
    )


def _set_optional_balcony(
    result: AnalysisResponse,
    pages: list[PdfPage],
    target: _TargetPolicy,
    *,
    page: int,
    row_quote: str,
    unit_page: int,
    unit_quote: str,
    required_page: int,
    required_quote: str,
    included_page: int,
    included_quote: str,
    payments: list[AdditionalCostPayment],
    payment_page: int | None = None,
    payment_quote: str | None = None,
    note: str | None = None,
    note_page: int | None = None,
    note_quote: str | None = None,
) -> None:
    result.additional_costs = [
        AdditionalCost(
            type=AdditionalCostType.BALCONY_EXTENSION,
            name="발코니 확장 공사비",
            total_amount_manwon=(
                _exact_manwon(target.balcony_total_won)
                if target.balcony_total_won is not None
                else None
            ),
            required=False,
            included_in_sale_price=False,
            applicable_unit_type=target.unit_name,
            payments=payments,
            note=note,
        )
    ]
    row = _evidence(pages, field="/additional_costs/0", page=page, quote=row_quote)
    result.evidence.extend(
        [
            row,
            _evidence(
                pages,
                field="/additional_costs/0/applicable_unit_type",
                page=unit_page,
                quote=unit_quote,
            ),
            _evidence(
                pages,
                field="/additional_costs/0/required",
                page=required_page,
                quote=required_quote,
            ),
            _evidence(
                pages,
                field="/additional_costs/0/included_in_sale_price",
                page=included_page,
                quote=included_quote,
            ),
        ]
    )
    if payments:
        if payment_page is None or payment_quote is None:
            raise AssertionError("추가비용 분납의 헤더 근거가 필요합니다.")
        payment_evidence = _evidence(
            pages,
            field="/additional_costs/0/payments",
            page=payment_page,
            quote=payment_quote,
        )
        result.evidence.append(payment_evidence)
        # These audited layouts are not always row-oriented. Persist exact
        # leaf anchors so canonical re-grounding can retain source-proven stage
        # and timing fields without trying to infer a different table shape.
        for index, payment in enumerate(payments):
            timing_field = "due_date" if payment.due_date is not None else "due_text"
            result.evidence.extend(
                [
                    payment_evidence.model_copy(
                        update={"field": f"/additional_costs/0/payments/{index}/stage"}
                    ),
                    payment_evidence.model_copy(
                        update={
                            "field": f"/additional_costs/0/payments/{index}/{timing_field}"
                        }
                    ),
                ]
            )
    if note is not None:
        if note_page is None or note_quote is None:
            raise AssertionError("추가비용 note의 근거가 필요합니다.")
        result.evidence.append(
            _evidence(
                pages,
                field="/additional_costs/0/note",
                page=note_page,
                quote=note_quote,
            )
        )


def _repair_0367_cost(
    result: AnalysisResponse, pages: list[PdfPage], target: _TargetPolicy
) -> None:
    payments: list[AdditionalCostPayment] = []
    if target.balcony_total_won in {11_490_000, 18_760_000}:
        total = target.balcony_total_won
        if total is None:
            raise AssertionError
        payments = [
            _cost_payment(1, PaymentStage.CONTRACT, 2_000_000, due_text="계약 시"),
            _cost_payment(2, PaymentStage.BALANCE, total - 2_000_000, due_text="입주 시"),
        ]
    note = None
    note_page = None
    note_quote = None
    if target.balcony_variant_ambiguous:
        note = "층 미지정: 2~37층 985.6만원, 38층 1,057.5만원"
        note_page = 42
        note_quote = (
            "84A타입 38층(최상층) 세대는 2~37층 세대보다 침실(2,3) 및 알파룸 "
            "창호 면적이 넓어 발코니 확장 공급금액이 상이"
        )
    _set_optional_balcony(
        result,
        pages,
        target,
        page=42,
        row_quote=_COST_ROW_0367,
        unit_page=42,
        unit_quote=_COST_HEADER_0367,
        required_page=60,
        required_quote="선택사항에 따라 별도 안내",
        included_page=42,
        included_quote="발코니 확장비용은 공동주택(아파트) 공급금액과 별도",
        payments=payments,
        payment_page=60 if payments else None,
        payment_quote=("납입금액 2,000,000 선택사항에 따라 별도 안내" if payments else None),
        note=note,
        note_page=note_page,
        note_quote=note_quote,
    )


def _repair_0371_cost(
    result: AnalysisResponse, pages: list[PdfPage], target: _TargetPolicy
) -> None:
    unit_id = result.target_unit.unit_type_id or ""
    _set_optional_balcony(
        result,
        pages,
        target,
        page=49,
        row_quote=_COST_ROWS_0371[unit_id],
        unit_page=49,
        unit_quote=_COST_ROWS_0371[unit_id],
        required_page=49,
        required_quote="발코니 확장은 세대별로 선택하여 계약하는 별도 계약 품목",
        included_page=49,
        included_quote="공동주택 분양가에는 미포함",
        payments=[],
    )


def _repair_0374_cost(
    result: AnalysisResponse, pages: list[PdfPage], target: _TargetPolicy
) -> None:
    unit_id = result.target_unit.unit_type_id or ""
    payments: list[AdditionalCostPayment] = []
    if unit_id == "08":
        payments = [
            _cost_payment(1, PaymentStage.CONTRACT, 2_050_000, due_text="계약시"),
            _cost_payment(2, PaymentStage.INTERIM, 2_050_000, due_date=date(2026, 10, 30)),
            _cost_payment(3, PaymentStage.BALANCE, 16_400_000, due_text="입주지정일"),
        ]
    aliases = {
        "05": (
            7,
            "051.9953A1 ⇒ 051.9953E(약식표기 51A1)",
        ),
        "09": (
            7,
            "059.9987A1 ⇒ 059.9987D(약식표기 59A1)",
        ),
    }
    unit_page, unit_quote = aliases.get(unit_id, (48, _COST_ROWS_0374[unit_id]))
    _set_optional_balcony(
        result,
        pages,
        target,
        page=48,
        row_quote=_COST_ROWS_0374[unit_id],
        unit_page=unit_page,
        unit_quote=unit_quote,
        required_page=49,
        required_quote="발코니 확장공사 여부를 선택하여",
        included_page=49,
        included_quote="발코니 확장 공사비는 공동주택 공급금액과 별도",
        payments=payments,
        payment_page=48 if payments else None,
        payment_quote=("계약시 2026.10.30 입주지정일" if payments else None),
    )


def _repair_0376_cost(
    result: AnalysisResponse, pages: list[PdfPage], target: _TargetPolicy
) -> None:
    result.additional_costs = [
        AdditionalCost(
            type=AdditionalCostType.BALCONY_EXTENSION,
            name="발코니 확장 공사비",
            total_amount_manwon=None,
            required=True,
            included_in_sale_price=True,
            applicable_unit_type=target.unit_name,
            payments=[],
            note="공급가 포함 · 개별 선택·제외 불가",
        )
    ]
    included = _evidence(
        pages,
        field="/additional_costs/0",
        page=38,
        quote=(
            "발코니 확장 및 실제 시공된 품목은 공급가에 포함되어 있는 품목으로 "
            "개별 선택·제외할 수 없습니다."
        ),
    )
    result.evidence.extend(
        [
            included,
            included.model_copy(update={"field": "/additional_costs/0/required"}),
            included.model_copy(update={"field": "/additional_costs/0/included_in_sale_price"}),
            included.model_copy(update={"field": "/additional_costs/0/note"}),
            _evidence(
                pages,
                field="/additional_costs/0/applicable_unit_type",
                page=6,
                quote=target.payment.row_prefix,
            ),
        ]
    )


def _repair_0383_cost(
    result: AnalysisResponse, pages: list[PdfPage], target: _TargetPolicy
) -> None:
    unit_id = result.target_unit.unit_type_id or ""
    total = target.balcony_total_won
    if total is None:
        raise AssertionError
    contract = total // 10
    _set_optional_balcony(
        result,
        pages,
        target,
        page=42,
        row_quote=_COST_ROWS_0383[unit_id],
        unit_page=42,
        unit_quote=_COST_ROWS_0383[unit_id],
        required_page=43,
        required_quote="발코니확장 여부를 선택하여 발코니 확장 계약을 체결",
        included_page=43,
        included_quote="발코니 확장 비용은 공동주택 분양금액과 별도",
        payments=[
            _cost_payment(1, PaymentStage.CONTRACT, contract, due_text="계약시"),
            _cost_payment(2, PaymentStage.BALANCE, total - contract, due_text="입주지정일"),
        ],
        payment_page=42,
        payment_quote="계약시 입주지정일",
    )


def _repair_costs(result: AnalysisResponse, pages: list[PdfPage], target: _TargetPolicy) -> None:
    repairs = {
        "2026000367": _repair_0367_cost,
        "2026000371": _repair_0371_cost,
        "2026000374": _repair_0374_cost,
        "2026000376": _repair_0376_cost,
        "2026000383": _repair_0383_cost,
    }
    repairs[result.complex_id](result, pages, target)


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


def repair_expanded_audited_candidate_b(
    result: AnalysisResponse,
    *,
    pages: list[PdfPage],
) -> AnalysisResponse:
    """Apply source-locked corrections without granting human review.

    Unknown documents pass through unchanged. Any known complex with a different
    source, page count, unit, normalized name, price, or table row fails closed.
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
    if ExceptionFlag.ADDITIONAL_COST_SCOPE_LIMITED not in corrected.exception_flags:
        corrected.exception_flags.append(ExceptionFlag.ADDITIONAL_COST_SCOPE_LIMITED)
    return _finalize_without_approval(corrected, pages)
