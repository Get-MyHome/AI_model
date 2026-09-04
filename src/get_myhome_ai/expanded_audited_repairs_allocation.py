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
    ExceptionFlag,
    Installment,
    InterestType,
    InterimLoan,
    LoanArrangementStatus,
    PaymentBasis,
    PaymentComponent,
    PaymentSchedule,
    PaymentStage,
    ReviewStatus,
    ValueOrigin,
)
from get_myhome_ai.pdf_text import PdfPage
from get_myhome_ai.review import prepare_review_draft


class ExpandedAllocationRepairError(ValueError):
    """Raised when an allocation repair is not proven by its locked PDF."""


@dataclass(frozen=True)
class _TargetPolicy:
    raw_unit_name: str
    unit_name: str
    sale_price_manwon: int
    payment_row_label: str
    balcony_total_manwon: int


@dataclass(frozen=True)
class _DocumentPolicy:
    source_sha256: str
    source_page_count: int
    mapping_page: int
    payment_page: int
    payment_dates: tuple[date, ...]
    move_in_page: int
    move_in_month: str
    loan_page: int
    balcony_page: int
    balcony_interim_date: date | None
    balcony_balance_due_text: str
    targets: dict[str, _TargetPolicy]


_LOAN_START = "본 아파트의 중도금 대출은 이자후불제이며"
_LOAN_END = "(단, 정부정책 및 금융권 사정 등의 사유로 다소 변경할 수 있음)"
_ALLOCATION_EVIDENCE_FIELD = "/interim_loan/self_funding_origin"


def _target(
    raw_unit_name: str,
    unit_name: str,
    sale_price_manwon: int,
    payment_row_label: str,
    balcony_total_manwon: int,
) -> _TargetPolicy:
    return _TargetPolicy(
        raw_unit_name=raw_unit_name,
        unit_name=unit_name,
        sale_price_manwon=sale_price_manwon,
        payment_row_label=payment_row_label,
        balcony_total_manwon=balcony_total_manwon,
    )


_POLICIES: dict[str, _DocumentPolicy] = {
    "2026000355": _DocumentPolicy(
        source_sha256="377c2ebcf0c6ec451c741825a522da16fd79f1e76618b9c6b7074291004d8427",
        source_page_count=61,
        mapping_page=7,
        payment_page=9,
        payment_dates=(
            date(2027, 1, 30),
            date(2027, 5, 31),
            date(2027, 10, 29),
            date(2028, 3, 30),
            date(2028, 8, 30),
            date(2029, 1, 30),
        ),
        move_in_page=7,
        move_in_month="2029-05",
        loan_page=38,
        balcony_page=42,
        balcony_interim_date=date(2027, 1, 30),
        balcony_balance_due_text="입주지정일",
        targets={
            "01": _target("036.9533", "36", 69_590, "15~19층", 800),
            "02": _target("059.9667A", "59A", 126_350, "15~18층", 1_700),
            "03": _target("059.9424B", "59B", 124_200, "15~20층", 1_700),
            "04": _target("084.9807A", "84A", 149_910, "20층", 2_200),
        },
    ),
    "2026000364": _DocumentPolicy(
        source_sha256="b6ef16cf0d3cc4edfd3e9089e701b8f6ab53a2be8aaefcda9908206cdb3bdef0",
        source_page_count=74,
        mapping_page=6,
        payment_page=8,
        payment_dates=(
            date(2027, 1, 25),
            date(2027, 6, 25),
            date(2027, 11, 25),
            date(2028, 8, 25),
            date(2029, 1, 25),
            date(2029, 6, 25),
        ),
        move_in_page=6,
        move_in_month="2030-01",
        loan_page=39,
        balcony_page=41,
        balcony_interim_date=None,
        balcony_balance_due_text="입주지정기간",
        targets={
            "01": _target("044.9631A", "44A", 132_510, "10~12층", 970),
            "02": _target("044.5894B", "44B", 130_370, "5~6층", 1_080),
            "03": _target("049.9779", "49", 146_600, "10~11층", 1_880),
            "04": _target("059.9025A", "59A", 172_900, "3층", 2_480),
            "05": _target("059.9030B", "59B", 183_790, "5~8층", 2_560),
            "06": _target("059.8890C", "59C", 186_630, "20층 이상", 2_830),
            "07": _target("059.9245D", "59D", 185_080, "20층 이상", 2_460),
        },
    ),
}

