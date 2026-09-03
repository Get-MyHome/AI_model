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
    LoanSettlementRequirement,
    PaymentBasis,
    PaymentStage,
    RiskClause,
    RiskClauseCode,
    ValueOrigin,
)

RATIO_HEADER = re.compile(
    r"(?P<down_text>계약\s*금\s*\(\s*(?P<down>\d+(?:\.\d+)?)\s*%\s*\))"
    r".{0,500}?"
    r"(?P<interim_text>중도\s*금\s*\(\s*(?P<interim>\d+(?:\.\d+)?)\s*%\s*\))"
    r".{0,500}?"
    r"(?P<balance_text>잔\s*금\s*\(\s*(?P<balance>\d+(?:\.\d+)?)\s*%\s*\))",
    re.DOTALL,
)
INTERIM_FIRST_RATIO_HEADER = re.compile(
    r"(?P<interim_text>중도\s*금\s*\(\s*(?P<interim>\d+(?:\.\d+)?)\s*%\s*\))"
    r".{0,500}?"
    r"(?P<down_text>계약\s*금\s*\(\s*(?P<down>\d+(?:\.\d+)?)\s*%\s*\))"
    r".{0,500}?"
    r"(?P<balance_text>잔\s*금\s*\(\s*(?P<balance>\d+(?:\.\d+)?)\s*%\s*\))",
    re.DOTALL,
)
PARTIAL_RATIO_HEADER = re.compile(
    r"(?P<down_text>계약\s*금\s*\(\s*(?P<down>\d+(?:\.\d+)?)\s*%\s*\))"
    r".{0,500}?"
    r"(?P<interim_text>중도\s*금\s*\(\s*(?P<interim>\d+(?:\.\d+)?)\s*%\s*\))"
    r".{0,500}?"
    r"(?P<balance_text>잔\s*금)",
    re.DOTALL,
)
REORDERED_RATIO_HEADER = re.compile(
    r"계약\s*금"
    r"(?:(?!\(\s*\d+(?:\.\d+)?\s*%\s*\)).){0,500}?"
    r"(?P<interim_text>중도\s*금\s*\(\s*(?P<interim>\d+(?:\.\d+)?)\s*%\s*\))"
    r".{0,300}?"
    r"(?P<balance_text>잔\s*금\s*\(\s*(?P<balance>\d+(?:\.\d+)?)\s*%\s*\))"
    r"(?:(?!\d+\s*(?:회|차)).){0,300}?"
    r"(?P<down_text>\(\s*(?P<down>\d+(?:\.\d+)?)\s*%\s*\))",
    re.DOTALL,
)
INSTALLMENT_HEADER = re.compile(r"(?<!\d)(\d+)\s*(?:회차|회|차)\s*\(\s*(\d+(?:\.\d+)?)\s*%\s*\)")
SIMPLE_INSTALLMENT_HEADER = re.compile(r"(?<!\d)(\d+)\s*(?:회차|회|차)(?!\s*\()")
DATE_TEXT = re.compile(r"(?<!\d)(20\d{2}|[2-9]\d)[.-]\s*(\d{1,2})[.-]\s*(\d{1,2})[.]?")
MOVE_IN_MONTH = re.compile(r"입주시기\s*[:\N{FULLWIDTH COLON}]\s*(20\d{2})년\s*(\d{1,2})월")
MAIN_PAYMENT_HEADING = re.compile(r"공급금액\s*(?:및\s*납부일정|표)")
BALANCE_DUE_PERIOD = re.compile(
    r"(?P<raw>입주지정기간의.{0,450}?만료일\s*또는"
    r".{0,650}?세대출입.{0,250}?열쇠\s*수령일\s*중.{0,180}?선\s*도래일)",
    re.DOTALL,
)
BALANCE_DUE_EARLIER = re.compile(
    r"(?P<raw>입주지정기간.{0,500}?만료일.{0,400}?또는"
    r".{0,1000}?실입주일\s*중.{0,250}?빠른\s*날)",
    re.DOTALL,
)
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
LOAN_RATIO_PARAGRAPH = re.compile(
    r"(?P<raw>"
    r"(?:중도금\s*(?:대출|융자)|(?:대출|융자).{0,40}?중도금)"
    r".{0,180}?"
    r"(?:전체|총)?\s*(?:공급|분양)\s*(?:대금|금액)(?:의|에)?\s*(?:중도금\s*)?"
    r"(?P<arranged>\d+(?:\.\d+)?)\s*%\s*범위(?:\s*\([^)]*\))?\s*내"
    r".{0,100}?(?:시행|알선|가능|예정)"
    r")",
    re.DOTALL,
)
LOAN_RATIO_BEFORE_ARRANGEMENT = re.compile(
    r"(?P<raw>"
    r"(?:전체|총)\s*(?:공급|분양)\s*금액\s*중\s*"
    r"(?P<arranged>\d+(?:\.\d+)?)\s*%\s*이내\s*중도금에\s*대하여"
    r".{0,180}?(?:사업주체|시행위탁자|시행사).{0,80}?알선한\s*금융기관"
    r")",
    re.DOTALL,
)
LOAN_RATIO_FULL_INSTALLMENTS = re.compile(
    r"(?P<raw>중도금\s*\([^)]*\)\s*\(\s*(?:공급|분양)대금의\s*"
    r"(?P<arranged>\d+(?:\.\d+)?)\s*%\s*\)\s*은\s*중도금\s*대출금으로\s*납부)",
    re.DOTALL,
)
LOAN_RATIO_INTEREST_ARRANGEMENT = re.compile(
    r"(?P<raw>(?:공급|분양)\s*대금의\s*"
    r"(?P<arranged>\d+(?:\.\d+)?)\s*%\s*이내에서"
    r".{0,120}?이자후불제\s*조건으로\s*중도금\s*대출(?:을|를)?\s*알선)",
    re.DOTALL,
)
PREPAY = re.compile(r"(?P<raw>분양대금의\s*총\s*(?P<ratio>\d+(?:\.\d+)?)\s*%\s*완납\s*후)")
PREPAY_PAID = re.compile(
    r"(?P<raw>(?:공급|분양)대금의\s*(?P<ratio>\d+(?:\.\d+)?)\s*%"
    r"(?:\s*\([^)]*\))?\s*(?:납입|완납)\s*시"
    r".{0,80}?중도금\s*대출(?:을|\s))",
    re.DOTALL,
)
PREPAY_CONTRACT_RATIO = re.compile(
    r"(?P<raw>계약금\s*\(\s*(?:공급|분양)(?:대금|가액)의\s*"
    r"(?P<ratio>\d+(?:\.\d+)?)\s*%\s*\)\s*완납"
    r"(?:\s*및.{0,80}?)?\s*이?후"
    r".{0,100}?중도금\s*대출)",
    re.DOTALL,
)
PREPAY_SIMPLE_CONTRACT_RATIO = re.compile(
    r"(?P<raw>계약금\s*(?P<ratio>\d+(?:\.\d+)?)\s*%\s*완납\s*이?후"
    r".{0,100}?중도금\s*대출)",
    re.DOTALL,
)
PREPAY_TOTAL_PAID = re.compile(
    r"(?P<raw>대출(?:은행|취급기관).{0,80}?협약.{0,120}?"
    r"(?:공급|분양)대금의\s*(?P<ratio>\d+(?:\.\d+)?)\s*%\s*이상"
    r"\s*납부\s*(?:이\s*)?후\s*(?:중도금\s*)?대출이?\s*가능)",
    re.DOTALL,
)
PREPAY_CONTRACT_COMPLETION = re.compile(
    r"(?P<raw>계약금.{0,100}?완납\s*(?:이\s*)?후.{0,160}?중도금\s*대출)",
    re.DOTALL,
)
NOT_AVAILABLE = re.compile(r"(?P<raw>본\s*아파트는\s*중도금대출이?\s*불가하며)")
DEFERRED_INTEREST = re.compile(
    r"(?P<raw>중도금\s*대출\s*이자는.{0,300}?대납.{0,300}?(?:정산|완납))",
    re.DOTALL,
)
INTEREST_LABEL = re.compile(
    r"(?P<raw>(?:대출\s*조건은\s*)?중도금(?:\s*대출)?(?:\s*조건)?(?:은|는)?"
    r".{0,40}?[“\"']?이자\s*후불제[”\"']?)",
    re.DOTALL,
)
INTEREST_LOAN_TERMS = re.compile(
    r"(?P<raw>중도금\s*대출\s*조건은.{0,300}?이자\s*후불제)",
    re.DOTALL,
)
INTEREST_BEFORE_LOAN = re.compile(
    r"(?P<raw>이자후불제\s*조건으로.{0,80}?중도금\s*대출(?:을|를)?\s*알선)",
    re.DOTALL,
)
INTEREST_FREE_LABEL = re.compile(r"(?P<raw>중도금(?:\s*대출)?\s*무이자)")
INTEREST_SETTLEMENT = re.compile(
    r"(?P<raw>(?:"
    r"(?:사업주체가\s*)?대납한\s*중도금\s*대출이자"
    r".{0,180}?(?:일시\s*)?(?:납부|정산|완납)"
    r"|중도금\s*대출.{0,220}?대납이자"
    r".{0,180}?(?:일시\s*)?(?:납부|정산|완납)"
    r"))",
    re.DOTALL,
)
EXPLICIT_INCLUDED = re.compile(r"공급가에는?.{0,100}?발코니.{0,100}?포함", re.DOTALL)
ARRANGEMENT_PLANNED = re.compile(
    r"(?P<raw>(?:"
    r"(?:중도금.{0,40}?)?(?:대출|융자)(?:을|를)?\s*알선.{0,80}?(?:예정|가능|할\s*수)"
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
    r"(?P<raw>(?:(?:주택형|타입)별[^.\n]{0,80}?(?:대출|중도금)"
    r"[^.\n]{0,60}?(?:상이|다름)"
    r"|(?:대출|중도금)[^.\n]{0,80}?(?:주택형|타입)별"
    r"[^.\n]{0,60}?(?:상이|다름)))",
)
INDIVIDUAL_REVIEW_FINAL = re.compile(
    r"(?P<raw>대출\s*가능\s*여부는\s*확정\s*사항이\s*아니며"
    r".{0,160}?대출취급기관의\s*심사를\s*거쳐\s*최종\s*결정)",
    re.DOTALL,
)
SETTLEMENT_REPAY_OR_CONVERT = re.compile(
    r"(?P<raw>계약자는\s*입주.{0,100}?(?:중도금\s*)?대출금을?\s*"
    r"상환하거나\s*담보대출로\s*전환)",
    re.DOTALL,
)
SETTLEMENT_ENTRY_DOCUMENT = re.compile(
    r"(?P<raw>입주지정기간.{0,300}?중도금\s*대출\s*상환\s*영수증"
    r".{0,100}?또는.{0,100}?중도금\s*대출에서\s*담보대출로\s*대환)",
    re.DOTALL,
)
SETTLEMENT_EXTENSION = re.compile(
    r"(?P<raw>대출\s*기간\s*만료\s*시.{0,160}?대출\s*기간\s*연장)",
    re.DOTALL,
)
INTEREST_BORROWER_BURDEN = re.compile(
    r"(?P<raw>중도금\s*대출\s*이자.{0,320}?(?:"
    r"대납이자.{0,160}?(?:완납|정산|납부)"
    r"|입주(?:지정기간|개시일).{0,100}?(?:부터|이후).{0,120}?"
    r"계약자가.{0,80}?직접\s*납부))",
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


def _exact_manwon(value_won: int) -> int | None:
    """Convert won to integer manwon only when the conversion is lossless.

    The public schema intentionally uses integer manwon for backend compatibility.
    Rounding a source value such as 715,000 won to 72 manwon would turn a known
    amount into a false exact value, so unsupported precision must abstain.
    """

    if value_won % 10_000:
        return None
    return value_won // 10_000


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


def _representative_installment_ratios(
    document_text: str,
    *,
    installment_count: int,
    expected_down_ratio: float | None,
    expected_interim_ratio: float | None,
    expected_balance_ratio: float | None,
) -> tuple[str, list[float]] | None:
    """Recover ratios only from a complete, arithmetically closed payment row.

    Some announcement tables label the six interim columns as ``1회`` ...
    ``6회`` without repeating ``(10%)``.  In that case we may derive the
    ratios from a representative row, but only when one value in the row is a
    sale-price candidate and every following payment adds back to that exact
    value.  This avoids assuming that equal-looking columns are equal shares.
    """

    if (
        installment_count < 1
        or expected_down_ratio is None
        or expected_interim_ratio is None
        or expected_balance_ratio is None
    ):
        return None

    candidates: list[tuple[str, list[float]]] = []
    for line in document_text.splitlines():
        values = _won_values(line)
        for sale_index, sale_price_won in enumerate(values):
            payment_values = values[sale_index + 1 :]
            if sale_price_won <= 0 or len(payment_values) < installment_count + 2:
                continue

            contract_values = payment_values[: -(installment_count + 1)]
            interim_values = payment_values[-(installment_count + 1) : -1]
            balance_value = payment_values[-1]
            if not contract_values or any(value <= 0 for value in payment_values):
                continue
            if sum(contract_values) + sum(interim_values) + balance_value != sale_price_won:
                continue

            ratios = [round(value / sale_price_won, 10) for value in interim_values]
            if (
                abs(sum(contract_values) / sale_price_won - expected_down_ratio) > 0.001
                or abs(sum(ratios) - expected_interim_ratio) > 0.001
                or abs(balance_value / sale_price_won - expected_balance_ratio) > 0.001
            ):
                continue
            candidates.append((line.strip(), ratios))

    if not candidates:
        return None
    first_ratios = candidates[0][1]
    if any(
        any(abs(left - right) > 0.001 for left, right in zip(first_ratios, ratios, strict=True))
        for _, ratios in candidates[1:]
    ):
        return None
    return candidates[0]


def _ground_payment(
    draft: ExtractionDraft,
    pages: list[CandidatePage],
    *,
    sale_price_manwon: int | None,
) -> list[Evidence]:
    evidence: list[Evidence] = []
    schedule = draft.payment_schedule

    # Model-proposed payment values are never retained on their own.  Rebuild
    # the schedule only from deterministic source patterns below; otherwise a
    # safe null/HOLD is preferable to an unsupported amount or date.
    for component in (
        schedule.down_payment,
        schedule.interim_payment,
        schedule.balance_payment,
    ):
        component.total_ratio = None
        component.total_amount_manwon = None
        component.basis = PaymentBasis.UNKNOWN
        component.installments = []
        component.due_date = None
        component.due_month = None
        component.due_text = None

    selections: list[tuple[int, CandidatePage, re.Match[str] | None]] = []
    for page in sorted(pages, key=lambda item: item.number):
        match = RATIO_HEADER.search(page.text)
        if match is None and MAIN_PAYMENT_HEADING.search(page.text):
            match = INTERIM_FIRST_RATIO_HEADER.search(page.text)
        if match is None and MAIN_PAYMENT_HEADING.search(page.text):
            match = REORDERED_RATIO_HEADER.search(page.text)
        if match is None:
            match = PARTIAL_RATIO_HEADER.search(page.text)
        if match:
            score = 0
            if _sale_price_row(page, sale_price_manwon) is not None:
                score += 100
            # A document can contain many option-payment tables with the same
            # 계약금/중도금/잔금 labels.  Prefer the apartment's canonical
            # supply/payment section over correction excerpts and paid-option
            # schedules when no target sale price was provided.
            main_heading = MAIN_PAYMENT_HEADING.search(page.text)
            if (
                main_heading is not None
                and main_heading.start() <= match.start() <= main_heading.end() + 1_200
            ):
                score += 200
            elif main_heading is not None:
                # A contents/correction page can mention "공급금액 표" after
                # an unrelated option schedule.  The heading alone is weak
                # evidence unless it actually introduces this ratio table.
                score += 20
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
            component.basis = PaymentBasis.RATIO
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
        all_installment_headers = INSTALLMENT_HEADER.findall(section[:1_800])
        installment_header_candidates: list[list[tuple[str, str]]] = []
        expected_ratio = schedule.interim_payment.total_ratio
        if expected_ratio is not None:
            for start, (number, _) in enumerate(all_installment_headers):
                if int(number) != 1:
                    continue
                for end in range(start + 1, len(all_installment_headers) + 1):
                    candidate = all_installment_headers[start:end]
                    numbers = [int(item_number) for item_number, _ in candidate]
                    ratios = [_ratio(value) for _, value in candidate]
                    if numbers != list(range(1, len(numbers) + 1)):
                        break
                    if abs(sum(ratios) - expected_ratio) <= 0.001:
                        installment_header_candidates.append(candidate)
        installment_headers: list[tuple[str, str]] = []
        if installment_header_candidates:
            longest_length = max(len(candidate) for candidate in installment_header_candidates)
            longest_candidates = [
                candidate
                for candidate in installment_header_candidates
                if len(candidate) == longest_length
            ]
            ratio_vectors = {
                tuple(_ratio(value) for _, value in candidate) for candidate in longest_candidates
            }
            if len(ratio_vectors) == 1:
                installment_headers = longest_candidates[0]
        simple_installment_matches = list(SIMPLE_INSTALLMENT_HEADER.finditer(section[:1_800]))
        simple_installment_numbers = [int(item.group(1)) for item in simple_installment_matches]
        declared_installment_count = len(installment_headers) or (
            max(simple_installment_numbers) if simple_installment_numbers else 0
        )
        # Dates after the declared N installments can be subscription or
        # construction notices.  Keep only the first N dates following the
        # payment header.
        all_dates = list(DATE_TEXT.finditer(section))
        date_offset = 0
        if installment_headers and declared_installment_count:
            first_ratio_header = INSTALLMENT_HEADER.search(section[:1_800])
            contract_numbers = [
                int(item.group(1))
                for item in simple_installment_matches
                if first_ratio_header is not None and item.start() < first_ratio_header.start()
            ]
            contract_dated_limit = max(contract_numbers, default=1) - 1
            # Skip only surplus dates.  This distinguishes a dated second
            # contract installment from text such as "계약 후 30일 이내".
            date_offset = min(
                max(0, len(all_dates) - declared_installment_count),
                max(0, contract_dated_limit),
            )
        dates = (
            all_dates[date_offset : date_offset + declared_installment_count]
            if declared_installment_count
            else []
        )
        representative_row = None
        if (
            not installment_headers
            and sale_price_manwon is None
            and len(dates) >= declared_installment_count > 0
        ):
            expected_balance_ratio = schedule.balance_payment.total_ratio
            if (
                expected_balance_ratio is None
                and schedule.down_payment.total_ratio is not None
                and schedule.interim_payment.total_ratio is not None
            ):
                expected_balance_ratio = round(
                    1 - schedule.down_payment.total_ratio - schedule.interim_payment.total_ratio,
                    10,
                )
            representative_row = _representative_installment_ratios(
                page.text,
                installment_count=declared_installment_count,
                expected_down_ratio=schedule.down_payment.total_ratio,
                expected_interim_ratio=schedule.interim_payment.total_ratio,
                expected_balance_ratio=expected_balance_ratio,
            )
        representative_ratios = representative_row[1] if representative_row else []
        row = _sale_price_row(page, sale_price_manwon)
        row_text: str | None = None
        row_values: list[int] = []
        interim_amounts_manwon: list[int | None] = []
        interim_ratios_from_row: list[float] = []
        interim_total_manwon: int | None = None
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
                schedule.down_payment.total_amount_manwon = _exact_manwon(
                    sum(contract_values)
                )
                days_due = re.search(r"(\d+)\s*일\s*이내", section)
                schedule.down_payment.installments = [
                    Installment(
                        number=index,
                        ratio=None,
                        amount_manwon=_exact_manwon(value),
                        due_date=None,
                        due_text=(
                            "계약 시"
                            if index == 1
                            else (f"계약 후 {days_due.group(1)}일 이내" if days_due else "계약 후")
                        ),
                    )
                    for index, value in enumerate(contract_values, start=1)
                ]
                interim_amounts_manwon = [_exact_manwon(value) for value in interim_values]
                interim_ratios_from_row = [
                    round(value / sale_price_won, 10) for value in interim_values
                ]
                interim_total_manwon = _exact_manwon(sum(interim_values))
                schedule.balance_payment.total_amount_manwon = _exact_manwon(balance_value)
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
                            interim_ratios_from_row[index]
                            if interim_ratios_from_row
                            else (representative_ratios[index] if representative_ratios else None)
                        )
                    ),
                    amount_manwon=(
                        interim_amounts_manwon[index] if interim_amounts_manwon else None
                    ),
                    due_date=date(
                        (
                            int(dates[index].group(1))
                            if len(dates[index].group(1)) == 4
                            else 2000 + int(dates[index].group(1))
                        ),
                        int(dates[index].group(2)),
                        int(dates[index].group(3)),
                    ),
                    due_text=None,
                )
                for index in range(installment_count)
            ]
            if interim_total_manwon is not None:
                schedule.interim_payment.total_amount_manwon = interim_total_manwon
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
            if representative_row:
                _append_evidence(
                    evidence,
                    Evidence(
                        field="/payment_schedule/interim_payment/installments",
                        page=page.number,
                        raw_text=representative_row[0],
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
        exact_balance_period = BALANCE_DUE_PERIOD.search(section)
        earlier_balance_period = BALANCE_DUE_EARLIER.search(section)
        # `입주시기` is a move-in-month heading, not a balance due rule.
        # Accept the compact table form `입주시` while excluding that heading.
        balance_at_move_in = re.search(r"입주\s*시(?!기)", section[:1_800])
        balance_at_move_in_day = re.search(r"(?<!실)입주일", section[:1_800])
        balance_period = re.search(r"(?:입주)?지정기간", section)
        if exact_balance_period:
            schedule.balance_payment.due_text = (
                "입주지정기간의 만료일 또는 세대출입 열쇠 수령일 중 선 도래일"
            )
            _append_evidence(
                evidence,
                Evidence(
                    field="/payment_schedule/balance_payment/due_text",
                    page=page.number,
                    raw_text=exact_balance_period.group("raw"),
                ),
            )
        elif earlier_balance_period:
            schedule.balance_payment.due_text = "입주지정기간 만료일 또는 실입주일 중 빠른 날"
            _append_evidence(
                evidence,
                Evidence(
                    field="/payment_schedule/balance_payment/due_text",
                    page=page.number,
                    raw_text=earlier_balance_period.group("raw"),
                ),
            )
        elif balance_at_move_in:
            schedule.balance_payment.due_text = "입주 시"
            _append_evidence(
                evidence,
                Evidence(
                    field="/payment_schedule/balance_payment/due_text",
                    page=page.number,
                    raw_text=balance_at_move_in.group(0),
                ),
            )
        elif balance_at_move_in_day:
            schedule.balance_payment.due_text = "입주일"
            _append_evidence(
                evidence,
                Evidence(
                    field="/payment_schedule/balance_payment/due_text",
                    page=page.number,
                    raw_text=balance_at_move_in_day.group(0),
                ),
            )
        elif balance_period:
            schedule.balance_payment.due_text = "입주지정기간"
            _append_evidence(
                evidence,
                Evidence(
                    field="/payment_schedule/balance_payment/due_text",
                    page=page.number,
                    raw_text=balance_period.group(0),
                ),
            )
        elif "입주지정일" in section:
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
        if not any(
            term in item.raw_text
            for term in ("중도금 대출", "대출취급기관", "대출 금융기관", "대출은행")
        ):
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
    ratio_only = (
        _find(pages, LOAN_RATIO_ONLY)
        or _find(pages, LOAN_RATIO_PARAGRAPH)
        or _find(pages, LOAN_RATIO_BEFORE_ARRANGEMENT)
        or _find(pages, LOAN_RATIO_FULL_INSTALLMENTS)
        or _find(pages, LOAN_RATIO_INTEREST_ARRANGEMENT)
    )
    discussion = _find(pages, ARRANGEMENT_DISCUSSION)
    planned = _find(pages, ARRANGEMENT_PLANNED)
    verified_banks = _verified_bank_names(draft)
    # Source-lock every quantitative loan field.  Values proposed by the model
    # are not retained unless one of the deterministic document patterns below
    # proves them.
    loan.arranged_ratio = None
    loan.arranged_amount_manwon = None
    loan.self_funding_ratio = None
    loan.self_funding_amount_manwon = None
    loan.self_funding_origin = None
    loan.prepay_requirement_ratio = None
    loan.bank_names = verified_banks
    if unavailable:
        page_number, match = unavailable
        loan.arrangement_status = LoanArrangementStatus.NOT_AVAILABLE
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
        loan.self_funding_ratio = _ratio(match.group("self"))
        loan.self_funding_origin = ValueOrigin.EXTRACTED
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
        _append_evidence(
            evidence,
            Evidence(
                field="/interim_loan/arranged_ratio",
                page=page_number,
                raw_text=match.group("raw"),
            ),
        )

    if not unavailable:
        if discussion:
            loan.arrangement_status = LoanArrangementStatus.UNDER_DISCUSSION
        elif verified_banks:
            loan.arrangement_status = LoanArrangementStatus.BANK_SELECTED
        elif planned:
            loan.arrangement_status = LoanArrangementStatus.PLANNED
        else:
            loan.arrangement_status = LoanArrangementStatus.NOT_STATED

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
        _append_evidence(evidence, bank_evidence)
        _append_evidence(
            evidence,
            Evidence(
                field="/interim_loan/arrangement_status",
                page=bank_evidence.page,
                raw_text=bank_evidence.raw_text,
            ),
        )

    prepay = (
        _find(pages, PREPAY)
        or _find(pages, PREPAY_PAID)
        or _find(pages, PREPAY_CONTRACT_RATIO)
        or _find(pages, PREPAY_SIMPLE_CONTRACT_RATIO)
        or _find(pages, PREPAY_TOTAL_PAID)
    )
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
    elif not unavailable:
        contract_completion = _find(pages, PREPAY_CONTRACT_COMPLETION)
        down_ratio = draft.payment_schedule.down_payment.total_ratio
        if contract_completion and down_ratio is not None:
            page_number, match = contract_completion
            loan.prepay_requirement_ratio = down_ratio
            _append_evidence(
                evidence,
                Evidence(
                    field="/interim_loan/prepay_requirement_ratio",
                    page=page_number,
                    raw_text=match.group("raw"),
                ),
            )

    free_interest = _find(pages, INTEREST_FREE_LABEL)
    deferred_interest = (
        _find(pages, INTEREST_LABEL)
        or _find(pages, INTEREST_LOAN_TERMS)
        or _find(pages, INTEREST_BEFORE_LOAN)
        or _find(pages, INTEREST_SETTLEMENT)
    )
    loan.interest_type = InterestType.UNKNOWN
    loan.interest_note = None
    if free_interest and deferred_interest and not unavailable:
        # Conflicting promotional and settlement clauses must not be collapsed
        # into "free".  The repayment clause proves deferred interest, while
        # validate_draft independently emits a source-conflict HOLD.
        page_number, match = deferred_interest
        loan.interest_type = InterestType.DEFERRED_INTEREST
        loan.interest_note = _normalized(match.group("raw"))[:500]
        for field in ("/interim_loan/interest_type", "/interim_loan/interest_note"):
            _append_evidence(
                evidence,
                Evidence(field=field, page=page_number, raw_text=match.group("raw")),
            )
    elif free_interest and not unavailable:
        page_number, match = free_interest
        loan.interest_type = InterestType.INTEREST_FREE
        _append_evidence(
            evidence,
            Evidence(
                field="/interim_loan/interest_type",
                page=page_number,
                raw_text=match.group("raw"),
            ),
        )
    elif deferred_interest and not unavailable:
        page_number, match = deferred_interest
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


def _settlement_deadline(raw_text: str) -> str:
    compact = _normalized(raw_text)
    if "입주증발급일" in compact and "입주지정기간종료일" in compact:
        return "입주증 발급일 또는 입주지정기간 종료일 중 빠른 날까지"
    if "입주지정기간" in compact:
        return "입주지정기간 내 입주증 발급 전"
    if "입주시" in compact:
        return "입주 시"
    return "입주 전"


def _ground_settlement(
    draft: ExtractionDraft,
    pages: list[CandidatePage],
) -> list[Evidence]:
    evidence: list[Evidence] = []
    loan = draft.interim_loan
    loan.settlement_requirement = LoanSettlementRequirement.NOT_STATED
    loan.settlement_deadline_text = None
    loan.extension_contingency_disclosed = None

    unavailable = _find(pages, NOT_AVAILABLE)
    direct = _find(pages, SETTLEMENT_REPAY_OR_CONVERT)
    entry_document = _find(pages, SETTLEMENT_ENTRY_DOCUMENT)
    extension = _find(pages, SETTLEMENT_EXTENSION)

    if unavailable:
        page_number, match = unavailable
        loan.settlement_requirement = LoanSettlementRequirement.NOT_APPLICABLE
        evidence.append(
            Evidence(
                field="/interim_loan/settlement_requirement",
                page=page_number,
                raw_text=match.group("raw"),
            )
        )
    elif direct or entry_document:
        page_number, match = direct or entry_document  # type: ignore[misc]
        raw_text = match.group("raw")
        loan.settlement_requirement = (
            LoanSettlementRequirement.REPAY_OR_CONVERT_TO_MORTGAGE
        )
        loan.settlement_deadline_text = _settlement_deadline(raw_text)
        for field in (
            "/interim_loan/settlement_requirement",
            "/interim_loan/settlement_deadline_text",
        ):
            evidence.append(Evidence(field=field, page=page_number, raw_text=raw_text))

    if extension:
        page_number, match = extension
        loan.extension_contingency_disclosed = True
        evidence.append(
            Evidence(
                field="/interim_loan/extension_contingency_disclosed",
                page=page_number,
                raw_text=match.group("raw"),
            )
        )
    return evidence


RISK_TEXT: dict[RiskClauseCode, tuple[PaymentStage, str, str]] = {
    RiskClauseCode.LOAN_MEDIATION_NOT_GUARANTEED: (
        PaymentStage.INTERIM,
        "사업주체의 중도금 대출 알선은 실제 실행을 보장하지 않습니다.",
        "알선 확정 여부와 불가 시 별도 조달 일정을 시행사에 확인하세요.",
    ),
    RiskClauseCode.INDIVIDUAL_REVIEW_REQUIRED: (
        PaymentStage.INTERIM,
        "사업장 대출 조건과 별개로 개인별 금융기관 심사가 남아 있습니다.",
        "소득·기존 대출을 기준으로 실제 승인 비율과 한도를 금융기관에 확인하세요.",
    ),
    RiskClauseCode.SELF_FUNDING_REQUIRED: (
        PaymentStage.INTERIM,
        "중도금 중 사업주체 알선 범위로 충당되지 않는 구간이 있습니다.",
        "알선 범위 밖 금액의 조달 방법과 회차별 납부 일정을 시행사에 확인하세요.",
    ),
    RiskClauseCode.INTEREST_PAYMENT_RISK: (
        PaymentStage.BALANCE,
        "입주 전후에 중도금 대출 이자 또는 대납이자 부담이 발생합니다.",
        "입주 시 정산액과 입주 이후 본인 부담 이자 시작일을 확인하세요.",
    ),
    RiskClauseCode.LOAN_NOT_AVAILABLE: (
        PaymentStage.INTERIM,
        "이 사업장은 중도금 대출이 불가하다고 명시돼 있습니다.",
        "중도금 전액의 회차별 직접 조달 계획을 확인하세요.",
    ),
    RiskClauseCode.TERMS_DIFFER_BY_HOUSING_TYPE: (
        PaymentStage.INTERIM,
        "중도금 대출 조건이 주택형별로 다를 수 있습니다.",
        "선택한 주택형에 적용되는 대출 비율과 조건을 별도로 확인하세요.",
    ),
}


def _risk_clause(
    *,
    code: RiskClauseCode,
    origin: ValueOrigin,
    evidence: list[Evidence],
) -> RiskClause:
    impact_stage, message, next_action = RISK_TEXT[code]
    return RiskClause(
        code=code,
        impact_stage=impact_stage,
        origin=origin,
        message=message,
        next_action=next_action,
        evidence=evidence,
    )


def _supporting_evidence(items: list[Evidence], field: str) -> Evidence | None:
    return next((item for item in items if item.field == field), None)


def _ground_risk_clauses(
    draft: ExtractionDraft,
    pages: list[CandidatePage],
    source_evidence: list[Evidence],
) -> None:
    clauses: list[RiskClause] = []
    loan = draft.interim_loan

    unavailable = _find(pages, NOT_AVAILABLE)
    mediation = _find(pages, MEDIATION_NOT_GUARANTEED)
    individual = _find(pages, INDIVIDUAL_REVIEW) or _find(pages, INDIVIDUAL_REVIEW_FINAL)
    terms_by_type = _find(pages, TERMS_BY_TYPE)
    interest_burden = _find(pages, INTEREST_BORROWER_BURDEN) or _find(
        pages, INTEREST_SETTLEMENT
    )

    direct_matches = (
        (
            RiskClauseCode.LOAN_NOT_AVAILABLE,
            unavailable,
        ),
        (
            RiskClauseCode.LOAN_MEDIATION_NOT_GUARANTEED,
            mediation if unavailable is None else None,
        ),
        (
            RiskClauseCode.INDIVIDUAL_REVIEW_REQUIRED,
            individual if unavailable is None else None,
        ),
        (
            RiskClauseCode.INTEREST_PAYMENT_RISK,
            interest_burden if unavailable is None else None,
        ),
        (
            RiskClauseCode.TERMS_DIFFER_BY_HOUSING_TYPE,
            terms_by_type if unavailable is None else None,
        ),
    )
    for code, found in direct_matches:
        if not found:
            continue
        page_number, match = found
        field = f"/risk_clauses/{len(clauses)}"
        clauses.append(
            _risk_clause(
                code=code,
                origin=ValueOrigin.EXTRACTED,
                evidence=[
                    Evidence(field=field, page=page_number, raw_text=match.group("raw"))
                ],
            )
        )

    if (
        unavailable is None
        and interest_burden is None
        and loan.interest_type == InterestType.DEFERRED_INTEREST
    ):
        interest_evidence = _supporting_evidence(
            source_evidence, "/interim_loan/interest_type"
        )
        if interest_evidence is not None:
            clauses.append(
                _risk_clause(
                    code=RiskClauseCode.INTEREST_PAYMENT_RISK,
                    origin=ValueOrigin.EXTRACTED,
                    evidence=[interest_evidence],
                )
            )

    self_funding_evidence = _supporting_evidence(
        source_evidence, "/interim_loan/self_funding_ratio"
    ) or _supporting_evidence(source_evidence, "/interim_loan/self_funding_amount_manwon")
    if self_funding_evidence is not None:
        clauses.append(
            _risk_clause(
                code=RiskClauseCode.SELF_FUNDING_REQUIRED,
                origin=ValueOrigin.EXTRACTED,
                evidence=[self_funding_evidence],
            )
        )
    else:
        interim_ratio = draft.payment_schedule.interim_payment.total_ratio
        arranged_ratio = loan.arranged_ratio
        if (
            interim_ratio is not None
            and arranged_ratio is not None
            and interim_ratio > arranged_ratio
        ):
            interim_evidence = _supporting_evidence(
                source_evidence, "/payment_schedule/interim_payment"
            )
            arranged_evidence = _supporting_evidence(
                source_evidence, "/interim_loan/arranged_ratio"
            )
            supports = [
                item for item in (interim_evidence, arranged_evidence) if item is not None
            ]
            if len(supports) == 2:
                clauses.append(
                    _risk_clause(
                        code=RiskClauseCode.SELF_FUNDING_REQUIRED,
                        origin=ValueOrigin.DERIVED,
                        evidence=supports,
                    )
                )

    draft.risk_clauses = clauses


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
                cost.total_amount_manwon = _exact_manwon(cost.total_amount_manwon)
            for payment in cost.payments:
                if payment.amount_manwon is not None:
                    payment.amount_manwon = _exact_manwon(payment.amount_manwon)

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
                cost.total_amount_manwon = _exact_manwon(row_amounts[0])
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
                            amount_manwon=_exact_manwon(amount),
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
                        payment.amount_manwon = _exact_manwon(amount)
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
    for item in _ground_settlement(grounded, pages):
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
    _ground_risk_clauses(grounded, pages, evidence)
    grounded.evidence = evidence
    return grounded


_REVIEW_REGROUNDED_EVIDENCE_PATHS = {
    "/interim_loan/settlement_requirement",
    "/interim_loan/settlement_deadline_text",
    "/interim_loan/extension_contingency_disclosed",
    "/exception_flags",
}


def reground_review_metadata(
    draft: ExtractionDraft,
    *,
    pages: list[CandidatePage],
) -> ExtractionDraft:
    """Rebuild deterministic review metadata from the exact source PDF.

    A review artifact is editable by design.  Consequently its settlement
    fields, risk clauses, exception flags, and their evidence cannot be used as
    an approval authority.  Re-run the deterministic source rules for those
    fields while retaining the human-reviewed factual payment/loan fields.
    """

    grounded = draft.model_copy(deep=True)
    evidence = [
        item
        for item in grounded.evidence
        if item.field not in _REVIEW_REGROUNDED_EVIDENCE_PATHS
    ]
    for item in _ground_settlement(grounded, pages):
        _append_evidence(evidence, item)
    for item in _ground_exception_flags(grounded, pages):
        _append_evidence(evidence, item)
    _ground_risk_clauses(grounded, pages, evidence)
    grounded.evidence = evidence
    return grounded
