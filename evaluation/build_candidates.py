from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

KEYWORDS = {
    "payment_table": ("공급금액", "계약금", "중도금", "잔금"),
    "loan_terms": ("중도금 대출", "중도금대출", "융자알선"),
    "loan_ratio": ("범위 내", "범위내", "자납", "직접 납부"),
    "interest": ("이자후불", "이자 후불", "무이자"),
    "move_in": ("입주시기", "입주 시기"),
}
DATE_PATTERN = re.compile(r"(?:20)?\d{2}[.\-]\d{1,2}[.\-]\d{1,2}")


def _compact(value: str) -> str:
    return " ".join(value.split())


def build_candidates(source_dir: Path) -> dict[str, Any]:
    """Locate candidate pages without creating or approving any ground truth."""
    documents: list[dict[str, Any]] = []
    for path in sorted(source_dir.glob("*.txt")):
        pages = path.read_text(encoding="utf-8", errors="replace").split("\f")
        if pages and not pages[-1].strip():
            pages.pop()
        candidates: list[dict[str, Any]] = []
        for page_number, page in enumerate(pages, start=1):
            compact = _compact(page)
            reasons: list[str] = []
            if all(term in compact for term in KEYWORDS["payment_table"]):
                reasons.append("payment_table")
            for name in ("loan_terms", "loan_ratio", "interest", "move_in"):
                if any(term in compact for term in KEYWORDS[name]):
                    reasons.append(name)
            date_count = len(DATE_PATTERN.findall(compact))
            if date_count >= 5:
                reasons.append("many_dates")
            if not reasons:
                continue
            candidates.append(
                {
                    "page": page_number,
                    "reasons": sorted(set(reasons)),
                    "date_count": date_count,
                    "snippet": compact[:1200],
                    "review_status": "UNREVIEWED",
                }
            )
        documents.append(
            {
                "complex_id": path.name.split("_", 1)[0],
                "text_filename": path.name,
                "candidate_pages": candidates,
            }
        )
    return {
        "artifact_type": "CANDIDATE_PAGES_ONLY",
        "ground_truth": False,
        "warning": (
            "Candidate pages require human source review; "
            "never score model output against this file."
        ),
        "documents": documents,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.dumps(build_candidates(args.source_dir), ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
