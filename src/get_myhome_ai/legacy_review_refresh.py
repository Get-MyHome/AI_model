from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from get_myhome_ai.models import AdditionalCostType, AnalysisResponse, ReviewStatus
from get_myhome_ai.normalization import normalize_unit_type_name
from get_myhome_ai.pdf_text import DownloadedPdf, PdfPage, extract_pdf_pages, load_pdf_from_path
from get_myhome_ai.review import load_result, prepare_review_draft, save_result, write_review_sheet
from get_myhome_ai.review_batch import (
    ApprovalDecision,
    ReviewDraftBatchManifest,
    ReviewDraftEntry,
    load_review_approval_manifest,
    load_review_draft_manifest,
)
from get_myhome_ai.review_candidate_correction import (
    CandidateCorrectionError,
    PageExtractor,
    PdfLoader,
    _copy_review_workspace,
    _resolve_relative,
    _sha256_file,
    _write_pending_approval_template,
)
from get_myhome_ai.settings import Settings


class LegacyRefreshError(CandidateCorrectionError):
    """Raised when a legacy fact set cannot be rebound to the current extractor."""


@dataclass(frozen=True)
class _LegacyRefreshPolicy:
    source_sha256: str
    source_page_count: int
    target: tuple[str, str, str, int]
    legacy_artifact_sha256: str
    legacy_review_status: ReviewStatus
    balcony_total_manwon: int
    balcony_payments_manwon: tuple[int, ...]


_SOURCE_0365 = "97e1c0987724ca05150f6c0e2f3aa34fea405523d55a0ee9d3b00c8f2a375b5b"
_SOURCE_0368 = "bf2de56ac7eadf705f71de85f3b08e3d50e6323a338d6ceb719ace954636ab95"
_SOURCE_0372 = "ef0ff3b5723da3ac4ee214a596921734dbfad6d1ed7be9564b6cf05b8783ba10"


def _policy(
    *,
    source_sha256: str,
    source_page_count: int,
    target: tuple[str, str, str, int],
    legacy_artifact_sha256: str,
    balcony_total_manwon: int,
    balcony_payments_manwon: tuple[int, ...],
    legacy_review_status: ReviewStatus = ReviewStatus.AUTO_EXTRACTED,
) -> _LegacyRefreshPolicy:
    return _LegacyRefreshPolicy(
        source_sha256=source_sha256,
        source_page_count=source_page_count,
        target=target,
        legacy_artifact_sha256=legacy_artifact_sha256,
        legacy_review_status=legacy_review_status,
        balcony_total_manwon=balcony_total_manwon,
        balcony_payments_manwon=balcony_payments_manwon,
    )


