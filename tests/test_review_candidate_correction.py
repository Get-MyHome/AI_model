from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import synthetic_pages

import get_myhome_ai.review_candidate_correction as correction
from get_myhome_ai.models import (
    AdditionalCost,
    AdditionalCostPayment,
    AdditionalCostType,
    AnalyzeRequest,
    ExceptionFlag,
    HoldReasonCode,
    Installment,
    PaymentStage,
    ReviewStatus,
)
from get_myhome_ai.pdf_text import DownloadedPdf, PdfPage
from get_myhome_ai.pipeline import AnalysisPipeline
from get_myhome_ai.providers.fixture import FixtureExtractor
from get_myhome_ai.review import load_result, save_result
from get_myhome_ai.review_batch import (
    APPROVAL_ATTESTATION,
    ApprovalDecision,
    load_review_approval_manifest,
    load_review_draft_manifest,
    prepare_review_batch,
    validate_review_batch_approval,
)
from get_myhome_ai.settings import Settings

PDF_CONTENT = b"%PDF-audited-correction"


def test_2026000293_uses_explicit_optional_choice_evidence() -> None:
    page, quote = correction._AUDITED_DOCUMENTS["2026000293"].optional_evidence

    assert page == 33
    assert "분양계약자가 선택 계약" in quote
    assert quote != "해당 사항은 별도계약으로 진행됩니다"


