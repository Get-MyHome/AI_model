from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from get_myhome_ai.models import AnalysisResponse, ReviewStatus
from get_myhome_ai.normalization import normalize_unit_type_name
from get_myhome_ai.pipeline import AnalysisPipeline
from get_myhome_ai.review import save_result, write_review_sheet
from get_myhome_ai.settings import Settings

INVENTORY_SCHEMA_VERSION = "owned_corpus_inventory_v1"
RUN_REPORT_SCHEMA_VERSION = "owned_corpus_extraction_run_v1"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class OwnedCorpusExtractionError(ValueError):
    """Raised when an exact extraction job cannot be built safely."""


@dataclass(frozen=True)
class ExtractionJob:
    complex_id: str
    unit_type_id: str
    unit_type_name: str
    normalized_unit_type_name: str
    sale_price_manwon: int
    pdf_path: Path
    source_sha256: str
    source_page_count: int

    @property
    def key(self) -> str:
        unit_id = re.sub(r"[^0-9A-Za-z_.-]+", "_", self.unit_type_id).strip("._")
        unit_name = re.sub(
            r"[^0-9A-Za-z_.-]+",
            "_",
            self.normalized_unit_type_name,
        ).strip("._")
        return (
            f"{self.complex_id}__{unit_id or 'unit'}__{unit_name or 'name'}__"
            f"{self.sale_price_manwon}"
        )


Analyzer = Callable[[ExtractionJob], Awaitable[AnalysisResponse]]


