from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from evaluation.evaluate_core import evaluate
from evaluation.validate_references import validate_all

REFERENCE_DIR = Path("evaluation/reference")
LOCAL_SOURCE_FALLBACK = Path(
    "/home/soccz/22tb/tmp/claude-1001/"
    "-mnt-20t-AI----/291eec41-8358-4bc9-b1f0-ced7d4ee1d23/"
    "scratchpad/gonggo"
)


def _hold(reason_code: str) -> dict[str, Any]:
    return {
        "reason_code": reason_code,
        "kind": "DOCUMENT_UNCERTAINTY",
        "blocking": True,
        "message": "확인이 필요합니다.",
        "next_action": "원문을 확인하세요.",
    }


def _response_for_reference(
    reference: dict[str, Any],
    *,
    pending_hold: bool = True,
) -> dict[str, Any]:
    payment = reference["labels"]["payment_schedule"]
    loan = reference["labels"]["interim_loan"]
    pending = any(item["verification"] == "VERIFIED_NOT_STATED" for item in loan.values())
    holds = [_hold("INTERIM_LOAN_RATIO_MISSING")] if pending and pending_hold else []
    return {
        "complex_id": reference["complex_id"],
        "analysis_status": "PARTIAL" if pending else "READY",
        "review_status": "NEEDS_REVIEW" if pending else "AUTO_EXTRACTED",
        "reviewer": None,
        "reviewed_at": None,
        "target_unit": {
            "unit_type_id": None,
            "unit_type_name": None,
            "sale_price_manwon": None,
        },
        "payment_schedule": {
            "down_payment": {
                "total_ratio": payment["down_payment_ratio"]["value"],
                "total_amount_manwon": None,
                "basis": "RATIO",
                "installments": [],
                "due_date": None,
                "due_month": None,
                "due_text": None,
            },
            "interim_payment": {
                "total_ratio": payment["interim_payment_ratio"]["value"],
                "total_amount_manwon": None,
                "basis": "RATIO",
                "installments": [
                    {
                        "number": row["number"],
                        "ratio": row["ratio"],
                        "amount_manwon": None,
                        "due_date": row["due_date"],
                        "due_text": None,
                    }
                    for row in payment["interim_installments"]["value"]
                ],
                "due_date": None,
                "due_month": None,
                "due_text": None,
            },
            "balance_payment": {
                "total_ratio": payment["balance_payment_ratio"]["value"],
                "total_amount_manwon": None,
                "basis": "RATIO",
                "installments": [],
                "due_date": None,
                "due_month": payment["move_in_month"]["value"],
                "due_text": payment["balance_due_text"]["value"],
            },
        },
        "interim_loan": {
            "arrangement_status": loan["arrangement_status"]["value"],
            "arranged_ratio": loan["arranged_ratio"]["value"],
            "arranged_amount_manwon": None,
            "self_funding_ratio": loan["self_funding_ratio"]["value"],
            "self_funding_amount_manwon": None,
            "self_funding_origin": None,
            "bank_names": [],
            "guarantee_provider": None,
            "interest_type": loan["interest_type"]["value"],
            "interest_note": None,
            "prepay_requirement_ratio": loan["prepay_requirement_ratio"]["value"],
        },
        "additional_costs": [],
        "analysis_summary": "테스트 응답",
        "holds": holds,
        "exception_flags": [],
        "evidence": [],
        "validation": {"passed": True, "issues": [], "derived_fields": []},
        "meta": {
            "schema_version": "v0.3",
            "extractor_version": "test",
            "prompt_version": "test",
            "provider": "test",
            "model": None,
            "source_sha256": reference["source"]["pdf_sha256"],
            "source_page_count": reference["source"]["page_count"],
            "candidate_pages": [],
            "analyzed_at": "2026-09-02T00:00:00Z",
        },
    }


