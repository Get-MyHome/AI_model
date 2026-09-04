from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from get_myhome_ai.audited_candidate_repairs import apply_audited_repairs
from get_myhome_ai.expanded_audited_candidate_repairs import (
    EXPANDED_AUDITED_POLICY_DATA,
    apply_expanded_audited_repairs,
)
from get_myhome_ai.expanded_audited_repairs_a import (
    _POLICIES as _EXPANDED_AUDITED_POLICIES_A,
)
from get_myhome_ai.expanded_audited_repairs_a import (
    repair_expanded_audited_candidate_a,
)
from get_myhome_ai.expanded_audited_repairs_allocation import (
    _POLICIES as _EXPANDED_AUDITED_ALLOCATION_POLICIES,
)
from get_myhome_ai.expanded_audited_repairs_allocation import (
    repair_expanded_audited_allocation,
)
from get_myhome_ai.expanded_audited_repairs_b import (
    _POLICIES as _EXPANDED_AUDITED_POLICIES_B,
)
from get_myhome_ai.expanded_audited_repairs_b import (
    repair_expanded_audited_candidate_b,
)
from get_myhome_ai.models import (
    AdditionalCostType,
    AnalysisResponse,
    Evidence,
    ExceptionFlag,
    HoldReasonCode,
    ReviewStatus,
)
from get_myhome_ai.normalization import normalize_unit_type_name
from get_myhome_ai.pdf_text import DownloadedPdf, PdfPage, extract_pdf_pages, load_pdf_from_path
from get_myhome_ai.review import load_result, prepare_review_draft, save_result, write_review_sheet
from get_myhome_ai.review_batch import (
    ApprovalDecision,
    ReviewApprovalChecks,
    ReviewApprovalItem,
    ReviewApprovalManifest,
    ReviewDraftBatchManifest,
    ReviewDraftEntry,
    load_review_approval_manifest,
    load_review_draft_manifest,
)
from get_myhome_ai.settings import Settings

PdfLoader = Callable[[str, Settings], DownloadedPdf]
PageExtractor = Callable[[bytes, Settings], list[PdfPage]]


class CandidateCorrectionError(ValueError):
    """Raised when an audited correction cannot be proven from the locked source."""


@dataclass(frozen=True)
class _DocumentPolicy:
    source_sha256: str
    source_page_count: int
    targets: frozenset[tuple[str, str, int]]
    not_included_evidence: tuple[int, str]
    optional_evidence: tuple[int, str]
    repair_sub_manwon_payment_row: bool = False


