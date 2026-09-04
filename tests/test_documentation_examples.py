from __future__ import annotations

import json
from pathlib import Path

from get_myhome_ai.models import AnalysisResponse, ReviewStatus

ROOT = Path(__file__).resolve().parents[1]


def test_analyze_response_v03_example_is_complete_canonical_contract() -> None:
    path = ROOT / "docs" / "examples" / "analyze-response-v0.3.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    response = AnalysisResponse.model_validate(payload)

    # exclude_none=False makes a missing optional/default field reappear in the dump,
    # so exact equality proves the checked-in example includes the complete shape.
    assert response.model_dump(mode="json", exclude_none=False) == payload
    assert response.meta.schema_version == "v0.3"
    assert response.meta.extractor_version == "0.2.3"
    assert response.review_status == ReviewStatus.AUTO_EXTRACTED
