from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from get_myhome_ai.models import (
    AnalysisResponse,
    AnalysisStatus,
    ExtractionDraft,
    HoldKind,
    HoldReasonCode,
)
from get_myhome_ai.normalization import normalize_draft
from get_myhome_ai.pdf_text import PdfPage
from get_myhome_ai.validation import validate_draft

try:
    from evaluation.validate_references import (
        EXPECTED_DOCUMENT_COUNT,
        SCORED_STATES,
        _iter_labels,
        validate_all,
    )
except ModuleNotFoundError:  # Direct `python evaluation/evaluate_core.py` invocation.
    from validate_references import (  # type: ignore[no-redef]
        EXPECTED_DOCUMENT_COUNT,
        SCORED_STATES,
        _iter_labels,
        validate_all,
    )


PENDING_HOLD_CODES: dict[str, set[HoldReasonCode]] = {
    "/interim_loan/arranged_ratio": {HoldReasonCode.INTERIM_LOAN_RATIO_MISSING},
    "/interim_loan/self_funding_ratio": {HoldReasonCode.INTERIM_LOAN_RATIO_MISSING},
}


def _actual_core(response: AnalysisResponse) -> dict[str, Any]:
    payment = response.payment_schedule
    interim = payment.interim_payment
    loan = response.interim_loan
    return {
        "payment_schedule": {
            "down_payment_ratio": payment.down_payment.total_ratio,
            "interim_payment_ratio": interim.total_ratio,
            "balance_payment_ratio": payment.balance_payment.total_ratio,
            "interim_installments": [
                {
                    "number": row.number,
                    "ratio": row.ratio,
                    "due_date": row.due_date.isoformat() if row.due_date else None,
                }
                for row in interim.installments
            ],
            "balance_due_text": payment.balance_payment.due_text,
            "move_in_month": payment.balance_payment.due_month,
        },
        "interim_loan": {
            "arrangement_status": loan.arrangement_status.value,
            "arranged_ratio": loan.arranged_ratio,
            "self_funding_ratio": loan.self_funding_ratio,
            "interest_type": loan.interest_type.value,
            "prepay_requirement_ratio": loan.prepay_requirement_ratio,
        },
    }


def _normalize(value: Any, path: str) -> Any:
    if path.endswith("/balance_due_text") and isinstance(value, str):
        return re.sub(r"\s+", "", value)
    if isinstance(value, float):
        return round(value, 10)
    if isinstance(value, list):
        return [_normalize(item, path) for item in value]
    if isinstance(value, dict):
        return {key: _normalize(item, path) for key, item in value.items()}
    return value


def _resolve(document: Any, path: str) -> tuple[bool, Any]:
    current = document
    for part in path.strip("/").split("/"):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _reject_boolean_core_numbers(payload: dict[str, Any]) -> None:
    numeric_paths = {
        "/payment_schedule/down_payment/total_ratio",
        "/payment_schedule/interim_payment/total_ratio",
        "/payment_schedule/balance_payment/total_ratio",
        "/interim_loan/arranged_ratio",
        "/interim_loan/self_funding_ratio",
        "/interim_loan/prepay_requirement_ratio",
    }
    for path in numeric_paths:
        exists, value = _resolve(payload, path)
        if exists and isinstance(value, bool):
            raise ValueError(f"actual response has boolean in numeric field {path}")
    installments = (
        payload.get("payment_schedule", {}).get("interim_payment", {}).get("installments", [])
    )
    if isinstance(installments, list):
        for index, row in enumerate(installments):
            if not isinstance(row, dict):
                continue
            for name in ("number", "ratio", "amount_manwon"):
                if isinstance(row.get(name), bool):
                    raise ValueError(
                        "actual response has boolean in numeric field "
                        f"/payment_schedule/interim_payment/installments/{index}/{name}"
                    )