_AUDITED_DOCUMENTS: dict[str, _DocumentPolicy] = {
    "2026000291": _DocumentPolicy(
        "65590f5b3582c25a3ab8164593f9e11ec12378056447205ca83ec9eebed51c68",
        63,
        frozenset({("01", "84A", 57440), ("02", "84B", 57370), ("03", "84C", 56360)}),
        (42, "발코니 확장 공사비는 공동주택 분양금액과 별도"),
        (43, "발코니확장을 선택하지 않을 경우"),
        repair_sub_manwon_payment_row=True,
    ),
    "2026000293": _DocumentPolicy(
        "854050229458698ee7e15b7453cc93a4e35ce3a07300dabcb7a708551d3e1983",
        61,
        frozenset(
            {
                ("01", "128", 78226),
                ("02", "142", 86897),
                ("03", "149", 92255),
                ("04", "156", 97087),
                ("05", "220P", 166636),
                ("06", "223P", 167888),
                ("07", "227P", 172530),
            }
        ),
        (10, "상기 공급금액에는 발코니 확장비용 및 추가선택품목 비용이 포함되어 있지 않으며"),
        (33, "발코니 확장 공사는 별도 계약품목으로 분양계약자가 선택 계약하는 사항"),
        repair_sub_manwon_payment_row=True,
    ),
    "2026000295": _DocumentPolicy(
        "441c1daaa49824db03d546647f3fbbb96cd583d2b8bab58679e8c3723829ea85",
        69,
        frozenset({("01", "77", 39900), ("02", "84A", 42700), ("03", "84B", 42600)}),
        (
            40,
            "상기 공급금액은 추가 선택품목(발코니 확장, 추가선택 유상옵션) "
            "비용이 포함되지 아니한 가격이며",
        ),
        (40, "추가 선택품목은 계약자가 선택사항으로"),
    ),
    "2026000312": _DocumentPolicy(
        "880cd8f4a5636eb44894af2fcf548583f0a71818071dd5a23a34dfd7a425aa8e",
        76,
        frozenset(
            {("01", "59A", 40400), ("02", "59B", 39000), ("03", "84A", 56800), ("04", "84B", 55200)}
        ),
        (9, "상기 공급금액은 발코니 확장비용 및 추가선택 품목 미포함 금액이며"),
        (9, "주택공급계약 체결 시 별도계약을 통해 선택이 가능합니다"),
    ),
    "2026000318": _DocumentPolicy(
        "fd293b213db44fdca53a432976a8db76cb7d15115ae208a87f9c93b206e6feba",
        73,
        frozenset({("01", "59", 62800), ("02", "75", 76000), ("03", "84", 84900)}),
        (41, "발코니 확장 공사비는 공동주택 공급(분양)금액과 별도"),
        (
            37,
            "계약체결 시 계약자가 선택하는 선택품목이 있을 경우 "
            "마감재 선택사항 및 추가옵션계약(발코니 확장 등)",
        ),
    ),
    "2026000327": _DocumentPolicy(
        "e67ea1355530fe737a1974496b9954fa33ece4e51043795f610c570c0adb0b70",
        36,
        frozenset(
            {("01", "59B", 45700), ("02", "74A", 58700), ("03", "84A", 64600), ("04", "84B", 65500)}
        ),
        (7, "상기 공급금액에는 발코니 확장 비용이 미포함되어 있습니다"),
        (7, "발코니 확장 및 미확장은 계약자의 선택"),
    ),
    "2026000358": _DocumentPolicy(
        "a100bfaaf4c2e16a92021b1b5f85688c9723a0e9963ff5479cdbfb2e79f4ff22",
        57,
        frozenset(
            {
                ("01", "39A", 103500),
                ("02", "39B", 100400),
                ("03", "59", 188500),
                ("04", "84A", 245500),
                ("05", "84B", 229500),
            }
        ),
        (8, "상기 공급금액에는 발코니 확장 및 추가 선택품목(유상옵션) 비용이 미포함된 가격이며"),
        (
            8,
            "발코니 확장 및 추가 선택품목(유상옵션)의 계약은 분양계약 시 또는 "
            "분양계약 이후에 별도의 계약을 통해 선택이 가능합니다",
        ),
    ),
    "2026000377": _DocumentPolicy(
        "90df6ecf889a6e3a13c6f725f3fd00b90284617777aa77b7a45403227f7d7433",
        73,
        frozenset(
            {
                ("01", "49", 56300),
                ("02", "59B", 72500),
                ("03", "74", 85700),
                ("04", "84B", 94000),
            }
        ),
        (9, "상기 공급금액에는 발코니 확장 비용, 추가선택 품목 비용이 미포함 되었으며"),
        (9, "주택 분양계약 체결 시 별도계약을 통해 선택이 가능합니다"),
    ),
    "2026000382": _DocumentPolicy(
        "2d2b5d419bf0e733cf4a4cddecb84d8c5f0cb424e41f6edbf94bc298082ad89a",
        74,
        frozenset(
            {
                ("01", "59A", 87500),
                ("02", "59B", 86400),
                ("03", "74A", 105500),
                ("04", "74B", 101500),
                ("05", "84A", 118200),
                ("06", "84B", 112300),
            }
        ),
        (10, "상기 공급금액에는 발코니 확장 및 추가선택품목(유상옵션) 비용이 미포함된 가격이며"),
        (
            10,
            "발코니 확장 및 추가선택품목(유상옵션)의 계약은 분양계약 시 또는 "
            "분양계약 이후에 별도의 계약을 통해 선택이 가능합니다",
        ),
    ),
}

_expanded_policy_ids = (
    set(EXPANDED_AUDITED_POLICY_DATA)
    | set(_EXPANDED_AUDITED_POLICIES_A)
    | set(_EXPANDED_AUDITED_ALLOCATION_POLICIES)
    | set(_EXPANDED_AUDITED_POLICIES_B)
)
if _expanded_policy_ids & set(_AUDITED_DOCUMENTS):
    raise AssertionError("확장 감사 문서가 기존 registry와 중복됩니다.")
