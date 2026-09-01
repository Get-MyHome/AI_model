from get_myhome_ai.candidates import select_candidate_pages
from get_myhome_ai.pdf_text import PdfPage


def test_selects_distant_categories_and_respects_limits() -> None:
    pages = [PdfPage(number=index, text="일반 내용") for index in range(1, 41)]
    pages[4] = PdfPage(number=5, text="계약금 중도금 잔금 공급금액 납부일정")
    pages[29] = PdfPage(number=30, text="중도금 대출 알선 자납 이자후불 금융기관")
    pages[37] = PdfPage(number=38, text="발코니 확장 추가선택품목 유상옵션")

    selected = select_candidate_pages(pages, max_pages=9, max_chars=10_000)
    numbers = {page.number for page in selected}

    assert {5, 30, 38} <= numbers
    assert len(selected) <= 9
    assert sum(len(page.text) for page in selected) <= 10_000


def test_exact_move_in_month_heading_is_reserved_before_high_frequency_boilerplate() -> None:
    pages = [
        PdfPage(number=index, text=f"입주지정일 잔금 납부 입주예정 {index} " * 20)
        for index in range(1, 12)
    ]
    pages[4] = PdfPage(number=5, text="■ 입주시기 : 2029년 08월 예정")

    selected = select_candidate_pages(pages, max_pages=4, max_chars=20_000)

    assert 5 in {page.number for page in selected}
