from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from get_myhome_ai.errors import AnalysisError
from get_myhome_ai.models import (
    AnalysisResponse,
    ReviewStatus,
    StrictModel,
    TargetUnit,
)
from get_myhome_ai.normalization import normalize_unit_type_name
from get_myhome_ai.pdf_text import (
    DownloadedPdf,
    PdfPage,
    extract_pdf_pages,
    load_pdf_from_path,
)
from get_myhome_ai.review import (
    approve_result,
    load_result,
    prepare_review_draft,
    save_result,
    write_review_sheet,
)
from get_myhome_ai.settings import Settings

INVENTORY_SCHEMA_VERSION = "owned_corpus_inventory_v1"
DRAFT_MANIFEST_SCHEMA_VERSION = "review_draft_batch_v1"
APPROVAL_MANIFEST_SCHEMA_VERSION = "review_approval_manifest_v2"
APPROVAL_RECEIPT_SCHEMA_VERSION = "review_approval_receipt_v2"
APPROVAL_ATTESTATION = (
    "I_REVIEWED_EACH_APPROVED_DRAFT_AGAINST_THE_EXACT_SOURCE_PDF"
)

SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NON_EMPTY = Annotated[str, Field(min_length=1, max_length=500)]
PdfLoader = Callable[[str, Settings], DownloadedPdf]
PageExtractor = Callable[[bytes, Settings], list[PdfPage]]


class ApprovalDecision(StrEnum):
    PENDING = "PENDING"
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class ReviewTargetIdentity(StrictModel):
    complex_id: Annotated[str, Field(min_length=1, max_length=100)]
    unit_type_id: Annotated[str, Field(min_length=1, max_length=100)]
    unit_type_name: Annotated[str, Field(min_length=1, max_length=100)]
    normalized_unit_type_name: Annotated[str, Field(min_length=1, max_length=100)]
    sale_price_manwon: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def normalized_name_matches_source(self) -> ReviewTargetIdentity:
        if normalize_unit_type_name(self.unit_type_name) != self.normalized_unit_type_name:
            raise ValueError("normalized_unit_type_name이 unit_type_name과 일치하지 않습니다.")
        return self


class ReviewDraftEntry(StrictModel):
    draft_id: SHA256
    artifact_type: Literal["REVIEW_DRAFT"] = "REVIEW_DRAFT"
    approval_state: Literal["PENDING"] = "PENDING"
    target: ReviewTargetIdentity
    source_sha256: SHA256
    source_page_count: Annotated[int, Field(ge=1)]
    source_pdf_path: NON_EMPTY
    source_auto_artifact_path: NON_EMPTY
    source_auto_artifact_sha256: SHA256
    draft_path: NON_EMPTY
    draft_sha256: SHA256
    checklist_path: NON_EMPTY
    supporting_reference_path: str | None
    supporting_reference_scope: Literal["DOCUMENT_LEVEL_CORE_ONLY"] | None
    schema_version: NON_EMPTY
    extractor_version: NON_EMPTY
    initial_review_status: Literal["AUTO_EXTRACTED", "NEEDS_REVIEW"]
    initial_validation_passed: bool
    approval_blockers: list[NON_EMPTY]
    preparation_warnings: list[NON_EMPTY]


class UnavailableReviewTarget(StrictModel):
    target: ReviewTargetIdentity
    reason_code: NON_EMPTY
    message: NON_EMPTY


class ReviewDraftSummary(StrictModel):
    target_count: Annotated[int, Field(ge=0)]
    pdf_backed_target_count: Annotated[int, Field(ge=0)]
    draft_count: Annotated[int, Field(ge=0)]
    approval_eligible_draft_count: Annotated[int, Field(ge=0)]
    blocked_draft_count: Annotated[int, Field(ge=0)]
    unavailable_target_count: Annotated[int, Field(ge=0)]


class ReviewDraftBatchManifest(StrictModel):
    schema_version: Literal["review_draft_batch_v1"] = DRAFT_MANIFEST_SCHEMA_VERSION
    artifact_type: Literal["REVIEW_DRAFT_BATCH"] = "REVIEW_DRAFT_BATCH"
    batch_id: SHA256
    created_at: datetime
    expected_schema_version: NON_EMPTY
    expected_extractor_version: NON_EMPTY
    source_inventory_path: NON_EMPTY
    source_inventory_sha256: SHA256
    summary: ReviewDraftSummary
    drafts: list[ReviewDraftEntry]
    unavailable_targets: list[UnavailableReviewTarget]

    @model_validator(mode="after")
    def draft_ids_are_unique(self) -> ReviewDraftBatchManifest:
        identifiers = [item.draft_id for item in self.drafts]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("draft_id must be unique")
        return self


class ReviewApprovalChecks(StrictModel):
    source_pdf_visual_reviewed: bool = False
    target_unit_and_sale_price_reviewed: bool = False
    payment_and_loan_terms_reviewed: bool = False
    evidence_pages_and_quotes_reviewed: bool = False
    additional_cost_scope_reviewed: bool = False

    def all_confirmed(self) -> bool:
        return all(self.model_dump().values())


class ReviewApprovalItem(StrictModel):
    draft_id: SHA256
    decision: ApprovalDecision = ApprovalDecision.PENDING
    draft_sha256: SHA256 | None = None
    checks: ReviewApprovalChecks = Field(default_factory=ReviewApprovalChecks)
    notes: Annotated[str | None, Field(max_length=2000)] = None

    @model_validator(mode="after")
    def approved_item_is_fully_attested(self) -> ReviewApprovalItem:
        if self.decision == ApprovalDecision.APPROVE:
            if self.draft_sha256 is None:
                raise ValueError("APPROVE item requires draft_sha256")
            if not self.checks.all_confirmed():
                raise ValueError("APPROVE item requires every review check")
        return self


