from __future__ import annotations

import pytest

from get_myhome_ai.candidates import CandidatePage
from get_myhome_ai.errors import InvalidPdfError, ProviderNotConfiguredError
from get_myhome_ai.pdf_text import load_pdf_from_path
from get_myhome_ai.providers.factory import create_provider
from get_myhome_ai.settings import Settings


async def test_openai_without_key_never_falls_back_to_fixture() -> None:
    provider = create_provider(Settings(ai_provider="openai", openai_api_key=None))

    assert provider.name == "openai"
    with pytest.raises(ProviderNotConfiguredError):
        await provider.extract(
            complex_id="test",
            pages=[
                CandidatePage(
                    number=1,
                    text="계약금 중도금 잔금",
                    score=1,
                    categories=frozenset({"payment"}),
                )
            ],
            unit_type_id=None,
            unit_type_name=None,
            sale_price_manwon=None,
        )


def test_local_loader_rejects_non_pdf(tmp_path) -> None:
    source = tmp_path / "not-a-pdf.txt"
    source.write_text("not a PDF", encoding="utf-8")

    with pytest.raises(InvalidPdfError):
        load_pdf_from_path(str(source), Settings(ai_provider="fixture"))
