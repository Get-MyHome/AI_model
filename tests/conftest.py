from __future__ import annotations

import os
from pathlib import Path

import pytest

from get_myhome_ai.fixtures import GoldenCase, load_golden_cases
from get_myhome_ai.pdf_text import PdfPage


@pytest.fixture(scope="session")
def golden_cases() -> dict[str, GoldenCase]:
    return load_golden_cases(Path("tests/fixtures/golden"))


@pytest.fixture(scope="session")
def golden_pdf_dir() -> Path:
    configured = os.environ.get("GOLDEN_PDF_DIR")
    fallback = Path(
        "/home/soccz/22tb/tmp/claude-1001/"
        "-mnt-20t-AI----/291eec41-8358-4bc9-b1f0-ced7d4ee1d23/"
        "scratchpad/gonggo"
    )
    directory = Path(configured) if configured else fallback
    if not directory.is_dir():
        pytest.skip("GOLDEN_PDF_DIR에 실제 공고문 PDF 디렉터리를 지정해야 합니다.")
    return directory


def synthetic_pages(case: GoldenCase, page_count: int = 60) -> list[PdfPage]:
    texts: dict[int, list[str]] = {}
    for evidence in case.expected.evidence:
        texts.setdefault(evidence.page, []).append(evidence.raw_text)
    return [
        PdfPage(number=number, text="\n".join(texts.get(number, ["일반 공고 내용"])))
        for number in range(1, page_count + 1)
    ]