class ReviewApprovalManifest(StrictModel):
    schema_version: Literal["review_approval_manifest_v2"] = (
        APPROVAL_MANIFEST_SCHEMA_VERSION
    )
    artifact_type: Literal["REVIEW_APPROVAL_MANIFEST"] = "REVIEW_APPROVAL_MANIFEST"
    draft_batch_id: SHA256
    draft_manifest_sha256: SHA256
    reviewer: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    reviewed_at: datetime | None = None
    attestation: Literal[
        "I_REVIEWED_EACH_APPROVED_DRAFT_AGAINST_THE_EXACT_SOURCE_PDF"
    ] | None = None
    items: list[ReviewApprovalItem]

    @model_validator(mode="after")
    def approved_items_require_reviewer_attestation(self) -> ReviewApprovalManifest:
        identifiers = [item.draft_id for item in self.items]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("approval manifest draft_id must be unique")
        if any(item.decision == ApprovalDecision.APPROVE for item in self.items):
            if self.reviewer is None or not self.reviewer.strip():
                raise ValueError("APPROVE requires a reviewer")
            if self.reviewed_at is None or self.reviewed_at.utcoffset() is None:
                raise ValueError("APPROVE requires a timezone-aware reviewed_at")
            if self.attestation != APPROVAL_ATTESTATION:
                raise ValueError("APPROVE requires the exact review attestation")
        return self


class ReviewApprovalReceiptItem(StrictModel):
    draft_id: SHA256
    target: ReviewTargetIdentity
    source_sha256: SHA256
    source_page_count: Annotated[int, Field(ge=1)]
    approved_draft_sha256: SHA256
    draft_was_edited: bool
    reviewed_artifact_path: NON_EMPTY
    reviewed_artifact_sha256: SHA256


class ReviewApprovalReceipt(StrictModel):
    schema_version: Literal["review_approval_receipt_v2"] = (
        APPROVAL_RECEIPT_SCHEMA_VERSION
    )
    artifact_type: Literal["REVIEW_APPROVAL_RECEIPT"] = "REVIEW_APPROVAL_RECEIPT"
    draft_batch_id: SHA256
    draft_manifest_sha256: SHA256
    reviewer: NON_EMPTY
    executed_at: datetime
    approval_manifest_path: NON_EMPTY
    approval_manifest_sha256: SHA256
    approved_count: Annotated[int, Field(ge=1)]
    items: list[ReviewApprovalReceiptItem]


@dataclass(frozen=True)
class _InventoryTarget:
    identity: ReviewTargetIdentity
    pdf_available: bool
    pdf_path: Path | None
    source_sha256: str | None
    source_page_count: int | None


@dataclass(frozen=True)
class _AutoCandidate:
    path: Path
    artifact_sha256: str
    result: AnalysisResponse


@dataclass(frozen=True)
class _ValidatedApproval:
    entry: ReviewDraftEntry
    approved_draft_sha256: str
    approved: AnalysisResponse
    destination_name: str


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_model(model: StrictModel, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = model.model_dump_json(indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        delete=False,
    ) as temporary:
        temporary.write(payload)
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, destination)


def _load_json_object(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"JSON을 읽을 수 없습니다: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON 최상위는 객체여야 합니다: {path}")
    return value


def _required_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}는 빈 문자열이 아닌 값이어야 합니다.")
    return value


def _target_identity(raw: Mapping[str, Any]) -> ReviewTargetIdentity:
    complex_id = _required_string(raw.get("complex_id"), field="complex_id")
    unit_type_id = _required_string(raw.get("unit_type_id"), field="unit_type_id")
    unit_type_name = _required_string(raw.get("unit_type_name"), field="unit_type_name")
    sale_price = raw.get("sale_price_manwon")
    if isinstance(sale_price, bool) or not isinstance(sale_price, int) or sale_price < 0:
        raise ValueError("sale_price_manwon은 0 이상 정수여야 합니다.")
    normalized = normalize_unit_type_name(unit_type_name)
    if normalized is None:
        raise ValueError("unit_type_name을 정규화할 수 없습니다.")
    return ReviewTargetIdentity(
        complex_id=complex_id,
        unit_type_id=unit_type_id,
        unit_type_name=unit_type_name,
        normalized_unit_type_name=normalized,
        sale_price_manwon=sale_price,
    )


