from __future__ import annotations

from typing import Protocol

from get_myhome_ai.candidates import CandidatePage
from get_myhome_ai.models import ExtractionDraft


class ExtractorProvider(Protocol):
    name: str
    model_name: str | None

    async def extract(
        self,
        *,
        complex_id: str,
        pages: list[CandidatePage],
        unit_type_id: str | None,
        unit_type_name: str | None,
        sale_price_manwon: int | None,
    ) -> ExtractionDraft: ...