EXPANDED_ALLOCATION_TARGET_COUNT = sum(len(policy.targets) for policy in _POLICIES.values())


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def _page(pages: list[PdfPage], number: int) -> PdfPage:
    matches = [page for page in pages if page.number == number]
    if len(matches) != 1:
        raise ExpandedAllocationRepairError(f"PDF {number}쪽을 유일하게 찾지 못했습니다.")
    return matches[0]


def _won_values(raw_text: str) -> list[int]:
    return [
        int(value.replace(",", ""))
        for value in re.findall(r"(?<![\d.])\d{1,3}(?:,\d{3})+(?![\d.])", raw_text)
    ]


def _contains_subsequence(values: list[int], expected: list[int]) -> bool:
    return any(
        values[index : index + len(expected)] == expected
        for index in range(len(values) - len(expected) + 1)
    )


def _line_with_values(
    page: PdfPage,
    *,
    row_label: str,
    expected_won: tuple[int, ...],
) -> str:
    expected = list(expected_won)
    matches = [
        line.strip()
        for line in page.text.splitlines()
        if _normalized(row_label) in _normalized(line)
        and _contains_subsequence(_won_values(line), expected)
    ]
    if len(matches) != 1:
        raise ExpandedAllocationRepairError(
            f"PDF {page.number}쪽의 exact 주택형·금액 행이 유일하지 않습니다."
        )
    return matches[0]


def _line_with_all(page: PdfPage, *fragments: str) -> str:
    matches = [
        line.strip()
        for line in page.text.splitlines()
        if all(_normalized(fragment) in _normalized(line) for fragment in fragments)
    ]
    if len(matches) != 1:
        raise ExpandedAllocationRepairError(
            f"PDF {page.number}쪽의 필수 문구를 가진 행이 유일하지 않습니다: {fragments}"
        )
    return matches[0]


def _line_with_dates(page: PdfPage, dates: tuple[date, ...]) -> str:
    matches = []
    for line in page.text.splitlines():
        digits = re.sub(r"\D", "", line)
        if all(value.strftime("%Y%m%d") in digits for value in dates) and "지정일" in line:
            matches.append(line.strip())
    if len(matches) != 1:
        raise ExpandedAllocationRepairError(
            f"PDF {page.number}쪽의 6개 중도금 납부일 행이 유일하지 않습니다."
        )
    return matches[0]


def _exact_paragraph(page: PdfPage, start: str, end: str) -> str:
    start_index = page.text.find(start)
    if start_index < 0:
        raise ExpandedAllocationRepairError(
            f"PDF {page.number}쪽에서 조건문 시작을 찾지 못했습니다."
        )
    end_index = page.text.find(end, start_index)
    if end_index < 0:
        raise ExpandedAllocationRepairError(f"PDF {page.number}쪽에서 조건문 끝을 찾지 못했습니다.")
    raw_text = page.text[start_index : end_index + len(end)].strip()
    required = (
        "40% 범위 내에서 중도금 융자 알선을 시행할 예정이며, "
        "1~4회차 대출을 받았을 경우 5~6회차 중도금(분양대금의 20%)은 "
        "계약자가 직접 납부해야 합니다."
    )
    if _normalized(required) not in _normalized(raw_text):
        raise ExpandedAllocationRepairError("알선 1~4회차/직접 납부 5~6회차 조건문이 변경됐습니다.")
    return raw_text


def _evidence(field: str, page: int, raw_text: str) -> Evidence:
    return Evidence(field=field, page=page, raw_text=raw_text)


