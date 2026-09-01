from __future__ import annotations

import re
import unicodedata
from datetime import date

from get_myhome_ai.candidates import CandidatePage
from get_myhome_ai.models import (
    AdditionalCost,
    AdditionalCostPayment,
    AdditionalCostType,
    Evidence,
    ExceptionFlag,
    ExtractionDraft,
    Installment,
    InterestType,
    LoanArrangementStatus,
    PaymentBasis,
    PaymentStage,
    ValueOrigin,
)

RATIO_HEADER = re.compile(
    r"(?P<down_text>계약금\s*\(\s*(?P<down>\d+(?:\.\d+)?)\s*%\s*\))"
    r".{0,500}?"
    r"(?P<interim_text>중도금\s*\(\s*(?P<interim>\d+(?:\.\d+)?)\s*%\s*\))"
    r".{0,500}?"
    r"(?P<balance_text>잔금\s*\(\s*(?P<balance>\d+(?:\.\d+)?)\s*%\s*\))",
    re.DOTALL,
)
PARTIAL_RATIO_HEADER = re.compile(
    r"(?P<down_text>계약금\s*\(\s*(?P<down>\d+(?:\.\d+)?)\s*%\s*\))"
    r".{0,500}?"
    r"(?P<interim_text>중도금\s*\(\s*(?P<interim>\d+(?:\.\d+)?)\s*%\s*\))"
    r".{0,500}?"
    r"(?P<balance_text>잔금)",
    re.DOTALL,
)
INSTALLMENT_HEADER = re.compile(r"(\d+)\s*회\s*\(\s*(\d+(?:\.\d+)?)\s*%\s*\)")
SIMPLE_INSTALLMENT_HEADER = re.compile(r"(?<!\d)(\d+)\s*회(?!\s*\()")
DATE_TEXT = re.compile(r"(20\d{2})[.-]\s*(\d{1,2})[.-]\s*(\d{1,2})[.]?")
MOVE_IN_MONTH = re.compile(r"입주시기\s*[:\N{FULLWIDTH COLON}]\s*(20\d{2})년\s*(\d{1,2})월")
LOAN_SPLIT = re.compile(
    r"(?P<raw>"
    r"총\s*(?:공급|분양)\s*대금의\s*(?P<interim>\d+(?:\.\d+)?)\s*%\s*중"
    r".{0,150}?"
    r"총\s*(?:공급|분양)\s*대금의\s*(?P<arranged>\d+(?:\.\d+)?)\s*%"
    r".{0,200}?"
    r"나머지\s*총\s*(?:공급|분양)\s*대금의\s*(?P<self>\d+(?:\.\d+)?)\s*%"
    r".{0,100}?(?:자납|직접\s*납부)"
    r")",
    re.DOTALL,
)
LOAN_RATIO_ONLY = re.compile(
    r"(?P<raw>대출은\s*중도금\s*\(\s*총\s*(?:공급|분양)\s*대금의\s*"
    r"(?P<arranged>\d+(?:\.\d+)?)\s*%\s*\)\s*범위\s*내에서\s*가능)"
)
PREPAY = re.compile(r"(?P<raw>분양대금의\s*총\s*(?P<ratio>\d+(?:\.\d+)?)\s*%\s*완납\s*후)")
NOT_AVAILABLE = re.compile(r"(?P<raw>본\s*아파트는\s*중도금대출이?\s*불가하며)")
DEFERRED_INTEREST = re.compile(
    r"(?P<raw>중도금\s*대출\s*이자는.{0,300}?대납.{0,300}?(?:정산|완납))",
    re.DOTALL,
)
INTEREST_LABEL = re.compile(r"(?P<raw>중도금\s*대출\s*[“\"']?이자후불제[”\"']?)")
EXPLICIT_INCLUDED = re.compile(r"공급가에는?.{0,100}?발코니.{0,100}?포함", re.DOTALL)
ARRANGEMENT_PLANNED = re.compile(
    r"(?P<raw>(?:"
    r"(?:중도금.{0,120}?)?(?:대출|융자)\s*알선.{0,80}?(?:예정|가능)"
    r"|(?:사업주체|시행위탁자|시행사).{0,80}?알선한.{0,50}?(?:대출취급기관|금융기관)"
    r"))",
    re.DOTALL,
)
ARRANGEMENT_DISCUSSION = re.compile(
    r"(?P<raw>(?:중도금.{0,120}?)?(?:대출|융자).{0,80}?(?:금융기관.{0,40}?)?협의\s*중)",
    re.DOTALL,
)
MEDIATION_NOT_GUARANTEED = re.compile(
    r"(?P<raw>(?:대출\s*)?알선.{0,140}?(?:불가할\s*수|의무사항이\s*아니))",
    re.DOTALL,
)
INDIVIDUAL_REVIEW = re.compile(
    r"(?P<raw>(?:개인|계약자).{0,120}?(?:신용|자격|사정|심사).{0,100}?(?:한도.{0,30}?상이|대출.{0,20}?불가|심사))",
    re.DOTALL,
)
TERMS_BY_TYPE = re.compile(
    r"(?P<raw>(?:주택형|타입)별.{0,100}?(?:대출|중도금).{0,80}?(?:상이|다름))",
    re.DOTALL,
)


