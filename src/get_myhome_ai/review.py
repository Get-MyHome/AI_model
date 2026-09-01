from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from get_myhome_ai.models import AnalysisResponse, ReviewStatus


def save_result(result: AnalysisResponse, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = result.model_dump_json(indent=2)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        delete=False,
    ) as temporary:
        temporary.write(payload)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, destination)


def load_result(path: Path) -> AnalysisResponse:
    return AnalysisResponse.model_validate_json(path.read_text(encoding="utf-8"))


def approve_result(result: AnalysisResponse, *, reviewer: str) -> AnalysisResponse:
    if not result.validation.passed:
        raise ValueError("고정 검증에 실패한 결과는 승인할 수 없습니다.")
    approved = result.model_copy(deep=True)
    approved.review_status = ReviewStatus.REVIEWED
    approved.reviewer = reviewer
    approved.reviewed_at = datetime.now(UTC)
    return approved


def write_review_sheet(result: AnalysisResponse, destination: Path) -> None:
    """Write a human-readable checklist without changing the machine result."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# 공고문 AI 추출 검수표 — {result.complex_id}",
        "",
        f"- 분석 상태: `{result.analysis_status}`",
        f"- 자동 검증: `{'PASS' if result.validation.passed else 'FAIL'}`",
        f"- PDF SHA-256: `{result.meta.source_sha256}`",
        f"- 물리 페이지 수: `{result.meta.source_page_count}`",
        f"- 후보 페이지: `{', '.join(map(str, result.meta.candidate_pages))}`",
        "",
        "## 검수 절차",
        "",
        "- [ ] 아래 추출값을 선택 주택형·동·층과 대조했다.",
        "- [ ] 각 근거 문장이 표시된 물리 PDF 페이지에 존재한다.",
        "- [ ] 선택비용과 분양가 포함비용을 기본 필요자금에 더하지 않았다.",
        "- [ ] HOLD 질문과 다음 행동이 실제 불확실성과 맞는다.",
        "",
        "## 고정 요약",
        "",
        result.analysis_summary,
        "",
        "## HOLD",
        "",
    ]
    if result.holds:
        for hold in result.holds:
            lines.append(f"- `{hold.reason_code}` — {hold.message} 다음 행동: {hold.next_action}")
    else:
        lines.append("- 없음")
    lines.extend(["", "## 근거", ""])
    for evidence in result.evidence:
        lines.append(f"- `{evidence.field}` / p.{evidence.page}: {evidence.raw_text}")
    lines.extend(["", "## 자동 검증 이슈", ""])
    if result.validation.issues:
        for issue in result.validation.issues:
            lines.append(
                f"- `{issue.severity}:{issue.code}` `{issue.field or '-'}` — {issue.message}"
            )
    else:
        lines.append("- 없음")
    payload = "\n".join(lines) + "\n"
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