def _write_actual(actual_dir: Path, reference: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    actual_dir.mkdir(parents=True, exist_ok=True)
    payload = _response_for_reference(reference, **kwargs)
    (actual_dir / f"{reference['complex_id']}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return payload


def _copy_evaluation_fixture(tmp_path: Path) -> Path:
    fixture = tmp_path / "evaluation"
    fixture.mkdir()
    shutil.copy2("evaluation/MANIFEST.json", fixture / "MANIFEST.json")
    shutil.copytree(REFERENCE_DIR, fixture / "reference")
    return fixture / "reference"


def test_remaining_24_reference_structure_is_complete_and_consistent() -> None:
    summary = validate_all(REFERENCE_DIR)
    assert summary.document_count == 24
    assert summary.scored_label_count == 260
    assert summary.pending_label_count == 4
    assert not summary.source_checked


def test_every_reference_quote_matches_locked_local_source() -> None:
    configured = os.getenv("GET_MYHOME_REFERENCE_SOURCE_DIR")
    source_dir = Path(configured) if configured else LOCAL_SOURCE_FALLBACK
    if not source_dir.is_dir():
        pytest.skip("set GET_MYHOME_REFERENCE_SOURCE_DIR to verify original PDF/TXT evidence")
    summary = validate_all(REFERENCE_DIR, source_dir)
    assert summary.document_count == 24
    assert summary.evidence_count > summary.scored_label_count
    assert summary.source_checked


def test_partial_one_of_24_is_explicitly_non_publishable(tmp_path: Path) -> None:
    reference = json.loads((REFERENCE_DIR / "2026000327.json").read_text(encoding="utf-8"))
    _write_actual(tmp_path, reference)

    report = evaluate(tmp_path, REFERENCE_DIR)

    assert report["aggregate_status"] == "INCOMPLETE_NON_PUBLISHABLE"
    assert report["publishable"] is False
    assert report["evaluated_document_count"] == 1
    assert len(report["missing_actual_ids"]) == 23
    assert report["label_match_rate"] is None
    assert report["scored_fields_exact_document_count"] is None
    metric = report["field_metrics"]["/interim_loan/arrangement_status"]
    assert metric == {
        "reference_total": 24,
        "eligible": 24,
        "pending": 0,
        "missing": 23,
        "compared": 1,
        "matched": 1,
        "match_rate": None,
    }
    assert report["cases"][0]["scored_fields_exact"] is True
    assert report["cases"][0]["safety_exact"] is False
    assert report["evidence_accuracy"]["status"] == "NOT_EVALUATED"


def test_pending_requires_null_correct_blocking_hold_and_non_ready_status(
    tmp_path: Path,
) -> None:
    reference = json.loads((REFERENCE_DIR / "2026000295.json").read_text(encoding="utf-8"))
    _write_actual(tmp_path, reference)

    report = evaluate(tmp_path, REFERENCE_DIR)

    assert report["pending_label_count"] == 2
    assert report["pending_safe_abstention_count"] == 2
    assert report["pending_safe_abstention_rate"] is None  # incomplete runs are non-publishable
    assert report["unsafe_pending_values"] == []


def test_pending_non_null_value_is_unsafe(tmp_path: Path) -> None:
    reference = json.loads((REFERENCE_DIR / "2026000295.json").read_text(encoding="utf-8"))
    payload = _write_actual(tmp_path, reference)
    payload["interim_loan"]["arranged_ratio"] = 0.4
    (tmp_path / "2026000295.json").write_text(json.dumps(payload), encoding="utf-8")

    report = evaluate(tmp_path, REFERENCE_DIR)

    assert report["pending_safe_abstention_count"] == 1
    assert report["unsafe_pending_values"] == [
        {
            "complex_id": "2026000295",
            "field": "/interim_loan/arranged_ratio",
            "actual": 0.4,
            "reason": "NON_NULL_VALUE",
        }
    ]


def test_pending_null_without_correct_hold_is_unsafe(tmp_path: Path) -> None:
    reference = json.loads((REFERENCE_DIR / "2026000295.json").read_text(encoding="utf-8"))
    _write_actual(tmp_path, reference, pending_hold=False)

    report = evaluate(tmp_path, REFERENCE_DIR)

    assert report["pending_safe_abstention_count"] == 0
    assert {item["reason"] for item in report["unsafe_pending_values"]} == {
        "CORRECT_BLOCKING_HOLD_MISSING"
    }


def test_pending_null_with_hold_but_ready_status_is_unsafe(tmp_path: Path) -> None:
    reference = json.loads((REFERENCE_DIR / "2026000295.json").read_text(encoding="utf-8"))
    payload = _write_actual(tmp_path, reference)
    payload["analysis_status"] = "READY"
    (tmp_path / "2026000295.json").write_text(json.dumps(payload), encoding="utf-8")

    report = evaluate(tmp_path, REFERENCE_DIR)

    assert report["pending_safe_abstention_count"] == 0
    assert {item["reason"] for item in report["unsafe_pending_values"]} == {"ANALYSIS_STATUS_READY"}


def test_actual_wrong_complex_id_is_rejected(tmp_path: Path) -> None:
    reference = json.loads((REFERENCE_DIR / "2026000327.json").read_text(encoding="utf-8"))
    payload = _write_actual(tmp_path, reference)
    payload["complex_id"] = "2026009999"
    (tmp_path / "2026000327.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match filename"):
        evaluate(tmp_path, REFERENCE_DIR)


def test_actual_wrong_source_sha_is_rejected(tmp_path: Path) -> None:
    reference = json.loads((REFERENCE_DIR / "2026000327.json").read_text(encoding="utf-8"))
    payload = _write_actual(tmp_path, reference)
    payload["meta"]["source_sha256"] = "0" * 64
    (tmp_path / "2026000327.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="source_sha256 mismatch"):
        evaluate(tmp_path, REFERENCE_DIR)


def test_actual_boolean_ratio_is_rejected_instead_of_coerced(tmp_path: Path) -> None:
    reference = json.loads((REFERENCE_DIR / "2026000327.json").read_text(encoding="utf-8"))
    payload = _write_actual(tmp_path, reference)
    payload["payment_schedule"]["down_payment"]["total_ratio"] = True
    (tmp_path / "2026000327.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="boolean in numeric field"):
        evaluate(tmp_path, REFERENCE_DIR)


def test_reference_boolean_ratio_is_rejected(tmp_path: Path) -> None:
    reference_dir = _copy_evaluation_fixture(tmp_path)
    path = reference_dir / "2026000327.json"
    reference = json.loads(path.read_text(encoding="utf-8"))
    reference["labels"]["payment_schedule"]["down_payment_ratio"]["value"] = True
    path.write_text(json.dumps(reference), encoding="utf-8")

    with pytest.raises(ValueError, match="non-boolean number"):
        validate_all(reference_dir)


def test_reference_visual_review_must_be_complete(tmp_path: Path) -> None:
    reference_dir = _copy_evaluation_fixture(tmp_path)
    path = reference_dir / "2026000327.json"
    reference = json.loads(path.read_text(encoding="utf-8"))
    reference["review"]["pdf_visual_review"] = "PENDING"
    path.write_text(json.dumps(reference), encoding="utf-8")

    with pytest.raises(ValueError, match="must both be COMPLETE"):
        validate_all(reference_dir)


def test_reference_set_missing_one_of_24_is_rejected(tmp_path: Path) -> None:
    reference_dir = _copy_evaluation_fixture(tmp_path)
    (reference_dir / "2026000327.json").unlink()

    with pytest.raises(ValueError, match="reference ids differ from manifest"):
        validate_all(reference_dir)


def test_self_reported_validation_is_recomputed_from_locked_source(tmp_path: Path) -> None:
    if not LOCAL_SOURCE_FALLBACK.is_dir():
        pytest.skip("locked source is unavailable")
    reference = json.loads((REFERENCE_DIR / "2026000327.json").read_text(encoding="utf-8"))
    _write_actual(tmp_path, reference)

    report = evaluate(tmp_path, REFERENCE_DIR, LOCAL_SOURCE_FALLBACK)
    recomputed = report["cases"][0]["recomputed_validation"]

    assert recomputed["self_reported_passed"] is True
    assert recomputed["passed"] is False
    assert recomputed["self_report_disagrees"] is True
    assert "EVIDENCE_MISSING" in recomputed["issue_codes"]