_LEGACY_REFRESH_POLICIES: dict[tuple[str, str, str, int], _LegacyRefreshPolicy] = {
    ("2026000365", "01", "59A", 55_400): _policy(
        source_sha256=_SOURCE_0365,
        source_page_count=62,
        target=("2026000365", "01", "59A", 55_400),
        legacy_artifact_sha256=(
            "1eee8edab1291f86e111110ea1f363d6063a5eb47cdcfa2bbc8b029363fcd42b"
        ),
        balcony_total_manwon=2_100,
        balcony_payments_manwon=(210, 420, 1_470),
    ),
    ("2026000365", "02", "59B", 54_800): _policy(
        source_sha256=_SOURCE_0365,
        source_page_count=62,
        target=("2026000365", "02", "59B", 54_800),
        legacy_artifact_sha256=(
            "f89991bd427a79b89475f0ceef98430716b099be70f817515ad8b2c62f21058d"
        ),
        balcony_total_manwon=2_100,
        balcony_payments_manwon=(210, 420, 1_470),
    ),
    ("2026000365", "03", "75", 65_900): _policy(
        source_sha256=_SOURCE_0365,
        source_page_count=62,
        target=("2026000365", "03", "75", 65_900),
        legacy_artifact_sha256=(
            "33234b1caea1b957f23445323262c3d9c5ca7246475b24032259060b209f9cf4"
        ),
        balcony_total_manwon=2_600,
        balcony_payments_manwon=(260, 520, 1_820),
    ),
    ("2026000365", "04", "84", 71_900): _policy(
        source_sha256=_SOURCE_0365,
        source_page_count=62,
        target=("2026000365", "04", "84", 71_900),
        legacy_artifact_sha256=(
            "b7772b1097f27c5e9d04b31be522c09b5b4fcf5e004375b2f5b62570fa060d6d"
        ),
        balcony_total_manwon=3_000,
        balcony_payments_manwon=(300, 600, 2_100),
    ),
    ("2026000368", "01", "84A", 51_200): _policy(
        source_sha256=_SOURCE_0368,
        source_page_count=49,
        target=("2026000368", "01", "84A", 51_200),
        legacy_artifact_sha256=(
            "16233594f0ffc6a383cfb313196e10650839ab0b7cc4d5bb0ee7e3814d14a930"
        ),
        balcony_total_manwon=3_000,
        balcony_payments_manwon=(300, 300, 2_400),
    ),
    ("2026000368", "02", "84B", 52_600): _policy(
        source_sha256=_SOURCE_0368,
        source_page_count=49,
        target=("2026000368", "02", "84B", 52_600),
        legacy_artifact_sha256=(
            "4e6f9afdda8d7b771b8647b7a72beb3f21ae5565963d713ece6f65d9ce9a133e"
        ),
        balcony_total_manwon=3_000,
        balcony_payments_manwon=(300, 300, 2_400),
    ),
    ("2026000368", "03", "84C", 52_500): _policy(
        source_sha256=_SOURCE_0368,
        source_page_count=49,
        target=("2026000368", "03", "84C", 52_500),
        legacy_artifact_sha256=(
            "4ae28654d527b9088971562163763d585524acbdfff11b7fb7241333706b9239"
        ),
        balcony_total_manwon=3_000,
        balcony_payments_manwon=(300, 300, 2_400),
    ),
    ("2026000368", "04", "84D", 50_500): _policy(
        source_sha256=_SOURCE_0368,
        source_page_count=49,
        target=("2026000368", "04", "84D", 50_500),
        legacy_artifact_sha256=(
            "b20682a5d23fa23ffb65db276f340e0baa6a25535087eb7289c56cd2ae233e0d"
        ),
        balcony_total_manwon=3_000,
        balcony_payments_manwon=(300, 300, 2_400),
    ),
    ("2026000372", "01", "59A", 108_650): _policy(
        source_sha256=_SOURCE_0372,
        source_page_count=52,
        target=("2026000372", "01", "59A", 108_650),
        legacy_artifact_sha256=(
            "4cfc0e4e065e48130fc12f42233c3a6009af45788adde1dacffe9f1f63bdb9f3"
        ),
        legacy_review_status=ReviewStatus.REVIEWED,
        balcony_total_manwon=1_870,
        balcony_payments_manwon=(187, 1_683),
    ),
    ("2026000372", "02", "84A", 137_330): _policy(
        source_sha256=_SOURCE_0372,
        source_page_count=52,
        target=("2026000372", "02", "84A", 137_330),
        legacy_artifact_sha256=(
            "d3cdc17768beb8fdee1b0de3ddf8c79a615c1b98fc890c4d9593c823574dc055"
        ),
        balcony_total_manwon=2_420,
        balcony_payments_manwon=(242, 2_178),
    ),
    ("2026000372", "03", "84B", 135_340): _policy(
        source_sha256=_SOURCE_0372,
        source_page_count=52,
        target=("2026000372", "03", "84B", 135_340),
        legacy_artifact_sha256=(
            "27c0cc8b5a6787ca2101d7c9cc46625171186f8bfa4b8bc02100e92f80afa4b8"
        ),
        balcony_total_manwon=2_420,
        balcony_payments_manwon=(242, 2_178),
    ),
}

LEGACY_REFRESH_COUNT = 11


def _entry_key(entry: ReviewDraftEntry) -> tuple[str, str, str, int]:
    return (
        entry.target.complex_id,
        entry.target.unit_type_id,
        entry.target.normalized_unit_type_name,
        entry.target.sale_price_manwon,
    )


def _result_key(result: AnalysisResponse) -> tuple[str, str, str, int]:
    target = result.target_unit
    if (
        target.unit_type_id is None
        or target.unit_type_name is None
        or target.sale_price_manwon is None
    ):
        raise LegacyRefreshError("구 대상 아티팩트의 exact target이 비어 있습니다.")
    return (
        result.complex_id,
        target.unit_type_id,
        target.unit_type_name,
        target.sale_price_manwon,
    )


