from __future__ import annotations

from dataclasses import dataclass

from get_myhome_ai.pdf_text import PdfPage

CATEGORY_TERMS: dict[str, tuple[str, ...]] = {
    "payment": ("계약금", "중도금", "잔금", "공급금액", "납부일정", "납부 일정"),
    "loan": (
        "중도금 대출",
        "중도금대출",
        "융자",
        "대출 알선",
        "대출취급기관",
        "자납",
        "이자후불",
        "무이자",
        "금융기관",
    ),
    "cost": (
        "발코니 확장",
        "발코니확장",
        "추가선택품목",
        "유상옵션",
        "시스템에어컨",
        "추가 비용",
    ),
    "balance": ("입주지정일", "입주예정", "잔금 납부", "입주 지정"),
}


@dataclass(frozen=True)
class CandidatePage:
    number: int
    text: str
    score: int
    categories: frozenset[str]


def _classify(page: PdfPage) -> CandidatePage | None:
    categories: set[str] = set()
    score = 0
    for category, terms in CATEGORY_TERMS.items():
        hits = sum(page.text.count(term) for term in terms)
        if hits:
            categories.add(category)
            score += hits

    if all(term in page.text for term in ("계약금", "중도금", "잔금")):
        categories.add("payment")
        score += 12
    if "중도금" in page.text and any(
        term in page.text for term in ("대출", "알선", "자납", "융자", "이자")
    ):
        categories.add("loan")
        score += 10
    if not categories:
        return None
    return CandidatePage(page.number, page.text, score, frozenset(categories))


def _compact_text(text: str, max_chars: int = 16_000) -> str:
    if len(text) <= max_chars:
        return text

    positions = sorted(
        {
            index
            for terms in CATEGORY_TERMS.values()
            for term in terms
            if (index := text.find(term)) >= 0
        }
    )
    windows: list[tuple[int, int]] = [(0, min(4_000, len(text)))]
    windows.extend((max(0, pos - 2_000), min(len(text), pos + 4_000)) for pos in positions)
    merged: list[tuple[int, int]] = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    compacted = "\n…\n".join(text[start:end] for start, end in merged)
    return compacted[:max_chars]


def select_candidate_pages(
    pages: list[PdfPage], *, max_pages: int, max_chars: int
) -> list[CandidatePage]:
    direct = [candidate for page in pages if (candidate := _classify(page))]
    if not direct:
        return []

    selected_numbers: set[int] = set()
    for category in CATEGORY_TERMS:
        category_pages = sorted(
            (page for page in direct if category in page.categories),
            key=lambda page: (-page.score, page.number),
        )
        for page in category_pages[:4]:
            if len(selected_numbers) >= max_pages:
                break
            selected_numbers.add(page.number)

    for page in sorted(direct, key=lambda item: (-item.score, item.number)):
        if len(selected_numbers) >= max_pages:
            break
        selected_numbers.add(page.number)

    direct_by_number = {page.number: page for page in direct}
    for number in sorted(tuple(selected_numbers)):
        for neighbor in (number - 1, number + 1):
            if 1 <= neighbor <= len(pages) and len(selected_numbers) < max_pages:
                selected_numbers.add(neighbor)

    result: list[CandidatePage] = []
    used_chars = 0
    for number in sorted(selected_numbers):
        source = pages[number - 1]
        classified = direct_by_number.get(number)
        text = _compact_text(source.text)
        remaining = max_chars - used_chars
        if remaining <= 0:
            break
        text = text[:remaining]
        if not text.strip():
            continue
        result.append(
            CandidatePage(
                number=number,
                text=text,
                score=classified.score if classified else 0,
                categories=classified.categories if classified else frozenset({"context"}),
            )
        )
        used_chars += len(text)
    return result


def format_candidate_document(pages: list[CandidatePage]) -> str:
    return "\n\n".join(f"[PAGE {page.number}]\n{page.text}" for page in pages)