async def _automatic_result(case):
    pages = synthetic_pages(case)

    async def loader(_url, _settings):
        return DownloadedPdf(
            content=PDF_CONTENT,
            sha256=hashlib.sha256(PDF_CONTENT).hexdigest(),
        )

    pipeline = AnalysisPipeline(
        settings=Settings(ai_provider="fixture"),
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


async def test_correction_keeps_candidate_pending_and_excludes_unrelated_option(
    golden_cases,
    monkeypatch,
) -> None:
    result, pages = await _automatic_result(golden_cases["2026000372"])
    pages[32] = PdfPage(
        number=33,
        text=(
            pages[32].text
            + "\n발코니 확장비는 공급금액에 포함되어 있지 않습니다."
            + "\n발코니 확장은 계약자의 선택사항입니다."
            + "\n시스템 에어컨 유상옵션"
        ),
    )
    result.additional_costs[0].required = None
    result.additional_costs[0].included_in_sale_price = None
    result.additional_costs.append(
        AdditionalCost(
            type=AdditionalCostType.SYSTEM_AIR_CONDITIONER,
            name="근거 없는 시스템에어컨",
            total_amount_manwon=0,
            required=None,
            included_in_sale_price=None,
            applicable_unit_type="59A",
            payments=[
                AdditionalCostPayment(
                    number=1,
                    stage=PaymentStage.CONTRACT,
                    amount_manwon=100,
                    due_date=None,
                    due_text="계약 시",
                )
            ],
            note=None,
        )
    )
    policy = correction._DocumentPolicy(
        source_sha256=result.meta.source_sha256,
        source_page_count=len(pages),
        targets=frozenset({("01", "59A", result.target_unit.sale_price_manwon)}),
        not_included_evidence=(33, "발코니 확장비는 공급금액에 포함되어 있지 않습니다."),
        optional_evidence=(33, "발코니 확장은 계약자의 선택사항입니다."),
    )
    monkeypatch.setattr(correction, "_AUDITED_DOCUMENTS", {result.complex_id: policy})

    prepared, actions = correction.correct_audited_review_candidate(
        result,
        source_sha256=result.meta.source_sha256,
        pages=pages,
    )

    assert prepared.review_status == ReviewStatus.AUTO_EXTRACTED
    assert prepared.reviewer is None
    assert prepared.reviewed_at is None
    assert prepared.validation.passed is True
    assert len(prepared.additional_costs) == 1
    assert prepared.additional_costs[0].type == AdditionalCostType.BALCONY_EXTENSION
    assert prepared.additional_costs[0].required is False
    assert prepared.additional_costs[0].included_in_sale_price is False
    assert ExceptionFlag.ADDITIONAL_COST_SCOPE_LIMITED in prepared.exception_flags
    scope_hold = next(
        hold
        for hold in prepared.holds
        if hold.reason_code == HoldReasonCode.ADDITIONAL_COST_SCOPE_LIMITED
    )
    assert scope_hold.blocking is False
    assert actions == [
        "GROUND_OPTIONAL_BALCONY_COST",
        "EXCLUDE_NON_BALCONY_OPTIONS:근거 없는 시스템에어컨",
    ]


async def test_correction_rejects_missing_explicit_optional_evidence(
    golden_cases,
    monkeypatch,
) -> None:
    result, pages = await _automatic_result(golden_cases["2026000372"])
    policy = correction._DocumentPolicy(
        source_sha256=result.meta.source_sha256,
        source_page_count=len(pages),
        targets=frozenset({("01", "59A", result.target_unit.sale_price_manwon)}),
        not_included_evidence=(33, "없는 문장"),
        optional_evidence=(33, "없는 선택 문장"),
    )
    monkeypatch.setattr(correction, "_AUDITED_DOCUMENTS", {result.complex_id: policy})

    with pytest.raises(correction.CandidateCorrectionError, match="명시적 선택/미포함"):
        correction.correct_audited_review_candidate(
            result,
            source_sha256=result.meta.source_sha256,
            pages=pages,
        )


async def test_correction_rejects_balcony_cost_for_another_unit(
    golden_cases,
    monkeypatch,
) -> None:
    result, pages = await _automatic_result(golden_cases["2026000372"])
    result.additional_costs[0].applicable_unit_type = "84A"
    policy = correction._DocumentPolicy(
        source_sha256=result.meta.source_sha256,
        source_page_count=len(pages),
        targets=frozenset({("01", "59A", result.target_unit.sale_price_manwon)}),
        not_included_evidence=(33, "발코니 확장비는 공급금액에 포함되어 있지 않습니다."),
        optional_evidence=(33, "발코니 확장은 계약자의 선택사항입니다."),
    )
    monkeypatch.setattr(correction, "_AUDITED_DOCUMENTS", {result.complex_id: policy})

    with pytest.raises(correction.CandidateCorrectionError, match="적용 주택형"):
        correction.correct_audited_review_candidate(
            result,
            source_sha256=result.meta.source_sha256,
            pages=pages,
        )


async def test_sub_manwon_payment_amounts_become_null_with_exact_ratios(golden_cases) -> None:
    result, _pages = await _automatic_result(golden_cases["2026000372"])
    result.target_unit.sale_price_manwon = 100_001
    result.payment_schedule.down_payment.installments = [
        Installment(
            number=1,
            ratio=None,
            amount_manwon=1000,
            due_date=None,
            due_text="계약 시",
        ),
        Installment(
            number=2,
            ratio=None,
            amount_manwon=9001,
            due_date=None,
            due_text="계약 후",
        ),
    ]
    source_values = [
        10_000_000,
        90_001_000,
        *([100_001_000] * 6),
        300_003_000,
    ]
    row = " ".join(f"{value:,}" for value in [1_000_010_000, *source_values])

    actions = correction._repair_sub_manwon_payment_row(
        result,
        [PdfPage(number=1, text=row)],
    )

    assert actions == ["ABSTAIN_SUB_MANWON_PAYMENT_AMOUNTS"]
    assert result.payment_schedule.down_payment.installments[0].amount_manwon == 1000
    assert result.payment_schedule.down_payment.installments[1].amount_manwon is None
    assert result.payment_schedule.down_payment.total_amount_manwon is None
    assert all(
        item.amount_manwon is None for item in result.payment_schedule.interim_payment.installments
    )
    assert all(
        item.ratio == pytest.approx(0.1)
        for item in result.payment_schedule.interim_payment.installments
    )
    assert result.payment_schedule.balance_payment.total_amount_manwon is None
    assert result.payment_schedule.balance_payment.total_ratio == pytest.approx(0.3)


async def test_2026000291_split_contract_sub_manwon_amounts_are_not_rounded(
    golden_cases,
) -> None:
    result, _pages = await _automatic_result(golden_cases["2026000372"])
    result.complex_id = "2026000291"
    result.target_unit.sale_price_manwon = 57_370
    result.payment_schedule.down_payment.installments = [
        Installment(
            number=1,
            ratio=None,
            amount_manwon=500,
            due_date=None,
            due_text="계약 시",
        ),
        Installment(
            number=2,
            ratio=None,
            amount_manwon=2368,
            due_date=None,
            due_text="계약 후",
        ),
    ]
    source_values = [
        5_000_000,
        23_685_000,
        *([57_370_000] * 6),
        200_795_000,
    ]
    row = " ".join(f"{value:,}" for value in [573_700_000, *source_values])

    actions = correction._repair_sub_manwon_payment_row(
        result,
        [PdfPage(number=8, text=row)],
    )

    assert correction._AUDITED_DOCUMENTS["2026000291"].repair_sub_manwon_payment_row
    assert actions == ["ABSTAIN_SUB_MANWON_PAYMENT_AMOUNTS"]
    assert result.payment_schedule.down_payment.installments[0].amount_manwon == 500
    assert result.payment_schedule.down_payment.installments[1].amount_manwon is None
    assert result.payment_schedule.down_payment.total_amount_manwon is None
    assert result.payment_schedule.down_payment.installments[1].ratio == pytest.approx(
        23_685_000 / 573_700_000
    )
    assert all(
        item.amount_manwon == 5737
        for item in result.payment_schedule.interim_payment.installments
    )
    assert result.payment_schedule.balance_payment.total_amount_manwon is None


def _write_inventory(tmp_path: Path, case, pages: list[PdfPage]) -> Path:
    pdf_path = tmp_path / "source" / "announcement.pdf"
    pdf_path.parent.mkdir()
    pdf_path.write_bytes(PDF_CONTENT)
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(
        json.dumps(
            {
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
                        "source_page_count": len(pages),
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return inventory_path


async def _review_workspace(golden_cases, tmp_path, monkeypatch):
    case = golden_cases["2026000372"]
    settings = Settings(ai_provider="fixture")
    automatic, pages = await _automatic_result(case)
    pages[32] = PdfPage(
        number=33,
        text=(
            pages[32].text
            + "\n발코니 확장비는 공급금액에 포함되어 있지 않습니다."
            + "\n발코니 확장은 계약자의 선택사항입니다."
            + "\n시스템에어컨 유상옵션"
        ),
    )
    policy = correction._DocumentPolicy(
        source_sha256=automatic.meta.source_sha256,
        source_page_count=len(pages),
        targets=frozenset({("01", "59A", case.sale_price_manwon)}),
        not_included_evidence=(33, "발코니 확장비는 공급금액에 포함되어 있지 않습니다."),
        optional_evidence=(33, "발코니 확장은 계약자의 선택사항입니다."),
    )
    monkeypatch.setattr(correction, "_AUDITED_DOCUMENTS", {case.complex_id: policy})
    monkeypatch.setattr(correction, "AUDITED_CANDIDATE_COUNT", 1)
    auto_dir = tmp_path / "auto"
    save_result(automatic, auto_dir / f"{case.complex_id}.json")
    source_workspace = tmp_path / "source-review-workspace"

    def page_extractor(_content, _settings):
        return pages

    prepare_review_batch(
        inventory_path=_write_inventory(tmp_path, case, pages),
        auto_artifact_dirs=[auto_dir],
        output_dir=source_workspace,
        settings=settings,
        page_extractor=page_extractor,
    )
    return settings, pages, source_workspace, page_extractor


async def test_corrected_workspace_is_directly_consumable_by_approval_validator(
    golden_cases,
    monkeypatch,
    tmp_path,
) -> None:
    settings, _pages, source_workspace, page_extractor = await _review_workspace(
        golden_cases, tmp_path, monkeypatch
    )
    source_manifest_path = source_workspace / "review-draft-manifest.json"
    output_workspace = tmp_path / "nested" / "corrected-review-workspace"

    payload = correction.prepare_audited_review_candidates(
        draft_manifest_path=source_manifest_path,
        output_dir=output_workspace,
        settings=settings,
        page_extractor=page_extractor,
    )

    output_manifest_path = output_workspace / "review-draft-manifest.json"
    assert output_manifest_path.read_bytes() == source_manifest_path.read_bytes()
    manifest = load_review_draft_manifest(output_manifest_path)
    assert payload["candidate_count"] == 1
    assert payload["workspace_draft_count"] == len(manifest.drafts) == 1
    entry = manifest.drafts[0]
    corrected = load_result(output_workspace / entry.draft_path)
    assert corrected.review_status == ReviewStatus.AUTO_EXTRACTED
    assert corrected.reviewer is None
    assert corrected.reviewed_at is None
    assert (output_workspace / entry.checklist_path).is_file()

    template_path = output_workspace / "review-approval-manifest.template.json"
    template = load_review_approval_manifest(template_path)
    assert template.reviewer is None
    assert template.reviewed_at is None
    assert template.attestation is None
    assert all(item.decision == ApprovalDecision.PENDING for item in template.items)
    assert template.items[0].draft_sha256 == hashlib.sha256(
        (output_workspace / entry.draft_path).read_bytes()
    ).hexdigest()

    approval_payload = json.loads(template_path.read_text(encoding="utf-8"))
    approval_payload["reviewer"] = "안지홍"
    approval_payload["reviewed_at"] = datetime.now(UTC).isoformat()
    approval_payload["attestation"] = APPROVAL_ATTESTATION
    approval_payload["items"][0]["decision"] = "APPROVE"
    approval_payload["items"][0]["checks"] = {
        "source_pdf_visual_reviewed": True,
        "target_unit_and_sale_price_reviewed": True,
        "payment_and_loan_terms_reviewed": True,
        "evidence_pages_and_quotes_reviewed": True,
        "additional_cost_scope_reviewed": True,
    }
    approval_path = tmp_path / "approval.json"
    approval_path.write_text(
        json.dumps(approval_payload, ensure_ascii=False),
        encoding="utf-8",
    )

    validated = validate_review_batch_approval(
        draft_manifest_path=output_manifest_path,
        approval_manifest_path=approval_path,
        reviewer="안지홍",
        settings=settings,
        explicit_confirmation=True,
        page_extractor=page_extractor,
    )
    assert len(validated) == 1


async def test_workspace_rejects_stale_batch_before_creating_output(
    golden_cases,
    monkeypatch,
    tmp_path,
) -> None:
    settings, _pages, source_workspace, page_extractor = await _review_workspace(
        golden_cases, tmp_path, monkeypatch
    )
    stale = settings.model_copy(update={"extractor_version": "0.2.0"})
    output = tmp_path / "must-not-exist"
    with pytest.raises(correction.CandidateCorrectionError, match="현재 서비스 버전"):
        correction.prepare_audited_review_candidates(
            draft_manifest_path=source_workspace / "review-draft-manifest.json",
            output_dir=output,
            settings=stale,
            page_extractor=page_extractor,
        )
    assert not output.exists()


async def test_workspace_rejects_tampered_candidate_atomically(
    golden_cases,
    monkeypatch,
    tmp_path,
) -> None:
    settings, _pages, source_workspace, page_extractor = await _review_workspace(
        golden_cases, tmp_path, monkeypatch
    )
    manifest = load_review_draft_manifest(source_workspace / "review-draft-manifest.json")
    draft = source_workspace / manifest.drafts[0].draft_path
    draft.write_text(draft.read_text(encoding="utf-8") + " ", encoding="utf-8")
    output = tmp_path / "must-not-exist"
    with pytest.raises(correction.CandidateCorrectionError, match="입력 초안 SHA-256"):
        correction.prepare_audited_review_candidates(
            draft_manifest_path=source_workspace / "review-draft-manifest.json",
            output_dir=output,
            settings=settings,
            page_extractor=page_extractor,
        )
    assert not output.exists()


async def test_workspace_rejects_stale_candidate_entry_atomically(
    golden_cases,
    monkeypatch,
    tmp_path,
) -> None:
    settings, _pages, source_workspace, page_extractor = await _review_workspace(
        golden_cases, tmp_path, monkeypatch
    )
    manifest_path = source_workspace / "review-draft-manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["drafts"][0]["extractor_version"] = "0.2.0"
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "must-not-exist"

    with pytest.raises(correction.CandidateCorrectionError, match="현재 스키마·추출기"):
        correction.prepare_audited_review_candidates(
            draft_manifest_path=manifest_path,
            output_dir=output,
            settings=settings,
            page_extractor=page_extractor,
        )
    assert not output.exists()


@pytest.mark.parametrize(
    "mutation, expected",
    [("missing", "정확히 존재"), ("duplicate", "중복 target")],
)
async def test_workspace_rejects_missing_or_duplicate_audited_target(
    golden_cases,
    monkeypatch,
    tmp_path,
    mutation,
    expected,
) -> None:
    settings, _pages, source_workspace, page_extractor = await _review_workspace(
        golden_cases, tmp_path, monkeypatch
    )
    manifest_path = source_workspace / "review-draft-manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "missing":
        payload["drafts"] = []
    else:
        duplicate = dict(payload["drafts"][0])
        duplicate["draft_id"] = "0" * 64
        payload["drafts"].append(duplicate)
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "must-not-exist"

    with pytest.raises(correction.CandidateCorrectionError, match=expected):
        correction.prepare_audited_review_candidates(
            draft_manifest_path=manifest_path,
            output_dir=output,
            settings=settings,
            page_extractor=page_extractor,
        )
    assert not output.exists()
