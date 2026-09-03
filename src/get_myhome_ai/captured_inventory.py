from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from get_myhome_ai.models import AnalysisResponse, ReviewStatus
from get_myhome_ai.normalization import normalize_unit_type_name

CAPTURE_SCHEMA_VERSION = "review_capture_v0.1"
INVENTORY_SCHEMA_VERSION = "owned_corpus_inventory_v1"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class CapturedInventoryError(ValueError):
    """Raised when a review capture cannot be source-locked safely."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CapturedInventoryError(f"JSON을 읽을 수 없습니다: {path}") from exc
    if not isinstance(value, dict):
        raise CapturedInventoryError(f"JSON 최상위는 객체여야 합니다: {path}")
    return value


def _required_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CapturedInventoryError(f"{field}는 빈 문자열이 아닌 값이어야 합니다.")
    return value.strip()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temporary:
        temporary.write(encoded)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def _matching_auto_results(auto_dir: Path, request_key: str) -> list[AnalysisResponse]:
    results: list[AnalysisResponse] = []
    for path in sorted(auto_dir.glob(f"{request_key}__*.json")):
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise CapturedInventoryError(f"자동결과를 읽을 수 없습니다: {path}") from exc
        expected_result_sha256 = path.stem.removeprefix(f"{request_key}__")
        if (
            SHA256_PATTERN.fullmatch(expected_result_sha256) is None
            or hashlib.sha256(raw).hexdigest() != expected_result_sha256
        ):
            raise CapturedInventoryError(f"자동결과 파일 SHA-256이 다릅니다: {path}")
        try:
            result = AnalysisResponse.model_validate_json(raw)
        except ValueError as exc:
            raise CapturedInventoryError(f"자동결과를 읽을 수 없습니다: {path}") from exc
        if result.review_status == ReviewStatus.REVIEWED:
            raise CapturedInventoryError(f"캡처 auto 디렉터리에 REVIEWED가 있습니다: {path}")
        results.append(result)
    if not results:
        raise CapturedInventoryError(f"request_key={request_key}: 자동결과가 없습니다.")
    return results


def build_captured_inventory(*, capture_dir: Path, output_path: Path) -> dict[str, Any]:
    """Build an exact review inventory from URL-free production captures."""

    capture_dir = capture_dir.expanduser().resolve(strict=True)
    request_dir = capture_dir / "requests"
    source_dir = (capture_dir / "sources").resolve(strict=True)
    auto_dir = capture_dir / "auto"
    if not request_dir.is_dir() or not auto_dir.is_dir():
        raise CapturedInventoryError("capture_dir에 requests, sources, auto가 모두 필요합니다.")

    targets: list[dict[str, Any]] = []
    seen: dict[tuple[str, str, str, int], str] = {}
    for request_path in sorted(request_dir.glob("*.json")):
        request = _read_object(request_path)
        if request.get("schema_version") != CAPTURE_SCHEMA_VERSION:
            raise CapturedInventoryError(f"지원하지 않는 캡처 스키마입니다: {request_path}")
        if "pdf_url" in request:
            raise CapturedInventoryError(f"캡처 메타데이터에 pdf_url이 있습니다: {request_path}")

        request_key = _required_string(request.get("request_key"), field="request_key")
        if request_path.stem != request_key:
            raise CapturedInventoryError(f"request_key와 파일명이 다릅니다: {request_path}")
        complex_id = _required_string(request.get("complex_id"), field="complex_id")
        unit_type_id = _required_string(request.get("unit_type_id"), field="unit_type_id")
        unit_type_name = _required_string(request.get("unit_type_name"), field="unit_type_name")
        sale_price = request.get("sale_price_manwon")
        if isinstance(sale_price, bool) or not isinstance(sale_price, int) or sale_price < 0:
            raise CapturedInventoryError("sale_price_manwon은 0 이상 정수여야 합니다.")
        source_sha256 = _required_string(
            request.get("source_sha256"), field="source_sha256"
        )
        if SHA256_PATTERN.fullmatch(source_sha256) is None:
            raise CapturedInventoryError("source_sha256 형식이 잘못됐습니다.")

        source_path = (source_dir / f"{source_sha256}.pdf").resolve(strict=True)
        if not source_path.is_relative_to(source_dir) or not source_path.is_file():
            raise CapturedInventoryError("캡처 PDF가 sources 디렉터리에 없습니다.")
        if _sha256_file(source_path) != source_sha256:
            raise CapturedInventoryError(f"캡처 PDF SHA-256이 다릅니다: {source_path.name}")

        results = _matching_auto_results(auto_dir, request_key)
        page_counts: set[int] = set()
        for result in results:
            target = result.target_unit
            if (
                result.complex_id != complex_id
                or target.unit_type_id != unit_type_id
                or target.unit_type_name != normalize_unit_type_name(unit_type_name)
                or target.sale_price_manwon != sale_price
                or result.meta.source_sha256 != source_sha256
            ):
                raise CapturedInventoryError(
                    f"요청과 자동결과 exact identity가 다릅니다: {request_path.name}"
                )
            page_counts.add(result.meta.source_page_count)
        if len(page_counts) != 1:
            raise CapturedInventoryError(
                f"request_key={request_key}: 자동결과 페이지 수가 충돌합니다."
            )

        identity = (complex_id, unit_type_id, unit_type_name, sale_price)
        previous_source = seen.get(identity)
        if previous_source is not None and previous_source != source_sha256:
            raise CapturedInventoryError(
                f"동일 target에 서로 다른 PDF가 캡처됐습니다: {identity!r}"
            )
        if previous_source is not None:
            continue
        seen[identity] = source_sha256
        targets.append(
            {
                "complex_id": complex_id,
                "unit_type_id": unit_type_id,
                "unit_type_name": unit_type_name,
                "sale_price_manwon": sale_price,
                "pdf_available": True,
                "pdf_path": source_path.name,
                "source_sha256": source_sha256,
                "source_page_count": page_counts.pop(),
            }
        )

    if not targets:
        raise CapturedInventoryError("완전한 exact 검수 캡처가 없습니다.")
    targets.sort(
        key=lambda item: (
            item["complex_id"],
            item["unit_type_id"],
            item["unit_type_name"],
            item["sale_price_manwon"],
        )
    )
    payload: dict[str, Any] = {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_directory": str(source_dir),
        "summary": {
            "captured_target_tuple_count": len(targets),
            "pdf_document_count": len({item["source_sha256"] for item in targets}),
        },
        "targets": targets,
    }
    _write_json_atomic(output_path.expanduser().absolute(), payload)
    return payload