def _remove_evidence(result: AnalysisResponse, prefixes: tuple[str, ...]) -> None:
    result.evidence = [
        item for item in result.evidence if not any(item.field.startswith(p) for p in prefixes)
    ]


def _source_lock(
    result: AnalysisResponse,
    pages: list[PdfPage],
) -> tuple[_DocumentPolicy, _TargetPolicy] | None:
    policy = _POLICIES.get(result.complex_id)
    if policy is None:
        return None
    if result.review_status == ReviewStatus.REVIEWED:
        raise ExpandedAllocationRepairError(
            "REVIEWED 결과는 자동 교정 입력으로 사용할 수 없습니다."
        )
    if (
        result.meta.source_sha256 != policy.source_sha256
        or result.meta.source_page_count != policy.source_page_count
        or len(pages) != policy.source_page_count
        or [page.number for page in pages] != list(range(1, policy.source_page_count + 1))
    ):
        raise ExpandedAllocationRepairError("감사한 PDF source lock과 다릅니다.")
    unit_id = result.target_unit.unit_type_id or ""
    target = policy.targets.get(unit_id)
    if target is None or (
        result.target_unit.unit_type_name != target.unit_name
        or result.target_unit.sale_price_manwon != target.sale_price_manwon
    ):
        raise ExpandedAllocationRepairError("감사한 exact unit tuple과 다릅니다.")
    mapping = _page(pages, policy.mapping_page)
    mapping_quote = f"{unit_id} {target.raw_unit_name} {target.unit_name}"
    if _normalized(mapping_quote) not in _normalized(mapping.text):
        raise ExpandedAllocationRepairError("요청 unit tuple을 PDF 공급대상표에서 찾지 못했습니다.")
    return policy, target


def _repair_payment(
    result: AnalysisResponse,
    pages: list[PdfPage],
    policy: _DocumentPolicy,
    target: _TargetPolicy,
) -> None:
    price = target.sale_price_manwon
    down = price // 10
    if price % 10:
        raise ExpandedAllocationRepairError("핵심 납부금이 정수 만원으로 닫히지 않습니다.")
    interim_each = down
    balance = price * 3 // 10
    expected_won = (
        price * 10_000,
        down * 10_000,
        *((interim_each * 10_000,) * 6),
        balance * 10_000,
    )
    payment_page = _page(pages, policy.payment_page)
    row = _line_with_values(
        payment_page,
        row_label=target.payment_row_label,
        expected_won=expected_won,
    )
    dates_line = _line_with_dates(payment_page, policy.payment_dates)
    move_in_line = _line_with_all(
        _page(pages, policy.move_in_page),
        "입주시기",
        policy.move_in_month.replace("-", "년 ") + "월",
    )

    result.payment_schedule = PaymentSchedule(
        down_payment=PaymentComponent(
            total_ratio=0.10,
            total_amount_manwon=down,
            basis=PaymentBasis.MIXED,
            installments=[
                Installment(
                    number=1,
                    ratio=0.10,
                    amount_manwon=down,
                    due_date=None,
                    due_text="계약 시",
                )
            ],
            due_date=None,
            due_month=None,
            due_text=None,
        ),
        interim_payment=PaymentComponent(
            total_ratio=0.60,
            total_amount_manwon=interim_each * 6,
            basis=PaymentBasis.MIXED,
            installments=[
                Installment(
                    number=index,
                    ratio=0.10,
                    amount_manwon=interim_each,
                    due_date=due_date,
                    due_text=None,
                )
                for index, due_date in enumerate(policy.payment_dates, start=1)
            ],
            due_date=None,
            due_month=None,
            due_text=None,
        ),
        balance_payment=PaymentComponent(
            total_ratio=0.30,
            total_amount_manwon=balance,
            basis=PaymentBasis.MIXED,
            installments=[],
            due_date=None,
            due_month=policy.move_in_month,
            due_text="입주지정일",
        ),
    )
    _remove_evidence(result, ("/payment_schedule/",))
    row_evidence = _evidence("/payment_schedule/down_payment", policy.payment_page, row)
    result.evidence.extend(
        [
            row_evidence,
            row_evidence.model_copy(update={"field": "/payment_schedule/interim_payment"}),
            row_evidence.model_copy(update={"field": "/payment_schedule/balance_payment"}),
            _evidence(
                "/payment_schedule/interim_payment/installments",
                policy.payment_page,
                dates_line,
            ),
            _evidence(
                "/payment_schedule/balance_payment/due_month",
                policy.move_in_page,
                move_in_line,
            ),
        ]
    )


