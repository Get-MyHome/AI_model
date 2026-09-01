from __future__ import annotations

import argparse
import asyncio
import html
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from evaluation.run_model import _is_complete, _write_json_atomic, load_jobs
from get_myhome_ai.models import AnalysisResponse
from get_myhome_ai.review import save_result

PDF_LINK_PATTERN = re.compile(
    r'href=["\'](?P<url>https://static\.applyhome\.co\.kr/[^"\']*getAtchmnfl\.do\?[^"\']+)["\']'
)


def extract_locked_pdf_url(detail_dir: Path, complex_id: str) -> str:
    detail_path = detail_dir / f"{complex_id}_detail.html"
    if not detail_path.is_file():
        raise FileNotFoundError(f"saved detail page is missing: {detail_path}")
    match = PDF_LINK_PATTERN.search(detail_path.read_text(encoding="utf-8", errors="replace"))
    if match is None:
        raise ValueError(f"{complex_id}: PDF link is missing from the saved detail page")
    url = html.unescape(match.group("url"))
    if urlparse(url).hostname != "static.applyhome.co.kr":
        raise ValueError(f"{complex_id}: unexpected PDF host")
    return url


async def _analyze(
    client: httpx.AsyncClient,
    *,
    endpoint: str,
    api_key: str,
    complex_id: str,
    pdf_url: str,
    max_attempts: int,
) -> AnalysisResponse:
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            response = await client.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"complex_id": complex_id, "pdf_url": pdf_url},
            )
            if response.status_code == 200:
                return AnalysisResponse.model_validate(response.json())
            retryable = response.status_code in {502, 503, 504}
            detail = response.json().get("error", {})
            message = detail.get("code") or f"HTTP_{response.status_code}"
            last_error = RuntimeError(message)
            if not retryable:
                break
        except (httpx.HTTPError, ValueError) as error:
            last_error = error
        if attempt + 1 < max_attempts:
            await asyncio.sleep(attempt + 1)
    detail = (
        f"{type(last_error).__name__}: {last_error}" if last_error is not None else "unknown error"
    )
    raise RuntimeError(f"AI endpoint analysis failed: {detail}") from last_error


async def run_api_batch(
    *,
    source_dir: Path,
    reference_dir: Path,
    output_dir: Path,
    endpoint: str,
    api_key: str,
    timeout_seconds: float,
    max_attempts: int,
    force: bool,
    selected_ids: set[str] | None = None,
) -> dict[str, object]:
    jobs = load_jobs(reference_dir, source_dir, selected_ids)
    report: dict[str, object] = {
        "schema_version": "api_model_batch_run_v0.1",
        "endpoint_kind": "authenticated_http_api",
        "started_at": datetime.now(UTC).isoformat(),
        "requested_document_count": len(jobs),
        "completed": [],
        "skipped": [],
        "failed": [],
    }
    completed: list[dict[str, object]] = report["completed"]  # type: ignore[assignment]
    skipped: list[str] = report["skipped"]  # type: ignore[assignment]
    failed: list[dict[str, object]] = report["failed"]  # type: ignore[assignment]
    report_path = output_dir / "run-report.json"
    output_dir.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=timeout_seconds, trust_env=False) as client:
        for index, job in enumerate(jobs, start=1):
            destination = output_dir / f"{job.complex_id}.json"
            if not force and _is_complete(destination, job):
                skipped.append(job.complex_id)
                print(f"[{index}/{len(jobs)}] SKIP {job.complex_id}", flush=True)
                _write_json_atomic(report_path, report)
                continue

            started = time.monotonic()
            print(f"[{index}/{len(jobs)}] START {job.complex_id}", flush=True)
            try:
                result = await _analyze(
                    client,
                    endpoint=endpoint,
                    api_key=api_key,
                    complex_id=job.complex_id,
                    pdf_url=extract_locked_pdf_url(source_dir, job.complex_id),
                    max_attempts=max_attempts,
                )
                if result.meta.source_sha256 != job.pdf_sha256:
                    raise ValueError("endpoint PDF digest differs from locked reference PDF")
                save_result(result, destination)
                elapsed = round(time.monotonic() - started, 3)
                completed.append(
                    {
                        "complex_id": job.complex_id,
                        "elapsed_seconds": elapsed,
                        "analysis_status": result.analysis_status,
                        "review_status": result.review_status,
                        "validation_passed": result.validation.passed,
                        "model": result.meta.model,
                    }
                )
                print(
                    f"[{index}/{len(jobs)}] DONE {job.complex_id} "
                    f"{elapsed:.1f}s validation={result.validation.passed}",
                    flush=True,
                )
            except Exception as error:  # Continue so one document cannot hide the rest.
                elapsed = round(time.monotonic() - started, 3)
                failed.append(
                    {
                        "complex_id": job.complex_id,
                        "elapsed_seconds": elapsed,
                        "error_type": type(error).__name__,
                        "message": str(error)[:300],
                    }
                )
                print(
                    f"[{index}/{len(jobs)}] FAIL {job.complex_id} {type(error).__name__}: {error}",
                    flush=True,
                )
            _write_json_atomic(report_path, report)

    report["finished_at"] = datetime.now(UTC).isoformat()
    report["completed_document_count"] = len(completed)
    report["skipped_document_count"] = len(skipped)
    report["failed_document_count"] = len(failed)
    _write_json_atomic(report_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the deployed AI API over all locked reference PDFs."
    )
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument(
        "--reference-dir",
        type=Path,
        default=Path(__file__).with_name("reference"),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:9000/api/analyze")
    parser.add_argument("--timeout-seconds", type=float, default=320.0)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    api_key = os.getenv("AI_API_KEY")
    if not api_key:
        parser.error("AI_API_KEY is required")
    report = asyncio.run(
        run_api_batch(
            source_dir=args.source_dir,
            reference_dir=args.reference_dir,
            output_dir=args.output_dir,
            endpoint=args.endpoint,
            api_key=api_key,
            timeout_seconds=args.timeout_seconds,
            max_attempts=args.max_attempts,
            force=args.force,
            selected_ids=set(args.ids) if args.ids else None,
        )
    )
    raise SystemExit(2 if report["failed"] else 0)


if __name__ == "__main__":
    main()
