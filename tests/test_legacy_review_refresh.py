from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import synthetic_pages

import get_myhome_ai.legacy_review_refresh as refresh
from get_myhome_ai.models import AnalyzeRequest, ReviewStatus
from get_myhome_ai.pdf_text import DownloadedPdf
from get_myhome_ai.pipeline import AnalysisPipeline
from get_myhome_ai.providers.fixture import FixtureExtractor
from get_myhome_ai.review import load_result, save_result
from get_myhome_ai.review_batch import (
    ApprovalDecision,
    load_review_approval_manifest,
    load_review_draft_manifest,
    prepare_review_batch,
)
from get_myhome_ai.settings import Settings

PDF_CONTENT = b"%PDF-legacy-refresh"


async def _automatic_result(case, settings: Settings):
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
    result = await pipeline.analyze_url(
        AnalyzeRequest(
            complex_id=case.complex_id,
            pdf_url="https://example.com/announcement.pdf",
            unit_type_id="01",
            unit_type_name="059.9883A",
            sale_price_manwon=case.sale_price_manwon,
        )
    )
    return result, pages


def _write_inventory(tmp_path: Path, case, pages) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "announcement.pdf").write_bytes(PDF_CONTENT)
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "schema_version": "owned_corpus_inventory_v1",
                "source_directory": str(source),
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
                        "source_page_count": len(pages),
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return inventory


async def _workspace(golden_cases, tmp_path, monkeypatch):
    case = golden_cases["2026000372"]
    settings = Settings(ai_provider="fixture")
    current, pages = await _automatic_result(case, settings)
    current.meta.model = "CURRENT_MODEL"
    auto_dir = tmp_path / "auto"
    save_result(current, auto_dir / "current.json")

    def page_extractor(_content, _settings):
        return pages

    current_workspace = tmp_path / "current-workspace"
    prepare_review_batch(
        inventory_path=_write_inventory(tmp_path, case, pages),
        auto_artifact_dirs=[auto_dir],
        output_dir=current_workspace,
        settings=settings,
        page_extractor=page_extractor,
    )

    legacy = current.model_copy(deep=True)
    legacy.meta.extractor_version = "0.2.0"
    legacy.meta.model = "LEGACY_MODEL"
    legacy.review_status = ReviewStatus.REVIEWED
    legacy.reviewer = "OLD_REVIEWER"
    legacy.reviewed_at = datetime(2026, 9, 3, tzinfo=UTC)
    legacy.additional_costs[0].name = "audited legacy balcony"
    legacy_dir = tmp_path / "legacy-workspace" / "drafts"
    legacy_path = tmp_path / "legacy-reviewed.json"
    save_result(legacy, legacy_path)

    target = (
        legacy.complex_id,
        legacy.target_unit.unit_type_id,
        legacy.target_unit.unit_type_name,
        legacy.target_unit.sale_price_manwon,
    )
    assert all(value is not None for value in target)
    policy = refresh._LegacyRefreshPolicy(
        source_sha256=legacy.meta.source_sha256,
        source_page_count=legacy.meta.source_page_count,
        target=target,
        legacy_artifact_sha256=hashlib.sha256(legacy_path.read_bytes()).hexdigest(),
        legacy_review_status=ReviewStatus.REVIEWED,
        balcony_total_manwon=legacy.additional_costs[0].total_amount_manwon,
        balcony_payments_manwon=tuple(
            item.amount_manwon for item in legacy.additional_costs[0].payments
        ),
    )
    legacy_dir.mkdir(parents=True)
    monkeypatch.setattr(refresh, "_LEGACY_REFRESH_POLICIES", {target: policy})
    monkeypatch.setattr(refresh, "LEGACY_REFRESH_COUNT", 1)
    return (
        settings,
        pages,
        current_workspace,
        legacy_path,
        page_extractor,
        target,
    )