def _repair_loan(
    result: AnalysisResponse,
    pages: list[PdfPage],
    policy: _DocumentPolicy,
    target: _TargetPolicy,
) -> None:
    paragraph = _exact_paragraph(_page(pages, policy.loan_page), _LOAN_START, _LOAN_END)
    result.interim_loan = InterimLoan(
        arrangement_status=LoanArrangementStatus.PLANNED,
        arranged_ratio=0.40,
        arranged_amount_manwon=target.sale_price_manwon * 4 // 10,
        self_funding_ratio=0.20,
        self_funding_amount_manwon=target.sale_price_manwon * 2 // 10,
        self_funding_origin=ValueOrigin.EXTRACTED,
        bank_names=[],
        guarantee_provider=None,
        interest_type=InterestType.DEFERRED_INTEREST,
        interest_note="중도금 대출 이자후불제",
        prepay_requirement_ratio=None,
    )
    _remove_evidence(result, ("/interim_loan/",))
    for field in (
        "/interim_loan/arrangement_status",
        "/interim_loan/arranged_ratio",
        "/interim_loan/arranged_amount_manwon",
        "/interim_loan/self_funding_ratio",
        "/interim_loan/self_funding_amount_manwon",
        _ALLOCATION_EVIDENCE_FIELD,
        "/interim_loan/interest_type",
        "/interim_loan/interest_note",
    ):
        result.evidence.append(_evidence(field, policy.loan_page, paragraph))


def _repair_balcony(
    result: AnalysisResponse,
    pages: list[PdfPage],
    policy: _DocumentPolicy,
    target: _TargetPolicy,
) -> None:
    page = _page(pages, policy.balcony_page)
    total = target.balcony_total_manwon
    contract = total // 10
    if total % 10:
        raise ExpandedAllocationRepairError("발코니 분납액이 정수 만원으로 닫히지 않습니다.")
    if policy.balcony_interim_date is None:
        payment_amounts = (contract, total - contract)
    else:
        payment_amounts = (contract, contract, total - 2 * contract)
    row = _line_with_values(
        page,
        row_label=target.unit_name,
        expected_won=tuple(value * 10_000 for value in (total, *payment_amounts)),
    )
    if policy.balcony_interim_date is None:
        header = _line_with_all(page, "계약금(10%)", "잔금(90%)")
        payments = [
            AdditionalCostPayment(
                number=1,
                stage=PaymentStage.CONTRACT,
                amount_manwon=payment_amounts[0],
                due_date=None,
                due_text="계약 시",
            ),
            AdditionalCostPayment(
                number=2,
                stage=PaymentStage.BALANCE,
                amount_manwon=payment_amounts[1],
                due_date=None,
                due_text=policy.balcony_balance_due_text,
            ),
        ]
        optional_line = _line_with_all(page, "별도로", "선택하여", "발코니 확장 계약")
        not_included_line = optional_line
    else:
        header = _line_with_all(page, "계약금(10%)", "중도금(10%)", "잔금(80%)")
        payments = [
            AdditionalCostPayment(
                number=1,
                stage=PaymentStage.CONTRACT,
                amount_manwon=payment_amounts[0],
                due_date=None,
                due_text="계약시",
            ),
            AdditionalCostPayment(
                number=2,
                stage=PaymentStage.INTERIM,
                amount_manwon=payment_amounts[1],
                due_date=policy.balcony_interim_date,
                due_text=None,
            ),
            AdditionalCostPayment(
                number=3,
                stage=PaymentStage.BALANCE,
                amount_manwon=payment_amounts[2],
                due_date=None,
                due_text=policy.balcony_balance_due_text,
            ),
        ]
        optional_line = _line_with_all(page, "세대별로 택하여", "별도 계약 품목")
        not_included_line = optional_line

    result.additional_costs = [
        AdditionalCost(
            type=AdditionalCostType.BALCONY_EXTENSION,
            name="발코니 확장 공사비",
            total_amount_manwon=total,
            required=False,
            included_in_sale_price=False,
            applicable_unit_type=target.unit_name,
            payments=payments,
            note="선택사항 · 공급금액 미포함; 전체 유상옵션 범위는 별도 확인",
        )
    ]
    _remove_evidence(result, ("/additional_costs/",))
    row_evidence = _evidence("/additional_costs/0", policy.balcony_page, row)
    result.evidence.extend(
        [
            row_evidence,
            row_evidence.model_copy(update={"field": "/additional_costs/0/type"}),
            row_evidence.model_copy(update={"field": "/additional_costs/0/name"}),
            row_evidence.model_copy(update={"field": "/additional_costs/0/applicable_unit_type"}),
            _evidence("/additional_costs/0/required", policy.balcony_page, optional_line),
            _evidence(
                "/additional_costs/0/included_in_sale_price",
                policy.balcony_page,
                not_included_line,
            ),
            _evidence("/additional_costs/0/note", policy.balcony_page, not_included_line),
        ]
    )
    for index, payment in enumerate(payments):
        for field in ("stage", "due_date" if payment.due_date is not None else "due_text"):
            result.evidence.append(
                _evidence(
                    f"/additional_costs/0/payments/{index}/{field}",
                    policy.balcony_page,
                    header,
                )
            )


