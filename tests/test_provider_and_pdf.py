from __future__ import annotations

import json

import httpx
import pytest

from get_myhome_ai.candidates import CandidatePage
from get_myhome_ai.errors import InvalidPdfError, InvalidPdfUrlError, ProviderNotConfiguredError
from get_myhome_ai.pdf_text import _validate_remote_url, load_pdf_from_path
from get_myhome_ai.providers.factory import create_provider
from get_myhome_ai.providers.ollama import OllamaExtractor
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


async def test_ollama_uses_three_bounded_structured_calls(golden_cases) -> None:
    case = golden_cases["2026000358"]
    expected = case.expected
    calls: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3.5:4b"}]})

        payload = json.loads(request.content)
        calls.append(payload)
        properties = payload["format"]["properties"]
        if "payment_schedule" in properties:
            content = {
                "payment_schedule": expected.payment_schedule.model_dump(mode="json"),
                "evidence": [
                    item.model_dump(mode="json")
                    for item in expected.evidence
                    if item.field.startswith("/payment_schedule")
                ],
            }
        elif "interim_loan" in properties:
            content = {
                "interim_loan": expected.interim_loan.model_dump(mode="json"),
                "evidence": [
                    item.model_dump(mode="json")
                    for item in expected.evidence
                    if item.field.startswith("/interim_loan")
                ],
                "exception_flags": [item.value for item in expected.exception_flags],
            }
        else:
            item = expected.additional_costs[0]
            finding = item.model_dump(mode="json")
            evidence = next(
                item for item in expected.evidence if item.field.startswith("/additional_costs")
            )
            finding["evidence_page"] = evidence.page
            finding["evidence_raw_text"] = evidence.raw_text
            content = {
                "findings": [finding],
            }
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": json.dumps(content)}},
        )

    provider = OllamaExtractor(
        Settings(
            ai_provider="ollama",
            ollama_model="qwen3.5:4b",
            ollama_chunk_max_chars=4_000,
        ),
        transport=httpx.MockTransport(handler),
    )
    pages = [
        CandidatePage(
            number=evidence.page,
            text=(
                f"발코니 확장\n{evidence.raw_text}"
                if evidence.field.startswith("/additional_costs")
                else evidence.raw_text
            ),
            score=20,
            categories=frozenset(
                {"cost"}
                if evidence.field.startswith("/additional_costs")
                else {"loan"}
                if evidence.field.startswith("/interim_loan")
                else {"payment", "balance"}
            ),
        )
        for evidence in expected.evidence
    ]

    assert await provider.ready() is True
    actual = await provider.extract(
        complex_id=case.complex_id,
        pages=pages,
        unit_type_id=None,
        unit_type_name=case.unit_type_name,
        sale_price_manwon=case.sale_price_manwon,
    )

    assert actual.payment_schedule == expected.payment_schedule
    assert actual.interim_loan == expected.interim_loan
    assert actual.additional_costs[0].total_amount_manwon == 1500
    assert actual.additional_costs[0].applicable_unit_type == "39A"
    assert all(item.field.startswith("/") for item in actual.evidence)
    assert all(
        not item.field.startswith("/additional_costs/")
        or item.field.startswith("/additional_costs/0")
        for item in actual.evidence
    )
    source_by_page: dict[int, str] = {}
    for page in pages:
        source_by_page[page.number] = source_by_page.get(page.number, "") + page.text
    assert all(item.raw_text in source_by_page[item.page] for item in actual.evidence)
    assert len(calls) == 3
    assert all(call["stream"] is False for call in calls)
    assert all(call["think"] is False for call in calls)
    assert all(call["format"]["type"] == "object" for call in calls)
    assert all(len(call["messages"][1]["content"]) < 6_000 for call in calls)


async def test_ollama_readiness_is_false_when_model_is_missing() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b"}]})

    provider = OllamaExtractor(
        Settings(ai_provider="ollama", ollama_model="qwen3.5:4b"),
        transport=httpx.MockTransport(handler),
    )

    assert await provider.ready() is False


def test_local_loader_rejects_non_pdf(tmp_path) -> None:
    source = tmp_path / "not-a-pdf.txt"
    source.write_text("not a PDF", encoding="utf-8")

    with pytest.raises(InvalidPdfError):
        load_pdf_from_path(str(source), Settings(ai_provider="fixture"))


@pytest.mark.parametrize(
    "url",
    [
        "https://user@example.com/document.pdf",
        "https://example.com:444/document.pdf",
        "http://example.com/document.pdf",
    ],
)
async def test_remote_pdf_url_rejects_unsafe_authority(url: str) -> None:
    with pytest.raises(InvalidPdfUrlError):
        await _validate_remote_url(url, Settings(ai_provider="fixture"))


async def test_pdf_host_allowlist_requires_exact_hostname() -> None:
    settings = Settings(ai_provider="fixture", pdf_allowed_hosts="example.com")

    with pytest.raises(InvalidPdfUrlError):
        await _validate_remote_url("https://files.example.com/document.pdf", settings)