def _ratio(value: str) -> float:
    return float(value) / 100


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def _evidence_key(item: Evidence) -> tuple[str, int, str]:
    return item.field, item.page, item.raw_text


def _append_evidence(items: list[Evidence], item: Evidence) -> None:
    if _evidence_key(item) not in {_evidence_key(existing) for existing in items}:
        items.append(item)


def _won_values(raw_text: str) -> list[int]:
    return [int(value.replace(",", "")) for value in re.findall(r"\d[\d,]{3,}", raw_text)]


def _sale_price_row(
    page: CandidatePage, sale_price_manwon: int | None
) -> tuple[str, list[int]] | None:
    if sale_price_manwon is None:
        return None
    sale_price_won = sale_price_manwon * 10_000
    for line in page.text.splitlines():
        values = _won_values(line)
        if sale_price_won in values:
            return line.strip(), values
    return None


def _ground_payment(
    draft: ExtractionDraft,
    pages: list[CandidatePage],
    *,
    sale_price_manwon: int | None,
) -> list[Evidence]:
    evidence: list[Evidence] = []
    schedule = draft.payment_schedule

    selections: list[tuple[int, CandidatePage, re.Match[str] | None]] = []
    for page in sorted(pages, key=lambda item: item.number):
        match = RATIO_HEADER.search(page.text) or PARTIAL_RATIO_HEADER.search(page.text)
        if match:
            score = 0
            if _sale_price_row(page, sale_price_manwon) is not None:
                score += 100
            if "공급금액" in page.text:
                score += 50
            if RATIO_HEADER.search(page.text):
                score += 30
            if any(term in page.text for term in ("추가선택품목", "발코니 확장 공사비")):
                score -= 20
            selections.append((score, page, match))

    if not selections:
        for page in sorted(pages, key=lambda item: item.number):
            if (
                all(term in page.text for term in ("계약금", "중도금", "잔금"))
                and "공급금액" in page.text
                and _sale_price_row(page, sale_price_manwon) is not None
            ):
                selections.append((100, page, None))

    selected = None
    if selections:
        _, selected_page, selected_match = max(
            selections,
            key=lambda item: (item[0], -item[1].number),
        )
        selected = selected_page, selected_match

    if selected:
        page, match = selected
        components = (
            ("down_payment", schedule.down_payment, "down", "down_text"),
            ("interim_payment", schedule.interim_payment, "interim", "interim_text"),
            ("balance_payment", schedule.balance_payment, "balance", "balance_text"),
        )
        for name, component, ratio_group, text_group in components:
            ratio_text = match.groupdict().get(ratio_group) if match is not None else None
            if match is None and name == "down_payment":
                down_ratio = re.search(r"계약금\s*\(\s*(\d+(?:\.\d+)?)\s*%\s*\)", page.text)
                ratio_text = down_ratio.group(1) if down_ratio else None
                if down_ratio:
                    _append_evidence(
                        evidence,
                        Evidence(
                            field="/payment_schedule/down_payment/total_ratio",
                            page=page.number,
                            raw_text=down_ratio.group(0),
                        ),
                    )
            component.total_ratio = _ratio(ratio_text) if ratio_text is not None else None
            component.total_amount_manwon = None
            component.basis = PaymentBasis.RATIO
            component.installments = []
            component.due_date = None
            component.due_month = None
            component.due_text = None
            if match is not None:
                _append_evidence(
                    evidence,
                    Evidence(
                        field=f"/payment_schedule/{name}",
                        page=page.number,
                        raw_text=match.group(text_group),
                    ),
                )

        section = page.text[match.start() : match.end() + 3_500] if match is not None else page.text
        installment_headers = INSTALLMENT_HEADER.findall(section[:1_800])
        simple_installment_numbers = [
            int(value) for value in SIMPLE_INSTALLMENT_HEADER.findall(section[:1_000])
        ]
        declared_installment_count = len(installment_headers) or (
            max(simple_installment_numbers) if simple_installment_numbers else 0
        )
        # Dates after the declared N installments can be subscription or
        # construction notices.  Keep only the first N dates following the
        # payment header.
        all_dates = list(DATE_TEXT.finditer(section))
        dates = all_dates[:declared_installment_count] if declared_installment_count else []
        row = _sale_price_row(page, sale_price_manwon)
        row_text: str | None = None
        row_values: list[int] = []
        interim_amounts_manwon: list[int] = []
        if row is not None and dates:
            row_text, row_values = row
            sale_price_won = sale_price_manwon * 10_000 if sale_price_manwon else 0
            sale_index = row_values.index(sale_price_won)
            payment_values = row_values[sale_index + 1 :]
            interim_count = declared_installment_count
            if len(payment_values) >= interim_count + 2:
                contract_values = payment_values[: -(interim_count + 1)]
                interim_values = payment_values[-(interim_count + 1) : -1]
                balance_value = payment_values[-1]
                schedule.down_payment.total_amount_manwon = round(sum(contract_values) / 10_000)
                days_due = re.search(r"(\d+)\s*일\s*이내", section)
                schedule.down_payment.installments = [
                    Installment(
                        number=index,
                        ratio=None,
                        amount_manwon=round(value / 10_000),
                        due_date=None,
                        due_text=(
                            "계약 시"
                            if index == 1
                            else (f"계약 후 {days_due.group(1)}일 이내" if days_due else "계약 후")
                        ),
                    )
                    for index, value in enumerate(contract_values, start=1)
                ]
                interim_amounts_manwon = [round(value / 10_000) for value in interim_values]
                schedule.balance_payment.total_amount_manwon = round(balance_value / 10_000)
                schedule.balance_payment.basis = PaymentBasis.MIXED
                for name in ("down_payment", "interim_payment", "balance_payment"):
                    _append_evidence(
                        evidence,
                        Evidence(
                            field=f"/payment_schedule/{name}",
                            page=page.number,
                            raw_text=row_text,
                        ),
                    )
                if match is None:
                    _append_evidence(
                        evidence,
                        Evidence(
                            field="/payment_schedule",
                            page=page.number,
                            raw_text=row_text,
                        ),
                    )

        installment_count = declared_installment_count
        if installment_count and len(dates) >= installment_count:
            schedule.interim_payment.installments = [
                Installment(
                    number=(
                        int(installment_headers[index][0]) if installment_headers else index + 1
                    ),
                    ratio=(
                        _ratio(installment_headers[index][1])
                        if installment_headers
                        else (
                            round(interim_amounts_manwon[index] / sale_price_manwon, 10)
                            if (
                                interim_amounts_manwon
                                and sale_price_manwon
                                and schedule.interim_payment.total_ratio is not None
                            )
                            else None
                        )
                    ),
                    amount_manwon=(
                        interim_amounts_manwon[index] if interim_amounts_manwon else None
                    ),
                    due_date=date(
                        int(dates[index].group(1)),
                        int(dates[index].group(2)),
                        int(dates[index].group(3)),
                    ),
                    due_text=None,
                )
                for index in range(installment_count)
            ]
            if interim_amounts_manwon:
                schedule.interim_payment.total_amount_manwon = sum(interim_amounts_manwon)
                schedule.interim_payment.basis = PaymentBasis.MIXED
            raw_dates = section[dates[0].start() : dates[installment_count - 1].end()]
            _append_evidence(
                evidence,
                Evidence(
                    field="/payment_schedule/interim_payment/installments",
                    page=page.number,
                    raw_text=raw_dates,
                ),
            )

        contract_due = re.search(r"(\d+)\s*일\s*이내", section)
        if contract_due:
            schedule.down_payment.due_text = f"계약 시 및 계약 후 {contract_due.group(1)}일 이내"
            _append_evidence(
                evidence,
                Evidence(
                    field="/payment_schedule/down_payment/due_text",
                    page=page.number,
                    raw_text=contract_due.group(0),
                ),
            )
        if "입주지정일" in section:
            schedule.balance_payment.due_text = "입주지정일"
            _append_evidence(
                evidence,
                Evidence(
                    field="/payment_schedule/balance_payment/due_text",
                    page=page.number,
                    raw_text="입주지정일",
                ),
            )

    for page in sorted(pages, key=lambda item: item.number):
        match = MOVE_IN_MONTH.search(page.text)
        if not match:
            continue
        schedule.balance_payment.due_month = f"{int(match.group(1)):04d}-{int(match.group(2)):02d}"
        if schedule.balance_payment.due_text is None:
            schedule.balance_payment.due_text = "입주지정일"
        _append_evidence(
            evidence,
            Evidence(
                field="/payment_schedule/balance_payment/due_month",
                page=page.number,
                raw_text=match.group(0),
            ),
        )
        break

    return evidence