def _load_inventory(path: Path) -> tuple[list[_InventoryTarget], Path]:
    payload = _load_json_object(path)
    if payload.get("schema_version") != INVENTORY_SCHEMA_VERSION:
        raise ValueError(
            f"인벤토리 schema_version은 {INVENTORY_SCHEMA_VERSION}이어야 합니다."
        )
    source_directory = Path(
        _required_string(payload.get("source_directory"), field="source_directory")
    )
    if not source_directory.is_absolute():
        source_directory = path.parent / source_directory
    source_directory = source_directory.resolve()
    raw_targets = payload.get("targets")
    if not isinstance(raw_targets, list):
        raise ValueError("인벤토리 targets는 목록이어야 합니다.")

    targets: list[_InventoryTarget] = []
    seen: set[tuple[str, str, str, int]] = set()
    for index, raw in enumerate(raw_targets):
        if not isinstance(raw, dict):
            raise ValueError(f"targets[{index}]는 객체여야 합니다.")
        identity = _target_identity(raw)
        key = (
            identity.complex_id,
            identity.unit_type_id,
            identity.normalized_unit_type_name,
            identity.sale_price_manwon,
        )
        if key in seen:
            raise ValueError(f"인벤토리에 중복 target이 있습니다: {key!r}")
        seen.add(key)

        pdf_available = raw.get("pdf_available")
        if not isinstance(pdf_available, bool):
            raise ValueError(f"targets[{index}].pdf_available은 bool이어야 합니다.")
        if not pdf_available:
            targets.append(
                _InventoryTarget(
                    identity=identity,
                    pdf_available=False,
                    pdf_path=None,
                    source_sha256=None,
                    source_page_count=None,
                )
            )
            continue

        raw_pdf_path = _required_string(raw.get("pdf_path"), field=f"targets[{index}].pdf_path")
        relative_pdf_path = Path(raw_pdf_path)
        if relative_pdf_path.is_absolute():
            raise ValueError(f"targets[{index}].pdf_path는 상대경로여야 합니다.")
        pdf_path = (source_directory / relative_pdf_path).resolve()
        if not pdf_path.is_relative_to(source_directory):
            raise ValueError(f"targets[{index}].pdf_path가 source_directory를 벗어납니다.")
        source_sha256 = _required_string(
            raw.get("source_sha256"), field=f"targets[{index}].source_sha256"
        )
        if re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None:
            raise ValueError(f"targets[{index}].source_sha256가 올바르지 않습니다.")
        source_page_count = raw.get("source_page_count")
        if (
            isinstance(source_page_count, bool)
            or not isinstance(source_page_count, int)
            or source_page_count < 1
        ):
            raise ValueError(f"targets[{index}].source_page_count가 올바르지 않습니다.")
        targets.append(
            _InventoryTarget(
                identity=identity,
                pdf_available=True,
                pdf_path=pdf_path,
                source_sha256=source_sha256,
                source_page_count=source_page_count,
            )
        )
    return targets, source_directory


def _load_auto_candidates(directories: Sequence[Path]) -> dict[str, list[_AutoCandidate]]:
    candidates: dict[str, list[_AutoCandidate]] = {}
    seen_paths: set[Path] = set()
    for directory in directories:
        for path in sorted(directory.glob("*.json")):
            resolved = path.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            try:
                result = load_result(resolved)
            except (OSError, ValueError):
                continue
            if result.review_status == ReviewStatus.REVIEWED:
                continue
            candidates.setdefault(result.complex_id, []).append(
                _AutoCandidate(
                    path=resolved,
                    artifact_sha256=_sha256_file(resolved),
                    result=result,
                )
            )
    return candidates


def _candidate_specificity(
    result: AnalysisResponse,
    target: ReviewTargetIdentity,
) -> int | None:
    unit = result.target_unit
    if unit.unit_type_id is None and unit.unit_type_name is None and unit.sale_price_manwon is None:
        return 0
    if (
        unit.unit_type_id in {None, target.unit_type_id}
        and unit.unit_type_name == target.normalized_unit_type_name
        and unit.sale_price_manwon == target.sale_price_manwon
    ):
        return 2 if unit.unit_type_id == target.unit_type_id else 1
    return None


def _select_auto_candidate(
    candidates: Mapping[str, list[_AutoCandidate]],
    target: _InventoryTarget,
    *,
    expected_schema_version: str,
    expected_extractor_version: str,
) -> tuple[_AutoCandidate | None, str | None]:
    by_complex = candidates.get(target.identity.complex_id, [])
    if not by_complex:
        return None, "AUTO_ARTIFACT_NOT_FOUND"
    source_locked = [
        item
        for item in by_complex
        if item.result.meta.source_sha256 == target.source_sha256
        and item.result.meta.source_page_count == target.source_page_count
    ]
    if not source_locked:
        return None, "AUTO_SOURCE_LOCK_MISMATCH"
    compatible = [
        (specificity, item)
        for item in source_locked
        if (specificity := _candidate_specificity(item.result, target.identity)) is not None
    ]
    if not compatible:
        return None, "TARGET_COMPATIBLE_AUTO_ARTIFACT_NOT_FOUND"
    compatible.sort(
        key=lambda value: (
            value[1].result.meta.extractor_version == expected_extractor_version,
            value[1].result.meta.schema_version == expected_schema_version,
            value[0],
            value[1].result.meta.analyzed_at,
            str(value[1].path),
        ),
        reverse=True,
    )
    return compatible[0][1], None


def _load_reference_index(reference_dir: Path | None) -> dict[tuple[str, str, int], Path]:
    if reference_dir is None:
        return {}
    references: dict[tuple[str, str, int], Path] = {}
    for path in sorted(reference_dir.glob("*.json")):
        try:
            payload = _load_json_object(path)
            source = payload["source"]
            if not isinstance(source, dict):
                continue
            complex_id = payload["complex_id"]
            sha256 = source["pdf_sha256"]
            page_count = source["page_count"]
            if (
                isinstance(complex_id, str)
                and isinstance(sha256, str)
                and isinstance(page_count, int)
                and payload.get("schema_version") == "core_reference_v0.1"
            ):
                references[(complex_id, sha256, page_count)] = path.resolve()
        except (KeyError, TypeError, ValueError):
            continue
    return references


