from __future__ import annotations

import re
from pathlib import Path

from get_myhome_ai.models import AnalysisResponse, AnalyzeRequest, ReviewStatus
from get_myhome_ai.normalization import normalize_unit_type_name
from get_myhome_ai.review import load_result

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _matches_request(candidate: AnalysisResponse, request: AnalyzeRequest) -> bool:
    target = candidate.target_unit
    return (
        candidate.complex_id == request.complex_id
        and target.unit_type_id == request.unit_type_id
        and target.sale_price_manwon == request.sale_price_manwon
        and target.unit_type_name == normalize_unit_type_name(request.unit_type_name)
    )


def find_reviewed_artifact(
    *,
    request: AnalyzeRequest,
    source_sha256: str,
    reviewed_artifact_dir: Path,
    schema_version: str,
    extractor_version: str,
) -> AnalysisResponse | None:
    """Find an immutable human review before invoking the extraction model.

    A complex ID alone is not an identity. The PDF digest, target unit ID and
    sale price all have to match. This prevents stale or cross-unit reviews
    from silently entering the backend funding calculation.
    """

    if not SHA256_PATTERN.fullmatch(source_sha256):
        return None
    if not reviewed_artifact_dir.is_dir():
        return None

    matches: list[AnalysisResponse] = []
    for path in reviewed_artifact_dir.glob("*.json"):
        try:
            candidate = load_result(path)
        except (OSError, ValueError):
            continue
        if candidate.review_status != ReviewStatus.REVIEWED:
            continue
        if not candidate.reviewer or not candidate.reviewer.strip():
            continue
        if candidate.reviewed_at is None:
            continue
        if not candidate.validation.passed:
            continue
        if candidate.meta.schema_version != schema_version:
            continue
        if candidate.meta.extractor_version != extractor_version:
            continue
        if candidate.meta.source_sha256 != source_sha256:
            continue
        if not SHA256_PATTERN.fullmatch(candidate.meta.source_sha256):
            continue
        if not _matches_request(candidate, request):
            continue
        matches.append(candidate)

    if not matches:
        return None
    return max(matches, key=lambda item: item.reviewed_at)
