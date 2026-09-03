from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import synthetic_pages

from get_myhome_ai.models import AnalyzeRequest, ReviewStatus
from get_myhome_ai.pdf_text import DownloadedPdf
from get_myhome_ai.pipeline import AnalysisPipeline
from get_myhome_ai.providers.fixture import FixtureExtractor
from get_myhome_ai.review import load_result, save_result
from get_myhome_ai.review_batch import (
    APPROVAL_ATTESTATION,
    ApprovalDecision,
    approve_review_batch,
    load_review_approval_manifest,
    load_review_draft_manifest,
    prepare_review_batch,
    validate_review_batch_approval,
)
from get_myhome_ai.settings import Settings

PDF_CONTENT = b"%PDF-batch-review"


async def _automatic_result(case, *, settings: Settings):
    pages = synthetic_pages(case)

    async def loader(_url, _settings):
        return DownloadedPdf(
            content=PDF_CONTENT,
            sha256=hashlib.sha256(PDF_CONTENT).hexdigest(),
        )

    pipeline = AnalysisPipeline(
        settings=settings,
        provider=FixtureExtractor({case.complex_id: case.expected}),
        url_loader=loader,
        page_extractor=lambda _content, _settings: pages,
    )
    return await pipeline.analyze_url(
        AnalyzeRequest(
            complex_id=case.complex_id,
            pdf_url="https://example.com/announcement.pdf",
        )
    )


def _write_inventory(tmp_path: Path, case) -> Path:
    pdf_path = tmp_path / "source" / "announcement.pdf"
    pdf_path.parent.mkdir()
    pdf_path.write_bytes(PDF_CONTENT)
    payload = {
        "schema_version": "owned_corpus_inventory_v1",
        "source_directory": "source",
        "summary": {},
        "documents": [],
        "targets": [
            {
                "complex_id": case.complex_id,
                "unit_type_id": "01",
                "unit_type_name": "059.9883A",
                "sale_price_manwon": case.sale_price_manwon,
                "pdf_available": True,
                "detail_html_path": "detail.html",
                "pdf_path": "announcement.pdf",
                "source_sha256": hashlib.sha256(PDF_CONTENT).hexdigest(),
                "source_page_count": len(synthetic_pages(case)),
            }
        ],
    }
    destination = tmp_path / "inventory.json"
    destination.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return destination


def _page_extractor(case):
    return lambda _content, _settings: synthetic_pages(case)


def _fill_approval(template_path: Path, destination: Path, *, reviewer: str) -> None:
    payload = json.loads(template_path.read_text(encoding="utf-8"))
    payload["reviewer"] = reviewer
    payload["reviewed_at"] = datetime.now(UTC).isoformat()
    payload["attestation"] = APPROVAL_ATTESTATION
    payload["items"][0]["decision"] = ApprovalDecision.APPROVE
    payload["items"][0]["checks"] = {
        "source_pdf_visual_reviewed": True,
        "target_unit_and_sale_price_reviewed": True,
        "payment_and_loan_terms_reviewed": True,
        "evidence_pages_and_quotes_reviewed": True,
        "additional_cost_scope_reviewed": True,
    }
    destination.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


