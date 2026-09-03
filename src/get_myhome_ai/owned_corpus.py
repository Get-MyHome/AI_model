from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

SCHEMA_VERSION = "owned_corpus_inventory_v1"
_DETAIL_FILENAME = re.compile(r"(?P<complex_id>\d+)_detail\.html")
_MANAGEMENT_NUMBER = re.compile(
    r"(?P<complex_id>\d+)\s*\(\s*(?P<unit_type_id>[^()]+?)\s*\)"
)
_PRICE = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)")
_PAGES = re.compile(r"^Pages:\s*(?P<count>\d+)\s*$", re.MULTILINE)


class OwnedCorpusError(ValueError):
    """Raised when an owned corpus cannot be inventoried without guessing."""


@dataclass(frozen=True)
class UnitTuple:
    unit_type_id: str
    unit_type_name: str
    sale_price_manwon: int

    def to_dict(self) -> dict[str, str | int]:
        return {
            "unit_type_id": self.unit_type_id,
            "unit_type_name": self.unit_type_name,
            "sale_price_manwon": self.sale_price_manwon,
        }


@dataclass(frozen=True)
class _Cell:
    text: str
    is_header: bool


@dataclass
class _Table:
    caption: str = ""
    rows: list[list[_Cell]] | None = None

    def __post_init__(self) -> None:
        if self.rows is None:
            self.rows = []


class _DetailParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[_Table] = []
        self.hrefs: list[str] = []
        self._table: _Table | None = None
        self._row: list[_Cell] | None = None
        self._cell_tag: str | None = None
        self._cell_parts: list[str] = []
        self._caption_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)
        if tag == "table":
            if self._table is not None:
                raise OwnedCorpusError("nested HTML tables are not supported")
            self._table = _Table()
        elif self._table is not None and tag == "caption":
            self._caption_parts = []
        elif self._table is not None and tag == "tr":
            if self._row is not None:
                raise OwnedCorpusError("nested HTML table rows are not supported")
            self._row = []
        elif self._row is not None and tag in {"td", "th"}:
            if self._cell_tag is not None:
                raise OwnedCorpusError("nested HTML table cells are not supported")
            self._cell_tag = tag
            self._cell_parts = []
        elif tag == "br":
            if self._cell_tag is not None:
                self._cell_parts.append(" ")
            elif self._caption_parts is not None:
                self._caption_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell_tag is not None:
            self._cell_parts.append(data)
        elif self._caption_parts is not None:
            self._caption_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and tag == self._cell_tag:
            assert self._row is not None
            self._row.append(
                _Cell(text=_normalized_text(self._cell_parts), is_header=tag == "th")
            )
            self._cell_tag = None
            self._cell_parts = []
        elif tag == "tr" and self._row is not None:
            assert self._table is not None
            assert self._table.rows is not None
            if self._row:
                self._table.rows.append(self._row)
            self._row = None
        elif tag == "caption" and self._caption_parts is not None:
            assert self._table is not None
            self._table.caption = _normalized_text(self._caption_parts)
            self._caption_parts = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None


def _normalized_text(parts: Sequence[str]) -> str:
    return " ".join("".join(parts).split())


def _single_table(parser: _DetailParser, caption: str) -> _Table:
    tables = [table for table in parser.tables if table.caption == caption]
    if len(tables) != 1:
        raise OwnedCorpusError(
            f"expected exactly one table captioned {caption!r}, found {len(tables)}"
        )
    return tables[0]


def _complex_name(parser: _DetailParser) -> str:
    table = _single_table(parser, "입주자모집공고 주요정보")
    assert table.rows is not None
    names = [cell.text for row in table.rows for cell in row if cell.is_header and cell.text]
    if not names:
        raise OwnedCorpusError("announcement name is missing from the primary information table")
    return names[0]


def _price_rows(parser: _DetailParser) -> dict[str, int]:
    table = _single_table(parser, "공급금액, 2순위 청약금")
    assert table.rows is not None
    prices: dict[str, int] = {}
    for row in table.rows:
        if len(row) < 2 or row[0].is_header or row[1].is_header:
            continue
        unit_type_name = row[0].text
        raw_price = row[1].text
        if not unit_type_name or _PRICE.fullmatch(raw_price) is None:
            continue
        price = int(raw_price.replace(",", ""))
        if unit_type_name in prices:
            raise OwnedCorpusError(f"duplicate sale-price row for unit type {unit_type_name!r}")
        prices[unit_type_name] = price
    if not prices:
        raise OwnedCorpusError("sale-price table contains no unit rows")
    return prices


