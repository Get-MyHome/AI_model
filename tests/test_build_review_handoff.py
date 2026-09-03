from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.build_review_handoff import build_handoff, render_markdown


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _single_page_pdf(label: str) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R >>",
        b"<< /Length 0 >>\nstream\n\nendstream",
    ]
    result = bytearray(f"%PDF-1.4\n% {label}\n".encode())
    offsets = [0]
    for number, content in enumerate(objects, start=1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode())
        result.extend(content)
        result.extend(b"\nendobj\n")
    xref_offset = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    result.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()
    )
    return bytes(result)


def _reviewed_payload(*, source_sha256: str, reviewed_at: str = "2026-09-04T00:00:00Z") -> dict:
    payload = json.loads(
        Path("docs/examples/analyze-response-v0.3.json").read_text(encoding="utf-8")
    )
    payload["complex_id"] = "C1"
    payload["review_status"] = "REVIEWED"
    payload["reviewer"] = "SECRET_REVIEWER"
    payload["reviewed_at"] = reviewed_at
    payload["target_unit"] = {
        "unit_type_id": "01",
        "unit_type_name": "59A",
        "sale_price_manwon": 108650,
    }
    payload["validation"]["passed"] = True
    payload["meta"]["schema_version"] = "v0.3"
    payload["meta"]["extractor_version"] = "0.2.0"
    payload["meta"]["source_sha256"] = source_sha256
    payload["meta"]["source_page_count"] = 1
    return payload


def _inventory(tmp_path: Path) -> dict:
    source_directory = tmp_path / "source"
    source_directory.mkdir()
    first_pdf = _single_page_pdf("owned-source-C1")
    second_pdf = _single_page_pdf("owned-source-C2")
    (source_directory / "C1.pdf").write_bytes(first_pdf)
    (source_directory / "C2.pdf").write_bytes(second_pdf)
    return {
        "schema_version": "owned_corpus_inventory_v1",
        "source_directory": str(source_directory.resolve()),
        "documents": [
            {
                "complex_id": "C1",
                "pdf_available": True,
                "pdf_path": "C1.pdf",
                "source_sha256": _sha256(first_pdf),
                "source_page_count": 1,
                "unit_tuples": [
                    {
                        "unit_type_id": "01",
                        "unit_type_name": "059.9883A",
                        "sale_price_manwon": 108650,
                    },
                    {
                        "unit_type_id": "02",
                        "unit_type_name": "084.0000A",
                        "sale_price_manwon": 120000,
                    },
                ],
            },
            {
                "complex_id": "C2",
                "pdf_available": True,
                "pdf_path": "C2.pdf",
                "source_sha256": _sha256(second_pdf),
                "source_page_count": 1,
                "unit_tuples": [
                    {
                        "unit_type_id": "01",
                        "unit_type_name": "059.0000B",
                        "sale_price_manwon": 90000,
                    }
                ],
            },
            {
                "complex_id": "C3",
                "pdf_available": False,
                "pdf_path": None,
                "source_sha256": None,
                "source_page_count": None,
                "unit_tuples": [
                    {
                        "unit_type_id": "01",
                        "unit_type_name": "055.0000A",
                        "sale_price_manwon": 50000,
                    }
                ],
            },
        ],
    }