async def test_prepare_batch_only_writes_unreviewed_drafts_and_pending_template(
    golden_cases,
    tmp_path,
) -> None:
    case = golden_cases["2026000372"]
    settings = Settings(ai_provider="fixture")
    automatic = await _automatic_result(case, settings=settings)
    auto_dir = tmp_path / "auto"
    save_result(automatic, auto_dir / f"{case.complex_id}.json")
    inventory_path = _write_inventory(tmp_path, case)
    output_dir = tmp_path / "review-work"

    result = prepare_review_batch(
        inventory_path=inventory_path,
        auto_artifact_dirs=[auto_dir],
        output_dir=output_dir,
        settings=settings,
        page_extractor=_page_extractor(case),
    )

    assert result.summary.draft_count == 1
    assert result.summary.approval_eligible_draft_count == 1
    manifest = load_review_draft_manifest(output_dir / "review-draft-manifest.json")
    entry = manifest.drafts[0]
    assert entry.artifact_type == "REVIEW_DRAFT"
    assert entry.approval_state == "PENDING"
    draft = load_result(output_dir / entry.draft_path)
    assert draft.review_status in {ReviewStatus.AUTO_EXTRACTED, ReviewStatus.NEEDS_REVIEW}
    assert draft.review_status != ReviewStatus.REVIEWED
    assert draft.reviewer is None
    assert draft.reviewed_at is None
    assert draft.target_unit.unit_type_id == "01"
    assert draft.target_unit.unit_type_name == "59A"
    assert draft.target_unit.sale_price_manwon == case.sale_price_manwon

    template = load_review_approval_manifest(
        output_dir / "review-approval-manifest.template.json"
    )
    assert template.reviewer is None
    assert template.items[0].decision == ApprovalDecision.PENDING
    assert not template.items[0].checks.all_confirmed()
    checklist = (output_dir / entry.checklist_path).read_text(encoding="utf-8")
    assert "`REVIEW_DRAFT`" in checklist
    assert "`REVIEWED`가 아닙니다" in checklist
    assert "## 납부구조" in checklist
    assert "## 중도금 금융조건" in checklist
    assert "## 추가비용" in checklist
    assert "## 위험조항" in checklist

    with pytest.raises(FileExistsError, match="새 output-dir"):
        prepare_review_batch(
            inventory_path=inventory_path,
            auto_artifact_dirs=[auto_dir],
            output_dir=output_dir,
            settings=settings,
            page_extractor=_page_extractor(case),
        )


