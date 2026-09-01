from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from datetime import date
from importlib.resources import files
from typing import Annotated, TypeVar, cast

import httpx
from pydantic import Field, ValidationError

from get_myhome_ai.candidates import CATEGORY_TERMS, CandidatePage
from get_myhome_ai.errors import ProviderError, ProviderNotConfiguredError
from get_myhome_ai.models import (
    AdditionalCost,
    AdditionalCostPayment,
    AdditionalCostType,
    Evidence,
    ExceptionFlag,
    ExtractionDraft,
    InterimLoan,
    PaymentSchedule,
    StrictModel,
)
from get_myhome_ai.providers.ollama_grounding import ground_ollama_draft
from get_myhome_ai.settings import Settings

logger = logging.getLogger(__name__)


class _PaymentExtraction(StrictModel):
    payment_schedule: PaymentSchedule
    evidence: Annotated[list[Evidence], Field(max_length=12)]


class _LoanExtraction(StrictModel):
    interim_loan: InterimLoan
    evidence: Annotated[list[Evidence], Field(max_length=10)]
    exception_flags: Annotated[list[ExceptionFlag], Field(max_length=10)]


class _FocusedCost(StrictModel):
    type: AdditionalCostType
    name: Annotated[str, Field(min_length=1, max_length=100)]
    total_amount_manwon: int | None
    required: bool | None
    included_in_sale_price: bool | None
    applicable_unit_type: Annotated[str | None, Field(max_length=100)]
    payments: Annotated[list[AdditionalCostPayment], Field(max_length=6)]
    note: Annotated[str | None, Field(max_length=500)]
    evidence_page: Annotated[int, Field(ge=1)]
    evidence_raw_text: Annotated[str, Field(min_length=1, max_length=300)]


class _CostExtraction(StrictModel):
    findings: Annotated[list[_FocusedCost], Field(max_length=3)]


StructuredResult = TypeVar(
    "StructuredResult",
    _PaymentExtraction,
    _LoanExtraction,
    _CostExtraction,
)


TASKS: tuple[tuple[str, tuple[str, ...], type[StrictModel]], ...] = (
    ("payment", ("payment", "balance"), _PaymentExtraction),
    ("loan", ("loan",), _LoanExtraction),
    ("cost", ("cost",), _CostExtraction),
)

MONTH_PATTERN = re.compile(r"^(\d{4})[-년.\s]+(0?[1-9]|1[0-2])(?:월)?$")
DATE_PATTERN = re.compile(
    r"^(\d{4})[-년.\s]+(0?[1-9]|1[0-2])[-월.\s]+(0?[1-9]|[12]\d|3[01])(?:일)?\.?$"
)


def _load_json_object(content: str) -> dict[str, object]:
    """Parse a structured response, tolerating only an outer prose/fence wrapper."""

    stripped = content.strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Ollama structured response must be a JSON object.")
    return parsed


