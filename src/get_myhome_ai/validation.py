from __future__ import annotations

import re
import unicodedata
from datetime import date

from get_myhome_ai.models import (
    ExtractionDraft,
    InterestType,
    IssueSeverity,
    LoanArrangementStatus,
    PaymentComponent,
    ValidationIssue,
    ValidationReport,
)
from get_myhome_ai.pdf_text import PdfPage

TOLERANCE = 0.001


def _issue(
    severity: IssueSeverity, code: str, message: str, field: str | None = None
) -> ValidationIssue:
    return ValidationIssue(severity=severity, code=code, field=field, message=message)


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def _component_amount(component: PaymentComponent, sale_price: int) -> float | None:
    if component.total_amount_manwon is not None:
        return float(component.total_amount_manwon)
    if component.total_ratio is not None:
        return component.total_ratio * sale_price
    return None


def _validate_component(
    component: PaymentComponent,
    path: str,
    issues: list[ValidationIssue],
) -> None:
    if component.total_ratio is None and component.total_amount_manwon is None:
        issues.append(
            _issue(
                IssueSeverity.ERROR,
                "PAYMENT_TOTAL_MISSING",
                "구간의 총 비율과 총 정액이 모두 없습니다.",
                path,
            )
        )

    ratios = [item.ratio for item in component.installments]
    if ratios and all(value is not None for value in ratios):
        installment_ratio_total = sum(value for value in ratios if value is not None)
        if not 0 <= installment_ratio_total <= 1:
            issues.append(
                _issue(
                    IssueSeverity.ERROR,
                    "INSTALLMENT_RATIO_TOTAL_OUT_OF_RANGE",
                    "회차별 비율 합이 0~100% 범위를 벗어납니다.",
                    f"{path}/installments",
                )
            )
    if component.total_ratio is not None and ratios and all(value is not None for value in ratios):
        total = sum(value for value in ratios if value is not None)
        if abs(total - component.total_ratio) > TOLERANCE:
            issues.append(
                _issue(
                    IssueSeverity.ERROR,
                    "INSTALLMENT_RATIO_SUM_MISMATCH",
                    "회차별 비율 합이 구간 총비율과 다릅니다.",
                    f"{path}/installments",
                )
            )

    amounts = [item.amount_manwon for item in component.installments]
    numbers = [item.number for item in component.installments]
    if numbers and numbers != list(range(1, len(numbers) + 1)):
        issues.append(
            _issue(
                IssueSeverity.ERROR,
                "INSTALLMENT_NUMBER_INVALID",
                "회차 번호가 1부터 순서대로 이어지지 않습니다.",
                f"{path}/installments",
            )
        )
    for index, item in enumerate(component.installments):
        if item.ratio is None and item.amount_manwon is None:
            issues.append(
                _issue(
                    IssueSeverity.ERROR,
                    "INSTALLMENT_VALUE_MISSING",
                    "회차의 비율과 금액이 모두 없습니다.",
                    f"{path}/installments/{index}",
                )
            )
        if item.due_date is None and item.due_text is None:
            issues.append(
                _issue(
                    IssueSeverity.ERROR,
                    "INSTALLMENT_DUE_MISSING",
                    "회차의 납부일 또는 납부 시점 문구가 없습니다.",
                    f"{path}/installments/{index}",
                )
            )
    if (
        component.total_amount_manwon is not None
        and amounts
        and all(value is not None for value in amounts)
        and sum(value for value in amounts if value is not None) != component.total_amount_manwon
    ):
        issues.append(
            _issue(
                IssueSeverity.ERROR,
                "INSTALLMENT_AMOUNT_SUM_MISMATCH",
                "회차별 금액 합이 구간 총액과 다릅니다.",
                f"{path}/installments",
            )
        )

    dated = [item.due_date for item in component.installments if item.due_date is not None]
    if dated != sorted(dated):
        issues.append(
            _issue(
                IssueSeverity.ERROR,
                "INSTALLMENT_DATE_ORDER_INVALID",
                "회차별 납부일이 오름차순이 아닙니다.",
                f"{path}/installments",
            )
        )


