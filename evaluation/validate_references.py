from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

SCORED_STATES = {"VERIFIED_TEXT", "VERIFIED_NORMALIZED", "VERIFIED_DERIVED"}
EXPECTED_ABSTENTION_STATES = {"VERIFIED_NOT_STATED"}
ALL_STATES = SCORED_STATES | EXPECTED_ABSTENTION_STATES
EXPECTED_DOCUMENT_COUNT = 24
CORE_LABEL_PATHS = {
    "/payment_schedule/down_payment_ratio",
    "/payment_schedule/interim_payment_ratio",
    "/payment_schedule/balance_payment_ratio",
    "/payment_schedule/interim_installments",
    "/payment_schedule/balance_due_text",
    "/payment_schedule/move_in_month",
    "/interim_loan/arrangement_status",
    "/interim_loan/arranged_ratio",
    "/interim_loan/self_funding_ratio",
    "/interim_loan/interest_type",
    "/interim_loan/prepay_requirement_ratio",
}
RATIO_PATHS = {
    "/payment_schedule/down_payment_ratio",
    "/payment_schedule/interim_payment_ratio",
    "/payment_schedule/balance_payment_ratio",
    "/interim_loan/arranged_ratio",
    "/interim_loan/self_funding_ratio",
    "/interim_loan/prepay_requirement_ratio",
}
ARRANGEMENT_STATUSES = {
    "NOT_STATED",
    "NOT_AVAILABLE",
    "PLANNED",
    "UNDER_DISCUSSION",
    "BANK_SELECTED",
}
INTEREST_TYPES = {
    "INTEREST_FREE",
    "DEFERRED_INTEREST",
    "BORROWER_PAYS",
    "MIXED",
    "UNKNOWN",
    "NOT_APPLICABLE",
}


@dataclass(frozen=True)
class ValidationSummary:
    document_count: int
    scored_label_count: int
    pending_label_count: int
    evidence_count: int
    source_checked: bool