def _parse_actual(payload: dict[str, Any], *, filename_id: str) -> AnalysisResponse:
    _reject_boolean_core_numbers(payload)
    try:
        response = AnalysisResponse.model_validate(payload)
    except ValidationError as exc:
        locations = ["/".join(str(item) for item in error["loc"]) for error in exc.errors()]
        raise ValueError(f"invalid AnalysisResponse for {filename_id}: {locations}") from exc
    if response.complex_id != filename_id:
        raise ValueError(
            f"actual complex_id {response.complex_id!r} does not match filename {filename_id!r}"
        )
    return response


def _source_pages(reference: dict[str, Any], source_dir: Path) -> list[PdfPage]:
    source = reference["source"]
    pdf_path = source_dir / source["pdf_filename"]
    text_path = source_dir / source["text_filename"]
    if not pdf_path.is_file() or not text_path.is_file():
        raise ValueError(f"locked source is missing for {reference['complex_id']}")
    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    if digest != source["pdf_sha256"]:
        raise ValueError(f"locked source SHA-256 mismatch for {reference['complex_id']}")
    parts = text_path.read_text(encoding="utf-8", errors="replace").split("\f")
    if parts and not parts[-1].strip():
        parts.pop()
    return [PdfPage(number=index, text=text) for index, text in enumerate(parts, start=1)]


def _provable_derived_fields(draft: ExtractionDraft) -> list[str]:
    """Infer only values that are mathematically derivable from the returned payload."""
    _normalized, normalization_fields = normalize_draft(draft)
    provable = set(normalization_fields)
    schedule = draft.payment_schedule
    components = (
        (schedule.down_payment, "/payment_schedule/down_payment"),
        (schedule.interim_payment, "/payment_schedule/interim_payment"),
        (schedule.balance_payment, "/payment_schedule/balance_payment"),
    )
    for component, base in components:
        ratios = [row.ratio for row in component.installments]
        if (
            component.total_ratio is not None
            and ratios
            and all(value is not None for value in ratios)
            and abs(sum(value for value in ratios if value is not None) - component.total_ratio)
            <= 1e-9
        ):
            provable.add(f"{base}/total_ratio")
        amounts = [row.amount_manwon for row in component.installments]
        if (
            component.total_amount_manwon is not None
            and amounts
            and all(value is not None for value in amounts)
            and sum(value for value in amounts if value is not None)
            == component.total_amount_manwon
        ):
            provable.add(f"{base}/total_amount_manwon")
    down = schedule.down_payment.total_ratio
    interim = schedule.interim_payment.total_ratio
    balance = schedule.balance_payment.total_ratio
    if (
        down is not None
        and interim is not None
        and balance is not None
        and abs(1.0 - down - interim - balance) <= 1e-9
    ):
        provable.add("/payment_schedule/balance_payment/total_ratio")
    loan = draft.interim_loan
    if (
        loan.self_funding_origin is not None
        and loan.self_funding_origin.value == "DERIVED"
        and interim is not None
        and loan.arranged_ratio is not None
        and loan.self_funding_ratio is not None
        and abs(interim - loan.arranged_ratio - loan.self_funding_ratio) <= 1e-9
    ):
        provable.update(
            {
                "/interim_loan/self_funding_ratio",
                "/interim_loan/self_funding_origin",
            }
        )
    return sorted(provable)


def _recompute_validation(
    response: AnalysisResponse,
    reference: dict[str, Any],
    source_dir: Path,
) -> dict[str, Any]:
    draft = ExtractionDraft.model_validate(
        {
            "payment_schedule": response.payment_schedule,
            "interim_loan": response.interim_loan,
            "additional_costs": response.additional_costs,
            "evidence": response.evidence,
            "exception_flags": response.exception_flags,
        }
    )
    report = validate_draft(
        draft,
        pages=_source_pages(reference, source_dir),
        derived_fields=_provable_derived_fields(draft),
        sale_price_manwon=response.target_unit.sale_price_manwon,
    )
    return {
        "passed": report.passed,
        "issue_codes": [issue.code for issue in report.issues],
        "self_reported_passed": response.validation.passed,
        "self_report_disagrees": response.validation.passed != report.passed,
    }