def has_exact_installment_allocation_evidence(result: AnalysisResponse) -> bool:
    """Return true only for the source-locked literal 1-4/5-6 allocation sentence."""

    policy = _POLICIES.get(result.complex_id)
    if policy is None:
        return False
    matches = [
        evidence
        for evidence in result.evidence
        if evidence.field == _ALLOCATION_EVIDENCE_FIELD
        and evidence.page == policy.loan_page
        and _normalized(_LOAN_START) in _normalized(evidence.raw_text)
        and _normalized("1~4회차 대출을 받았을 경우 5~6회차 중도금")
        in _normalized(evidence.raw_text)
        and _normalized(_LOAN_END) in _normalized(evidence.raw_text)
    ]
    return len(matches) == 1


def repair_expanded_audited_allocation(
    result: AnalysisResponse,
    *,
    pages: list[PdfPage],
) -> AnalysisResponse:
    """Repair two exact 1-4/5-6 allocation documents without approving them."""

    matched = _source_lock(result, pages)
    if matched is None:
        return result
    policy, target = matched
    corrected = result.model_copy(deep=True)
    _repair_payment(corrected, pages, policy, target)
    _repair_loan(corrected, pages, policy, target)
    _repair_balcony(corrected, pages, policy, target)
    if ExceptionFlag.ADDITIONAL_COST_SCOPE_LIMITED not in corrected.exception_flags:
        corrected.exception_flags.append(ExceptionFlag.ADDITIONAL_COST_SCOPE_LIMITED)
    prepared = prepare_review_draft(
        corrected,
        source_sha256=policy.source_sha256,
        pages=pages,
    )
    if not prepared.validation.passed:
        raise ExpandedAllocationRepairError("exact 교정본 PDF 재검증에 실패했습니다.")
    if not has_exact_installment_allocation_evidence(prepared):
        raise ExpandedAllocationRepairError(
            "회차별 조달 배분 조건문이 재검증 결과에 남지 않았습니다."
        )
    if prepared.review_status == ReviewStatus.REVIEWED:
        raise AssertionError("자동 교정이 REVIEWED를 부여했습니다.")
    return prepared