def test_handoff_is_exact_allowlist_and_redacts_review_identity(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    c1_hash = inventory["documents"][0]["source_sha256"]
    c2_hash = inventory["documents"][1]["source_sha256"]
    reviewed_dir = tmp_path / "reviewed"
    reviewed_dir.mkdir()
    current = _reviewed_payload(source_sha256=c1_hash)
    (reviewed_dir / "current.json").write_text(json.dumps(current), encoding="utf-8")
    stale = _reviewed_payload(source_sha256=c1_hash)
    stale["meta"]["extractor_version"] = "0.1.0"
    (reviewed_dir / "stale.json").write_text(json.dumps(stale), encoding="utf-8")
    (reviewed_dir / "invalid.json").write_text("not-json", encoding="utf-8")

    report = build_handoff(
        inventory=inventory,
        reviewed_dir=reviewed_dir,
        schema_version="v0.3",
        extractor_version="0.2.0",
        observations=[
            {
                "observation_id": "wrong-complex-source",
                "complex_id": "C1",
                "source_sha256": c2_hash,
                "source_page_count": 1,
                "pdf_url": "https://must-not-appear.invalid/secret",
            },
            {
                "observation_id": "unknown-prefix",
                "source_sha256_prefix": "e064",
                "source_page_count": 71,
            },
        ],
        generated_at=datetime(2026, 9, 4, tzinfo=UTC),
    )

    assert report["summary"] == {
        "document_count": 3,
        "pdf_backed_document_count": 2,
        "source_unavailable_document_count": 1,
        "target_tuple_count": 4,
        "pdf_backed_target_tuple_count": 3,
        "exact_reviewed_target_tuple_count": 1,
        "pending_human_review_target_tuple_count": 2,
        "review_conflict_target_tuple_count": 0,
        "source_unavailable_target_tuple_count": 1,
        "ineligible_reviewed_artifact_count": 2,
        "orphaned_eligible_reviewed_artifact_count": 0,
        "live_source_status_counts": {
            "NOT_IN_OWNED_CORPUS": 1,
            "REQUEST_SOURCE_MISMATCH": 1,
        },
    }
    target = report["backend_ready_targets"][0]
    assert target["complex_id"] == "C1"
    assert target["unit_type_name"] == "059.9883A"
    assert target["normalized_unit_type_name"] == "59A"
    assert target["artifact_file"] == "current.json"

    assert report["inventory_source_checks"] == [
        {
            "complex_id": "C1",
            "pdf_path": "C1.pdf",
            "source_sha256": c1_hash,
            "source_page_count": 1,
            "status": "SOURCE_IDENTITY_VERIFIED",
        },
        {
            "complex_id": "C2",
            "pdf_path": "C2.pdf",
            "source_sha256": c2_hash,
            "source_page_count": 1,
            "status": "SOURCE_IDENTITY_VERIFIED",
        },
    ]
    mismatch = report["live_source_checks"][0]
    assert mismatch["status"] == "REQUEST_SOURCE_MISMATCH"
    assert mismatch["matched_owned_complex_ids"] == ["C2"]
    assert mismatch["expected_comparison"] == {
        "source_sha256_matches": False,
        "source_page_count_matches": True,
    }
    assert report["live_source_checks"][1]["status"] == "NOT_IN_OWNED_CORPUS"

    serialized = json.dumps(report)
    assert "SECRET_REVIEWER" not in serialized
    assert str(inventory["source_directory"]) not in serialized
    assert "pdf_url" not in serialized
    assert "must-not-appear" not in serialized
    assert "current.json" in render_markdown(report) or target["complex_id"] in render_markdown(
        report
    )


def test_partial_source_identity_cannot_prove_request_match(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    c1_hash = inventory["documents"][0]["source_sha256"]
    report = build_handoff(
        inventory=inventory,
        reviewed_dir=tmp_path / "missing",
        schema_version="v0.3",
        extractor_version="0.2.0",
        observations=[
            {
                "observation_id": "prefix-only",
                "complex_id": "C1",
                "source_sha256_prefix": c1_hash[:4],
            }
        ],
        generated_at=datetime(2026, 9, 4, tzinfo=UTC),
    )

    check = report["live_source_checks"][0]
    assert check["status"] == "REQUEST_SOURCE_INCOMPLETE"
    assert check["expected_comparison"]["source_sha256_matches"] is True
    assert check["expected_comparison"]["source_page_count_matches"] is None


def test_matching_prefix_and_page_count_still_cannot_prove_match(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    c1_hash = inventory["documents"][0]["source_sha256"]
    report = build_handoff(
        inventory=inventory,
        reviewed_dir=tmp_path / "missing",
        schema_version="v0.3",
        extractor_version="0.2.0",
        observations=[
            {
                "observation_id": "prefix-and-pages",
                "complex_id": "C1",
                "source_sha256_prefix": c1_hash[:4],
                "source_page_count": 1,
            }
        ],
        generated_at=datetime(2026, 9, 4, tzinfo=UTC),
    )

    check = report["live_source_checks"][0]
    assert check["status"] == "REQUEST_SOURCE_INCOMPLETE"
    assert check["expected_comparison"] == {
        "source_sha256_matches": True,
        "source_page_count_matches": True,
    }


def test_noncanonical_review_target_is_not_backend_ready(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    c1_hash = inventory["documents"][0]["source_sha256"]
    reviewed_dir = tmp_path / "reviewed"
    reviewed_dir.mkdir()
    candidate = _reviewed_payload(source_sha256=c1_hash)
    candidate["target_unit"]["unit_type_name"] = "059.9883A"
    (reviewed_dir / "noncanonical.json").write_text(
        json.dumps(candidate), encoding="utf-8"
    )

    report = build_handoff(
        inventory=inventory,
        reviewed_dir=reviewed_dir,
        schema_version="v0.3",
        extractor_version="0.2.0",
    )

    assert report["backend_ready_targets"] == []
    assert report["ineligible_reviewed_artifacts"][0]["reasons"] == [
        "UNIT_TYPE_NAME_NOT_CANONICAL"
    ]


def test_naive_review_timestamp_is_not_backend_ready(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    c1_hash = inventory["documents"][0]["source_sha256"]
    reviewed_dir = tmp_path / "reviewed"
    reviewed_dir.mkdir()
    candidate = _reviewed_payload(
        source_sha256=c1_hash,
        reviewed_at="2026-09-04T00:00:00",
    )
    (reviewed_dir / "naive-time.json").write_text(json.dumps(candidate), encoding="utf-8")

    report = build_handoff(
        inventory=inventory,
        reviewed_dir=reviewed_dir,
        schema_version="v0.3",
        extractor_version="0.2.0",
    )

    assert report["backend_ready_targets"] == []
    assert report["ineligible_reviewed_artifacts"][0]["reasons"] == [
        "REVIEWED_AT_TIMEZONE_MISSING"
    ]


def test_duplicate_exact_reviewed_target_fails_closed_with_conflict(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    c1_hash = inventory["documents"][0]["source_sha256"]
    reviewed_dir = tmp_path / "reviewed"
    reviewed_dir.mkdir()
    first = _reviewed_payload(source_sha256=c1_hash)
    second = _reviewed_payload(
        source_sha256=c1_hash,
        reviewed_at="2026-09-04T01:00:00+00:00",
    )
    (reviewed_dir / "first.json").write_text(json.dumps(first), encoding="utf-8")
    (reviewed_dir / "second.json").write_text(json.dumps(second), encoding="utf-8")

    report = build_handoff(
        inventory=inventory,
        reviewed_dir=reviewed_dir,
        schema_version="v0.3",
        extractor_version="0.2.0",
    )

    assert report["backend_ready_targets"] == []
    assert report["summary"]["review_conflict_target_tuple_count"] == 1
    assert report["summary"]["pending_human_review_target_tuple_count"] == 2
    assert report["conflicting_reviewed_targets"] == [
        {
            "target": {
                "complex_id": "C1",
                "unit_type_id": "01",
                "unit_type_name": "059.9883A",
                "sale_price_manwon": 108650,
                "source_sha256": c1_hash,
                "source_page_count": 1,
            },
            "artifact_files": ["first.json", "second.json"],
            "reason": "MULTIPLE_REVIEWED_ARTIFACTS_FOR_EXACT_TARGET",
        }
    ]
    assert report["document_coverage"][0]["review_conflict_target_count"] == 1
    assert report["orphaned_eligible_reviewed_artifacts"] == []
    markdown = render_markdown(report)
    assert "MULTIPLE_REVIEWED_ARTIFACTS_FOR_EXACT_TARGET" in markdown
    assert "first.json" in markdown
    assert "second.json" in markdown


def test_changed_inventory_pdf_aborts_handoff(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    source_pdf = Path(inventory["source_directory"]) / inventory["documents"][0]["pdf_path"]
    source_pdf.write_bytes(b"%PDF-1.4\nchanged-after-inventory\n")

    with pytest.raises(ValueError, match="C1: inventory PDF SHA-256 mismatch"):
        build_handoff(
            inventory=inventory,
            reviewed_dir=tmp_path / "missing",
            schema_version="v0.3",
            extractor_version="0.2.0",
        )


def test_inventory_pdf_path_escape_aborts_handoff(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    outside_pdf = _single_page_pdf("outside-source")
    (tmp_path / "outside.pdf").write_bytes(outside_pdf)
    inventory["documents"][0]["pdf_path"] = "../outside.pdf"
    inventory["documents"][0]["source_sha256"] = _sha256(outside_pdf)

    with pytest.raises(ValueError, match="inventory PDF path escapes source_directory"):
        build_handoff(
            inventory=inventory,
            reviewed_dir=tmp_path / "missing",
            schema_version="v0.3",
            extractor_version="0.2.0",
        )


def test_missing_inventory_pdf_aborts_handoff(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    source_pdf = Path(inventory["source_directory"]) / inventory["documents"][0]["pdf_path"]
    source_pdf.unlink()

    with pytest.raises(ValueError, match="C1: inventory PDF is unavailable"):
        build_handoff(
            inventory=inventory,
            reviewed_dir=tmp_path / "missing",
            schema_version="v0.3",
            extractor_version="0.2.0",
        )


def test_wrong_inventory_page_count_aborts_handoff(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    inventory["documents"][0]["source_page_count"] = 2

    with pytest.raises(ValueError, match="C1: inventory PDF page count mismatch"):
        build_handoff(
            inventory=inventory,
            reviewed_dir=tmp_path / "missing",
            schema_version="v0.3",
            extractor_version="0.2.0",
        )


def test_duplicate_normalized_inventory_target_aborts_handoff(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    inventory["documents"][0]["unit_tuples"].append(
        {
            "unit_type_id": "01",
            "unit_type_name": "59A",
            "sale_price_manwon": 108650,
        }
    )

    with pytest.raises(ValueError, match="duplicate normalized inventory target"):
        build_handoff(
            inventory=inventory,
            reviewed_dir=tmp_path / "missing",
            schema_version="v0.3",
            extractor_version="0.2.0",
        )


def test_inconsistent_pdf_availability_aborts_handoff(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    inventory["documents"][0]["pdf_available"] = False

    with pytest.raises(ValueError, match="unavailable PDF must have null"):
        build_handoff(
            inventory=inventory,
            reviewed_dir=tmp_path / "missing",
            schema_version="v0.3",
            extractor_version="0.2.0",
        )


def test_wrong_inventory_schema_aborts_handoff(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    inventory["schema_version"] = "legacy"

    with pytest.raises(
        ValueError,
        match="inventory schema_version must be owned_corpus_inventory_v1",
    ):
        build_handoff(
            inventory=inventory,
            reviewed_dir=tmp_path / "missing",
            schema_version="v0.3",
            extractor_version="0.2.0",
        )
