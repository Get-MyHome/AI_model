"""Idempotent, offline preparation of received PDFs; never grants human approval."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from get_myhome_ai.captured_inventory import (
    SHA256_PATTERN,
    _write_json_atomic,
    build_captured_inventory,
)
from get_myhome_ai.models import AnalyzeRequest
from get_myhome_ai.review import load_result
from get_myhome_ai.review_batch import load_review_draft_manifest, prepare_review_batch
from get_myhome_ai.reviewed_store import find_reviewed_artifact
from get_myhome_ai.settings import Settings


def _prepare_one(
    *, request_path: Path, capture_dir: Path, output_dir: Path, settings: Settings
) -> dict:
    key = request_path.stem
    if SHA256_PATTERN.fullmatch(key) is None:
        return {"state": "INVALID_CAPTURE"}
    row = {"request_key": key}
    auto_files = sorted((capture_dir / "auto").glob(f"{key}__*.json"))
    if not auto_files:
        return {**row, "state": "WAITING_FOR_ANALYSIS"}

    # Isolate one request/revision: a bad or unfinished PDF must not block others.
    with tempfile.TemporaryDirectory(prefix=".inventory-", dir=output_dir) as temporary:
        payload = build_captured_inventory(
            capture_dir=capture_dir,
            output_path=Path(temporary) / "inventory.json",
            request_keys=frozenset({key}),
        )
    target = payload["targets"][0]
    row.update(target)
    row.pop("pdf_path")
    request = AnalyzeRequest(
        complex_id=target["complex_id"],
        unit_type_id=target["unit_type_id"],
        unit_type_name=target["unit_type_name"],
        sale_price_manwon=target["sale_price_manwon"],
        # Identity matching only; never downloaded or sent anywhere.
        pdf_url="https://invalid.example/identity-only.pdf",
    )
    reviewed = find_reviewed_artifact(
        request=request,
        source_sha256=target["source_sha256"],
        reviewed_artifact_dir=settings.reviewed_artifact_dir,
        schema_version=settings.schema_version,
        extractor_version=settings.extractor_version,
    )
    if reviewed is not None:
        return {**row, "state": "REVIEWED_AVAILABLE"}

    identity = {
        "preparation_version": 1,
        "target": target,
        "auto_files": [path.name for path in auto_files],
        "schema_version": settings.schema_version,
        "extractor_version": settings.extractor_version,
    }
    job_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    inventory = output_dir / "inputs" / f"{job_id}.json"
    auto_snapshot = output_dir / "inputs" / job_id / "auto"
    auto_snapshot.mkdir(parents=True, exist_ok=True, mode=0o700)
    for source in auto_files:
        raw = source.read_bytes()
        expected_sha = source.stem.removeprefix(f"{key}__")
        if hashlib.sha256(raw).hexdigest() != expected_sha:
            raise ValueError("Captured automatic result changed")
        destination = auto_snapshot / source.name
        if destination.exists():
            if destination.read_bytes() != raw:
                raise ValueError("Prepared automatic result changed")
        else:
            with tempfile.NamedTemporaryFile(dir=auto_snapshot, delete=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
                temporary_path = Path(stream.name)
            os.replace(temporary_path, destination)
    job_dir = output_dir / "jobs" / job_id
    if not inventory.exists():
        _write_json_atomic(inventory, payload)
    else:
        locked = json.loads(inventory.read_text(encoding="utf-8"))
        if not isinstance(locked, dict) or locked.get("targets") != payload["targets"]:
            raise ValueError("Prepared inventory identity changed")
    if not job_dir.exists():
        prepare_review_batch(
            inventory_path=inventory,
            auto_artifact_dirs=[auto_snapshot],
            output_dir=job_dir,
            settings=settings,
        )
    # Do not regenerate or overwrite a reviewer's edited draft or checklist.
    manifest_path = job_dir / "review-draft-manifest.json"
    manifest = load_review_draft_manifest(manifest_path)
    state = "DRAFT_PREPARED"
    if manifest.unavailable_targets:
        state = "PREPARATION_BLOCKED"
    elif any(entry.approval_blockers for entry in manifest.drafts):
        state = "VERSION_BLOCKED"
    for entry in manifest.drafts:
        draft_path = (job_dir / entry.draft_path).resolve()
        if not draft_path.is_relative_to(job_dir) or not draft_path.is_file():
            state = "PREPARATION_BLOCKED"
            break
        draft = load_result(draft_path)
        if (
            draft.review_status == "REVIEWED"
            or draft.meta.source_sha256 != target["source_sha256"]
            or draft.complex_id != target["complex_id"]
            or draft.target_unit.unit_type_id != target["unit_type_id"]
            or draft.target_unit.unit_type_name != entry.target.normalized_unit_type_name
            or draft.target_unit.sale_price_manwon != target["sale_price_manwon"]
        ):
            state = "PREPARATION_BLOCKED"
            break
    return {**row, "state": state, "draft_manifest": str(manifest_path)}


def prepare_inbox(*, capture_dir: Path, output_dir: Path, settings: Settings) -> dict:
    capture_dir = capture_dir.resolve()
    output_dir = output_dir.resolve()
    if output_dir == capture_dir or output_dir.is_relative_to(capture_dir):
        raise ValueError("Preparation output must be separate from captured input")
    if output_dir.is_relative_to(settings.reviewed_artifact_dir.resolve()):
        raise ValueError("Preparation output must not be the reviewed registry")
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (output_dir / ".prepare.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"state": "ALREADY_RUNNING"}
        rows = []
        for request_path in sorted((capture_dir / "requests").glob("*.json")):
            try:
                rows.append(
                    _prepare_one(
                        request_path=request_path,
                        capture_dir=capture_dir,
                        output_dir=output_dir,
                        settings=settings,
                    )
                )
            except (ValueError, OSError):
                # Never log untrusted payloads, URLs, credentials or PDF contents.
                rows.append(
                    {
                        "request_key": request_path.stem
                        if SHA256_PATTERN.fullmatch(request_path.stem)
                        else None,
                        "state": "INVALID_CAPTURE_OR_PREPARATION_FAILED",
                    }
                )
        report = {
            "schema_version": "prepared_inbox_v1",
            "updated_at": datetime.now(UTC).isoformat(),
            "scope": "RECEIVED_PDFS_ONLY",
            "human_approval_automatic": False,
            "source_available": (capture_dir / "requests").is_dir(),
            "counts": dict(Counter(row["state"] for row in rows)),
            "targets": rows,
        }
        _write_json_atomic(output_dir / "status.json", report)
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = prepare_inbox(
        capture_dir=args.capture_dir, output_dir=args.output_dir, settings=Settings()
    )
    print(json.dumps({key: report[key] for key in ("state", "counts") if key in report}))


if __name__ == "__main__":
    main()