def _merge_windows(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _focused_text(text: str, terms: Iterable[str], limit: int) -> str:
    if len(text) <= limit:
        return text

    positions = sorted(
        {match.start() for term in terms for match in re.finditer(re.escape(term), text)}
    )
    windows = [(max(0, position - 500), min(len(text), position + 2_500)) for position in positions]
    if not windows:
        return text[:limit]
    merged = _merge_windows(windows)
    ranked = sorted(
        merged,
        key=lambda window: (
            -sum(text[window[0] : window[1]].count(term) for term in terms),
            -("자납" in text[window[0] : window[1]]),
            -("이자후불" in text[window[0] : window[1]]),
            window[0],
        ),
    )
    chosen: list[tuple[int, int]] = []
    used = 0
    for start, end in ranked:
        length = end - start
        if chosen and used + length > limit:
            continue
        chosen.append((start, end))
        used += length
        if used >= limit:
            break
    focused = "\n…\n".join(text[start:end] for start, end in sorted(chosen))
    return focused[:limit]


def _task_document(
    pages: list[CandidatePage],
    *,
    categories: tuple[str, ...],
    max_chars: int,
    unit_type_name: str | None,
) -> str:
    primary = [page for page in pages if page.categories.intersection(categories)]
    if not primary:
        return ""

    def task_score(page: CandidatePage) -> int:
        text = page.text
        score = sum(
            text.count(term) for category in categories for term in CATEGORY_TERMS[category]
        )
        if "payment" in categories:
            if all(term in text for term in ("계약금", "중도금", "잔금")):
                score += 100
            if "공급금액" in text and "%" in text:
                score += 60
            if "cost" in page.categories:
                score -= 300
        if "balance" in categories and "입주시기" in text:
            score += 80
        if "loan" in categories:
            if (
                "중도금" in text
                and "%" in text
                and any(term in text for term in ("대출", "알선", "자납"))
            ):
                score += 100
            if any(term in text for term in ("이자후불", "무이자", "대출이 불가")):
                score += 60
        if "cost" in categories:
            if unit_type_name and unit_type_name in text:
                score += 150
            if "공급금액" in text and any(term in text for term in ("발코니", "추가선택품목")):
                score += 60
        return score

    def priority_rank(page: CandidatePage) -> int:
        text = page.text
        if (
            "payment" in categories
            and all(term in text for term in ("계약금", "중도금", "잔금"))
            and "공급금액" in text
            and "cost" not in page.categories
        ):
            return 0
        if "balance" in categories and "입주시기" in text:
            return 1
        if "loan" in categories and "중도금" in text and "%" in text:
            return 0
        if "cost" in categories and unit_type_name and unit_type_name in text:
            return 0
        return 2

    primary_numbers = {page.number for page in primary}
    related = [
        page
        for page in pages
        if page.number in primary_numbers
        or any(abs(page.number - number) == 1 for number in primary_numbers)
    ]
    ordered = sorted(
        related,
        key=lambda page: (
            page.number not in primary_numbers,
            priority_rank(page),
            -task_score(page),
            page.number,
        ),
    )
    terms = [term for category in categories for term in CATEGORY_TERMS[category]]
    if unit_type_name:
        terms.append(unit_type_name)

    blocks: list[tuple[int, str]] = []
    remaining = max_chars
    for page in ordered:
        if remaining <= 0:
            break
        per_page_limit = min(4_500, remaining)
        text = _focused_text(page.text, terms, per_page_limit).strip()
        if not text:
            continue
        blocks.append((page.number, text))
        remaining -= len(text)

    return "\n\n".join(f"[PAGE {number}]\n{text}" for number, text in sorted(blocks))


def _deduplicate_evidence(items: Iterable[Evidence]) -> list[Evidence]:
    result: list[Evidence] = []
    seen: set[tuple[str, int, str]] = set()
    for item in items:
        key = (item.field, item.page, item.raw_text)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _normalize_date_fields(value: object) -> None:
    if isinstance(value, list):
        for item in value:
            _normalize_date_fields(item)
        return
    if not isinstance(value, dict):
        return

    due_month = value.get("due_month")
    if isinstance(due_month, str):
        match = MONTH_PATTERN.fullmatch(due_month.strip())
        value["due_month"] = (
            f"{int(match.group(1)):04d}-{int(match.group(2)):02d}" if match else None
        )

    due_date = value.get("due_date")
    if isinstance(due_date, str):
        match = DATE_PATTERN.fullmatch(due_date.strip())
        if match:
            try:
                normalized = date(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                )
            except ValueError:
                normalized = None
            value["due_date"] = normalized.isoformat() if normalized else None
        else:
            value["due_date"] = None

    for child in value.values():
        _normalize_date_fields(child)


class OllamaExtractor:
    name = "ollama"

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.model_name = settings.ollama_model
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.settings.ollama_base_url.rstrip("/"),
            timeout=self.settings.ollama_timeout_seconds,
            transport=self._transport,
        )

    async def ready(self) -> bool:
        try:
            async with self._client() as client:
                response = await client.get("/api/tags")
                response.raise_for_status()
            model_names = {
                model.get("name") or model.get("model")
                for model in response.json().get("models", [])
            }
            return self.model_name in model_names
        except (httpx.HTTPError, ValueError, TypeError):
            return False

    async def _extract_task(
        self,
        client: httpx.AsyncClient,
        *,
        task: str,
        result_type: type[StructuredResult],
        target_context: str,
        document: str,
    ) -> StructuredResult:
        system_prompt = files("get_myhome_ai.prompts").joinpath("extract_v1.txt").read_text("utf-8")
        task_instruction = {
            "payment": (
                "계약금·중도금·잔금의 비율/정액/회차/납부일만 추출하세요. "
                "비율과 금액이 모두 없는 회차는 만들지 말고 installments를 빈 배열로 두세요."
            ),
            "loan": (
                "중도금 대출 알선 상태·대출/자납 비율·은행·보증·이자 조건만 추출하세요. "
                "사업주체가 이자를 대납한 뒤 입주 시 계약자가 정산하면 DEFERRED_INTEREST입니다. "
                "NOT_APPLICABLE은 중도금 대출 불가가 명시된 경우에만 사용하세요. "
                "분양보증 기관을 중도금 대출 보증기관으로 오인하지 마세요."
            ),
            "cost": (
                "발코니 확장비와 유상옵션 등 추가 비용만 추출하세요. "
                "대상 주택형이 있으면 그 주택형 행만 추출하고 다른 주택형은 나열하지 마세요. "
                "같은 비용 유형은 납부 회차를 payments에 합쳐 하나의 항목으로 만듭니다. "
                "최대 3개의 비용과 각 비용당 최대 6개의 납부 회차만 반환하세요. "
                "근거는 가장 짧은 연속 원문 한 개만 evidence_raw_text에 300자 이내로 복사하세요. "
                "원문이 '단위: 원'이면 금액을 10,000으로 나눠 만 원 정수로 반환하세요. "
                "예: 15,000,000원→1,500, 1,500,000원→150, 12,000,000원→1,200입니다. "
                "없으면 빈 배열입니다."
            ),
        }[task]
        payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": f"{system_prompt}\n\n이번 호출의 범위: {task_instruction}",
                },
                {
                    "role": "user",
                    "content": (
                        f"{target_context}\n\n"
                        "아래 공고문 페이지에서만 사실을 복사해 JSON Schema에 맞춰 반환하세요. "
                        "스키마의 모든 필드를 포함하고 JSON 외 텍스트는 쓰지 마세요. "
                        "evidence는 값 그룹당 가장 짧은 연속 원문 1개만 사용하고, "
                        "같은 필드에 여러 문장을 나열하지 마세요.\n\n"
                        f"{document}"
                    ),
                },
            ],
            "stream": False,
            "think": False,
            "format": result_type.model_json_schema(),
            "options": {
                "temperature": 0,
                "num_ctx": self.settings.ollama_num_ctx,
                "num_predict": min(
                    self.settings.ollama_num_predict,
                    {"payment": 2_500, "loan": 2_400, "cost": 2_400}[task],
                ),
            },
            "keep_alive": self.settings.ollama_keep_alive,
        }
        last_error: Exception | None = None
        for attempt in range(self.settings.ollama_max_attempts):
            content: str | None = None
            if attempt:
                payload["messages"][1]["content"] += (
                    "\n\n이전 구조화 응답이 JSON Schema 검증을 통과하지 못했습니다. "
                    "원문에 없는 값을 추가하지 말고 모든 필드와 enum을 정확히 맞춰 다시 반환하세요."
                )
            try:
                response = await client.post("/api/chat", json=payload)
                if response.status_code == 404:
                    raise ProviderNotConfiguredError(
                        f"Ollama model이 없습니다: {self.model_name}. 먼저 모델을 pull하세요."
                    )
                response.raise_for_status()
                content = response.json()["message"]["content"]
                parsed = _load_json_object(content)
                _normalize_date_fields(parsed)
                return result_type.model_validate(parsed)
            except ProviderNotConfiguredError:
                raise
            except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError) as exc:
                last_error = exc
                if isinstance(exc, ValidationError):
                    logger.warning(
                        "Ollama %s schema validation failed on attempt %d: %s",
                        task,
                        attempt + 1,
                        [
                            {
                                "loc": ".".join(str(part) for part in error["loc"]),
                                "type": error["type"],
                            }
                            for error in exc.errors(include_input=False, include_url=False)
                        ],
                    )
                else:
                    json_detail = (
                        f" msg={exc.msg} line={exc.lineno} column={exc.colno}"
                        if isinstance(exc, json.JSONDecodeError)
                        else ""
                    )
                    logger.warning(
                        "Ollama %s extraction failed on attempt %d: %s%s%s",
                        task,
                        attempt + 1,
                        type(exc).__name__,
                        json_detail,
                        (
                            " "
                            f"chars={len(content)} "
                            f"object_bounds={content.find('{') >= 0 and content.rfind('}') > 0}"
                            if isinstance(content, str)
                            else ""
                        ),
                    )
        raise ProviderError(f"Ollama {task} 구조화 추출에 실패했습니다.") from last_error

    async def extract(
        self,
        *,
        complex_id: str,
        pages: list[CandidatePage],
        unit_type_id: str | None,
        unit_type_name: str | None,
        sale_price_manwon: int | None,
    ) -> ExtractionDraft:
        target_context = (
            f"complex_id={complex_id}\n"
            f"unit_type_id={unit_type_id or '미지정'}\n"
            f"unit_type_name={unit_type_name or '미지정'}\n"
            "sale_price_manwon="
            f"{sale_price_manwon if sale_price_manwon is not None else '미지정'}"
        )
        results: dict[str, StrictModel] = {}
        async with self._client() as client:
            for task, categories, result_type in TASKS:
                if task == "cost" and unit_type_name is None:
                    results[task] = _CostExtraction(findings=[])
                    continue
                document = _task_document(
                    pages,
                    categories=categories,
                    max_chars=min(
                        self.settings.ollama_chunk_max_chars,
                        6_000 if task == "cost" else self.settings.ollama_chunk_max_chars,
                    ),
                    unit_type_name=unit_type_name,
                )
                if not document and task == "cost":
                    results[task] = _CostExtraction(findings=[])
                    continue
                results[task] = await self._extract_task(
                    client,
                    task=task,
                    result_type=result_type,
                    target_context=target_context,
                    document=document,
                )

        payment = cast(_PaymentExtraction, results["payment"])
        loan = cast(_LoanExtraction, results["loan"])
        cost = cast(_CostExtraction, results["cost"])
        additional_costs = [
            AdditionalCost.model_validate(
                item.model_dump(exclude={"evidence_page", "evidence_raw_text"})
            )
            for item in cost.findings
        ]
        cost_evidence = [
            Evidence(
                field=f"/additional_costs/{index}",
                page=item.evidence_page,
                raw_text=item.evidence_raw_text,
            )
            for index, item in enumerate(cost.findings)
        ]
        draft = ExtractionDraft(
            payment_schedule=payment.payment_schedule,
            interim_loan=loan.interim_loan,
            additional_costs=additional_costs,
            evidence=_deduplicate_evidence(payment.evidence + loan.evidence + cost_evidence),
            exception_flags=list(dict.fromkeys(loan.exception_flags)),
        )
        return ground_ollama_draft(
            draft,
            pages=pages,
            unit_type_name=unit_type_name,
            sale_price_manwon=sale_price_manwon,
        )