_AUDITED_DOCUMENTS.update(
    {
        complex_id: _DocumentPolicy(**values)
        for complex_id, values in EXPANDED_AUDITED_POLICY_DATA.items()
    }
)
_AUDITED_DOCUMENTS.update(
    {
        complex_id: _DocumentPolicy(
            source_sha256=policy.source_sha256,
            source_page_count=policy.source_page_count,
            targets=frozenset(
                (unit_id, target.unit_name, target.sale_price_manwon)
                for unit_id, target in policy.targets.items()
            ),
            # These fields are unused for the A workflow because its own
            # source-locked cost repair handles every target before the generic
            # balcony filter is reached.
            not_included_evidence=(policy.payment_header_page, policy.payment_header_quote),
            optional_evidence=(policy.payment_header_page, policy.payment_header_quote),
        )
        for complex_id, policy in _EXPANDED_AUDITED_POLICIES_A.items()
    }
)

_AUDITED_DOCUMENTS.update(
    {
        complex_id: _DocumentPolicy(
            source_sha256=policy.source_sha256,
            source_page_count=policy.source_page_count,
            targets=frozenset(
                (unit_id, target.unit_name, target.sale_price_manwon)
                for unit_id, target in policy.targets.items()
            ),
            # Unused: the allocation repair performs complete source-locked
            # schedule, loan, and cost correction before generic handling.
            not_included_evidence=(policy.payment_page, "unused"),
            optional_evidence=(policy.payment_page, "unused"),
        )
        for complex_id, policy in _EXPANDED_AUDITED_ALLOCATION_POLICIES.items()
    }
)

_AUDITED_DOCUMENTS.update(
    {
        complex_id: _DocumentPolicy(
            source_sha256=policy.source_sha256,
            source_page_count=policy.source_page_count,
            targets=frozenset(
                (unit_id, target.unit_name, target.sale_price_manwon)
                for unit_id, target in policy.targets.items()
            ),
            # Unused: the B repair performs complete source-locked schedule,
            # loan, and canonical balcony handling before generic handling.
            not_included_evidence=(policy.payment_header_page, policy.payment_header_quote),
            optional_evidence=(policy.payment_header_page, policy.payment_header_quote),
        )
        for complex_id, policy in _EXPANDED_AUDITED_POLICIES_B.items()
    }
)

