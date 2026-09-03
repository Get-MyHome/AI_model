from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from get_myhome_ai.owned_corpus import (
    OwnedCorpusError,
    build_owned_corpus_inventory,
    read_pdf_page_count,
    serialize_inventory,
    write_inventory,
)


def _detail_html(
    complex_id: str,
    *,
    name: str,
    rows: list[tuple[str, str, str]],
    attachment_number: str | None,
) -> str:
    attachment = ""
    if attachment_number is not None:
        attachment = (
            '<a href="https://static.applyhome.co.kr/ai/aia/getAtchmnfl.do?'
            f"houseManageNo={complex_id}&amp;pblancNo={complex_id}&amp;"
            f'atchmnflSeqNo=10&amp;atchmnflSn={attachment_number}">모집공고문 보기</a>'
        )
    supply_rows = "".join(
        "<tr>"
        + ("<td rowspan=\"2\">민영</td>" if index == 0 else "")
        + f"<td>{unit_name}</td><td>80.0000</td><td>1</td><td>2</td><td>3</td>"
        + f"<td>{complex_id}({unit_id})</td></tr>"
        for index, (unit_id, unit_name, _price) in enumerate(rows)
    )
    price_rows = "".join(
        f'<tr><td>{unit_name}</td><td class="txt_r">{price}</td><td>없음</td></tr>'
        for _unit_id, unit_name, price in reversed(rows)
    )
    return f"""<!doctype html><html><body>
    <table><caption>입주자모집공고 주요정보</caption><tr><th>{name}</th></tr></table>
    {attachment}
    <table><caption>입주자모집공고 공급대상</caption>
      <tr><th>주택형</th><th>주택관리번호(모델번호)</th></tr>{supply_rows}
    </table>
    <table><caption>공급금액, 2순위 청약금</caption>
      <tr><th>주택형</th><th>공급금액(최고가 기준)</th></tr>{price_rows}
    </table>
    </body></html>"""


def test_builds_sorted_exact_targets_and_pdf_coverage(tmp_path: Path) -> None:
    pdf_content = b"%PDF-owned-corpus"
    (tmp_path / "200_3.pdf").write_bytes(pdf_content)
    (tmp_path / "200_detail.html").write_text(
        _detail_html(
            "200",
            name="두 번째 단지",
            rows=[("02", "084.0000B", "52,000"), ("01", "059.0000A", "41,500")],
            attachment_number="3",
        ),
        encoding="utf-8",
    )
    (tmp_path / "100_detail.html").write_text(
        _detail_html(
            "100",
            name="첫 번째 단지",
            rows=[("01", "055.0000A", "39,900")],
            attachment_number=None,
        ),
        encoding="utf-8",
    )

    counted: list[str] = []

    def page_counter(path: Path) -> int:
        counted.append(path.name)
        return 71

    inventory = build_owned_corpus_inventory(tmp_path, page_counter=page_counter)

    assert inventory["schema_version"] == "owned_corpus_inventory_v1"
    assert inventory["summary"] == {
        "detail_html_document_count": 2,
        "pdf_document_count": 1,
        "html_only_document_count": 1,
        "all_unit_tuple_count": 3,
        "pdf_backed_unit_tuple_count": 2,
        "html_only_unit_tuple_count": 1,
        "html_only_complex_ids": ["100"],
    }
    assert counted == ["200_3.pdf"]
    assert [target["complex_id"] for target in inventory["targets"]] == ["100", "200", "200"]
    assert [target["unit_type_id"] for target in inventory["targets"]] == ["01", "01", "02"]

    missing_pdf = inventory["targets"][0]
    assert missing_pdf == {
        "complex_id": "100",
        "unit_type_id": "01",
        "unit_type_name": "055.0000A",
        "sale_price_manwon": 39_900,
        "pdf_available": False,
        "detail_html_path": "100_detail.html",
        "pdf_path": None,
        "source_sha256": None,
        "source_page_count": None,
    }
    pdf_target = inventory["targets"][1]
    assert pdf_target["sale_price_manwon"] == 41_500
    assert pdf_target["pdf_available"] is True
    assert pdf_target["pdf_path"] == "200_3.pdf"
    assert pdf_target["source_sha256"] == hashlib.sha256(pdf_content).hexdigest()
    assert pdf_target["source_page_count"] == 71
    assert inventory["documents"][1]["expected_pdf_path"] == "200_3.pdf"


def test_serialization_is_deterministic_and_cannot_signal_review_approval(tmp_path: Path) -> None:
    (tmp_path / "100_detail.html").write_text(
        _detail_html(
            "100",
            name="단지",
            rows=[("01", "059.0000A", "41,500")],
            attachment_number=None,
        ),
        encoding="utf-8",
    )
    first = build_owned_corpus_inventory(tmp_path, page_counter=lambda _path: 1)
    second = build_owned_corpus_inventory(tmp_path, page_counter=lambda _path: 99)

    first_payload = serialize_inventory(first)
    second_payload = serialize_inventory(second)
    assert first_payload == second_payload
    assert "REVIEWED" not in first_payload
    assert "review_status" not in first_payload

    destination = tmp_path / "output" / "inventory.json"
    write_inventory(first, destination)
    assert destination.read_text(encoding="utf-8") == first_payload
    assert json.loads(first_payload) == first


def test_rejects_stored_pdf_that_does_not_match_html_attachment(tmp_path: Path) -> None:
    (tmp_path / "100_detail.html").write_text(
        _detail_html(
            "100",
            name="단지",
            rows=[("01", "059.0000A", "41,500")],
            attachment_number="3",
        ),
        encoding="utf-8",
    )
    (tmp_path / "100_4.pdf").write_bytes(b"%PDF-wrong-attachment")

    with pytest.raises(OwnedCorpusError, match="do not match the HTML attachment"):
        build_owned_corpus_inventory(tmp_path, page_counter=lambda _path: 1)


def test_rejects_price_row_without_exact_supply_model_row(tmp_path: Path) -> None:
    html = _detail_html(
        "100",
        name="단지",
        rows=[("01", "059.0000A", "41,500")],
        attachment_number=None,
    ).replace(
        "</table>\n    </body>",
        '<tr><td>084.0000B</td><td>52,000</td><td>없음</td></tr></table>\n    </body>',
    )
    (tmp_path / "100_detail.html").write_text(html, encoding="utf-8")

    with pytest.raises(OwnedCorpusError, match="without an exact supply/model row"):
        build_owned_corpus_inventory(tmp_path, page_counter=lambda _path: 1)


def test_pdf_page_count_uses_pdfinfo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pdf_path = tmp_path / "source.pdf"
    pdf_path.write_bytes(b"%PDF")

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            ["pdfinfo"], 0, stdout="Title: x\nPages: 52\n", stderr=""
        )

    monkeypatch.setattr("get_myhome_ai.owned_corpus.subprocess.run", fake_run)
    assert read_pdf_page_count(pdf_path) == 52