def _artifact_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_legacy_artifacts(
    *,
    legacy_workspace_dir: Path,
    historical_reviewed_artifact: Path,
) -> dict[tuple[str, str, str, int], tuple[Path, AnalysisResponse]]:
    expected_by_hash = {
        policy.legacy_artifact_sha256: (key, policy)
        for key, policy in _LEGACY_REFRESH_POLICIES.items()
        if policy.legacy_review_status != ReviewStatus.REVIEWED
    }
    if len(expected_by_hash) != LEGACY_REFRESH_COUNT - 1:
        raise LegacyRefreshError("구 AUTO 교정본 SHA-256 정책이 중복되었습니다.")

    located: dict[tuple[str, str, str, int], tuple[Path, AnalysisResponse]] = {}
    for path in sorted((legacy_workspace_dir / "drafts").glob("*.json")):
        matched = expected_by_hash.get(_artifact_sha256(path))
        if matched is None:
            continue
        key, policy = matched
        if key in located:
            raise LegacyRefreshError(f"{key}: 구 교정본 SHA-256이 중복됩니다.")
        result = load_result(path)
        _validate_legacy_result(result, policy=policy)
        located[key] = (path, result)

    reviewed_path = historical_reviewed_artifact.resolve()
    if not reviewed_path.is_file():
        raise LegacyRefreshError("과거 REVIEWED 아티팩트가 없습니다.")
    reviewed_hash = _artifact_sha256(reviewed_path)
    reviewed_matches = [
        (key, policy)
        for key, policy in _LEGACY_REFRESH_POLICIES.items()
        if policy.legacy_review_status == ReviewStatus.REVIEWED
        and policy.legacy_artifact_sha256 == reviewed_hash
    ]
    if len(reviewed_matches) != 1:
        raise LegacyRefreshError("과거 REVIEWED 아티팩트 SHA-256이 감사본과 다릅니다.")
    key, policy = reviewed_matches[0]
    reviewed = load_result(reviewed_path)
    _validate_legacy_result(reviewed, policy=policy)
    located[key] = (reviewed_path, reviewed)

    missing = sorted(set(_LEGACY_REFRESH_POLICIES) - set(located))
    if missing or len(located) != LEGACY_REFRESH_COUNT:
        raise LegacyRefreshError(f"감사된 구 아티팩트 11건이 정확히 없습니다: {missing!r}")
    return located


def _validate_legacy_result(
    result: AnalysisResponse,
    *,
    policy: _LegacyRefreshPolicy,
) -> None:
    if _result_key(result) != policy.target:
        raise LegacyRefreshError(f"{policy.target}: 구 아티팩트 target이 다릅니다.")
    if (
        result.meta.source_sha256 != policy.source_sha256
        or result.meta.source_page_count != policy.source_page_count
    ):
        raise LegacyRefreshError(f"{policy.target}: 구 아티팩트 source lock이 다릅니다.")
    if result.meta.schema_version != "v0.3" or result.meta.extractor_version != "0.2.0":
        raise LegacyRefreshError(f"{policy.target}: 감사된 구 버전 아티팩트가 아닙니다.")
    if result.review_status != policy.legacy_review_status:
        raise LegacyRefreshError(f"{policy.target}: 예상한 구 검수 상태가 아닙니다.")
    if policy.legacy_review_status == ReviewStatus.AUTO_EXTRACTED and (
        result.reviewer is not None or result.reviewed_at is not None
    ):
        raise LegacyRefreshError(f"{policy.target}: AUTO 교정본에 검수자 메타가 있습니다.")
    if policy.legacy_review_status == ReviewStatus.REVIEWED and (
        result.reviewer is None or result.reviewed_at is None
    ):
        raise LegacyRefreshError(f"{policy.target}: 과거 REVIEWED 근거가 완전하지 않습니다.")
    if len(result.additional_costs) != 1:
        raise LegacyRefreshError(f"{policy.target}: 발코니 선택비용 범위가 1건이 아닙니다.")
    cost = result.additional_costs[0]
    if cost.type != AdditionalCostType.BALCONY_EXTENSION:
        raise LegacyRefreshError(f"{policy.target}: 구 추가비용이 발코니 확장비가 아닙니다.")
    if (
        cost.total_amount_manwon != policy.balcony_total_manwon
        or tuple(item.amount_manwon for item in cost.payments)
        != policy.balcony_payments_manwon
    ):
        raise LegacyRefreshError(f"{policy.target}: 감사된 발코니 금액과 다릅니다.")