def _unit_tuples(parser: _DetailParser, expected_complex_id: str) -> tuple[UnitTuple, ...]:
    prices = _price_rows(parser)
    supply = _single_table(parser, "입주자모집공고 공급대상")
    assert supply.rows is not None
    tuples: list[UnitTuple] = []
    seen_ids: set[str] = set()
    matched_price_names: set[str] = set()

    for row in supply.rows:
        management_cells = [
            match
            for cell in row
            if not cell.is_header
            and (match := _MANAGEMENT_NUMBER.fullmatch(cell.text)) is not None
        ]
        if not management_cells:
            continue
        if len(management_cells) != 1:
            raise OwnedCorpusError("supply row contains multiple management/model numbers")
        management = management_cells[0]
        complex_id = management.group("complex_id")
        if complex_id != expected_complex_id:
            raise OwnedCorpusError(
                f"detail filename id {expected_complex_id} does not match "
                f"supply row id {complex_id}"
            )
        unit_type_id = management.group("unit_type_id").strip()
        if not unit_type_id:
            raise OwnedCorpusError("empty unit type id in supply table")
        if unit_type_id in seen_ids:
            raise OwnedCorpusError(f"duplicate unit type id {unit_type_id!r}")

        matching_names = {cell.text for cell in row if cell.text in prices}
        if len(matching_names) != 1:
            raise OwnedCorpusError(
                f"unit {unit_type_id!r} matches {len(matching_names)} sale-price rows"
            )
        unit_type_name = matching_names.pop()
        tuples.append(
            UnitTuple(
                unit_type_id=unit_type_id,
                unit_type_name=unit_type_name,
                sale_price_manwon=prices[unit_type_name],
            )
        )
        seen_ids.add(unit_type_id)
        matched_price_names.add(unit_type_name)

    if not tuples:
        raise OwnedCorpusError("supply table contains no unit/model rows")
    unmatched_prices = set(prices) - matched_price_names
    if unmatched_prices:
        raise OwnedCorpusError(
            "sale-price rows without an exact supply/model row: "
            + ", ".join(sorted(unmatched_prices))
        )
    if len(matched_price_names) != len(tuples):
        raise OwnedCorpusError("multiple supply/model rows use the same unit type name")
    return tuple(
        sorted(
            tuples,
            key=lambda item: (
                item.unit_type_id,
                item.unit_type_name,
                item.sale_price_manwon,
            ),
        )
    )