AUDITED_CANDIDATE_COUNT = sum(len(policy.targets) for policy in _AUDITED_DOCUMENTS.values())


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_relative(root: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute():
        raise CandidateCorrectionError("초안 경로는 배치 디렉터리 기준 상대경로여야 합니다.")
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise CandidateCorrectionError("초안 경로가 배치 디렉터리를 벗어납니다.")
    return resolved


def _policy_for(result: AnalysisResponse) -> _DocumentPolicy:
    policy = _AUDITED_DOCUMENTS.get(result.complex_id)
    target = result.target_unit
    key = (target.unit_type_id or "", target.unit_type_name or "", target.sale_price_manwon or -1)
    if policy is None or key not in policy.targets:
        raise CandidateCorrectionError(
            f"감사 대상 후보에 포함되지 않은 target입니다: {result.complex_id}/{key}"
        )
    if (
        result.meta.source_sha256 != policy.source_sha256
        or result.meta.source_page_count != policy.source_page_count
    ):
        raise CandidateCorrectionError("감사한 PDF source lock과 다릅니다.")
    return policy


def _source_evidence(pages: list[PdfPage], evidence: tuple[int, str]) -> Evidence:
    page_number, quote = evidence
    page = next((item for item in pages if item.number == page_number), None)
    if page is None or _normalized(quote) not in _normalized(page.text):
        raise CandidateCorrectionError(
            f"명시적 선택/미포함 근거를 PDF {page_number}쪽에서 찾지 못했습니다."
        )
    return Evidence(field="/additional_costs/0", page=page_number, raw_text=quote)


def _won_values(raw_text: str) -> list[int]:
    return [int(value.replace(",", "")) for value in re.findall(r"\d[\d,]{3,}", raw_text)]


def _exact_manwon(value_won: int) -> int | None:
    return value_won // 10_000 if value_won % 10_000 == 0 else None


def _ground_balcony_cost(
    result: AnalysisResponse, pages: list[PdfPage], policy: _DocumentPolicy
) -> list[str]:
    balcony = [
        (index, cost)
        for index, cost in enumerate(result.additional_costs)
        if cost.type == AdditionalCostType.BALCONY_EXTENSION
    ]
    if len(balcony) != 1:
        raise CandidateCorrectionError("선택 주택형의 발코니 확장비가 정확히 1건이 아닙니다.")
    original_index, cost = balcony[0]
    if normalize_unit_type_name(cost.applicable_unit_type) != normalize_unit_type_name(
        result.target_unit.unit_type_name
    ):
        raise CandidateCorrectionError("발코니 확장비의 적용 주택형이 요청 target과 다릅니다.")
    if (
        cost.total_amount_manwon is None
        or not cost.payments
        or any(payment.amount_manwon is None for payment in cost.payments)
    ):
        raise CandidateCorrectionError(
            "발코니 확장비 총액·분납액이 정수 만원으로 확정되지 않았습니다."
        )
    if sum(payment.amount_manwon or 0 for payment in cost.payments) != cost.total_amount_manwon:
        raise CandidateCorrectionError("발코니 확장비 분납 합계가 총액과 다릅니다.")

    field = f"/additional_costs/{original_index}"
    expected = [
        cost.total_amount_manwon * 10_000,
        *(
            payment.amount_manwon * 10_000
            for payment in cost.payments
            if payment.amount_manwon is not None
        ),
    ]
    row_evidence = []
    page_map = {page.number: page for page in pages}
    for item in result.evidence:
        page = page_map.get(item.page)
        if (
            item.field == field
            and page is not None
            and _normalized(item.raw_text) in _normalized(page.text)
            and _normalized(result.target_unit.unit_type_name or "") in _normalized(item.raw_text)
            and _won_values(item.raw_text) == expected
        ):
            row_evidence.append(item)
    if len(row_evidence) != 1:
        raise CandidateCorrectionError("발코니 확장비의 exact target 행 근거가 유일하지 않습니다.")

    removed = [
        item.name for index, item in enumerate(result.additional_costs) if index != original_index
    ]
    cost.required = False
    cost.included_in_sale_price = False
    cost.note = "선택사항 · 공급금액 미포함; 전체 유상옵션 범위는 별도 확인"
    result.additional_costs = [cost]
    result.evidence = [
        item for item in result.evidence if not item.field.startswith("/additional_costs/")
    ]
    row = row_evidence[0].model_copy(update={"field": "/additional_costs/0"})
    result.evidence.extend(
        [
            row,
            _source_evidence(pages, policy.not_included_evidence),
            _source_evidence(pages, policy.optional_evidence),
        ]
    )
    actions = ["GROUND_OPTIONAL_BALCONY_COST"]
    if removed:
        actions.append("EXCLUDE_NON_BALCONY_OPTIONS:" + ",".join(removed))
    return actions


def _repair_sub_manwon_payment_row(result: AnalysisResponse, pages: list[PdfPage]) -> list[str]:
    schedule = result.payment_schedule
    down_count = len(schedule.down_payment.installments)
    interim_count = len(schedule.interim_payment.installments)
    sale_price = result.target_unit.sale_price_manwon
    if sale_price is None or down_count < 1 or interim_count < 1:
        raise CandidateCorrectionError(
            "분양가·납부회차가 없어 만원 미만 금액을 검증할 수 없습니다."
        )
    sale_price_won = sale_price * 10_000
    matches: list[list[int]] = []
    for page in pages:
        for line in page.text.splitlines():
            values = _won_values(line)
            for index, value in enumerate(values):
                payments = values[index + 1 :]
                expected_count = down_count + interim_count + 1
                if (
                    value == sale_price_won
                    and len(payments) == expected_count
                    and sum(payments) == value
                ):
                    matches.append(payments)
    if len(matches) != 1:
        raise CandidateCorrectionError("산술이 닫힌 exact 분양가 행이 유일하지 않습니다.")
    values = matches[0]
    down_values = values[:down_count]
    interim_values = values[down_count : down_count + interim_count]
    balance_value = values[-1]
    if all(value % 10_000 == 0 for value in values):
        return []

    components = (
        (schedule.down_payment, down_values),
        (schedule.interim_payment, interim_values),
    )
    for component, source_values in components:
        component.total_ratio = sum(source_values) / sale_price_won
        component.total_amount_manwon = _exact_manwon(sum(source_values))
        for installment, source_value in zip(component.installments, source_values, strict=True):
            installment.ratio = source_value / sale_price_won
            installment.amount_manwon = _exact_manwon(source_value)
    schedule.balance_payment.total_ratio = balance_value / sale_price_won
    schedule.balance_payment.total_amount_manwon = _exact_manwon(balance_value)
    return ["ABSTAIN_SUB_MANWON_PAYMENT_AMOUNTS"]


def correct_audited_review_candidate(
    result: AnalysisResponse,
    *,
    source_sha256: str,
    pages: list[PdfPage],
) -> tuple[AnalysisResponse, list[str]]:
    """Prepare one of the source-audited tuples without granting review approval."""

    if result.review_status == ReviewStatus.REVIEWED or result.reviewer or result.reviewed_at:
        raise CandidateCorrectionError(
            "REVIEWED 또는 검수자 메타데이터가 있는 파일은 입력할 수 없습니다."
        )
    policy = _policy_for(result)
    if source_sha256 != policy.source_sha256 or len(pages) != policy.source_page_count:
        raise CandidateCorrectionError("실제 PDF가 감사한 source lock과 다릅니다.")

    corrected = result.model_copy(deep=True)
    corrected_a = repair_expanded_audited_candidate_a(corrected, pages=pages)
    corrected_allocation = (
        corrected_a
        if corrected_a is not corrected
        else repair_expanded_audited_allocation(corrected, pages=pages)
    )
    corrected_b = (
        corrected_allocation
        if corrected_allocation is not corrected
        else repair_expanded_audited_candidate_b(corrected, pages=pages)
    )
    if corrected_b is corrected:
        actions, handles_additional_costs = apply_audited_repairs(corrected, pages=pages)
        expanded_actions, expanded_handles_costs = apply_expanded_audited_repairs(
            corrected,
            pages=pages,
        )
        actions.extend(expanded_actions)
        handles_additional_costs = handles_additional_costs or expanded_handles_costs
    else:
        if corrected_a is not corrected:
            action = "GROUND_EXPANDED_A_EXACT_SOURCE_FACTS"
        elif corrected_allocation is not corrected:
            action = "GROUND_EXACT_INSTALLMENT_ALLOCATION"
        else:
            action = "GROUND_EXPANDED_B_EXACT_SOURCE_FACTS"
        actions = [action]
        corrected = corrected_b
        handles_additional_costs = True
    if not handles_additional_costs:
        actions.extend(_ground_balcony_cost(corrected, pages, policy))
    if policy.repair_sub_manwon_payment_row:
        actions.extend(_repair_sub_manwon_payment_row(corrected, pages))
    # Custom repair modules source-check and prepare their own output. Run the
    # shared canonical preparation once more anyway so every handler leaves the
    # same stable deterministic envelope (notably ``derived_fields``). A final
    # repeat must be equivalent at the model level.
    prepared = prepare_review_draft(
        corrected,
        source_sha256=source_sha256,
        pages=pages,
    )
    repeated = prepare_review_draft(
        prepared,
        source_sha256=source_sha256,
        pages=pages,
    )
    if prepared.model_dump(mode="json") != repeated.model_dump(mode="json"):
        raise CandidateCorrectionError("교정 후 canonical 재검증이 멱등이 아닙니다.")
    if not prepared.validation.passed or prepared.review_status != ReviewStatus.AUTO_EXTRACTED:
        target = prepared.target_unit
        key = (
            f"{prepared.complex_id}/{target.unit_type_id or '-'}"
            f"/{target.unit_type_name or '-'}/{target.sale_price_manwon or '-'}"
        )
        issue_codes = ",".join(issue.code for issue in prepared.validation.issues) or "none"
        raise CandidateCorrectionError(
            f"{key}: 교정 후 출처 재검증을 통과한 AUTO_EXTRACTED 초안이 "
            f"아닙니다 (review_status={prepared.review_status.value}, "
            f"validation_passed={prepared.validation.passed}, issues={issue_codes})."
        )
    if prepared.reviewer is not None or prepared.reviewed_at is not None:
        raise AssertionError("교정 유틸리티가 검수자 메타데이터를 생성했습니다.")
    if ExceptionFlag.ADDITIONAL_COST_SCOPE_LIMITED not in prepared.exception_flags:
        raise CandidateCorrectionError("비발코니 유상옵션 범위 제한 근거를 확인하지 못했습니다.")
    if not any(
        hold.reason_code == HoldReasonCode.ADDITIONAL_COST_SCOPE_LIMITED and hold.blocking is False
        for hold in prepared.holds
    ):
        raise CandidateCorrectionError("비발코니 유상옵션 범위 제한 HOLD를 확인하지 못했습니다.")
    return prepared, actions


def _entry_key(entry: ReviewDraftEntry) -> tuple[str, str, str, int]:
    return (
        entry.target.complex_id,
        entry.target.unit_type_id,
        entry.target.normalized_unit_type_name,
        entry.target.sale_price_manwon,
    )


def _audited_keys() -> set[tuple[str, str, str, int]]:
    return {
        (complex_id, unit_id, unit_name, sale_price)
        for complex_id, policy in _AUDITED_DOCUMENTS.items()
        for unit_id, unit_name, sale_price in policy.targets
    }


def _copy_review_workspace(
    *,
    manifest_path: Path,
    manifest: ReviewDraftBatchManifest,
    stage: Path,
) -> None:
    """Copy the complete manifest-addressable workspace without following paths out."""

    source_root = manifest_path.parent
    for entry in manifest.drafts:
        for relative_value in (entry.draft_path, entry.checklist_path):
            source = _resolve_relative(source_root, relative_value)
            if not source.is_file():
                raise CandidateCorrectionError(
                    f"{entry.draft_id}: 복제할 검수 워크스페이스 파일이 없습니다."
                )
            destination = _resolve_relative(stage, relative_value)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    source_template = source_root / "review-approval-manifest.template.json"
    if not source_template.is_file():
        raise CandidateCorrectionError("원본 승인 template이 없습니다.")
    template = load_review_approval_manifest(source_template)
    if template.draft_batch_id != manifest.batch_id:
        raise CandidateCorrectionError("원본 승인 template의 batch_id가 다릅니다.")
    if {item.draft_id for item in template.items} != {entry.draft_id for entry in manifest.drafts}:
        raise CandidateCorrectionError("원본 승인 template의 항목 집합이 다릅니다.")

    # Keep the immutable batch manifest byte-for-byte identical. Draft edits are
    # attested by the approval manifest's per-file SHA-256, as in manual review.
    shutil.copy2(manifest_path, stage / "review-draft-manifest.json")


def _write_pending_approval_template(*, manifest: ReviewDraftBatchManifest, stage: Path) -> None:
    manifest_path = stage / "review-draft-manifest.json"
    template = ReviewApprovalManifest(
        draft_batch_id=manifest.batch_id,
        draft_manifest_sha256=_sha256_file(manifest_path),
        reviewer=None,
        reviewed_at=None,
        attestation=None,
        items=[
            ReviewApprovalItem(
                draft_id=entry.draft_id,
                decision=ApprovalDecision.PENDING,
                draft_sha256=_sha256_file(_resolve_relative(stage, entry.draft_path)),
                checks=ReviewApprovalChecks(),
                notes=None,
            )
            for entry in manifest.drafts
        ],
    )
    (stage / "review-approval-manifest.template.json").write_text(
        template.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def prepare_audited_review_candidates(
    *,
    draft_manifest_path: Path,
    output_dir: Path,
    settings: Settings,
    pdf_loader: PdfLoader = load_pdf_from_path,
    page_extractor: PageExtractor = extract_pdf_pages,
) -> dict[str, Any]:
    """Atomically copy a full review workspace and correct source-audited PENDING drafts."""

    draft_manifest_path = draft_manifest_path.resolve()
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
        raise CandidateCorrectionError(
            "현재 서비스 버전으로 새로 준비한 review batch가 필요합니다."
        )
    entry_keys = [_entry_key(entry) for entry in manifest.drafts]
    if len(entry_keys) != len(set(entry_keys)):
        raise CandidateCorrectionError("검수 batch에 중복 target이 있습니다.")
    entries = dict(zip(entry_keys, manifest.drafts, strict=True))
    wanted = _audited_keys()
    missing = sorted(wanted - set(entries))
    if missing or len(wanted) != AUDITED_CANDIDATE_COUNT:
        raise CandidateCorrectionError(
            f"감사 target 후보가 배치에 정확히 존재하지 않습니다: {missing!r}"
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
        for key in sorted(wanted):
            entry = entries[key]
            if entry.approval_blockers:
                raise CandidateCorrectionError(
                    f"{entry.draft_id}: 승인 차단 사유가 있는 초안은 교정할 수 없습니다."
                )
            if (
                entry.schema_version != settings.schema_version
                or entry.extractor_version != settings.extractor_version
            ):
                raise CandidateCorrectionError(
                    f"{entry.draft_id}: 현재 스키마·추출기 버전의 초안이 아닙니다."
                )
            draft_path = _resolve_relative(draft_manifest_path.parent, entry.draft_path)
            if _sha256_file(draft_path) != entry.draft_sha256:
                raise CandidateCorrectionError(f"{entry.draft_id}: 입력 초안 SHA-256이 다릅니다.")
            result = load_result(draft_path)
            if (
                result.meta.schema_version != settings.schema_version
                or result.meta.extractor_version != settings.extractor_version
            ):
                raise CandidateCorrectionError(
                    f"{entry.draft_id}: 초안 본문의 스키마·추출기 버전이 현재 설정과 다릅니다."
                )
            if (
                result.complex_id != entry.target.complex_id
                or result.target_unit.unit_type_id != entry.target.unit_type_id
                or result.target_unit.unit_type_name != entry.target.normalized_unit_type_name
                or result.target_unit.sale_price_manwon != entry.target.sale_price_manwon
            ):
                raise CandidateCorrectionError(
                    f"{entry.draft_id}: 초안 target이 매니페스트와 다릅니다."
                )
            pdf_path = Path(entry.source_pdf_path).resolve()
            if pdf_path not in pdf_cache:
                downloaded = pdf_loader(str(pdf_path), settings)
                pages = page_extractor(downloaded.content, settings)
                pdf_cache[pdf_path] = downloaded, pages
            downloaded, pages = pdf_cache[pdf_path]
            if downloaded.sha256 != entry.source_sha256 or len(pages) != entry.source_page_count:
                raise CandidateCorrectionError(f"{entry.draft_id}: PDF source lock이 다릅니다.")

            corrected, actions = correct_audited_review_candidate(
                result,
                source_sha256=downloaded.sha256,
                pages=pages,
            )
            relative_draft = Path(entry.draft_path)
            relative_checklist = Path(entry.checklist_path)
            destination = _resolve_relative(stage, entry.draft_path)
            save_result(corrected, destination)
            write_review_sheet(
                corrected,
                _resolve_relative(stage, entry.checklist_path),
                preface=[
                    "> 이 파일은 자동 교정된 `REVIEW_DRAFT`이며 "
                    "사람 승인 전에는 `REVIEWED`가 아닙니다.",
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
                    "input_draft_sha256": entry.draft_sha256,
                    "output_draft_path": relative_draft.as_posix(),
                    "output_draft_sha256": _sha256_file(destination),
                    "checklist_path": relative_checklist.as_posix(),
                    "review_status": corrected.review_status.value,
                    "reviewer": None,
                    "reviewed_at": None,
                    "validation_passed": corrected.validation.passed,
                    "corrections": actions,
                }
            )
        _write_pending_approval_template(manifest=manifest, stage=stage)
        payload: dict[str, Any] = {
            "schema_version": "review_candidate_correction_v1",
            "artifact_type": "REVIEW_CANDIDATE_CORRECTION_BATCH",
            "created_at": datetime.now(UTC).isoformat(),
            "source_draft_manifest_path": str(draft_manifest_path),
            "source_draft_manifest_sha256": _sha256_file(draft_manifest_path),
            "candidate_count": len(records),
            "workspace_draft_count": len(manifest.drafts),
            "approval_state": "PENDING",
            "reviewer": None,
            "reviewed_at": None,
            "candidates": records,
        }
        (stage / "review-candidate-correction-manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(stage, output_dir)
        return payload
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
