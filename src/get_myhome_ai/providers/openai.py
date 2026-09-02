from __future__ import annotations

from importlib.resources import files

from openai import AsyncOpenAI

from get_myhome_ai.candidates import CandidatePage, format_candidate_document
from get_myhome_ai.errors import ProviderError, ProviderNotConfiguredError
from get_myhome_ai.models import ExtractionDraft
from get_myhome_ai.providers.ollama_grounding import ground_ollama_draft
from get_myhome_ai.settings import Settings


class OpenAIExtractor:
    name = "openai"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model_name = settings.openai_model
        if settings.openai_api_key is None:
            self.client: AsyncOpenAI | None = None
        else:
            self.client = AsyncOpenAI(
                api_key=settings.openai_api_key.get_secret_value(),
                timeout=settings.openai_timeout_seconds,
            )

    async def ready(self) -> bool:
        return self.client is not None

    async def extract(
        self,
        *,
        complex_id: str,
        pages: list[CandidatePage],
        unit_type_id: str | None,
        unit_type_name: str | None,
        sale_price_manwon: int | None,
    ) -> ExtractionDraft:
        if self.client is None:
            raise ProviderNotConfiguredError("OPENAI_API_KEY가 설정되지 않았습니다.")

        system_prompt = files("get_myhome_ai.prompts").joinpath("extract_v1.txt").read_text("utf-8")
        target_context = (
            f"complex_id={complex_id}\n"
            f"unit_type_id={unit_type_id or '미지정'}\n"
            f"unit_type_name={unit_type_name or '미지정'}\n"
            "sale_price_manwon="
            f"{sale_price_manwon if sale_price_manwon is not None else '미지정'}"
        )
        document = format_candidate_document(pages)

        try:
            response = await self.client.responses.parse(
                model=self.model_name,
                input=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": (
                            f"{target_context}\n\n다음은 공고문 후보 페이지입니다.\n{document}"
                        ),
                    },
                ],
                text_format=ExtractionDraft,
                store=False,
            )
        except Exception as exc:
            raise ProviderError("AI 구조화 추출 요청에 실패했습니다.") from exc

        if response.output_parsed is None:
            raise ProviderError("AI가 검증 가능한 구조화 결과를 반환하지 않았습니다.")
        return ground_ollama_draft(
            response.output_parsed,
            pages=pages,
            unit_type_name=unit_type_name,
            sale_price_manwon=sale_price_manwon,
        )
