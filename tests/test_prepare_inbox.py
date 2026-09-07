from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest
from conftest import synthetic_pages
from test_captured_inventory import _capture

from get_myhome_ai import prepare_inbox as module
from get_myhome_ai.models import AnalyzeRequest
from get_myhome_ai.pdf_text import DownloadedPdf
from get_myhome_ai.review import load_result, save_result
from get_myhome_ai.review_batch import prepare_review_batch
from get_myhome_ai.review_capture import capture_review_result, capture_review_source
from get_myhome_ai.settings import Settings


@pytest.fixture
def inbox(golden_cases, tmp_path, monkeypatch):
    capture_dir, source_sha = _capture(golden_cases, tmp_path)
    settings = Settings(
        ai_provider="fixture",
        extractor_version="0.2.0",
        reviewed_artifact_dir=tmp_path / "reviewed",
    )
    monkeypatch.setattr(
        module,
        "prepare_review_batch",
        lambda **kwargs: prepare_review_batch(
            **kwargs,
            page_extractor=lambda *_: synthetic_pages(golden_cases["2026000358"], 58),
        ),
    )
    return capture_dir, tmp_path / "prepared", settings, source_sha


def run(inbox):
    capture, output, settings, _ = inbox
    return module.prepare_inbox(capture_dir=capture, output_dir=output, settings=settings)


def test_prepares_pending_draft_without_overwriting_edits(inbox):
    first = run(inbox)
    assert first["counts"] == {"DRAFT_PREPARED": 1}
    assert first["human_approval_automatic"] is False
    draft = next(inbox[1].glob("jobs/*/drafts/*.json"))
    result = load_result(draft)
    assert result.review_status != "REVIEWED"
    assert result.reviewer is None
    result.analysis_summary = "원문 대조 중인 수정본"
    save_result(result, draft)
    edited_bytes = draft.read_bytes()
    second = run(inbox)
    assert first["targets"] == second["targets"]
    assert draft.read_bytes() == edited_bytes
    assert not inbox[2].reviewed_artifact_dir.exists()


def test_bad_capture_does_not_block_other_target(inbox):
    bad_key = "a" * 64
    (inbox[0] / "requests" / f"{bad_key}.json").write_text("not-json")
    (inbox[0] / "auto" / f"{bad_key}__{'b' * 64}.json").write_text("not-json")
    assert run(inbox)["counts"] == {
        "DRAFT_PREPARED": 1,
        "INVALID_CAPTURE_OR_PREPARATION_FAILED": 1,
    }


def test_pending_auto_is_retried_when_result_arrives(inbox):
    auto = next((inbox[0] / "auto").glob("*.json"))
    parked = auto.with_suffix(".pending")
    auto.rename(parked)
    assert run(inbox)["counts"] == {"WAITING_FOR_ANALYSIS": 1}
    parked.rename(auto)
    assert run(inbox)["counts"] == {"DRAFT_PREPARED": 1}


def test_changed_pdf_for_same_unit_gets_separate_job(inbox):
    original = load_result(next((inbox[0] / "auto").glob("*.json")))
    source = b"%PDF-changed-document"
    source_sha = hashlib.sha256(source).hexdigest()
    target = original.target_unit
    key = capture_review_source(
        inbox[0],
        AnalyzeRequest(
            complex_id=original.complex_id,
            pdf_url="https://example.com/revision.pdf?private-signature=SECRET",
            **target.model_dump(),
        ),
        DownloadedPdf(content=source, sha256=source_sha),
    )
    original.meta.source_sha256 = source_sha
    capture_review_result(inbox[0], key, original)
    report = run(inbox)
    assert report["counts"] == {"DRAFT_PREPARED": 2}
    assert len(list(inbox[1].glob("jobs/*/review-draft-manifest.json"))) == 2
    assert "SECRET" not in json.dumps(report)


def test_exact_reviewed_target_is_not_prepared_again(inbox):
    original = load_result(next((inbox[0] / "auto").glob("*.json")))
    original.review_status = "REVIEWED"
    original.reviewer = "test-only-reviewer"
    original.reviewed_at = datetime.now(UTC)
    save_result(original, inbox[2].reviewed_artifact_dir / "fixture.json")
    assert run(inbox)["counts"] == {"REVIEWED_AVAILABLE": 1}
    assert not (inbox[1] / "jobs").exists()


def test_each_request_uses_only_its_own_hash_verified_automatic_result(inbox):
    original = load_result(next((inbox[0] / "auto").glob("*.json")))
    content = (inbox[0] / "sources" / f"{inbox[3]}.pdf").read_bytes()
    target = original.target_unit.model_dump()
    target["unit_type_name"] += " "
    key = capture_review_source(
        inbox[0],
        AnalyzeRequest(
            complex_id=original.complex_id,
            pdf_url="https://example.com/alias.pdf",
            **target,
        ),
        DownloadedPdf(content=content, sha256=inbox[3]),
    )
    original.meta.analyzed_at = datetime.now(UTC)
    capture_review_result(inbox[0], key, original)
    report = run(inbox)
    assert report["counts"] == {"DRAFT_PREPARED": 2}
    for row in report["targets"]:
        manifest = module.load_review_draft_manifest(module.Path(row["draft_manifest"]))
        source = module.Path(manifest.drafts[0].source_auto_artifact_path)
        assert source.name.startswith(row["request_key"] + "__")
        assert source.is_relative_to(inbox[1] / "inputs")


def test_tampered_source_is_rejected_even_after_preparation(inbox):
    run(inbox)
    (inbox[0] / "sources" / f"{inbox[3]}.pdf").write_bytes(b"tampered")
    assert run(inbox)["counts"] == {"INVALID_CAPTURE_OR_PREPARATION_FAILED": 1}


def test_broken_inventory_is_isolated_and_missing_draft_is_not_ready(inbox):
    run(inbox)
    inventory = next(inbox[1].glob("inputs/*.json"))
    original = inventory.read_bytes()
    inventory.write_text("{}")
    assert run(inbox)["counts"] == {"INVALID_CAPTURE_OR_PREPARATION_FAILED": 1}
    inventory.write_bytes(original)
    draft = next(inbox[1].glob("jobs/*/drafts/*.json"))
    draft.rename(draft.with_suffix(".pending"))
    assert run(inbox)["counts"] == {"PREPARATION_BLOCKED": 1}


def test_url_in_capture_fails_without_leaking_it(inbox):
    path = next((inbox[0] / "requests").glob("*.json"))
    payload = json.loads(path.read_text())
    payload["pdf_url"] = "https://example.com/?signature=SECRET"
    path.write_text(json.dumps(payload))
    report = run(inbox)
    assert report["counts"] == {"INVALID_CAPTURE_OR_PREPARATION_FAILED": 1}
    assert "SECRET" not in (inbox[1] / "status.json").read_text()


def test_old_extractor_does_not_become_approved(inbox):
    inbox[2].extractor_version = "new-extractor"
    assert run(inbox)["counts"] == {"VERSION_BLOCKED": 1}


def test_rejects_output_inside_capture_or_reviewed_registry(inbox):
    for output in (inbox[0], inbox[0] / "prepared", inbox[2].reviewed_artifact_dir):
        with pytest.raises(ValueError):
            module.prepare_inbox(capture_dir=inbox[0], output_dir=output, settings=inbox[2])
