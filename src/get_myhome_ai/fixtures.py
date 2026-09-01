from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from get_myhome_ai.models import ExtractionDraft


@dataclass(frozen=True)
class GoldenCase:
    complex_id: str
    pdf_filename: str
    unit_type_name: str | None
    sale_price_manwon: int | None
    expected: ExtractionDraft


def load_golden_cases(directory: Path) -> dict[str, GoldenCase]:
    cases: dict[str, GoldenCase] = {}
    for path in sorted(directory.glob("[0-9]*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        input_data = data["input"]
        complex_id = str(input_data["complex_id"])
        if complex_id in cases:
            raise ValueError(f"중복된 골든 complex_id입니다: {complex_id}")
        cases[complex_id] = GoldenCase(
            complex_id=complex_id,
            pdf_filename=str(input_data["pdf_filename"]),
            unit_type_name=input_data.get("unit_type_name"),
            sale_price_manwon=input_data.get("sale_price_manwon"),
            expected=ExtractionDraft.model_validate(data["expected"]),
        )
    if not cases:
        raise ValueError(f"골든 fixture를 찾지 못했습니다: {directory}")
    return cases