def _normalize_quote(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _iter_labels(value: Any, path: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict) and {"value", "verification", "evidence"} <= value.keys():
        yield path, value
        return
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _iter_labels(item, f"{path}/{key}")


def _page_count(text: str) -> int:
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    return len(pages)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _require_exact_keys(value: Any, required: set[str], *, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context}: expected object")
    if set(value) != required:
        raise ValueError(
            f"{context}: keys differ (missing={sorted(required - set(value))}, "
            f"extra={sorted(set(value) - required)})"
        )
    return value


def _validate_label_value(path: str, value: Any, *, context: str) -> None:
    if value is None:
        return
    if path in RATIO_PATHS:
        if not _is_number(value) or not 0 <= value <= 1:
            raise ValueError(f"{context}: ratio must be a non-boolean number in [0, 1]")
        return
    if path == "/payment_schedule/interim_installments":
        if not isinstance(value, list) or not value:
            raise ValueError(f"{context}: installments must be a non-empty list")
        for index, row in enumerate(value):
            row_context = f"{context}/{index}"
            row = _require_exact_keys(
                row,
                {"number", "ratio", "due_date"},
                context=row_context,
            )
            if isinstance(row["number"], bool) or not isinstance(row["number"], int):
                raise ValueError(f"{row_context}: number must be an integer")
            if not _is_number(row["ratio"]) or not 0 <= row["ratio"] <= 1:
                raise ValueError(f"{row_context}: ratio must be a non-boolean number in [0, 1]")
            if not isinstance(row["due_date"], str):
                raise ValueError(f"{row_context}: due_date must be a string")
            date.fromisoformat(row["due_date"])
        return
    if path == "/payment_schedule/balance_due_text":
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{context}: balance_due_text must be a non-empty string")
        return
    if path == "/payment_schedule/move_in_month":
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
            raise ValueError(f"{context}: move_in_month must be YYYY-MM")
        return
    if path == "/interim_loan/arrangement_status":
        if value not in ARRANGEMENT_STATUSES:
            raise ValueError(f"{context}: invalid arrangement status")
        return
    if path == "/interim_loan/interest_type" and value not in INTEREST_TYPES:
        raise ValueError(f"{context}: invalid interest type")


def _validate_reference_shape(case: Any, path: Path) -> list[tuple[str, dict[str, Any]]]:
    case = _require_exact_keys(
        case,
        {"schema_version", "complex_id", "source", "review", "labels"},
        context=str(path),
    )
    if case["schema_version"] != "core_reference_v0.1":
        raise ValueError(f"{path}: unsupported schema version")
    if not isinstance(case["complex_id"], str) or not re.fullmatch(r"\d{10}", case["complex_id"]):
        raise ValueError(f"{path}: complex_id must be 10 digits")
    source = _require_exact_keys(
        case["source"],
        {"pdf_filename", "text_filename", "pdf_sha256", "page_count"},
        context=f"{path}:source",
    )
    source_filenames = (source["pdf_filename"], source["text_filename"])
    if not all(isinstance(value, str) and value for value in source_filenames):
        raise ValueError(f"{path}: source filenames must be non-empty strings")
    if not isinstance(source["pdf_sha256"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", source["pdf_sha256"]
    ):
        raise ValueError(f"{path}: invalid PDF SHA-256")
    if (
        isinstance(source["page_count"], bool)
        or not isinstance(source["page_count"], int)
        or source["page_count"] < 1
    ):
        raise ValueError(f"{path}: page_count must be a positive integer")
    review = case["review"]
    if not isinstance(review, dict):
        raise ValueError(f"{path}: review must be an object")
    allowed_review_keys = {
        "text_source_review",
        "pdf_visual_review",
        "scope",
        "excluded_fields",
        "notes",
    }
    required_review_keys = allowed_review_keys - {"notes"}
    if not required_review_keys <= set(review) or not set(review) <= allowed_review_keys:
        raise ValueError(f"{path}: invalid review keys")
    if review["text_source_review"] != "COMPLETE" or review["pdf_visual_review"] != "COMPLETE":
        raise ValueError(f"{path}: text and PDF visual review must both be COMPLETE")
    if review["scope"] != "DOCUMENT_LEVEL_CORE":
        raise ValueError(f"{path}: invalid review scope")
    if not isinstance(review["excluded_fields"], dict) or not all(
        isinstance(key, str) and key and isinstance(value, str) and value
        for key, value in review["excluded_fields"].items()
    ):
        raise ValueError(f"{path}: excluded_fields must map strings to non-empty strings")
    if "notes" in review and (
        not isinstance(review["notes"], list)
        or not all(isinstance(note, str) for note in review["notes"])
    ):
        raise ValueError(f"{path}: notes must be an array of strings")
    labels = list(_iter_labels(case["labels"]))
    paths = {label_path for label_path, _item in labels}
    if paths != CORE_LABEL_PATHS:
        raise ValueError(
            f"{path}: core label paths differ "
            f"(missing={sorted(CORE_LABEL_PATHS - paths)}, "
            f"extra={sorted(paths - CORE_LABEL_PATHS)})"
        )
    for label_path, item in labels:
        context = f"{path}:{label_path}"
        item = _require_exact_keys(
            item,
            {"value", "verification", "evidence"},
            context=context,
        )
        if not isinstance(item["evidence"], list):
            raise ValueError(f"{context}: evidence must be an array")
        for index, evidence in enumerate(item["evidence"]):
            evidence = _require_exact_keys(
                evidence,
                {"page", "raw_text"},
                context=f"{context}:evidence/{index}",
            )
            if (
                isinstance(evidence["page"], bool)
                or not isinstance(evidence["page"], int)
                or evidence["page"] < 1
            ):
                raise ValueError(f"{context}: evidence page must be a positive integer")
            if not isinstance(evidence["raw_text"], str) or not evidence["raw_text"].strip():
                raise ValueError(f"{context}: evidence raw_text must be non-empty")
        _validate_label_value(label_path, item["value"], context=context)
    return labels


def validate_all(reference_dir: Path, source_dir: Path | None = None) -> ValidationSummary:
    manifest = json.loads((reference_dir.parent / "MANIFEST.json").read_text(encoding="utf-8"))
    if set(manifest) != {
        "schema_version",
        "document_count",
        "reference_ids",
        "existing_full_golden_ids",
        "metric_scope",
        "claim_limit",
    }:
        raise ValueError("manifest keys do not match the reference manifest schema")
    if manifest["schema_version"] != "core_reference_manifest_v0.1":
        raise ValueError("unsupported reference manifest schema version")
    if manifest["document_count"] != EXPECTED_DOCUMENT_COUNT:
        raise ValueError(
            f"reference manifest must lock exactly {EXPECTED_DOCUMENT_COUNT} documents"
        )
    if (
        not isinstance(manifest["reference_ids"], list)
        or len(manifest["reference_ids"]) != EXPECTED_DOCUMENT_COUNT
        or len(set(manifest["reference_ids"])) != EXPECTED_DOCUMENT_COUNT
        or not all(
            isinstance(value, str) and re.fullmatch(r"\d{10}", value)
            for value in manifest["reference_ids"]
        )
    ):
        raise ValueError("manifest reference_ids must contain 24 unique 10-digit ids")
    files = sorted(reference_dir.glob("*.json"))
    expected_ids = manifest["reference_ids"]
    actual_ids = [path.stem for path in files]
    if actual_ids != expected_ids:
        raise ValueError(f"reference ids differ from manifest: {actual_ids!r}")
    if len(files) != manifest["document_count"]:
        raise ValueError("manifest document_count does not match reference files")

    scored = 0
    pending = 0
    evidence_count = 0
    for path in files:
        case = json.loads(path.read_text(encoding="utf-8"))
        labels = _validate_reference_shape(case, path)
        if case["complex_id"] != path.stem:
            raise ValueError(f"{path}: complex_id does not match filename")
        for label_path, item in labels:
            state = item["verification"]
            if state not in ALL_STATES:
                raise ValueError(f"{path}:{label_path}: unknown state {state}")
            if state in SCORED_STATES:
                scored += 1
                if item["value"] is None:
                    raise ValueError(f"{path}:{label_path}: scored value is null")
                if not item["evidence"]:
                    raise ValueError(f"{path}:{label_path}: scored label has no evidence")
            else:
                pending += 1
                if item["value"] is not None:
                    raise ValueError(
                        f"{path}:{label_path}: expected-abstention label must remain null"
                    )
                if item["evidence"]:
                    raise ValueError(
                        f"{path}:{label_path}: absent information cannot carry positive evidence"
                    )
            evidence_count += len(item["evidence"])

        schedule = case["labels"]["payment_schedule"]
        ratio_sum = sum(
            schedule[name]["value"]
            for name in (
                "down_payment_ratio",
                "interim_payment_ratio",
                "balance_payment_ratio",
            )
        )
        if abs(ratio_sum - 1.0) > 1e-9:
            raise ValueError(f"{path}: payment ratios sum to {ratio_sum}")
        rows = schedule["interim_installments"]["value"]
        if [row["number"] for row in rows] != list(range(1, len(rows) + 1)):
            raise ValueError(f"{path}: installment numbers are not consecutive")
        installment_sum = sum(row["ratio"] for row in rows)
        if abs(installment_sum - schedule["interim_payment_ratio"]["value"]) > 1e-9:
            raise ValueError(f"{path}: installment ratios do not match interim total")
        due_dates = [date.fromisoformat(row["due_date"]) for row in rows]
        if due_dates != sorted(due_dates):
            raise ValueError(f"{path}: installment dates are not non-decreasing")
        move_in_year, move_in_month = map(int, schedule["move_in_month"]["value"].split("-"))
        if (move_in_year, move_in_month) < (due_dates[-1].year, due_dates[-1].month):
            raise ValueError(f"{path}: move-in month is before the final interim installment")

        loan = case["labels"]["interim_loan"]
        arranged = loan["arranged_ratio"]["value"]
        self_funding = loan["self_funding_ratio"]["value"]
        interim = schedule["interim_payment_ratio"]["value"]
        if arranged is not None and arranged > interim:
            raise ValueError(f"{path}: arranged ratio exceeds interim ratio")
        if (
            arranged is not None
            and self_funding is not None
            and abs(arranged + self_funding - interim) > 1e-9
        ):
            raise ValueError(f"{path}: loan split does not match interim ratio")
        prepay = loan["prepay_requirement_ratio"]["value"]
        if prepay > schedule["down_payment_ratio"]["value"]:
            raise ValueError(f"{path}: loan prepayment exceeds total down payment")

        if source_dir is None:
            continue
        source = case["source"]
        pdf_path = source_dir / source["pdf_filename"]
        text_path = source_dir / source["text_filename"]
        if not pdf_path.is_file() or not text_path.is_file():
            raise ValueError(f"{path}: locked PDF/TXT source is missing")
        digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        if digest != source["pdf_sha256"]:
            raise ValueError(f"{path}: PDF SHA-256 mismatch")
        text = text_path.read_text(encoding="utf-8", errors="replace")
        pages = text.split("\f")
        if pages and not pages[-1].strip():
            pages.pop()
        if _page_count(text) != source["page_count"]:
            raise ValueError(f"{path}: source page_count mismatch")
        for label_path, item in labels:
            for evidence in item["evidence"]:
                page_number = evidence["page"]
                if not 1 <= page_number <= len(pages):
                    raise ValueError(f"{path}:{label_path}: evidence page out of range")
                quote = _normalize_quote(evidence["raw_text"])
                page = _normalize_quote(pages[page_number - 1])
                if quote not in page:
                    raise ValueError(
                        f"{path}:{label_path}: quote not found on page {page_number}: "
                        f"{evidence['raw_text']!r}"
                    )

    return ValidationSummary(
        document_count=len(files),
        scored_label_count=scored,
        pending_label_count=pending,
        evidence_count=evidence_count,
        source_checked=source_dir is not None,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reference-dir",
        type=Path,
        default=Path(__file__).with_name("reference"),
    )
    parser.add_argument("--source-dir", type=Path)
    args = parser.parse_args()
    print(validate_all(args.reference_dir, args.source_dir))


if __name__ == "__main__":
    main()