def _draft_id_from_values(
    identity: ReviewTargetIdentity,
    *,
    source_sha256: str,
    auto_artifact_sha256: str,
) -> str:
    payload = json.dumps(
        {
            "complex_id": identity.complex_id,
            "unit_type_id": identity.unit_type_id,
            "unit_type_name": identity.unit_type_name,
            "sale_price_manwon": identity.sale_price_manwon,
            "source_sha256": source_sha256,
            "auto_artifact_sha256": auto_artifact_sha256,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return _sha256_bytes(payload)


def _draft_id(target: _InventoryTarget, candidate: _AutoCandidate) -> str:
    assert target.source_sha256 is not None
    return _draft_id_from_values(
        target.identity,
        source_sha256=target.source_sha256,
        auto_artifact_sha256=candidate.artifact_sha256,
    )


def _safe_filename_component(value: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", value).strip("._")
    return safe[:60] or "unit"


def _unavailable(target: _InventoryTarget, reason_code: str) -> UnavailableReviewTarget:
    messages = {
        "PDF_UNAVAILABLE": "보유 자료에 원본 PDF가 없어 검수 초안을 만들 수 없습니다.",
        "PDF_SOURCE_LOCK_MISMATCH": "실제 PDF의 SHA-256 또는 페이지 수가 인벤토리와 다릅니다.",
        "PDF_LOAD_FAILED": "보유 PDF를 읽고 페이지를 추출하지 못했습니다.",
        "AUTO_ARTIFACT_NOT_FOUND": "해당 공고의 자동 추출 결과가 없습니다.",
        "AUTO_SOURCE_LOCK_MISMATCH": "자동 추출 결과와 보유 PDF의 source lock이 다릅니다.",
        "TARGET_COMPATIBLE_AUTO_ARTIFACT_NOT_FOUND": (
            "문서 공통 또는 해당 주택형·분양가와 일치하는 자동 추출 결과가 없습니다."
        ),
    }
    return UnavailableReviewTarget(
        target=target.identity,
        reason_code=reason_code,
        message=messages[reason_code],
    )


def _approval_template(
    manifest: ReviewDraftBatchManifest,
    *,
    draft_manifest_sha256: str,
) -> ReviewApprovalManifest:
    return ReviewApprovalManifest(
        draft_batch_id=manifest.batch_id,
        draft_manifest_sha256=draft_manifest_sha256,
        reviewer=None,
        reviewed_at=None,
        attestation=None,
        items=[
            ReviewApprovalItem(
                draft_id=item.draft_id,
                decision=ApprovalDecision.PENDING,
                draft_sha256=item.draft_sha256,
                checks=ReviewApprovalChecks(),
                notes=None,
            )
            for item in manifest.drafts
        ],
    )


def _batch_id(
    *,
    inventory_sha256: str,
    expected_schema_version: str,
    expected_extractor_version: str,
    drafts: Sequence[ReviewDraftEntry],
    unavailable: Sequence[UnavailableReviewTarget],
) -> str:
    payload = {
        "inventory_sha256": inventory_sha256,
        "expected_schema_version": expected_schema_version,
        "expected_extractor_version": expected_extractor_version,
        "draft_ids": sorted(item.draft_id for item in drafts),
        "unavailable": sorted(
            (
                item.target.complex_id,
                item.target.unit_type_id,
                item.target.unit_type_name,
                item.target.sale_price_manwon,
                item.reason_code,
            )
            for item in unavailable
        ),
    }
    return _sha256_bytes(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )


def _target_key(identity: ReviewTargetIdentity) -> tuple[str, str, str, int]:
    return (
        identity.complex_id,
        identity.unit_type_id,
        identity.unit_type_name,
        identity.sale_price_manwon,
    )


def _version_blockers(
    result: AnalysisResponse,
    *,
    expected_schema_version: str,
    expected_extractor_version: str,
) -> list[str]:
    blockers: list[str] = []
    if result.meta.schema_version != expected_schema_version:
        blockers.append(f"SCHEMA_VERSION_MISMATCH:{result.meta.schema_version}")
    if result.meta.extractor_version != expected_extractor_version:
        blockers.append(f"EXTRACTOR_VERSION_MISMATCH:{result.meta.extractor_version}")
    return blockers


def prepare_review_batch(
    *,
    inventory_path: Path,
    auto_artifact_dirs: Sequence[Path],
    output_dir: Path,
    settings: Settings,
    reference_dir: Path | None = None,
    pdf_loader: PdfLoader = load_pdf_from_path,
    page_extractor: PageExtractor = extract_pdf_pages,
) -> ReviewDraftBatchManifest:
    """Create editable review drafts, never REVIEWED artifacts."""

    if not auto_artifact_dirs:
        raise ValueError("최소 하나의 auto artifact 디렉터리가 필요합니다.")
    inventory_path = inventory_path.resolve()
    output_dir = output_dir.absolute()
    if output_dir.exists():
        raise FileExistsError(
            f"기존 검수 수정본을 보호하기 위해 새 output-dir이 필요합니다: {output_dir}"
        )
    targets, _source_directory = _load_inventory(inventory_path)
    candidates = _load_auto_candidates([path.resolve() for path in auto_artifact_dirs])
    references = _load_reference_index(reference_dir.resolve() if reference_dir else None)
    inventory_sha256 = _sha256_file(inventory_path)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    drafts: list[ReviewDraftEntry] = []
    unavailable: list[UnavailableReviewTarget] = []
    pdf_cache: dict[Path, tuple[DownloadedPdf, list[PdfPage]] | None] = {}
    try:
        for target in targets:
            if not target.pdf_available or target.pdf_path is None:
                unavailable.append(_unavailable(target, "PDF_UNAVAILABLE"))
                continue
            candidate, unavailable_reason = _select_auto_candidate(
                candidates,
                target,
                expected_schema_version=settings.schema_version,
                expected_extractor_version=settings.extractor_version,
            )
            if candidate is None:
                unavailable.append(
                    _unavailable(target, unavailable_reason or "AUTO_ARTIFACT_NOT_FOUND")
                )
                continue

            if target.pdf_path not in pdf_cache:
                try:
                    downloaded = pdf_loader(str(target.pdf_path), settings)
                    pages = page_extractor(downloaded.content, settings)
                    pdf_cache[target.pdf_path] = (downloaded, pages)
                except (AnalysisError, OSError):
                    pdf_cache[target.pdf_path] = None
            loaded = pdf_cache[target.pdf_path]
            if loaded is None:
                unavailable.append(_unavailable(target, "PDF_LOAD_FAILED"))
                continue
            downloaded, pages = loaded
            if (
                downloaded.sha256 != target.source_sha256
                or len(pages) != target.source_page_count
            ):
                unavailable.append(_unavailable(target, "PDF_SOURCE_LOCK_MISMATCH"))
                continue

            result = candidate.result.model_copy(deep=True)
            result.target_unit = TargetUnit(
                unit_type_id=target.identity.unit_type_id,
                unit_type_name=target.identity.normalized_unit_type_name,
                sale_price_manwon=target.identity.sale_price_manwon,
            )
            prepared = prepare_review_draft(
                result,
                source_sha256=downloaded.sha256,
                pages=pages,
            )
            if prepared.review_status == ReviewStatus.REVIEWED:
                raise AssertionError("검수 초안 준비가 REVIEWED 상태를 만들었습니다.")

            draft_id = _draft_id(target, candidate)
            stem = "__".join(
                (
                    _safe_filename_component(target.identity.complex_id),
                    _safe_filename_component(target.identity.unit_type_id),
                    str(target.identity.sale_price_manwon),
                    draft_id[:12],
                )
            )
            relative_draft_path = Path("drafts") / f"{stem}.review-draft.json"
            relative_checklist_path = Path("checklists") / f"{stem}.review.md"
            draft_path = stage / relative_draft_path
            checklist_path = stage / relative_checklist_path
            save_result(prepared, draft_path)
            draft_sha256 = _sha256_file(draft_path)

            reference_path = references.get(
                (
                    target.identity.complex_id,
                    downloaded.sha256,
                    len(pages),
                )
            )
            blockers = _version_blockers(
                prepared,
                expected_schema_version=settings.schema_version,
                expected_extractor_version=settings.extractor_version,
            )
            warnings: list[str] = []
            if not prepared.validation.passed:
                warnings.append("INITIAL_VALIDATION_FAILED_EDIT_AND_REVALIDATE")
            if reference_path is not None:
                warnings.append("REFERENCE_EXCLUDES_UNIT_AMOUNTS_AND_ADDITIONAL_COSTS")
            preface = [
                "> 이 파일은 `REVIEW_DRAFT`입니다. 운영 `reviewed` 저장소에 복사하지 마십시오.",
                "> 승인 매니페스트와 검수자의 명시적 확인 전에는 `REVIEWED`가 아닙니다.",
                "",
                f"- draft_id: `{draft_id}`",
                f"- 백엔드 원본 unit_type_name: `{target.identity.unit_type_name}`",
                f"- 초안 SHA-256: `{draft_sha256}`",
                f"- 승인 차단 사유: `{', '.join(blockers) if blockers else '없음'}`",
            ]
            if reference_path is not None:
                preface.extend(
                    [
                        f"- 참고 라벨: `{reference_path}`",
                        "- 참고 라벨은 문서 공통 핵심필드만 다루며 주택형 금액·추가비용·"
                        "은행·보증기관은 별도 검수 대상입니다.",
                    ]
                )
            write_review_sheet(prepared, checklist_path, preface=preface)

            drafts.append(
                ReviewDraftEntry(
                    draft_id=draft_id,
                    target=target.identity,
                    source_sha256=downloaded.sha256,
                    source_page_count=len(pages),
                    source_pdf_path=str(target.pdf_path),
                    source_auto_artifact_path=str(candidate.path),
                    source_auto_artifact_sha256=candidate.artifact_sha256,
                    draft_path=relative_draft_path.as_posix(),
                    draft_sha256=draft_sha256,
                    checklist_path=relative_checklist_path.as_posix(),
                    supporting_reference_path=(
                        str(reference_path) if reference_path is not None else None
                    ),
                    supporting_reference_scope=(
                        "DOCUMENT_LEVEL_CORE_ONLY" if reference_path is not None else None
                    ),
                    schema_version=prepared.meta.schema_version,
                    extractor_version=prepared.meta.extractor_version,
                    initial_review_status=prepared.review_status.value,
                    initial_validation_passed=prepared.validation.passed,
                    approval_blockers=blockers,
                    preparation_warnings=warnings,
                )
            )

        batch_id = _batch_id(
            inventory_sha256=inventory_sha256,
            expected_schema_version=settings.schema_version,
            expected_extractor_version=settings.extractor_version,
            drafts=drafts,
            unavailable=unavailable,
        )
        blocked_count = sum(bool(item.approval_blockers) for item in drafts)
        manifest = ReviewDraftBatchManifest(
            batch_id=batch_id,
            created_at=datetime.now(UTC),
            expected_schema_version=settings.schema_version,
            expected_extractor_version=settings.extractor_version,
            source_inventory_path=str(inventory_path),
            source_inventory_sha256=inventory_sha256,
            summary=ReviewDraftSummary(
                target_count=len(targets),
                pdf_backed_target_count=sum(item.pdf_available for item in targets),
                draft_count=len(drafts),
                approval_eligible_draft_count=len(drafts) - blocked_count,
                blocked_draft_count=blocked_count,
                unavailable_target_count=len(unavailable),
            ),
            drafts=drafts,
            unavailable_targets=unavailable,
        )
        draft_manifest_path = stage / "review-draft-manifest.json"
        _write_model(manifest, draft_manifest_path)
        _write_model(
            _approval_template(
                manifest,
                draft_manifest_sha256=_sha256_file(draft_manifest_path),
            ),
            stage / "review-approval-manifest.template.json",
        )
        os.replace(stage, output_dir)
        return manifest
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def load_review_draft_manifest(path: Path) -> ReviewDraftBatchManifest:
    return ReviewDraftBatchManifest.model_validate_json(path.read_text(encoding="utf-8"))


def load_review_approval_manifest(path: Path) -> ReviewApprovalManifest:
    return ReviewApprovalManifest.model_validate_json(path.read_text(encoding="utf-8"))


def _manifest_relative_path(manifest_path: Path, value: str) -> Path:
    raw = Path(value)
    if raw.is_absolute():
        raise ValueError("초안·체크리스트 경로는 배치 매니페스트 기준 상대경로여야 합니다.")
    root = manifest_path.parent.resolve()
    resolved = (root / raw).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("배치 아티팩트 경로가 매니페스트 디렉터리를 벗어납니다.")
    return resolved


def _validate_draft_identity(result: AnalysisResponse, entry: ReviewDraftEntry) -> None:
    target = entry.target
    if result.review_status == ReviewStatus.REVIEWED:
        raise ValueError(f"{entry.draft_id}: REVIEWED 파일은 초안으로 승인할 수 없습니다.")
    if result.reviewer is not None or result.reviewed_at is not None:
        raise ValueError(f"{entry.draft_id}: 초안에 검수자 메타데이터가 있습니다.")
    if result.complex_id != target.complex_id:
        raise ValueError(f"{entry.draft_id}: complex_id가 다릅니다.")
    if (
        result.target_unit.unit_type_id != target.unit_type_id
        or result.target_unit.unit_type_name != target.normalized_unit_type_name
        or result.target_unit.sale_price_manwon != target.sale_price_manwon
    ):
        raise ValueError(f"{entry.draft_id}: 주택형·분양가 target이 다릅니다.")
    if (
        result.meta.source_sha256 != entry.source_sha256
        or result.meta.source_page_count != entry.source_page_count
    ):
        raise ValueError(f"{entry.draft_id}: 초안 source lock이 다릅니다.")


def _validate_draft_manifest_integrity(
    *,
    draft_manifest_path: Path,
    draft_manifest: ReviewDraftBatchManifest,
    approval_manifest: ReviewApprovalManifest,
) -> None:
    """Rebuild every immutable batch claim from its source before approval."""

    actual_manifest_sha256 = _sha256_file(draft_manifest_path)
    if approval_manifest.draft_manifest_sha256 != actual_manifest_sha256:
        raise ValueError("승인 매니페스트가 가리키는 검수 배치 SHA-256이 다릅니다.")

    inventory_path = Path(draft_manifest.source_inventory_path).resolve()
    if not inventory_path.is_file():
        raise ValueError("검수 배치의 원본 인벤토리를 찾을 수 없습니다.")
    actual_inventory_sha256 = _sha256_file(inventory_path)
    if actual_inventory_sha256 != draft_manifest.source_inventory_sha256:
        raise ValueError("검수 배치의 원본 인벤토리 SHA-256이 다릅니다.")
    inventory_targets, _source_directory = _load_inventory(inventory_path)
    inventory_by_key = {_target_key(item.identity): item for item in inventory_targets}

    represented = [
        *(_target_key(item.target) for item in draft_manifest.drafts),
        *(_target_key(item.target) for item in draft_manifest.unavailable_targets),
    ]
    if len(represented) != len(set(represented)):
        raise ValueError("검수 배치에 중복 target이 있습니다.")
    if set(represented) != set(inventory_by_key):
        raise ValueError("검수 배치 target 집합이 원본 인벤토리와 다릅니다.")

    expected_summary = ReviewDraftSummary(
        target_count=len(inventory_targets),
        pdf_backed_target_count=sum(item.pdf_available for item in inventory_targets),
        draft_count=len(draft_manifest.drafts),
        approval_eligible_draft_count=sum(
            not item.approval_blockers for item in draft_manifest.drafts
        ),
        blocked_draft_count=sum(
            bool(item.approval_blockers) for item in draft_manifest.drafts
        ),
        unavailable_target_count=len(draft_manifest.unavailable_targets),
    )
    if draft_manifest.summary != expected_summary:
        raise ValueError("검수 배치 summary가 실제 항목 집계와 다릅니다.")
    expected_batch_id = _batch_id(
        inventory_sha256=actual_inventory_sha256,
        expected_schema_version=draft_manifest.expected_schema_version,
        expected_extractor_version=draft_manifest.expected_extractor_version,
        drafts=draft_manifest.drafts,
        unavailable=draft_manifest.unavailable_targets,
    )
    if draft_manifest.batch_id != expected_batch_id:
        raise ValueError("검수 배치 batch_id가 원본 항목에서 재계산한 값과 다릅니다.")

    expected_item_ids = {item.draft_id for item in draft_manifest.drafts}
    approval_item_ids = {item.draft_id for item in approval_manifest.items}
    if approval_item_ids != expected_item_ids:
        raise ValueError("승인 매니페스트 항목 집합이 검수 배치와 다릅니다.")

    for entry in draft_manifest.drafts:
        inventory_target = inventory_by_key[_target_key(entry.target)]
        if not inventory_target.pdf_available or inventory_target.pdf_path is None:
            raise ValueError(f"{entry.draft_id}: PDF 없는 target에 검수 초안이 있습니다.")
        if (
            Path(entry.source_pdf_path).resolve() != inventory_target.pdf_path
            or entry.source_sha256 != inventory_target.source_sha256
            or entry.source_page_count != inventory_target.source_page_count
        ):
            raise ValueError(f"{entry.draft_id}: 원본 인벤토리 source lock과 다릅니다.")

        auto_path = Path(entry.source_auto_artifact_path).resolve()
        if not auto_path.is_file():
            raise ValueError(f"{entry.draft_id}: 원본 자동추출 artifact가 없습니다.")
        actual_auto_sha256 = _sha256_file(auto_path)
        if actual_auto_sha256 != entry.source_auto_artifact_sha256:
            raise ValueError(f"{entry.draft_id}: 원본 자동추출 artifact SHA-256이 다릅니다.")
        auto_result = load_result(auto_path)
        if auto_result.review_status == ReviewStatus.REVIEWED:
            raise ValueError(
                f"{entry.draft_id}: REVIEWED artifact를 자동추출 원본으로 쓸 수 없습니다."
            )
        if (
            auto_result.complex_id != entry.target.complex_id
            or auto_result.meta.source_sha256 != entry.source_sha256
            or auto_result.meta.source_page_count != entry.source_page_count
            or _candidate_specificity(auto_result, entry.target) is None
        ):
            raise ValueError(f"{entry.draft_id}: 자동추출 artifact의 source·target이 다릅니다.")
        if (
            entry.schema_version != auto_result.meta.schema_version
            or entry.extractor_version != auto_result.meta.extractor_version
        ):
            raise ValueError(f"{entry.draft_id}: 자동추출 artifact 버전 메타데이터가 다릅니다.")
        actual_blockers = _version_blockers(
            auto_result,
            expected_schema_version=draft_manifest.expected_schema_version,
            expected_extractor_version=draft_manifest.expected_extractor_version,
        )
        if entry.approval_blockers != actual_blockers:
            raise ValueError(f"{entry.draft_id}: 승인 차단 사유가 원본 artifact와 다릅니다.")
        expected_draft_id = _draft_id_from_values(
            entry.target,
            source_sha256=entry.source_sha256,
            auto_artifact_sha256=actual_auto_sha256,
        )
        if entry.draft_id != expected_draft_id:
            raise ValueError(f"{entry.draft_id}: draft_id 재계산 결과가 다릅니다.")
        draft_path = _manifest_relative_path(draft_manifest_path, entry.draft_path)
        checklist_path = _manifest_relative_path(
            draft_manifest_path, entry.checklist_path
        )
        if not draft_path.is_file() or not checklist_path.is_file():
            raise ValueError(f"{entry.draft_id}: 초안 또는 체크리스트 파일이 없습니다.")


def validate_review_batch_approval(
    *,
    draft_manifest_path: Path,
    approval_manifest_path: Path,
    reviewer: str,
    settings: Settings,
    explicit_confirmation: bool,
    pdf_loader: PdfLoader = load_pdf_from_path,
    page_extractor: PageExtractor = extract_pdf_pages,
) -> list[_ValidatedApproval]:
    """Validate an explicit reviewer manifest without writing REVIEWED artifacts."""

    if not explicit_confirmation:
        raise ValueError("명시적 승인 확인이 필요합니다.")
    reviewer = reviewer.strip()
    if not reviewer:
        raise ValueError("검수자 이름이 필요합니다.")
    draft_manifest_path = draft_manifest_path.resolve()
    approval_manifest_path = approval_manifest_path.resolve()
    draft_manifest = load_review_draft_manifest(draft_manifest_path)
    approval_manifest = load_review_approval_manifest(approval_manifest_path)
    if approval_manifest.draft_batch_id != draft_manifest.batch_id:
        raise ValueError("승인 매니페스트의 draft_batch_id가 다릅니다.")
    if approval_manifest.reviewer != reviewer:
        raise ValueError("명령행 검수자와 승인 매니페스트 검수자가 다릅니다.")
    if draft_manifest.expected_schema_version != settings.schema_version:
        raise ValueError("현재 서비스와 초안 배치의 schema_version이 다릅니다.")
    if draft_manifest.expected_extractor_version != settings.extractor_version:
        raise ValueError("현재 서비스와 초안 배치의 extractor_version이 다릅니다.")
    _validate_draft_manifest_integrity(
        draft_manifest_path=draft_manifest_path,
        draft_manifest=draft_manifest,
        approval_manifest=approval_manifest,
    )

    approved_items = [
        item for item in approval_manifest.items if item.decision == ApprovalDecision.APPROVE
    ]
    if not approved_items:
        raise ValueError("승인 매니페스트에 APPROVE 항목이 없습니다.")
    entries = {item.draft_id: item for item in draft_manifest.drafts}
    unknown_ids = sorted(item.draft_id for item in approved_items if item.draft_id not in entries)
    if unknown_ids:
        raise ValueError(f"알 수 없는 draft_id입니다: {unknown_ids!r}")

    pdf_cache: dict[Path, tuple[DownloadedPdf, list[PdfPage]]] = {}
    validated: list[_ValidatedApproval] = []
    destination_names: set[str] = set()
    for approval in approved_items:
        entry = entries[approval.draft_id]
        if entry.approval_blockers:
            raise ValueError(
                f"{entry.draft_id}: 승인 차단 사유가 남아 있습니다: {entry.approval_blockers!r}"
            )
        draft_path = _manifest_relative_path(draft_manifest_path, entry.draft_path)
        actual_draft_sha256 = _sha256_file(draft_path)
        if approval.draft_sha256 != actual_draft_sha256:
            raise ValueError(f"{entry.draft_id}: 승인한 초안 SHA-256과 실제 파일이 다릅니다.")
        result = load_result(draft_path)
        _validate_draft_identity(result, entry)
        if result.meta.schema_version != settings.schema_version:
            raise ValueError(f"{entry.draft_id}: schema_version이 현재 서비스와 다릅니다.")
        if result.meta.extractor_version != settings.extractor_version:
            raise ValueError(f"{entry.draft_id}: extractor_version이 현재 서비스와 다릅니다.")

        pdf_path = Path(entry.source_pdf_path).resolve()
        if pdf_path not in pdf_cache:
            downloaded = pdf_loader(str(pdf_path), settings)
            pages = page_extractor(downloaded.content, settings)
            pdf_cache[pdf_path] = (downloaded, pages)
        downloaded, pages = pdf_cache[pdf_path]
        if downloaded.sha256 != entry.source_sha256 or len(pages) != entry.source_page_count:
            raise ValueError(f"{entry.draft_id}: 승인 시점 PDF source lock이 다릅니다.")
        approved = approve_result(
            result,
            reviewer=reviewer,
            source_sha256=downloaded.sha256,
            pages=pages,
        )
        if approved.review_status != ReviewStatus.REVIEWED:
            raise AssertionError("명시적 승인 단계가 REVIEWED를 만들지 못했습니다.")
        target = entry.target
        destination_name = "__".join(
            (
                _safe_filename_component(target.complex_id),
                _safe_filename_component(target.unit_type_id),
                str(target.sale_price_manwon),
                entry.draft_id[:12],
            )
        ) + ".json"
        if destination_name in destination_names:
            raise ValueError(f"중복 승인 출력 파일명입니다: {destination_name}")
        destination_names.add(destination_name)
        validated.append(
            _ValidatedApproval(
                entry=entry,
                approved_draft_sha256=actual_draft_sha256,
                approved=approved,
                destination_name=destination_name,
            )
        )
    return validated


def approve_review_batch(
    *,
    draft_manifest_path: Path,
    approval_manifest_path: Path,
    output_dir: Path,
    reviewer: str,
    settings: Settings,
    explicit_confirmation: bool,
    pdf_loader: PdfLoader = load_pdf_from_path,
    page_extractor: PageExtractor = extract_pdf_pages,
) -> ReviewApprovalReceipt:
    """Write REVIEWED artifacts only after the full explicit approval preflight."""

    output_dir = output_dir.absolute()
    if output_dir.exists():
        raise FileExistsError(
            f"기존 REVIEWED 결과를 보호하기 위해 새 output-dir이 필요합니다: {output_dir}"
        )
    validated = validate_review_batch_approval(
        draft_manifest_path=draft_manifest_path,
        approval_manifest_path=approval_manifest_path,
        reviewer=reviewer,
        settings=settings,
        explicit_confirmation=explicit_confirmation,
        pdf_loader=pdf_loader,
        page_extractor=page_extractor,
    )
    draft_manifest = load_review_draft_manifest(draft_manifest_path.resolve())
    approval_manifest_path = approval_manifest_path.resolve()
    approval_manifest_sha256 = _sha256_file(approval_manifest_path)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    receipt_items: list[ReviewApprovalReceiptItem] = []
    try:
        for item in validated:
            destination = stage / item.destination_name
            save_result(item.approved, destination)
            receipt_items.append(
                ReviewApprovalReceiptItem(
                    draft_id=item.entry.draft_id,
                    target=item.entry.target,
                    source_sha256=item.entry.source_sha256,
                    source_page_count=item.entry.source_page_count,
                    approved_draft_sha256=item.approved_draft_sha256,
                    draft_was_edited=(
                        item.approved_draft_sha256 != item.entry.draft_sha256
                    ),
                    reviewed_artifact_path=item.destination_name,
                    reviewed_artifact_sha256=_sha256_file(destination),
                )
            )
        receipt = ReviewApprovalReceipt(
            draft_batch_id=draft_manifest.batch_id,
            draft_manifest_sha256=_sha256_file(draft_manifest_path.resolve()),
            reviewer=reviewer.strip(),
            executed_at=datetime.now(UTC),
            approval_manifest_path=str(approval_manifest_path),
            approval_manifest_sha256=approval_manifest_sha256,
            approved_count=len(receipt_items),
            items=receipt_items,
        )
        _write_model(receipt, stage / "review-approval-receipt.json")
        os.replace(stage, output_dir)
        return receipt
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