async def test_refresh_uses_current_envelope_and_keeps_everything_pending(
    golden_cases,
    tmp_path,
    monkeypatch,
) -> None:
    (
        settings,
        _pages,
        current_workspace,
        legacy_path,
        page_extractor,
        target,
    ) = await _workspace(golden_cases, tmp_path, monkeypatch)
    output = tmp_path / "refreshed"

    payload = refresh.prepare_legacy_review_refresh(
        draft_manifest_path=current_workspace / "review-draft-manifest.json",
        legacy_workspace_dir=tmp_path / "legacy-workspace",
        historical_reviewed_artifact=legacy_path,
        output_dir=output,
        settings=settings,
        page_extractor=page_extractor,
    )

    manifest = load_review_draft_manifest(output / "review-draft-manifest.json")
    assert payload["workspace_draft_count"] == 1
    assert payload["refreshed_candidate_count"] == 1
    entry = manifest.drafts[0]
    result = load_result(output / entry.draft_path)
    assert result.meta.extractor_version == settings.extractor_version
    assert result.meta.extractor_version != "0.2.0"
    assert result.meta.model == "CURRENT_MODEL"
    assert result.additional_costs[0].name == "audited legacy balcony"
    assert result.review_status == ReviewStatus.AUTO_EXTRACTED
    assert result.reviewer is None
    assert result.reviewed_at is None
    assert result.validation.passed is True

    wrong_unit = result.model_copy(deep=True)
    wrong_unit.additional_costs[0].applicable_unit_type = "84A"
    with pytest.raises(refresh.LegacyRefreshError, match="발코니 선택비용"):
        refresh._validate_refreshed_cost(
            wrong_unit, policy=refresh._LEGACY_REFRESH_POLICIES[target]
        )

    template = load_review_approval_manifest(
        output / "review-approval-manifest.template.json"
    )
    assert template.reviewer is None
    assert template.reviewed_at is None
    assert template.attestation is None
    assert all(item.decision == ApprovalDecision.PENDING for item in template.items)
    assert template.items[0].draft_sha256 == hashlib.sha256(
        (output / entry.draft_path).read_bytes()
    ).hexdigest()
    record = payload["candidates"][0]
    assert record["legacy_review_status"] == "REVIEWED"
    assert record["legacy_reviewer_carried_forward"] is False
    assert record["canonical_revalidation_idempotent"] is True


async def test_refresh_rejects_tampered_legacy_artifact_atomically(
    golden_cases,
    tmp_path,
    monkeypatch,
) -> None:
    (
        settings,
        _pages,
        current_workspace,
        legacy_path,
        page_extractor,
        _target,
    ) = await _workspace(golden_cases, tmp_path, monkeypatch)
    legacy_path.write_text(
        legacy_path.read_text(encoding="utf-8") + " ",
        encoding="utf-8",
    )
    output = tmp_path / "must-not-exist"

    with pytest.raises(refresh.LegacyRefreshError, match="SHA-256"):
        refresh.prepare_legacy_review_refresh(
            draft_manifest_path=current_workspace / "review-draft-manifest.json",
            legacy_workspace_dir=tmp_path / "legacy-workspace",
            historical_reviewed_artifact=legacy_path,
            output_dir=output,
            settings=settings,
            page_extractor=page_extractor,
        )
    assert not output.exists()


async def test_refresh_rejects_non_current_workspace_before_output(
    golden_cases,
    tmp_path,
    monkeypatch,
) -> None:
    (
        settings,
        _pages,
        current_workspace,
        legacy_path,
        page_extractor,
        _target,
    ) = await _workspace(golden_cases, tmp_path, monkeypatch)
    manifest_path = current_workspace / "review-draft-manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["expected_extractor_version"] = "0.2.0"
    manifest_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "must-not-exist"

    with pytest.raises(refresh.LegacyRefreshError, match="현재 extractor"):
        refresh.prepare_legacy_review_refresh(
            draft_manifest_path=manifest_path,
            legacy_workspace_dir=tmp_path / "legacy-workspace",
            historical_reviewed_artifact=legacy_path,
            output_dir=output,
            settings=settings,
            page_extractor=page_extractor,
        )
    assert not output.exists()


async def test_refresh_rejects_source_template_hash_mismatch_atomically(
    golden_cases,
    tmp_path,
    monkeypatch,
) -> None:
    (
        settings,
        _pages,
        current_workspace,
        legacy_path,
        page_extractor,
        _target,
    ) = await _workspace(golden_cases, tmp_path, monkeypatch)
    template_path = current_workspace / "review-approval-manifest.template.json"
    raw = json.loads(template_path.read_text(encoding="utf-8"))
    raw["items"][0]["draft_sha256"] = "0" * 64
    template_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "must-not-exist"

    with pytest.raises(refresh.LegacyRefreshError, match="template SHA-256"):
        refresh.prepare_legacy_review_refresh(
            draft_manifest_path=current_workspace / "review-draft-manifest.json",
            legacy_workspace_dir=tmp_path / "legacy-workspace",
            historical_reviewed_artifact=legacy_path,
            output_dir=output,
            settings=settings,
            page_extractor=page_extractor,
        )
    assert not output.exists()