def _find(pages: list[CandidatePage], pattern: re.Pattern[str]) -> tuple[int, re.Match[str]] | None:
    for page in sorted(pages, key=lambda item: item.number):
        match = pattern.search(page.text)
        if match:
            return page.number, match
    return None


def _verified_bank_names(draft: ExtractionDraft) -> list[str]:
    if not draft.interim_loan.bank_names:
        return []
    for item in draft.evidence:
        if item.field != "/interim_loan/bank_names":
            continue
        if not any(term in item.raw_text for term in ("중도금", "대출", "금융기관")):
            continue
        if all(name in item.raw_text for name in draft.interim_loan.bank_names):
            return draft.interim_loan.bank_names
    return []


def _ground_loan(draft: ExtractionDraft, pages: list[CandidatePage]) -> list[Evidence]:
    evidence: list[Evidence] = []
    loan = draft.interim_loan
    loan.guarantee_provider = None

    unavailable = _find(pages, NOT_AVAILABLE)
    split = _find(pages, LOAN_SPLIT)
    ratio_only = _find(pages, LOAN_RATIO_ONLY)
    discussion = _find(pages, ARRANGEMENT_DISCUSSION)
    planned = _find(pages, ARRANGEMENT_PLANNED)
    verified_banks = _verified_bank_names(draft)
    if unavailable:
        page_number, match = unavailable
        loan.arrangement_status = LoanArrangementStatus.NOT_AVAILABLE
        loan.arranged_ratio = None
        loan.arranged_amount_manwon = None
        loan.self_funding_ratio = None
        loan.self_funding_amount_manwon = None
        loan.self_funding_origin = None
        loan.bank_names = []
        loan.interest_type = InterestType.NOT_APPLICABLE
        loan.interest_note = None
        loan.prepay_requirement_ratio = None
        _append_evidence(
            evidence,
            Evidence(
                field="/interim_loan/arrangement_status",
                page=page_number,
                raw_text=match.group("raw"),
            ),
        )
    elif split:
        page_number, match = split
        if discussion:
            loan.arrangement_status = LoanArrangementStatus.UNDER_DISCUSSION
        elif verified_banks:
            loan.arrangement_status = LoanArrangementStatus.BANK_SELECTED
        elif planned:
            loan.arrangement_status = LoanArrangementStatus.PLANNED
        else:
            loan.arrangement_status = LoanArrangementStatus.NOT_STATED
        loan.arranged_ratio = _ratio(match.group("arranged"))
        loan.arranged_amount_manwon = None
        loan.self_funding_ratio = _ratio(match.group("self"))
        loan.self_funding_amount_manwon = None
        loan.self_funding_origin = ValueOrigin.EXTRACTED
        loan.bank_names = verified_banks
        _append_evidence(
            evidence,
            Evidence(
                field="/interim_loan/arranged_ratio",
                page=page_number,
                raw_text=match.group("raw"),
            ),
        )
        _append_evidence(
            evidence,
            Evidence(
                field="/interim_loan/self_funding_ratio",
                page=page_number,
                raw_text=match.group("raw"),
            ),
        )
    elif ratio_only:
        page_number, match = ratio_only
        if discussion:
            loan.arrangement_status = LoanArrangementStatus.UNDER_DISCUSSION
        elif verified_banks:
            loan.arrangement_status = LoanArrangementStatus.BANK_SELECTED
        elif planned:
            loan.arrangement_status = LoanArrangementStatus.PLANNED
        else:
            loan.arrangement_status = LoanArrangementStatus.NOT_STATED
        loan.arranged_ratio = _ratio(match.group("arranged"))
        loan.arranged_amount_manwon = None
        loan.self_funding_ratio = None
        loan.self_funding_amount_manwon = None
        loan.self_funding_origin = None
        loan.bank_names = verified_banks
        _append_evidence(
            evidence,
            Evidence(
                field="/interim_loan/arranged_ratio",
                page=page_number,
                raw_text=match.group("raw"),
            ),
        )

    status_evidence = discussion or planned
    if status_evidence and not unavailable:
        page_number, match = status_evidence
        _append_evidence(
            evidence,
            Evidence(
                field="/interim_loan/arrangement_status",
                page=page_number,
                raw_text=match.group("raw"),
            ),
        )
    elif verified_banks and not unavailable:
        bank_evidence = next(
            item for item in draft.evidence if item.field == "/interim_loan/bank_names"
        )
        _append_evidence(
            evidence,
            Evidence(
                field="/interim_loan/arrangement_status",
                page=bank_evidence.page,
                raw_text=bank_evidence.raw_text,
            ),
        )

    prepay = _find(pages, PREPAY)
    if prepay and not unavailable:
        page_number, match = prepay
        loan.prepay_requirement_ratio = _ratio(match.group("ratio"))
        _append_evidence(
            evidence,
            Evidence(
                field="/interim_loan/prepay_requirement_ratio",
                page=page_number,
                raw_text=match.group("raw"),
            ),
        )

    interest = _find(pages, INTEREST_LABEL) or _find(pages, DEFERRED_INTEREST)
    if interest and not unavailable:
        page_number, match = interest
        loan.interest_type = InterestType.DEFERRED_INTEREST
        loan.interest_note = _normalized(match.group("raw"))[:500]
        _append_evidence(
            evidence,
            Evidence(
                field="/interim_loan/interest_type",
                page=page_number,
                raw_text=match.group("raw"),
            ),
        )
        _append_evidence(
            evidence,
            Evidence(
                field="/interim_loan/interest_note",
                page=page_number,
                raw_text=match.group("raw"),
            ),
        )
    return evidence


