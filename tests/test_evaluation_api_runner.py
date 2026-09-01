from __future__ import annotations

from pathlib import Path

import pytest

from evaluation.run_api import extract_locked_pdf_url


def test_extract_locked_pdf_url_from_saved_detail_page(tmp_path: Path) -> None:
    (tmp_path / "2026000001_detail.html").write_text(
        '<a href="https://static.applyhome.co.kr/ai/aia/getAtchmnfl.do?'
        'houseManageNo=2026000001&amp;atchmnflSn=3">PDF</a>',
        encoding="utf-8",
    )

    actual = extract_locked_pdf_url(tmp_path, "2026000001")

    assert actual == (
        "https://static.applyhome.co.kr/ai/aia/getAtchmnfl.do?houseManageNo=2026000001&atchmnflSn=3"
    )


def test_extract_locked_pdf_url_rejects_missing_link(tmp_path: Path) -> None:
    (tmp_path / "2026000001_detail.html").write_text("<html></html>", encoding="utf-8")

    with pytest.raises(ValueError, match="PDF link is missing"):
        extract_locked_pdf_url(tmp_path, "2026000001")