def _pending_is_safe(response: AnalysisResponse, field: str, value: Any) -> tuple[bool, str]:
    if value is not None:
        return False, "NON_NULL_VALUE"
    expected_codes = PENDING_HOLD_CODES.get(field)
    if not expected_codes:
        return False, "NO_DEFINED_SAFE_HOLD"
    valid_hold = any(
        hold.reason_code in expected_codes
        and hold.kind == HoldKind.DOCUMENT_UNCERTAINTY
        and hold.blocking
        for hold in response.holds
    )
    if not valid_hold:
        return False, "CORRECT_BLOCKING_HOLD_MISSING"
    if response.analysis_status not in {AnalysisStatus.PARTIAL, AnalysisStatus.HOLD}:
        return False, "ANALYSIS_STATUS_READY"
    return True, "SAFE_ABSTENTION"


def _artifact_ids(actual_dir: Path) -> set[str]:
    return {path.stem for path in actual_dir.glob("*.json") if re.fullmatch(r"\d{10}", path.stem)}


def _init_field_metrics(paths: Iterable[str]) -> dict[str, dict[str, int]]:
    return {
        path: {
            "reference_total": 0,
            "eligible": 0,
            "pending": 0,
            "missing": 0,
            "compared": 0,
            "matched": 0,
        }
        for path in paths
    }