def _read_json_object(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OwnedCorpusExtractionError(f"JSON을 읽을 수 없습니다: {path}") from exc
    if not isinstance(value, dict):
        raise OwnedCorpusExtractionError(f"JSON 최상위는 객체여야 합니다: {path}")
    return value


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OwnedCorpusExtractionError(f"{field}는 빈 문자열이 아닌 값이어야 합니다.")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_extraction_jobs(
    inventory_path: Path,
    *,
    selected_complex_ids: set[str] | None = None,
) -> list[ExtractionJob]:
    """Load every exact PDF-backed target and re-check its source hash."""

    inventory_path = inventory_path.expanduser().resolve(strict=True)
    payload = _read_json_object(inventory_path)
    if payload.get("schema_version") != INVENTORY_SCHEMA_VERSION:
        raise OwnedCorpusExtractionError(
            f"schema_version은 {INVENTORY_SCHEMA_VERSION}이어야 합니다."
        )
    source_directory = Path(
        _required_string(payload.get("source_directory"), "source_directory")
    )
    if not source_directory.is_absolute():
        source_directory = inventory_path.parent / source_directory
    source_directory = source_directory.expanduser().resolve(strict=True)
    if not source_directory.is_dir():
        raise OwnedCorpusExtractionError("source_directory가 디렉터리가 아닙니다.")

    targets = payload.get("targets")
    if not isinstance(targets, list):
        raise OwnedCorpusExtractionError("targets는 배열이어야 합니다.")

    jobs: list[ExtractionJob] = []
    seen: set[tuple[str, str, str, int]] = set()
    source_hash_cache: dict[Path, str] = {}
    for index, raw in enumerate(targets):
        if not isinstance(raw, dict):
            raise OwnedCorpusExtractionError(f"targets[{index}]는 객체여야 합니다.")
        complex_id = _required_string(raw.get("complex_id"), f"targets[{index}].complex_id")
        if selected_complex_ids is not None and complex_id not in selected_complex_ids:
            continue
        if raw.get("pdf_available") is not True:
            continue
        unit_type_id = _required_string(
            raw.get("unit_type_id"), f"targets[{index}].unit_type_id"
        )
        unit_type_name = _required_string(
            raw.get("unit_type_name"), f"targets[{index}].unit_type_name"
        )
        normalized_name = normalize_unit_type_name(unit_type_name)
        if normalized_name is None:
            raise OwnedCorpusExtractionError(
                f"targets[{index}].unit_type_name을 정규화할 수 없습니다."
            )
        price = raw.get("sale_price_manwon")
        if isinstance(price, bool) or not isinstance(price, int) or price < 0:
            raise OwnedCorpusExtractionError(
                f"targets[{index}].sale_price_manwon은 0 이상 정수여야 합니다."
            )
        relative_pdf = Path(
            _required_string(raw.get("pdf_path"), f"targets[{index}].pdf_path")
        )
        if relative_pdf.is_absolute():
            raise OwnedCorpusExtractionError(f"targets[{index}].pdf_path는 상대경로여야 합니다.")
        pdf_path = (source_directory / relative_pdf).resolve(strict=True)
        if not pdf_path.is_relative_to(source_directory) or not pdf_path.is_file():
            raise OwnedCorpusExtractionError(
                f"targets[{index}].pdf_path가 source_directory의 파일이 아닙니다."
            )
        source_sha256 = _required_string(
            raw.get("source_sha256"), f"targets[{index}].source_sha256"
        )
        if SHA256_PATTERN.fullmatch(source_sha256) is None:
            raise OwnedCorpusExtractionError(f"targets[{index}].source_sha256 형식이 잘못됐습니다.")
        if pdf_path not in source_hash_cache:
            source_hash_cache[pdf_path] = _sha256_file(pdf_path)
        actual_sha256 = source_hash_cache[pdf_path]
        if actual_sha256 != source_sha256:
            raise OwnedCorpusExtractionError(f"{pdf_path.name}: 인벤토리 source SHA-256 불일치")
        page_count = raw.get("source_page_count")
        if isinstance(page_count, bool) or not isinstance(page_count, int) or page_count < 1:
            raise OwnedCorpusExtractionError(
                f"targets[{index}].source_page_count는 양의 정수여야 합니다."
            )

        identity = (complex_id, unit_type_id, normalized_name, price)
        if identity in seen:
            raise OwnedCorpusExtractionError(f"중복 exact target: {identity!r}")
        seen.add(identity)
        jobs.append(
            ExtractionJob(
                complex_id=complex_id,
                unit_type_id=unit_type_id,
                unit_type_name=unit_type_name,
                normalized_unit_type_name=normalized_name,
                sale_price_manwon=price,
                pdf_path=pdf_path,
                source_sha256=source_sha256,
                source_page_count=page_count,
            )
        )

    jobs.sort(
        key=lambda item: (
            item.complex_id,
            item.unit_type_id,
            item.unit_type_name,
            item.sale_price_manwon,
        )
    )
    if selected_complex_ids is not None:
        present = {job.complex_id for job in jobs}
        missing = selected_complex_ids - present
        if missing:
            raise OwnedCorpusExtractionError(
                "PDF-backed target이 없는 complex_id: " + ", ".join(sorted(missing))
            )
    return jobs


def _is_current_complete(path: Path, job: ExtractionJob, settings: Settings) -> bool:
    if not path.is_file():
        return False
    try:
        result = AnalysisResponse.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    target = result.target_unit
    return (
        result.review_status != ReviewStatus.REVIEWED
        and result.complex_id == job.complex_id
        and target.unit_type_id == job.unit_type_id
        and target.unit_type_name == job.normalized_unit_type_name
        and target.sale_price_manwon == job.sale_price_manwon
        and result.meta.source_sha256 == job.source_sha256
        and result.meta.source_page_count == job.source_page_count
        and result.meta.schema_version == settings.schema_version
        and result.meta.extractor_version == settings.extractor_version
    )


def _existing_review_status(path: Path) -> ReviewStatus | None:
    if not path.is_file():
        return None
    try:
        result = AnalysisResponse.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return result.review_status


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temporary:
        temporary.write(payload)
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


async def run_extraction_jobs(
    *,
    jobs: Sequence[ExtractionJob],
    output_dir: Path,
    settings: Settings,
    analyzer: Analyzer,
    force: bool = False,
) -> dict[str, Any]:
    """Run exact-target extraction sequentially with resumable atomic reports."""

    output_dir = output_dir.expanduser().absolute()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "run-report.json"
    report: dict[str, Any] = {
        "schema_version": RUN_REPORT_SCHEMA_VERSION,
        "artifact_type": "AUTO_EXTRACTION_BATCH",
        "warning": "자동 추출 결과입니다. 사람 검수 전에는 REVIEWED로 사용할 수 없습니다.",
        "started_at": datetime.now(UTC).isoformat(),
        "requested_target_count": len(jobs),
        "completed": [],
        "skipped": [],
        "failed": [],
    }
    report["run_state"] = "RUNNING"
    try:
        for index, job in enumerate(jobs, start=1):
            destination = output_dir / f"{job.key}.json"
            review_sheet = output_dir / f"{job.key}.review.md"
            if not force and _is_current_complete(destination, job, settings):
                report["skipped"].append(job.key)
                print(f"[{index}/{len(jobs)}] SKIP {job.key}", flush=True)
                _write_json_atomic(report_path, report)
                continue

            started = time.monotonic()
            print(f"[{index}/{len(jobs)}] START {job.key}", flush=True)
            try:
                existing_status = _existing_review_status(destination)
                if destination.exists() and existing_status is None:
                    raise OwnedCorpusExtractionError(
                        "기존 artifact가 유효한 AnalysisResponse가 아니므로 덮어쓰지 않습니다."
                    )
                if existing_status == ReviewStatus.REVIEWED:
                    raise OwnedCorpusExtractionError(
                        "기존 REVIEWED artifact는 자동 배치가 덮어쓸 수 없습니다."
                    )
                if destination.exists() and not force:
                    raise OwnedCorpusExtractionError(
                        "기존 artifact가 현재 source/target/version과 일치하지 않습니다. "
                        "확인 후 --force로 자동 결과만 교체하세요."
                    )
                async with asyncio.timeout(settings.analysis_timeout_seconds):
                    result = await analyzer(job)
                if result.review_status == ReviewStatus.REVIEWED:
                    raise OwnedCorpusExtractionError(
                        "자동 배치가 REVIEWED 결과를 반환했습니다."
                    )
                if not _matches_job(result, job, settings):
                    raise OwnedCorpusExtractionError(
                        "자동 추출 결과의 source/target/version lock 불일치"
                    )
                save_result(result, destination)
                write_review_sheet(result, review_sheet)
                elapsed = round(time.monotonic() - started, 3)
                report["completed"].append(
                    {
                        "key": job.key,
                        "elapsed_seconds": elapsed,
                        "analysis_status": result.analysis_status,
                        "review_status": result.review_status,
                        "validation_passed": result.validation.passed,
                    }
                )
                print(
                    f"[{index}/{len(jobs)}] DONE {job.key} {elapsed:.1f}s "
                    f"validation={result.validation.passed}",
                    flush=True,
                )
            except Exception as error:  # One bad PDF must not hide corpus coverage.
                elapsed = round(time.monotonic() - started, 3)
                report["failed"].append(
                    {
                        "key": job.key,
                        "elapsed_seconds": elapsed,
                        "error_type": type(error).__name__,
                        "message": str(error)[:500],
                    }
                )
                print(
                    f"[{index}/{len(jobs)}] FAIL {job.key} {type(error).__name__}: {error}",
                    flush=True,
                )
            _write_json_atomic(report_path, report)
    except BaseException:
        report["run_state"] = "INTERRUPTED"
        raise
    else:
        report["run_state"] = "COMPLETED"
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["completed_target_count"] = len(report["completed"])
        report["skipped_target_count"] = len(report["skipped"])
        report["failed_target_count"] = len(report["failed"])
        report["validation_failed_target_count"] = sum(
            item["validation_passed"] is False for item in report["completed"]
        )
        _write_json_atomic(report_path, report)
    return report


def _matches_job(
    result: AnalysisResponse,
    job: ExtractionJob,
    settings: Settings,
) -> bool:
    target = result.target_unit
    return (
        result.complex_id == job.complex_id
        and target.unit_type_id == job.unit_type_id
        and target.unit_type_name == job.normalized_unit_type_name
        and target.sale_price_manwon == job.sale_price_manwon
        and result.meta.source_sha256 == job.source_sha256
        and result.meta.source_page_count == job.source_page_count
        and result.meta.schema_version == settings.schema_version
        and result.meta.extractor_version == settings.extractor_version
    )


async def run_owned_corpus_extraction(
    *,
    inventory_path: Path,
    output_dir: Path,
    settings: Settings,
    pipeline: AnalysisPipeline,
    selected_complex_ids: set[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    jobs = load_extraction_jobs(
        inventory_path,
        selected_complex_ids=selected_complex_ids,
    )

    async def analyze(job: ExtractionJob) -> AnalysisResponse:
        return await pipeline.analyze_file(
            complex_id=job.complex_id,
            path=str(job.pdf_path),
            unit_type_id=job.unit_type_id,
            unit_type_name=job.unit_type_name,
            sale_price_manwon=job.sale_price_manwon,
        )

    return await run_extraction_jobs(
        jobs=jobs,
        output_dir=output_dir,
        settings=settings,
        analyzer=analyze,
        force=force,
    )