def _extracted_value_paths(draft: ExtractionDraft, derived: set[str]) -> list[str]:
    paths: list[str] = []
    schedule = draft.payment_schedule
    for name, component in (
        ("down_payment", schedule.down_payment),
        ("interim_payment", schedule.interim_payment),
        ("balance_payment", schedule.balance_payment),
    ):
        base = f"/payment_schedule/{name}"
        for field in ("total_ratio", "total_amount_manwon", "due_date", "due_month", "due_text"):
            if getattr(component, field) is not None:
                paths.append(f"{base}/{field}")
        for index, installment in enumerate(component.installments):
            for field in ("ratio", "amount_manwon", "due_date", "due_text"):
                if getattr(installment, field) is not None:
                    paths.append(f"{base}/installments/{index}/{field}")

    loan = draft.interim_loan
    if loan.arrangement_status != LoanArrangementStatus.NOT_STATED:
        paths.append("/interim_loan/arrangement_status")
    for field in (
        "arranged_ratio",
        "arranged_amount_manwon",
        "self_funding_ratio",
        "self_funding_amount_manwon",
        "interest_note",
        "prepay_requirement_ratio",
    ):
        if getattr(loan, field) is not None:
            paths.append(f"/interim_loan/{field}")
    if loan.bank_names:
        paths.append("/interim_loan/bank_names")
    if loan.guarantee_provider is not None:
        paths.append("/interim_loan/guarantee_provider")
    if loan.interest_type not in {InterestType.UNKNOWN, InterestType.NOT_APPLICABLE}:
        paths.append("/interim_loan/interest_type")

    for index, cost in enumerate(draft.additional_costs):
        base = f"/additional_costs/{index}"
        paths.extend([f"{base}/type", f"{base}/name"])
        for field in (
            "total_amount_manwon",
            "required",
            "included_in_sale_price",
            "applicable_unit_type",
            "note",
        ):
            if getattr(cost, field) is not None:
                paths.append(f"{base}/{field}")
        for payment_index, payment in enumerate(cost.payments):
            payment_base = f"{base}/payments/{payment_index}"
            paths.append(f"{payment_base}/stage")
            for field in ("amount_manwon", "due_date", "due_text"):
                if getattr(payment, field) is not None:
                    paths.append(f"{payment_base}/{field}")

    return [path for path in paths if path not in derived]


def _path_has_evidence(path: str, evidence_fields: set[str]) -> bool:
    if path in evidence_fields:
        return True

    # A table row can legitimately support all fields in one payment component
    # or one additional-cost item.  Other parents (notably /interim_loan) are
    # intentionally *not* accepted: a quote about the loan ratio must not also
    # prove the bank, interest type, or prepayment requirement.
    group_evidence = {
        evidence
        for evidence in evidence_fields
        if evidence
        in {
            "/payment_schedule/down_payment",
            "/payment_schedule/interim_payment",
            "/payment_schedule/balance_payment",
            "/payment_schedule/interim_payment/installments",
        }
        or re.fullmatch(r"/additional_costs/\d+", evidence)
    }
    return any(path.startswith(f"{evidence}/") for evidence in group_evidence)


def _json_pointer_exists(document: object, pointer: str) -> bool:
    if not pointer.startswith("/"):
        return False
    current = document
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return False
            current = current[token]
        elif isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError):
                return False
        else:
            return False
    return True


