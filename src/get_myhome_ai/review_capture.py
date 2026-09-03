from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from get_myhome_ai.models import AnalysisResponse, AnalyzeRequest
from get_myhome_ai.pdf_text import DownloadedPdf


def _request_key(request: AnalyzeRequest, source_sha256: str) -> str:
    identity = {
        "complex_id": request.complex_id,
        "unit_type_id": request.unit_type_id,
        "unit_type_name": request.unit_type_name,
        "sale_price_manwon": request.sale_price_manwon,
        "source_sha256": source_sha256,
    }
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_bytes_once(path: Path, content: bytes) -> None:
    """Write an immutable capture without replacing an existing source."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        existing_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        incoming_sha256 = hashlib.sha256(content).hexdigest()
        if existing_sha256 != incoming_sha256:
            raise ValueError(
                f"기존 검수 캡처와 내용이 다릅니다: {path.name}"
            ) from exc
        return
    with os.fdopen(descriptor, "wb") as destination:
        destination.write(content)
        destination.flush()
        os.fsync(destination.fileno())


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(encoded)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    os.chmod(temporary_path, 0o600)
    os.replace(temporary_path, path)


def capture_review_source(
    root: Path,
    request: AnalyzeRequest,
    downloaded: DownloadedPdf,
) -> str:
    """Persist a public source PDF and URL-free exact-target metadata for review.

    The crawler's pre-signed URL is intentionally never serialized.
    """

    request_key = _request_key(request, downloaded.sha256)
    source_path = root / "sources" / f"{downloaded.sha256}.pdf"
    _write_bytes_once(source_path, downloaded.content)
    _write_json_atomic(
        root / "requests" / f"{request_key}.json",
        {
            "schema_version": "review_capture_v0.1",
            "captured_at": datetime.now(UTC).isoformat(),
            "request_key": request_key,
            "complex_id": request.complex_id,
            "unit_type_id": request.unit_type_id,
            "unit_type_name": request.unit_type_name,
            "sale_price_manwon": request.sale_price_manwon,
            "source_sha256": downloaded.sha256,
            "source_pdf_path": str(source_path.resolve()),
        },
    )
    return request_key


def capture_review_result(root: Path, request_key: str, result: AnalysisResponse) -> None:
    """Persist every matching automatic result without replacing an earlier run."""

    encoded = result.model_dump_json(indent=2).encode("utf-8")
    result_sha256 = hashlib.sha256(encoded).hexdigest()
    _write_bytes_once(
        root / "auto" / f"{request_key}__{result_sha256}.json",
        encoded,
    )