def evaluate(
    actual_dir: Path,
    reference_dir: Path,
    source_dir: Path | None = None,
) -> dict[str, Any]:
    reference_summary = validate_all(reference_dir, source_dir)
    manifest = json.loads((reference_dir.parent / "MANIFEST.json").read_text(encoding="utf-8"))
    expected_ids: list[str] = manifest["reference_ids"]
    actual_ids = _artifact_ids(actual_dir)
    missing_ids = sorted(set(expected_ids) - actual_ids)
    unexpected_ids = sorted(actual_ids - set(expected_ids))

    references = {
        complex_id: json.loads((reference_dir / f"{complex_id}.json").read_text(encoding="utf-8"))
        for complex_id in expected_ids
    }
    all_paths = sorted(
        {path for reference in references.values() for path, _ in _iter_labels(reference["labels"])}
    )
    field_metrics = _init_field_metrics(all_paths)
    case_reports: list[dict[str, Any]] = []
    matched = 0
    compared = 0
    pending = 0
    pending_safe = 0
    unsafe_pending_values: list[dict[str, Any]] = []
    recomputed_validation_evaluated = 0
    recomputed_validation_passed = 0

    for complex_id in expected_ids:
        reference = references[complex_id]
        labels = list(_iter_labels(reference["labels"]))
        for path, expected_label in labels:
            metric = field_metrics[path]
            metric["reference_total"] += 1
            if expected_label["verification"] in SCORED_STATES:
                metric["eligible"] += 1
            else:
                metric["pending"] += 1

        actual_path = actual_dir / f"{complex_id}.json"
        if not actual_path.is_file():
            for path, _label in labels:
                field_metrics[path]["missing"] += 1
            continue

        payload = json.loads(actual_path.read_text(encoding="utf-8"))
        response = _parse_actual(payload, filename_id=complex_id)
        if response.meta.source_sha256 != reference["source"]["pdf_sha256"]:
            raise ValueError(f"actual source_sha256 mismatch for {complex_id}")
        if response.meta.source_page_count != reference["source"]["page_count"]:
            raise ValueError(f"actual source_page_count mismatch for {complex_id}")
        actual = _actual_core(response)

        recomputed_validation: dict[str, Any] | None = None
        if source_dir is not None:
            recomputed_validation = _recompute_validation(response, reference, source_dir)
            recomputed_validation_evaluated += 1
            recomputed_validation_passed += int(recomputed_validation["passed"])

        fields: list[dict[str, Any]] = []
        case_pending_safe = True
        case_pending_count = 0
        for path, expected_label in labels:
            exists, value = _resolve(actual, path)
            metric = field_metrics[path]
            if not exists:
                metric["missing"] += 1
                if expected_label["verification"] not in SCORED_STATES:
                    pending += 1
                    case_pending_count += 1
                    case_pending_safe = False
                continue
            if expected_label["verification"] not in SCORED_STATES:
                pending += 1
                case_pending_count += 1
                safe, reason = _pending_is_safe(response, path, value)
                pending_safe += int(safe)
                case_pending_safe = case_pending_safe and safe
                if not safe:
                    unsafe_pending_values.append(
                        {
                            "complex_id": complex_id,
                            "field": path,
                            "actual": value,
                            "reason": reason,
                        }
                    )
                continue
            expected = expected_label["value"]
            is_match = _normalize(value, path) == _normalize(expected, path)
            matched += int(is_match)
            compared += 1
            metric["matched"] += int(is_match)
            metric["compared"] += 1
            fields.append(
                {
                    "field": path,
                    "match": is_match,
                    "expected": expected,
                    "actual": value,
                }
            )
        scored_fields_exact = bool(fields) and all(field["match"] for field in fields)
        validation_safe = bool(recomputed_validation and recomputed_validation["passed"])
        safety_exact = (
            scored_fields_exact
            and (case_pending_count == 0 or case_pending_safe)
            and validation_safe
        )
        case_reports.append(
            {
                "complex_id": complex_id,
                "matched_labels": sum(field["match"] for field in fields),
                "compared_labels": len(fields),
                "scored_fields_exact": scored_fields_exact,
                "safety_exact": safety_exact,
                "pending_label_count": case_pending_count,
                "pending_safe": case_pending_safe if case_pending_count else None,
                "recomputed_validation": recomputed_validation,
                "fields": fields,
            }
        )

    complete_reference_set = (
        reference_summary.document_count == EXPECTED_DOCUMENT_COUNT
        and len(expected_ids) == EXPECTED_DOCUMENT_COUNT
    )
    complete_actual_set = not missing_ids and not unexpected_ids
    publishable = complete_reference_set and complete_actual_set and source_dir is not None
    aggregate_status = "PUBLISHABLE" if publishable else "INCOMPLETE_NON_PUBLISHABLE"

    return {
        "scope": "document_level_core_reference_v0.1",
        "aggregate_status": aggregate_status,
        "publishable": publishable,
        "expected_document_count": EXPECTED_DOCUMENT_COUNT,
        "reference_document_count": reference_summary.document_count,
        "evaluated_document_count": len(case_reports),
        "missing_actual_ids": missing_ids,
        "unexpected_actual_ids": unexpected_ids,
        "scored_fields_exact_document_count": (
            sum(case["scored_fields_exact"] for case in case_reports) if publishable else None
        ),
        "safety_exact_document_count": (
            sum(case["safety_exact"] for case in case_reports) if publishable else None
        ),
        "matched_label_count": matched,
        "compared_label_count": compared,
        "label_match_rate": matched / compared if publishable and compared else None,
        "field_metrics": {
            path: values
            | {
                "match_rate": (
                    values["matched"] / values["compared"]
                    if publishable and values["compared"]
                    else None
                )
            }
            for path, values in sorted(field_metrics.items())
        },
        "pending_label_count": pending,
        "pending_safe_abstention_count": pending_safe,
        "pending_safe_abstention_rate": (
            pending_safe / pending if publishable and pending else None
        ),
        "unsafe_pending_values": unsafe_pending_values,
        "recomputed_validation_evaluated_document_count": recomputed_validation_evaluated,
        "recomputed_validation_passed_document_count": recomputed_validation_passed,
        "evidence_accuracy": {
            "status": "NOT_EVALUATED",
            "reason": (
                "Source-locked validation checks quote existence and evidence coverage, but this "
                "evaluator does not score whether each quote semantically proves its field."
            ),
        },
        "claim_limit": (
            "Only a complete 24-reference, 24-actual, source-locked run is publishable. "
            "The score excludes unit-specific amounts, additional costs, bank/provider fields, "
            "and pending labels from field accuracy. Safety exactness and pending abstention are "
            "reported separately. It is not 27-document full-field accuracy."
        ),
        "cases": case_reports,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--actual-dir", type=Path, required=True)
    parser.add_argument(
        "--reference-dir",
        type=Path,
        default=Path(__file__).with_name("reference"),
    )
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(args.actual_dir, args.reference_dir, args.source_dir)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