async def test_batch_approval_requires_manifest_reviewer_and_explicit_confirmation(
    golden_cases,
    tmp_path,
) -> None:
    case = golden_cases["2026000372"]
    settings = Settings(ai_provider="fixture")
    automatic = await _automatic_result(case, settings=settings)
    auto_dir = tmp_path / "auto"
    save_result(automatic, auto_dir / f"{case.complex_id}.json")
    work_dir = tmp_path / "review-work"
    prepare_review_batch(
        inventory_path=_write_inventory(tmp_path, case),
        auto_artifact_dirs=[auto_dir],
        output_dir=work_dir,
        settings=settings,
        page_extractor=_page_extractor(case),
    )
    approval_path = tmp_path / "approval.json"
    _fill_approval(
        work_dir / "review-approval-manifest.template.json",
        approval_path,
        reviewer="안지홍",
    )

    incomplete_payload = json.loads(approval_path.read_text(encoding="utf-8"))
    incomplete_payload["items"][0]["checks"]["additional_cost_scope_reviewed"] = False
    incomplete_path = tmp_path / "approval-missing-additional-cost-check.json"
    incomplete_path.write_text(
        json.dumps(incomplete_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="every review check"):
        load_review_approval_manifest(incomplete_path)

    with pytest.raises(ValueError, match="명시적 승인"):
        validate_review_batch_approval(
            draft_manifest_path=work_dir / "review-draft-manifest.json",
            approval_manifest_path=approval_path,
            reviewer="안지홍",
            settings=settings,
            explicit_confirmation=False,
            page_extractor=_page_extractor(case),
        )
    with pytest.raises(ValueError, match="검수자가 다릅니다"):
        validate_review_batch_approval(
            draft_manifest_path=work_dir / "review-draft-manifest.json",
            approval_manifest_path=approval_path,
            reviewer="다른 검수자",
            settings=settings,
            explicit_confirmation=True,
            page_extractor=_page_extractor(case),
        )

    reviewed_dir = tmp_path / "reviewed-batch"
    receipt = approve_review_batch(
        draft_manifest_path=work_dir / "review-draft-manifest.json",
        approval_manifest_path=approval_path,
        output_dir=reviewed_dir,
        reviewer="안지홍",
        settings=settings,
        explicit_confirmation=True,
        page_extractor=_page_extractor(case),
    )

    assert receipt.approved_count == 1
    assert receipt.schema_version == "review_approval_receipt_v2"
    assert receipt.draft_manifest_sha256 == hashlib.sha256(
        (work_dir / "review-draft-manifest.json").read_bytes()
    ).hexdigest()
    assert receipt.items[0].target.unit_type_name == "059.9883A"
    reviewed = load_result(reviewed_dir / receipt.items[0].reviewed_artifact_path)
    assert reviewed.review_status == ReviewStatus.REVIEWED
    assert reviewed.reviewer == "안지홍"
    assert reviewed.reviewed_at is not None
    assert (reviewed_dir / "review-approval-receipt.json").is_file()


async def test_batch_approval_rejects_tampered_draft_hash(golden_cases, tmp_path) -> None:
    case = golden_cases["2026000372"]
    settings = Settings(ai_provider="fixture")
    automatic = await _automatic_result(case, settings=settings)
    auto_dir = tmp_path / "auto"
    save_result(automatic, auto_dir / f"{case.complex_id}.json")
    work_dir = tmp_path / "review-work"
    prepare_review_batch(
        inventory_path=_write_inventory(tmp_path, case),
        auto_artifact_dirs=[auto_dir],
        output_dir=work_dir,
        settings=settings,
        page_extractor=_page_extractor(case),
    )
    approval_path = tmp_path / "approval.json"
    _fill_approval(
        work_dir / "review-approval-manifest.template.json",
        approval_path,
        reviewer="안지홍",
    )
    manifest = load_review_draft_manifest(work_dir / "review-draft-manifest.json")
    draft_path = work_dir / manifest.drafts[0].draft_path
    draft_path.write_text(draft_path.read_text(encoding="utf-8") + " ", encoding="utf-8")

    with pytest.raises(ValueError, match="초안 SHA-256"):
        validate_review_batch_approval(
            draft_manifest_path=work_dir / "review-draft-manifest.json",
            approval_manifest_path=approval_path,
            reviewer="안지홍",
            settings=settings,
            explicit_confirmation=True,
            page_extractor=_page_extractor(case),
        )


async def test_outdated_extractor_draft_is_prepared_but_cannot_be_approved(
    golden_cases,
    tmp_path,
) -> None:
    case = golden_cases["2026000372"]
    settings = Settings(ai_provider="fixture")
    automatic = await _automatic_result(case, settings=settings)
    automatic.meta.extractor_version = "0.1.0"
    auto_dir = tmp_path / "auto"
    save_result(automatic, auto_dir / f"{case.complex_id}.json")
    work_dir = tmp_path / "review-work"
    manifest = prepare_review_batch(
        inventory_path=_write_inventory(tmp_path, case),
        auto_artifact_dirs=[auto_dir],
        output_dir=work_dir,
        settings=settings,
        page_extractor=_page_extractor(case),
    )
    assert manifest.summary.draft_count == 1
    assert manifest.summary.approval_eligible_draft_count == 0
    assert manifest.drafts[0].approval_blockers == [
        "EXTRACTOR_VERSION_MISMATCH:0.1.0"
    ]

    approval_path = tmp_path / "approval.json"
    _fill_approval(
        work_dir / "review-approval-manifest.template.json",
        approval_path,
        reviewer="안지홍",
    )
    with pytest.raises(ValueError, match="승인 차단 사유"):
        validate_review_batch_approval(
            draft_manifest_path=work_dir / "review-draft-manifest.json",
            approval_manifest_path=approval_path,
            reviewer="안지홍",
            settings=settings,
            explicit_confirmation=True,
            page_extractor=_page_extractor(case),
        )


async def test_batch_approval_rejects_tampered_manifest_hash(
    golden_cases,
    tmp_path,
) -> None:
    case = golden_cases["2026000372"]
    settings = Settings(ai_provider="fixture")
    automatic = await _automatic_result(case, settings=settings)
    auto_dir = tmp_path / "auto"
    save_result(automatic, auto_dir / f"{case.complex_id}.json")
    work_dir = tmp_path / "review-work"
    prepare_review_batch(
        inventory_path=_write_inventory(tmp_path, case),
        auto_artifact_dirs=[auto_dir],
        output_dir=work_dir,
        settings=settings,
        page_extractor=_page_extractor(case),
    )
    approval_path = tmp_path / "approval.json"
    _fill_approval(
        work_dir / "review-approval-manifest.template.json",
        approval_path,
        reviewer="안지홍",
    )
    manifest_path = work_dir / "review-draft-manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["drafts"][0]["preparation_warnings"] = ["TAMPERED"]
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="검수 배치 SHA-256"):
        validate_review_batch_approval(
            draft_manifest_path=manifest_path,
            approval_manifest_path=approval_path,
            reviewer="안지홍",
            settings=settings,
            explicit_confirmation=True,
            page_extractor=_page_extractor(case),
        )