def _refresh_result(
    current: AnalysisResponse,
    legacy: AnalysisResponse,
    *,
    policy: _LegacyRefreshPolicy,
    source_sha256: str,
    pages: list[PdfPage],
    settings: Settings,
) -> AnalysisResponse:
    if _result_key(current) != policy.target:
        raise LegacyRefreshError(f"{policy.target}: 현재 초안 target이 다릅니다.")
    if (
        current.meta.source_sha256 != policy.source_sha256
        or current.meta.source_page_count != policy.source_page_count
        or source_sha256 != policy.source_sha256
        or len(pages) != policy.source_page_count
    ):
        raise LegacyRefreshError(f"{policy.target}: 현재 PDF source lock이 다릅니다.")
    if (
        current.meta.schema_version != settings.schema_version
        or current.meta.extractor_version != settings.extractor_version
    ):
        raise LegacyRefreshError(f"{policy.target}: 현재 extractor 초안이 아닙니다.")
    if (
        current.review_status == ReviewStatus.REVIEWED
        or current.reviewer is not None
        or current.reviewed_at is not None
    ):
        raise LegacyRefreshError(f"{policy.target}: 현재 review draft에 승인 메타가 있습니다.")

    # Keep the current extractor envelope and import only audited source facts.
    # The old status, reviewer, validation, HOLDs, summary, risk clauses, and
    # deterministic flags are deliberately not carried across the version boundary.
    rebound = current.model_copy(deep=True)
    rebound.payment_schedule = legacy.payment_schedule.model_copy(deep=True)
    rebound.interim_loan = legacy.interim_loan.model_copy(deep=True)
    rebound.additional_costs = [item.model_copy(deep=True) for item in legacy.additional_costs]
    rebound.risk_clauses = []
    rebound.evidence = [item.model_copy(deep=True) for item in legacy.evidence]
    rebound.exception_flags = []
    rebound.review_status = ReviewStatus.AUTO_EXTRACTED
    rebound.reviewer = None
    rebound.reviewed_at = None
    rebound.additional_costs[0].required = False
    rebound.additional_costs[0].included_in_sale_price = False
    rebound.additional_costs[0].note = (
        "선택사항 · 공급금액 미포함; 전체 유상옵션 범위는 별도 확인"
    )

    prepared = prepare_review_draft(
        rebound,
        source_sha256=source_sha256,
        pages=pages,
    )
    repeated = prepare_review_draft(
        prepared,
        source_sha256=source_sha256,
        pages=pages,
    )
    if prepared.model_dump(mode="json") != repeated.model_dump(mode="json"):
        raise LegacyRefreshError(f"{policy.target}: 현재 규칙 재검증이 멱등이 아닙니다.")
    if not prepared.validation.passed or prepared.review_status != ReviewStatus.AUTO_EXTRACTED:
        raise LegacyRefreshError(f"{policy.target}: 현재 출처 재검증을 통과하지 못했습니다.")
    if prepared.reviewer is not None or prepared.reviewed_at is not None:
        raise AssertionError("구 검수자 메타데이터가 새 초안으로 전파됐습니다.")
    if (
        prepared.meta.schema_version != settings.schema_version
        or prepared.meta.extractor_version != settings.extractor_version
    ):
        raise AssertionError("현재 extractor envelope이 보존되지 않았습니다.")
    _validate_refreshed_cost(prepared, policy=policy)
    return prepared


def _validate_refreshed_cost(
    result: AnalysisResponse,
    *,
    policy: _LegacyRefreshPolicy,
) -> None:
    if len(result.additional_costs) != 1:
        raise LegacyRefreshError(f"{policy.target}: 재검증 후 발코니 선택비용 범위가 다릅니다.")
    cost = result.additional_costs[0]
    if (
        normalize_unit_type_name(cost.applicable_unit_type)
        != normalize_unit_type_name(policy.target[2])
        or cost.type != AdditionalCostType.BALCONY_EXTENSION
        or cost.total_amount_manwon != policy.balcony_total_manwon
        or tuple(item.amount_manwon for item in cost.payments)
        != policy.balcony_payments_manwon
        or cost.required is not False
        or cost.included_in_sale_price is not False
    ):
        raise LegacyRefreshError(f"{policy.target}: 재검증 후 발코니 선택비용이 다릅니다.")


