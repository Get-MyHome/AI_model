from __future__ import annotations

from get_myhome_ai.candidates import CandidatePage
from get_myhome_ai.errors import ProviderError
from get_myhome_ai.models import ExtractionDraft


class FixtureExtractor:
    name = "fixture"
    model_name = None

    def __init__(self, fixtures: dict[str, ExtractionDraft]) -> None:
        self.fixtures = fixtures

    async def extract(
        self,
        *,
        complex_id: str,
        pages: list[CandidatePage],
        unit_type_id: str | None,
        unit_type_name: str | None,
        sale_price_manwon: int | None,
    ) -> ExtractionDraft:
        del pages, unit_type_id, unit_type_name, sale_price_manwon
        try:
            return self.fixtures[complex_id].model_copy(deep=True)
        except KeyError as exc:
            raise ProviderError(f"테스트 fixture가 없습니다: {complex_id}") from exc