async def test_batch_approval_recomputes_version_blockers_from_auto_artifact(
    golden_cases,
    tmp_path,
) -> None:
    case = golden_cases["2026000372"]
    settings = Settings(ai_provider="fixture")
    automatic = await _automatic_result(case, settings=settings)
    automatic.meta.extractor_version = "0.1.0"
    auto_dir = tmp_path / "auto"
    save_result(automatic, auto_dir / f"{case.complex_id}.json")
    work_dir = tmp_path / "review-work"
    prepare_review_batch(
        inventory_path=_write_inventory(tmp_path, case),
        auto_artifact_dirs=[auto_dir],
        output_dir=work_dir,
        settings=settings,
        page_extractor=_page_extractor(case),
    )

    manifest_path = work_dir / "review-draft-manifest.json"
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_payload["drafts"][0]["extractor_version"] = settings.extractor_version
    manifest_payload["drafts"][0]["approval_blockers"] = []
    manifest_payload["summary"]["approval_eligible_draft_count"] = 1
    manifest_payload["summary"]["blocked_draft_count"] = 0
    manifest_path.write_text(
        json.dumps(manifest_payload, ensure_ascii=False),
        encoding="utf-8",
    )

    draft_path = work_dir / manifest_payload["drafts"][0]["draft_path"]
    draft_payload = json.loads(draft_path.read_text(encoding="utf-8"))
    draft_payload["meta"]["extractor_version"] = settings.extractor_version
    draft_path.write_text(json.dumps(draft_payload, ensure_ascii=False), encoding="utf-8")

    approval_path = tmp_path / "approval.json"
    approval_payload = json.loads(
        (work_dir / "review-approval-manifest.template.json").read_text(
            encoding="utf-8"
        )
    )
    approval_payload["draft_manifest_sha256"] = hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    approval_payload["reviewer"] = "안지홍"
    approval_payload["reviewed_at"] = datetime.now(UTC).isoformat()
    approval_payload["attestation"] = APPROVAL_ATTESTATION
    approval_payload["items"][0]["decision"] = ApprovalDecision.APPROVE
    approval_payload["items"][0]["draft_sha256"] = hashlib.sha256(
        draft_path.read_bytes()
    ).hexdigest()
    approval_payload["items"][0]["checks"] = {
        "source_pdf_visual_reviewed": True,
        "target_unit_and_sale_price_reviewed": True,
        "payment_and_loan_terms_reviewed": True,
        "evidence_pages_and_quotes_reviewed": True,
        "additional_cost_scope_reviewed": True,
    }
    approval_path.write_text(
        json.dumps(approval_payload, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="자동추출 artifact 버전"):
        validate_review_batch_approval(
            draft_manifest_path=manifest_path,
            approval_manifest_path=approval_path,
            reviewer="안지홍",
            settings=settings,
            explicit_confirmation=True,
            page_extractor=_page_extractor(case),
        )


async def test_prepare_batch_rejects_duplicate_normalized_target(
    golden_cases,
    tmp_path,
) -> None:
    case = golden_cases["2026000372"]
    settings = Settings(ai_provider="fixture")
    automatic = await _automatic_result(case, settings=settings)
    auto_dir = tmp_path / "auto"
    save_result(automatic, auto_dir / f"{case.complex_id}.json")
    inventory_path = _write_inventory(tmp_path, case)
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    duplicate = dict(inventory["targets"][0])
    duplicate["unit_type_name"] = "59A"
    inventory["targets"].append(duplicate)
    inventory_path.write_text(json.dumps(inventory, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="중복 target"):
        prepare_review_batch(
            inventory_path=inventory_path,
            auto_artifact_dirs=[auto_dir],
            output_dir=tmp_path / "review-work",
            settings=settings,
            page_extractor=_page_extractor(case),
        )
