from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evaluation.run_model import load_jobs


def _write_reference(reference_dir: Path, *, complex_id: str, filename: str, digest: str) -> None:
    reference_dir.mkdir(parents=True, exist_ok=True)
    (reference_dir / f"{complex_id}.json").write_text(
        json.dumps(
            {
                "complex_id": complex_id,
                "source": {"pdf_filename": filename, "pdf_sha256": digest},
            }
        ),
        encoding="utf-8",
    )


def test_load_jobs_locks_pdf_digest_and_selection(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    first = source_dir / "first.pdf"
    second = source_dir / "second.pdf"
    first.write_bytes(b"%PDF-first")
    second.write_bytes(b"%PDF-second")
    reference_dir = tmp_path / "reference"
    _write_reference(
        reference_dir,
        complex_id="100",
        filename=first.name,
        digest=hashlib.sha256(first.read_bytes()).hexdigest(),
    )
    _write_reference(
        reference_dir,
        complex_id="200",
        filename=second.name,
        digest=hashlib.sha256(second.read_bytes()).hexdigest(),
    )

    jobs = load_jobs(reference_dir, source_dir, {"200"})

    assert [job.complex_id for job in jobs] == ["200"]
    assert jobs[0].pdf_path == second


def test_load_jobs_rejects_changed_source(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    pdf = source_dir / "source.pdf"
    pdf.write_bytes(b"changed")
    reference_dir = tmp_path / "reference"
    _write_reference(
        reference_dir,
        complex_id="100",
        filename=pdf.name,
        digest=hashlib.sha256(b"original").hexdigest(),
    )

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_jobs(reference_dir, source_dir)
