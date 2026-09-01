from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from get_myhome_ai.models import AnalysisResponse
from get_myhome_ai.pipeline import AnalysisPipeline
from get_myhome_ai.providers.factory import create_provider
from get_myhome_ai.review import save_result
from get_myhome_ai.settings import Settings


@dataclass(frozen=True)
class BatchJob:
    complex_id: str
    pdf_path: Path
    pdf_sha256: str


def load_jobs(
    reference_dir: Path,
    source_dir: Path,
    selected_ids: set[str] | None = None,
) -> list[BatchJob]:
    jobs: list[BatchJob] = []
    for reference_path in sorted(reference_dir.glob("*.json")):
        reference = json.loads(reference_path.read_text(encoding="utf-8"))
        complex_id = reference["complex_id"]
        if selected_ids is not None and complex_id not in selected_ids:
            continue
        source = reference["source"]
        pdf_path = source_dir / source["pdf_filename"]
        if not pdf_path.is_file():
            raise FileNotFoundError(f"locked source PDF is missing: {pdf_path}")
        digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        if digest != source["pdf_sha256"]:
            raise ValueError(f"{complex_id}: locked source PDF SHA-256 mismatch")
        jobs.append(
            BatchJob(
                complex_id=complex_id,
                pdf_path=pdf_path,
                pdf_sha256=digest,
            )
        )
    if selected_ids is not None:
        missing = selected_ids - {job.complex_id for job in jobs}
        if missing:
            raise ValueError(f"unknown complex ids: {', '.join(sorted(missing))}")
    return jobs


def _is_complete(path: Path, job: BatchJob) -> bool:
    if not path.is_file():
        return False
    try:
        result = AnalysisResponse.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return result.complex_id == job.complex_id and result.meta.source_sha256 == job.pdf_sha256


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


async def run_batch(
    *,
    jobs: list[BatchJob],
    output_dir: Path,
    settings: Settings,
    force: bool,
) -> dict[str, Any]:
    pipeline = AnalysisPipeline(settings=settings, provider=create_provider(settings))
    report: dict[str, Any] = {
        "schema_version": "model_batch_run_v0.1",
        "provider": pipeline.provider.name,
        "model": pipeline.provider.model_name,
        "started_at": datetime.now(UTC).isoformat(),
        "requested_document_count": len(jobs),
        "completed": [],
        "skipped": [],
        "failed": [],
    }
    report_path = output_dir / "run-report.json"
    output_dir.mkdir(parents=True, exist_ok=True)

    for index, job in enumerate(jobs, start=1):
        destination = output_dir / f"{job.complex_id}.json"
        if not force and _is_complete(destination, job):
            report["skipped"].append(job.complex_id)
            print(f"[{index}/{len(jobs)}] SKIP {job.complex_id}", flush=True)
            _write_json_atomic(report_path, report)
            continue

        started = time.monotonic()
        print(f"[{index}/{len(jobs)}] START {job.complex_id}", flush=True)
        try:
            async with asyncio.timeout(settings.analysis_timeout_seconds):
                result = await pipeline.analyze_file(
                    complex_id=job.complex_id,
                    path=str(job.pdf_path),
                )
            save_result(result, destination)
            elapsed = round(time.monotonic() - started, 3)
            report["completed"].append(
                {
                    "complex_id": job.complex_id,
                    "elapsed_seconds": elapsed,
                    "analysis_status": result.analysis_status,
                    "review_status": result.review_status,
                    "validation_passed": result.validation.passed,
                }
            )
            print(
                f"[{index}/{len(jobs)}] DONE {job.complex_id} "
                f"{elapsed:.1f}s validation={result.validation.passed}",
                flush=True,
            )
        except Exception as error:  # Continue so one malformed PDF cannot hide the rest.
            elapsed = round(time.monotonic() - started, 3)
            report["failed"].append(
                {
                    "complex_id": job.complex_id,
                    "elapsed_seconds": elapsed,
                    "error_type": type(error).__name__,
                    "message": str(error)[:500],
                }
            )
            print(
                f"[{index}/{len(jobs)}] FAIL {job.complex_id} {type(error).__name__}: {error}",
                flush=True,
            )
        _write_json_atomic(report_path, report)

    report["finished_at"] = datetime.now(UTC).isoformat()
    report["completed_document_count"] = len(report["completed"])
    report["skipped_document_count"] = len(report["skipped"])
    report["failed_document_count"] = len(report["failed"])
    report["jobs"] = [asdict(job) | {"pdf_path": str(job.pdf_path)} for job in jobs]
    _write_json_atomic(report_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the local extraction model over locked reference PDFs sequentially."
    )
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument(
        "--reference-dir",
        type=Path,
        default=Path(__file__).with_name("reference"),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    selected_ids = set(args.ids) if args.ids else None
    jobs = load_jobs(args.reference_dir, args.source_dir, selected_ids)
    report = asyncio.run(
        run_batch(
            jobs=jobs,
            output_dir=args.output_dir,
            settings=settings,
            force=args.force,
        )
    )
    raise SystemExit(2 if report["failed"] else 0)


if __name__ == "__main__":
    main()