def validate_draft(
    draft: ExtractionDraft,
    *,
    pages: list[PdfPage],
    derived_fields: list[str],
    sale_price_manwon: int | None,
) -> ValidationReport:
    issues: list[ValidationIssue] = []
    schedule = draft.payment_schedule
    for component, path in (
        (schedule.down_payment, "/payment_schedule/down_payment"),
        (schedule.interim_payment, "/payment_schedule/interim_payment"),
        (schedule.balance_payment, "/payment_schedule/balance_payment"),
    ):
        _validate_component(component, path, issues)

    balance = schedule.balance_payment
    if all(value is None for value in (balance.due_date, balance.due_month, balance.due_text)):
        issues.append(
            _issue(
                IssueSeverity.WARNING,
                "BALANCE_DUE_MISSING",
                "잔금 납부일 또는 입주예정월을 확인하지 못했습니다.",
                "/payment_schedule/balance_payment",
            )
        )

    ratios = [
        schedule.down_payment.total_ratio,
        schedule.interim_payment.total_ratio,
        schedule.balance_payment.total_ratio,
    ]
    if (
        all(value is not None for value in ratios)
        and abs(sum(value for value in ratios if value is not None) - 1.0) > TOLERANCE
    ):
        issues.append(
            _issue(
                IssueSeverity.ERROR,
                "PAYMENT_RATIO_SUM_MISMATCH",
                "계약금·중도금·잔금 비율 합이 100%가 아닙니다.",
                "/payment_schedule",
            )
        )

    if sale_price_manwon is not None:
        for component_name, component in (
            ("down_payment", schedule.down_payment),
            ("interim_payment", schedule.interim_payment),
            ("balance_payment", schedule.balance_payment),
        ):
            if component.total_ratio is not None and component.total_amount_manwon is not None:
                expected_amount = component.total_ratio * sale_price_manwon
                if abs(expected_amount - component.total_amount_manwon) > 1:
                    issues.append(
                        _issue(
                            IssueSeverity.ERROR,
                            "COMPONENT_RATIO_AMOUNT_MISMATCH",
                            "구간 비율로 계산한 금액과 추출 총액이 다릅니다.",
                            f"/payment_schedule/{component_name}",
                        )
                    )
        amounts = [
            _component_amount(schedule.down_payment, sale_price_manwon),
            _component_amount(schedule.interim_payment, sale_price_manwon),
            _component_amount(schedule.balance_payment, sale_price_manwon),
        ]
        if (
            all(value is not None for value in amounts)
            and abs(sum(value for value in amounts if value is not None) - sale_price_manwon) > 1
        ):
            issues.append(
                _issue(
                    IssueSeverity.ERROR,
                    "PAYMENT_AMOUNT_SUM_MISMATCH",
                    "계약금·중도금·잔금 합이 선택 세대 분양가와 다릅니다.",
                    "/payment_schedule",
                )
            )

    loan = draft.interim_loan
    interim_ratio = schedule.interim_payment.total_ratio
    if (
        loan.arranged_ratio is not None
        and interim_ratio is not None
        and loan.arranged_ratio - interim_ratio > TOLERANCE
    ):
        issues.append(
            _issue(
                IssueSeverity.ERROR,
                "LOAN_EXCEEDS_INTERIM_PAYMENT",
                "중도금 대출비율이 중도금 총비율보다 큽니다.",
                "/interim_loan/arranged_ratio",
            )
        )
    interim_amount = schedule.interim_payment.total_amount_manwon
    if (
        loan.arranged_amount_manwon is not None
        and loan.self_funding_amount_manwon is not None
        and interim_amount is not None
        and loan.arranged_amount_manwon + loan.self_funding_amount_manwon != interim_amount
    ):
        issues.append(
            _issue(
                IssueSeverity.ERROR,
                "LOAN_AND_SELF_FUNDING_AMOUNT_SUM_MISMATCH",
                "대출금액과 자납금액 합이 중도금 총액과 다릅니다.",
                "/interim_loan",
            )
        )
    if sale_price_manwon is not None:
        for ratio_field, amount_field in (
            ("arranged_ratio", "arranged_amount_manwon"),
            ("self_funding_ratio", "self_funding_amount_manwon"),
        ):
            ratio = getattr(loan, ratio_field)
            amount = getattr(loan, amount_field)
            if (
                ratio is not None
                and amount is not None
                and abs(ratio * sale_price_manwon - amount) > 1
            ):
                issues.append(
                    _issue(
                        IssueSeverity.ERROR,
                        "LOAN_RATIO_AMOUNT_MISMATCH",
                        "대출 또는 자납 비율로 계산한 금액과 추출 금액이 다릅니다.",
                        f"/interim_loan/{ratio_field}",
                    )
                )
    if (
        loan.arranged_ratio is not None
        and loan.self_funding_ratio is not None
        and interim_ratio is not None
        and abs(loan.arranged_ratio + loan.self_funding_ratio - interim_ratio) > TOLERANCE
    ):
        issues.append(
            _issue(
                IssueSeverity.ERROR,
                "LOAN_AND_SELF_FUNDING_SUM_MISMATCH",
                "대출비율과 자납비율 합이 중도금 총비율과 다릅니다.",
                "/interim_loan",
            )
        )
    if loan.arrangement_status == LoanArrangementStatus.NOT_AVAILABLE and (
        (loan.arranged_ratio or 0) != 0 or (loan.arranged_amount_manwon or 0) != 0
    ):
        issues.append(
            _issue(
                IssueSeverity.ERROR,
                "NOT_AVAILABLE_WITH_POSITIVE_LOAN",
                "대출 불가 상태인데 대출 가능 금액 또는 비율이 0보다 큽니다.",
                "/interim_loan",
            )
        )

    interim_dates = [
        item.due_date for item in schedule.interim_payment.installments if item.due_date is not None
    ]
    balance_date: date | None = schedule.balance_payment.due_date
    if balance_date is not None and interim_dates and balance_date < max(interim_dates):
        issues.append(
            _issue(
                IssueSeverity.ERROR,
                "BALANCE_DATE_BEFORE_INTERIM",
                "잔금일이 마지막 중도금일보다 빠릅니다.",
                "/payment_schedule/balance_payment/due_date",
            )
        )

    page_map = {page.number: page.text for page in pages}
    draft_document = draft.model_dump(mode="json")
    valid_evidence_fields: set[str] = set()
    for index, evidence in enumerate(draft.evidence):
        evidence_path = f"/evidence/{index}"
        page_text = page_map.get(evidence.page)
        if page_text is None:
            issues.append(
                _issue(
                    IssueSeverity.ERROR,
                    "EVIDENCE_PAGE_OUT_OF_RANGE",
                    "근거 페이지가 PDF 범위를 벗어났습니다.",
                    evidence_path,
                )
            )
            continue
        if not evidence.field.startswith("/"):
            issues.append(
                _issue(
                    IssueSeverity.ERROR,
                    "EVIDENCE_PATH_INVALID",
                    "근거 필드는 JSON Pointer 형식이어야 합니다.",
                    evidence_path,
                )
            )
            continue
        if not _json_pointer_exists(draft_document, evidence.field):
            issues.append(
                _issue(
                    IssueSeverity.ERROR,
                    "EVIDENCE_PATH_NOT_FOUND",
                    "근거 필드가 실제 추출 결과 경로를 가리키지 않습니다.",
                    evidence_path,
                )
            )
            continue
        if _normalized_text(evidence.raw_text) not in _normalized_text(page_text):
            issues.append(
                _issue(
                    IssueSeverity.ERROR,
                    "EVIDENCE_TEXT_NOT_FOUND",
                    "근거 문장을 해당 PDF 페이지에서 찾지 못했습니다.",
                    evidence_path,
                )
            )
            continue
        valid_evidence_fields.add(evidence.field)

    for path in _extracted_value_paths(draft, set(derived_fields)):
        if not _path_has_evidence(path, valid_evidence_fields):
            issues.append(
                _issue(
                    IssueSeverity.ERROR,
                    "EVIDENCE_MISSING",
                    "추출값을 뒷받침하는 원문 근거가 없습니다.",
                    path,
                )
            )

    for index, cost in enumerate(draft.additional_costs):
        payment_amounts = [payment.amount_manwon for payment in cost.payments]
        if (
            cost.total_amount_manwon is not None
            and payment_amounts
            and all(value is not None for value in payment_amounts)
            and abs(
                sum(value for value in payment_amounts if value is not None)
                - cost.total_amount_manwon
            )
            > 1
        ):
            issues.append(
                _issue(
                    IssueSeverity.ERROR,
                    "ADDITIONAL_COST_PAYMENT_SUM_MISMATCH",
                    "추가비용 회차 합이 총액과 다릅니다.",
                    f"/additional_costs/{index}/payments",
                )
            )
        for payment_index, payment in enumerate(cost.payments):
            if payment.amount_manwon is None:
                issues.append(
                    _issue(
                        IssueSeverity.WARNING,
                        "ADDITIONAL_COST_PAYMENT_AMOUNT_MISSING",
                        "추가비용 회차 금액을 확인하지 못했습니다.",
                        f"/additional_costs/{index}/payments/{payment_index}",
                    )
                )
        if cost.included_in_sale_price is True:
            continue
        if not cost.payments or all(
            payment.stage.value == "UNKNOWN"
            and payment.due_date is None
            and payment.due_text is None
            for payment in cost.payments
        ):
            issues.append(
                _issue(
                    IssueSeverity.WARNING,
                    "ADDITIONAL_COST_SCHEDULE_MISSING",
                    "추가비용의 납부 구간 또는 납부 시점을 확인하지 못했습니다.",
                    f"/additional_costs/{index}/payments",
                )
            )
        if cost.total_amount_manwon is None or cost.required is None:
            issues.append(
                _issue(
                    IssueSeverity.WARNING,
                    "ADDITIONAL_COST_INCOMPLETE",
                    "추가비용의 금액 또는 필수 여부가 불명확합니다.",
                    f"/additional_costs/{index}",
                )
            )

    passed = not any(issue.severity == IssueSeverity.ERROR for issue in issues)
    return ValidationReport(
        passed=passed,
        issues=issues,
        derived_fields=derived_fields,
    )