def _expected_pdf_filename(parser: _DetailParser, expected_complex_id: str) -> str | None:
    filenames: set[str] = set()
    for href in parser.hrefs:
        parsed = urlparse(href)
        if not parsed.path.endswith("/getAtchmnfl.do"):
            continue
        query = parse_qs(parsed.query)
        house_ids = query.get("houseManageNo", [])
        attachment_numbers = query.get("atchmnflSn", [])
        if house_ids != [expected_complex_id]:
            raise OwnedCorpusError("attachment link houseManageNo does not match detail filename")
        if len(attachment_numbers) != 1 or not attachment_numbers[0].isdigit():
            raise OwnedCorpusError("attachment link has no unambiguous numeric atchmnflSn")
        filenames.add(f"{expected_complex_id}_{attachment_numbers[0]}.pdf")
    if len(filenames) > 1:
        raise OwnedCorpusError("detail HTML refers to multiple announcement PDF attachments")
    return next(iter(filenames), None)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_pdf_page_count(path: Path) -> int:
    """Return the physical PDF page count reported by Poppler's pdfinfo."""

    try:
        completed = subprocess.run(
            ["pdfinfo", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
            env=os.environ | {"LC_ALL": "C"},
        )
    except FileNotFoundError as exc:
        raise OwnedCorpusError("pdfinfo is required to count source PDF pages") from exc
    except subprocess.TimeoutExpired as exc:
        raise OwnedCorpusError(f"pdfinfo timed out for {path.name}") from exc
    if completed.returncode != 0:
        message = completed.stderr.strip().splitlines()
        detail = message[0] if message else "unknown pdfinfo error"
        raise OwnedCorpusError(f"cannot inspect {path.name}: {detail}")
    match = _PAGES.search(completed.stdout)
    if match is None or int(match.group("count")) < 1:
        raise OwnedCorpusError(f"pdfinfo returned no positive page count for {path.name}")
    return int(match.group("count"))


def _source_pdf(
    source_directory: Path,
    *,
    complex_id: str,
    expected_filename: str | None,
) -> Path | None:
    candidates = sorted(
        path for path in source_directory.glob(f"{complex_id}_*.pdf") if path.is_file()
    )
    if expected_filename is not None:
        expected_path = source_directory / expected_filename
        unexpected = [path for path in candidates if path.name != expected_filename]
        if unexpected:
            names = ", ".join(path.name for path in unexpected)
            raise OwnedCorpusError(
                f"{complex_id}: stored PDF(s) do not match the HTML attachment: {names}"
            )
        return expected_path if expected_path.is_file() else None
    if len(candidates) > 1:
        raise OwnedCorpusError(f"{complex_id}: multiple stored PDFs and no exact HTML attachment")
    return candidates[0] if candidates else None


def build_owned_corpus_inventory(
    source_directory: Path,
    *,
    page_counter: Callable[[Path], int] = read_pdf_page_count,
) -> dict[str, Any]:
    """Build a source-locked coverage inventory without granting review approval."""

    root = source_directory.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise OwnedCorpusError(f"source directory is not a directory: {root}")
    detail_paths = sorted(root.glob("*_detail.html"), key=lambda path: path.name)
    if not detail_paths:
        raise OwnedCorpusError(f"no *_detail.html files found in {root}")

    documents: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    seen_complex_ids: set[str] = set()
    for detail_path in detail_paths:
        filename_match = _DETAIL_FILENAME.fullmatch(detail_path.name)
        if filename_match is None:
            raise OwnedCorpusError(f"invalid detail HTML filename: {detail_path.name}")
        complex_id = filename_match.group("complex_id")
        if complex_id in seen_complex_ids:
            raise OwnedCorpusError(f"duplicate detail HTML for complex id {complex_id}")
        seen_complex_ids.add(complex_id)

        parser = _DetailParser()
        parser.feed(detail_path.read_text(encoding="utf-8"))
        parser.close()
        unit_tuples = _unit_tuples(parser, complex_id)
        expected_pdf_filename = _expected_pdf_filename(parser, complex_id)
        pdf_path = _source_pdf(
            root,
            complex_id=complex_id,
            expected_filename=expected_pdf_filename,
        )
        pdf_available = pdf_path is not None
        source_sha256 = _sha256(pdf_path) if pdf_path is not None else None
        source_page_count = page_counter(pdf_path) if pdf_path is not None else None
        if source_page_count is not None and source_page_count < 1:
            raise OwnedCorpusError(f"{pdf_path.name}: page counter returned a non-positive value")

        detail_relative = detail_path.relative_to(root).as_posix()
        pdf_relative = pdf_path.relative_to(root).as_posix() if pdf_path is not None else None
        document = {
            "complex_id": complex_id,
            "complex_name": _complex_name(parser),
            "detail_html_path": detail_relative,
            "detail_html_sha256": _sha256(detail_path),
            "expected_pdf_path": expected_pdf_filename,
            "pdf_available": pdf_available,
            "pdf_path": pdf_relative,
            "source_sha256": source_sha256,
            "source_page_count": source_page_count,
            "unit_tuple_count": len(unit_tuples),
            "unit_tuples": [item.to_dict() for item in unit_tuples],
        }
        documents.append(document)
        for item in unit_tuples:
            targets.append(
                {
                    "complex_id": complex_id,
                    **item.to_dict(),
                    "pdf_available": pdf_available,
                    "detail_html_path": detail_relative,
                    "pdf_path": pdf_relative,
                    "source_sha256": source_sha256,
                    "source_page_count": source_page_count,
                }
            )

    targets.sort(
        key=lambda item: (
            item["complex_id"],
            item["unit_type_id"],
            item["unit_type_name"],
            item["sale_price_manwon"],
        )
    )
    pdf_documents = sum(document["pdf_available"] for document in documents)
    pdf_targets = sum(target["pdf_available"] for target in targets)
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": "OWNED_CORPUS_COVERAGE_INVENTORY",
        "source_directory": str(root),
        "summary": {
            "detail_html_document_count": len(documents),
            "pdf_document_count": pdf_documents,
            "html_only_document_count": len(documents) - pdf_documents,
            "all_unit_tuple_count": len(targets),
            "pdf_backed_unit_tuple_count": pdf_targets,
            "html_only_unit_tuple_count": len(targets) - pdf_targets,
            "html_only_complex_ids": [
                document["complex_id"]
                for document in documents
                if not document["pdf_available"]
            ],
        },
        "documents": documents,
        "targets": targets,
    }


def serialize_inventory(inventory: dict[str, Any]) -> str:
    return json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_inventory(inventory: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = serialize_inventory(inventory)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        delete=False,
    ) as temporary:
        temporary.write(payload)
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, destination)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a deterministic, non-approving inventory of stored detail HTML/PDFs."
    )
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument(
        "--output",
        default="-",
        help="JSON destination, or '-' for stdout (default: '-').",
    )
    args = parser.parse_args(argv)
    try:
        inventory = build_owned_corpus_inventory(args.source_dir)
        if args.output == "-":
            sys.stdout.write(serialize_inventory(inventory))
        else:
            write_inventory(inventory, Path(args.output))
    except (OSError, UnicodeError, OwnedCorpusError) as exc:
        parser.exit(2, f"owned-corpus inventory failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