def _ground_exception_flags(
    draft: ExtractionDraft,
    pages: list[CandidatePage],
) -> list[Evidence]:
    evidence: list[Evidence] = []
    flags: list[ExceptionFlag] = []

    mediation = _find(pages, MEDIATION_NOT_GUARANTEED)
    if mediation and draft.interim_loan.arrangement_status != LoanArrangementStatus.NOT_AVAILABLE:
        flags.append(ExceptionFlag.LOAN_MEDIATION_NOT_GUARANTEED)
        page_number, match = mediation
        evidence.append(
            Evidence(field="/exception_flags", page=page_number, raw_text=match.group("raw"))
        )

    if (draft.interim_loan.self_funding_ratio or 0) > 0 or (
        draft.interim_loan.self_funding_amount_manwon or 0
    ) > 0:
        flags.append(ExceptionFlag.SELF_FUNDING_REQUIRED)

    terms_by_type = _find(pages, TERMS_BY_TYPE)
    if terms_by_type:
        flags.append(ExceptionFlag.TERMS_DIFFER_BY_TYPE)
        page_number, match = terms_by_type
        evidence.append(
            Evidence(field="/exception_flags", page=page_number, raw_text=match.group("raw"))
        )

    individual_review = _find(pages, INDIVIDUAL_REVIEW)
    if individual_review:
        flags.append(ExceptionFlag.INDIVIDUAL_REVIEW_NOTED)
        page_number, match = individual_review
        evidence.append(
            Evidence(field="/exception_flags", page=page_number, raw_text=match.group("raw"))
        )

    draft.exception_flags = flags
    return evidence