def _verify_pending_source_workspace(
    *,
    manifest: ReviewDraftBatchManifest,
    source_root: Path,
) -> None:
    template_path = source_root / "review-approval-manifest.template.json"
    if not template_path.is_file():
        raise LegacyRefreshError("현재 워크스페이스의 승인 template이 없습니다.")
    template = load_review_approval_manifest(template_path)
    if (
        template.draft_batch_id != manifest.batch_id
        or template.reviewer is not None
        or template.reviewed_at is not None
        or template.attestation is not None
        or any(item.decision != ApprovalDecision.PENDING for item in template.items)
    ):
        raise LegacyRefreshError("현재 워크스페이스가 all-PENDING 상태가 아닙니다.")
    by_id = {item.draft_id: item for item in template.items}
    if set(by_id) != {entry.draft_id for entry in manifest.drafts}:
        raise LegacyRefreshError("현재 승인 template의 항목 집합이 다릅니다.")
    for entry in manifest.drafts:
        actual = _sha256_file(_resolve_relative(source_root, entry.draft_path))
        if by_id[entry.draft_id].draft_sha256 != actual:
            raise LegacyRefreshError(
                f"{entry.draft_id}: 현재 초안이 승인 template SHA-256과 다릅니다."
            )


def _copy_prior_correction_provenance(
    source_root: Path,
    stage: Path,
) -> dict[str, str] | None:
    prior = source_root / "review-candidate-correction-manifest.json"
    if prior.is_file():
        try:
            payload = json.loads(prior.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LegacyRefreshError("선행 교정 manifest를 읽을 수 없습니다.") from exc
        candidates = payload.get("candidates") if isinstance(payload, dict) else None
        if (
            not isinstance(candidates, list)
            or payload.get("candidate_count") != len(candidates)
            or payload.get("approval_state") != "PENDING"
        ):
            raise LegacyRefreshError("선행 교정 manifest 구조가 안전한 PENDING 배치가 아닙니다.")
        for record in candidates:
            if not isinstance(record, dict):
                raise LegacyRefreshError("선행 교정 manifest의 candidate가 객체가 아닙니다.")
            relative = record.get("output_draft_path")
            expected = record.get("output_draft_sha256")
            if not isinstance(relative, str) or not isinstance(expected, str):
                raise LegacyRefreshError("선행 교정 manifest의 출력 근거가 비어 있습니다.")
            actual = _sha256_file(_resolve_relative(source_root, relative))
            if actual != expected:
                raise LegacyRefreshError("선행 교정 candidate SHA-256이 manifest와 다릅니다.")
        destination = stage / prior.name
        shutil.copy2(prior, destination)
        source_hash = _sha256_file(prior)
        if _sha256_file(destination) != source_hash:
            raise LegacyRefreshError("선행 교정 manifest 복제 SHA-256이 다릅니다.")
        return {"path": str(prior), "sha256": source_hash}
    return None


def prepare_legacy_review_refresh(
    *,
    draft_manifest_path: Path,
    legacy_workspace_dir: Path,
    historical_reviewed_artifact: Path,
    output_dir: Path,
    settings: Settings,
    pdf_loader: PdfLoader = load_pdf_from_path,
    page_extractor: PageExtractor = extract_pdf_pages,
) -> dict[str, Any]:
    """Rebind exactly 11 audited old fact sets into a current PENDING workspace."""

    if len(_LEGACY_REFRESH_POLICIES) != LEGACY_REFRESH_COUNT:
        raise LegacyRefreshError("구 refresh 정책이 정확히 11건이 아닙니다.")
    draft_manifest_path = draft_manifest_path.resolve()
    source_root = draft_manifest_path.parent
    output_dir = output_dir.absolute()
    if output_dir.exists():
        raise FileExistsError(
            f"기존 검수 후보를 보호하기 위해 새 output-dir이 필요합니다: {output_dir}"
        )
    manifest = load_review_draft_manifest(draft_manifest_path)
    if (
        manifest.expected_schema_version != settings.schema_version
        or manifest.expected_extractor_version != settings.extractor_version
    ):
        raise LegacyRefreshError("현재 extractor로 새로 준비한 review batch가 필요합니다.")
    entry_keys = [_entry_key(entry) for entry in manifest.drafts]
    if len(entry_keys) != len(set(entry_keys)):
        raise LegacyRefreshError("현재 review batch에 중복 target이 있습니다.")
    entries = dict(zip(entry_keys, manifest.drafts, strict=True))
    missing = sorted(set(_LEGACY_REFRESH_POLICIES) - set(entries))
    if missing:
        raise LegacyRefreshError(f"현재 review batch에 구 refresh target이 없습니다: {missing!r}")
    _verify_pending_source_workspace(manifest=manifest, source_root=source_root)
    legacy_artifacts = _load_legacy_artifacts(
        legacy_workspace_dir=legacy_workspace_dir.resolve(),
        historical_reviewed_artifact=historical_reviewed_artifact,
    )

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    records: list[dict[str, Any]] = []
    pdf_cache: dict[Path, tuple[DownloadedPdf, list[PdfPage]]] = {}
    try:
        _copy_review_workspace(
            manifest_path=draft_manifest_path,
            manifest=manifest,
            stage=stage,
        )
        prior_correction = _copy_prior_correction_provenance(source_root, stage)
        for key in sorted(_LEGACY_REFRESH_POLICIES):
            policy = _LEGACY_REFRESH_POLICIES[key]
            entry = entries[key]
            if (
                entry.schema_version != settings.schema_version
                or entry.extractor_version != settings.extractor_version
                or entry.approval_blockers
            ):
                raise LegacyRefreshError(
                    f"{entry.draft_id}: 현재 초안 entry에 버전 차단 사유가 있습니다."
                )
            current_path = _resolve_relative(source_root, entry.draft_path)
            if _sha256_file(current_path) != entry.draft_sha256:
                raise LegacyRefreshError(
                    f"{entry.draft_id}: 구 refresh 대상의 현재 초안 SHA-256이 다릅니다."
                )
            current = load_result(current_path)
            legacy_path, legacy = legacy_artifacts[key]

            pdf_path = Path(entry.source_pdf_path).resolve()
            if pdf_path not in pdf_cache:
                downloaded = pdf_loader(str(pdf_path), settings)
                pages = page_extractor(downloaded.content, settings)
                pdf_cache[pdf_path] = downloaded, pages
            downloaded, pages = pdf_cache[pdf_path]
            if (
                downloaded.sha256 != entry.source_sha256
                or len(pages) != entry.source_page_count
            ):
                raise LegacyRefreshError(f"{entry.draft_id}: 실제 PDF source lock이 다릅니다.")

            refreshed = _refresh_result(
                current,
                legacy,
                policy=policy,
                source_sha256=downloaded.sha256,
                pages=pages,
                settings=settings,
            )
            destination = _resolve_relative(stage, entry.draft_path)
            save_result(refreshed, destination)
            write_review_sheet(
                refreshed,
                _resolve_relative(stage, entry.checklist_path),
                preface=[
                    "> 구 아티팩트의 감사된 사실 필드만 현재 extractor 초안에 재결합했습니다.",
                    "> 과거 REVIEWED 상태·검수자·시각은 승계하지 않았습니다. "
                    "사람 승인 전까지 PENDING입니다.",
                    f"> approval_state: `PENDING`; draft_id: `{entry.draft_id}`",
                ],
            )
            records.append(
                {
                    "draft_id": entry.draft_id,
                    "approval_state": "PENDING",
                    "target": entry.target.model_dump(mode="json"),
                    "source_sha256": entry.source_sha256,
                    "source_page_count": entry.source_page_count,
                    "current_input_draft_sha256": entry.draft_sha256,
                    "current_extractor_version": refreshed.meta.extractor_version,
                    "legacy_artifact_path": str(legacy_path),
                    "legacy_artifact_sha256": policy.legacy_artifact_sha256,
                    "legacy_extractor_version": legacy.meta.extractor_version,
                    "legacy_review_status": legacy.review_status.value,
                    "legacy_reviewer_carried_forward": False,
                    "output_draft_path": Path(entry.draft_path).as_posix(),
                    "output_draft_sha256": _sha256_file(destination),
                    "checklist_path": Path(entry.checklist_path).as_posix(),
                    "review_status": refreshed.review_status.value,
                    "reviewer": None,
                    "reviewed_at": None,
                    "validation_passed": refreshed.validation.passed,
                    "canonical_revalidation_idempotent": True,
                }
            )

        _write_pending_approval_template(manifest=manifest, stage=stage)
        payload: dict[str, Any] = {
            "schema_version": "legacy_review_refresh_v1",
            "artifact_type": "LEGACY_REVIEW_REFRESH_BATCH",
            "created_at": datetime.now(UTC).isoformat(),
            "source_draft_manifest_path": str(draft_manifest_path),
            "source_draft_manifest_sha256": _sha256_file(draft_manifest_path),
            "prior_candidate_correction_manifest": prior_correction,
            "workspace_draft_count": len(manifest.drafts),
            "refreshed_candidate_count": len(records),
            "approval_state": "PENDING",
            "reviewer": None,
            "reviewed_at": None,
            "candidates": records,
        }
        (stage / "legacy-review-refresh-manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(stage, output_dir)
        return payload
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