def _cost_row(
    pages: list[CandidatePage],
    unit_name: str,
    cost_type: str,
) -> tuple[int, str] | None:
    terms = {
        "BALCONY_EXTENSION": ("발코니 확장", "발코니확장"),
        "SYSTEM_AIR_CONDITIONER": ("시스템 에어컨", "시스템에어컨"),
    }.get(cost_type, ())
    all_headings = ("발코니 확장", "발코니확장", "시스템 에어컨", "시스템에어컨")
    for page in sorted(pages, key=lambda item: item.number):
        if "cost" not in page.categories:
            continue
        if terms and not any(term in page.text for term in terms):
            continue
        lines = page.text.splitlines()
        heading_indices = [
            index for index, line in enumerate(lines) if any(term in line for term in terms)
        ]
        for heading_index in heading_indices:
            for index in range(heading_index, min(len(lines), heading_index + 120)):
                line = lines[index]
                if index > heading_index and any(term in line for term in all_headings):
                    break
                if _normalized(unit_name) in _normalized(line) and len(_won_values(line)) >= 2:
                    return page.number, line.strip()
    return None


def _cost_payment_template(
    pages: list[CandidatePage],
    *,
    page_number: int,
    raw_row: str,
) -> list[tuple[PaymentStage, date | None, str | None]]:
    page = next((item for item in pages if item.number == page_number), None)
    if page is None:
        return []
    lines = page.text.splitlines()
    row_index = next(
        (index for index, line in enumerate(lines) if _normalized(raw_row) in _normalized(line)),
        None,
    )
    if row_index is None:
        return []
    start = max(0, row_index - 20)
    candidates = [
        (index, re.findall(r"계약금|중도금|잔금", lines[index]))
        for index in range(start, row_index)
    ]
    candidates = [(index, labels) for index, labels in candidates if len(labels) >= 2]
    if not candidates:
        return []
    header_index, labels = candidates[-1]
    context = " ".join(lines[header_index:row_index])
    dates = list(DATE_TEXT.finditer(context))
    date_index = 0
    result: list[tuple[PaymentStage, date | None, str | None]] = []
    for label in labels:
        if label == "계약금":
            result.append((PaymentStage.CONTRACT, None, "계약 시"))
        elif label == "중도금":
            due_date = None
            if date_index < len(dates):
                match = dates[date_index]
                due_date = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
                date_index += 1
            result.append((PaymentStage.INTERIM, due_date, None))
        else:
            result.append((PaymentStage.BALANCE, None, "입주지정일"))
    return result


def _ground_costs(
    draft: ExtractionDraft,
    pages: list[CandidatePage],
    *,
    unit_type_name: str | None,
    sale_price_manwon: int | None,
) -> list[Evidence]:
    evidence: list[Evidence] = []
    unit = unit_type_name.split()[0] if unit_type_name else None

    included_evidence = _find(pages, EXPLICIT_INCLUDED)
    explicitly_included = included_evidence is not None
    if (
        explicitly_included
        and unit
        and not any(
            cost.type == AdditionalCostType.BALCONY_EXTENSION for cost in draft.additional_costs
        )
    ):
        draft.additional_costs.append(
            AdditionalCost(
                type=AdditionalCostType.BALCONY_EXTENSION,
                name="발코니 확장비",
                total_amount_manwon=None,
                required=True,
                included_in_sale_price=True,
                applicable_unit_type=unit,
                payments=[],
                note="공급금액에 포함",
            )
        )
    grounded_costs: list[AdditionalCost] = []
    grounded_rows: list[tuple[int, str] | None] = []
    seen: set[tuple[str, int, str]] = set()
    for cost in draft.additional_costs:
        if "시스템" in cost.name and "에어컨" in cost.name:
            cost.type = AdditionalCostType.SYSTEM_AIR_CONDITIONER
        is_included_balcony = (
            explicitly_included and cost.type == AdditionalCostType.BALCONY_EXTENSION
        )
        if is_included_balcony:
            assert included_evidence is not None
            included_key = (
                cost.type.value,
                included_evidence[0],
                included_evidence[1].group(0),
            )
            if included_key in seen:
                continue
            seen.add(included_key)
            row = None
        else:
            row = _cost_row(pages, unit, cost.type.value) if unit else None
        if unit and row is None and not is_included_balcony:
            continue
        if unit:
            cost.applicable_unit_type = unit
        if row is not None:
            key = (cost.type.value, row[0], row[1])
            if key in seen:
                continue
            seen.add(key)
        grounded_costs.append(cost)
        grounded_rows.append(row)
    draft.additional_costs = grounded_costs

    for index, (cost, row) in enumerate(zip(draft.additional_costs, grounded_rows, strict=True)):
        values = [cost.total_amount_manwon]
        values.extend(payment.amount_manwon for payment in cost.payments)
        amounts = [value for value in values if value is not None]
        looks_like_won = bool(
            amounts and sale_price_manwon is not None and max(amounts) > sale_price_manwon
        )
        if looks_like_won:
            if cost.total_amount_manwon is not None:
                cost.total_amount_manwon = round(cost.total_amount_manwon / 10_000)
            for payment in cost.payments:
                if payment.amount_manwon is not None:
                    payment.amount_manwon = round(payment.amount_manwon / 10_000)

        is_included_balcony = (
            explicitly_included and cost.type == AdditionalCostType.BALCONY_EXTENSION
        )
        cost.applicable_unit_type = unit or cost.applicable_unit_type
        cost.included_in_sale_price = True if is_included_balcony else None
        cost.required = True if is_included_balcony else None
        cost.note = "공급금액에 포함" if is_included_balcony else None
        if is_included_balcony:
            cost.total_amount_manwon = None
            cost.payments = []
        if included_evidence is not None and is_included_balcony:
            page_number, match = included_evidence
            _append_evidence(
                evidence,
                Evidence(
                    field=f"/additional_costs/{index}",
                    page=page_number,
                    raw_text=match.group(0),
                ),
            )
        for payment in cost.payments:
            if payment.stage in {PaymentStage.CONTRACT, PaymentStage.BALANCE}:
                payment.due_date = None
            if payment.due_text and DATE_TEXT.fullmatch(payment.due_text.strip()):
                match = DATE_TEXT.fullmatch(payment.due_text.strip())
                assert match is not None
                payment.due_date = date(
                    int(match.group(1)), int(match.group(2)), int(match.group(3))
                )
                payment.due_text = None

        if row is not None:
            page_number, raw_text = row
            row_amounts = _won_values(raw_text)
            if len(row_amounts) >= 2:
                cost.total_amount_manwon = round(row_amounts[0] / 10_000)
                payment_amounts = row_amounts[1:]
                template = _cost_payment_template(
                    pages,
                    page_number=page_number,
                    raw_row=raw_text,
                )
                if len(template) == len(payment_amounts):
                    cost.payments = [
                        AdditionalCostPayment(
                            number=number,
                            stage=stage,
                            amount_manwon=round(amount / 10_000),
                            due_date=due_date,
                            due_text=due_text,
                        )
                        for number, ((stage, due_date, due_text), amount) in enumerate(
                            zip(template, payment_amounts, strict=True),
                            start=1,
                        )
                    ]
                elif len(cost.payments) == len(payment_amounts):
                    for payment, amount in zip(cost.payments, payment_amounts, strict=True):
                        payment.amount_manwon = round(amount / 10_000)
                        if payment.stage in {
                            PaymentStage.CONTRACT,
                            PaymentStage.BALANCE,
                            PaymentStage.MOVE_IN,
                        }:
                            payment.due_date = None
                else:
                    # The row proves the total, but the model-proposed schedule
                    # does not align with its payment columns.  Preserve the
                    # proven total and surface an explicit schedule HOLD instead
                    # of retaining contradictory model installments.
                    cost.payments = []
            _append_evidence(
                evidence,
                Evidence(
                    field=f"/additional_costs/{index}",
                    page=page_number,
                    raw_text=raw_text,
                ),
            )
    return evidence


def _valid_model_evidence(items: list[Evidence], pages: list[CandidatePage]) -> list[Evidence]:
    page_text = {page.number: _normalized(page.text) for page in pages}
    return [
        item
        for item in items
        if (
            # Payment and additional-cost values are repaired below.  Keeping the
            # model's pre-repair array indices can attach an otherwise exact quote
            # to the wrong installment or to a cost item that was filtered out.
            # Those sections therefore use only the post-repair evidence emitted
            # by the deterministic grounders.
            item.field
            in {
                "/interim_loan/interest_type",
                "/interim_loan/interest_note",
                "/interim_loan/bank_names",
            }
            and item.page in page_text
            and _normalized(item.raw_text) in page_text[item.page]
        )
    ]


def ground_ollama_draft(
    draft: ExtractionDraft,
    *,
    pages: list[CandidatePage],
    unit_type_name: str | None,
    sale_price_manwon: int | None,
) -> ExtractionDraft:
    grounded = draft.model_copy(deep=True)
    evidence = _valid_model_evidence(grounded.evidence, pages)
    for item in _ground_payment(grounded, pages, sale_price_manwon=sale_price_manwon):
        _append_evidence(evidence, item)
    for item in _ground_loan(grounded, pages):
        _append_evidence(evidence, item)
    for item in _ground_costs(
        grounded,
        pages,
        unit_type_name=unit_type_name,
        sale_price_manwon=sale_price_manwon,
    ):
        _append_evidence(evidence, item)
    for item in _ground_exception_flags(grounded, pages):
        _append_evidence(evidence, item)
    grounded.evidence = evidence
    return grounded
