#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from get_myhome_ai.candidates import select_candidate_pages
from get_myhome_ai.models import LoanSettlementRequirement, RiskClauseCode
from get_myhome_ai.normalization import normalize_draft
from get_myhome_ai.pdf_text import extract_pdf_pages, load_pdf_from_path
from get_myhome_ai.pipeline import _empty_draft
from get_myhome_ai.providers.ollama_grounding import ground_ollama_draft
from get_myhome_ai.settings import Settings

ALL_IDS = {
    "2026000282",
    "2026000291",
    "2026000293",
    "2026000295",
    "2026000312",
    "2026000315",
    "2026000316",
    "2026000318",
    "2026000323",
    "2026000327",
    "2026000331",
    "2026000342",
    "2026000354",
    "2026000355",
    "2026000356",
    "2026000358",
    "2026000364",
    "2026000365",
    "2026000367",
    "2026000368",
    "2026000371",
    "2026000372",
    "2026000374",
    "2026000376",
    "2026000377",
    "2026000382",
    "2026000383",
}
INTEREST_IDS = {
    "2026000282",
    "2026000293",
    "2026000295",
    "2026000312",
    "2026000315",
    "2026000316",
    "2026000318",
    "2026000323",
    "2026000327",
    "2026000331",
    "2026000342",
    "2026000354",
    "2026000355",
    "2026000356",
    "2026000358",
    "2026000364",
    "2026000365",
    "2026000367",
    "2026000368",
    "2026000371",
    "2026000372",
    "2026000374",
    "2026000377",
    "2026000382",
    "2026000383",
}
SELF_FUNDING_IDS = {
    "2026000355",
    "2026000358",
    "2026000364",
    "2026000372",
    "2026000374",
    "2026000377",
}
RISK_CODES = set(RiskClauseCode)
REPO_ROOT = Path(__file__).resolve().parents[1]


def _expected_settlement(complex_id: str) -> LoanSettlementRequirement:
    if complex_id == "2026000323":
        return LoanSettlementRequirement.NOT_STATED
    if complex_id == "2026000376":
        return LoanSettlementRequirement.NOT_APPLICABLE
    return LoanSettlementRequirement.REPAY_OR_CONVERT_TO_MORTGAGE


def _expected_risks(complex_id: str) -> set[RiskClauseCode]:
    if complex_id == "2026000376":
        return {RiskClauseCode.LOAN_NOT_AVAILABLE}
    result = {
        RiskClauseCode.LOAN_MEDIATION_NOT_GUARANTEED,
        RiskClauseCode.INDIVIDUAL_REVIEW_REQUIRED,
    }
    if complex_id in INTEREST_IDS:
        result.add(RiskClauseCode.INTEREST_PAYMENT_RISK)
    if complex_id in SELF_FUNDING_IDS:
        result.add(RiskClauseCode.SELF_FUNDING_REQUIRED)
    return result


def _default_pdf_dir() -> Path:
    configured = os.environ.get("GOLDEN_PDF_DIR")
    if configured:
        return Path(configured)
    return Path(
        "/home/soccz/22tb/tmp/claude-1001/"
        "-mnt-20t-AI----/291eec41-8358-4bc9-b1f0-ced7d4ee1d23/"
        "scratchpad/gonggo"
    )


def _expected_sha256() -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted((REPO_ROOT / "evaluation/reference").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        result[str(payload["complex_id"])] = str(payload["source"]["pdf_sha256"])
    golden_manifest = json.loads(
        (REPO_ROOT / "tests/fixtures/golden/MANIFEST.json").read_text(encoding="utf-8")
    )
    for filename, metadata in golden_manifest.items():
        result[filename.split("_")[0]] = str(metadata["sha256"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-dir", type=Path, default=_default_pdf_dir())
    args = parser.parse_args()
    settings = Settings(ai_provider="fixture")
    paths = {path.name.split("_")[0]: path for path in args.pdf_dir.glob("*.pdf")}
    missing = sorted(ALL_IDS - paths.keys())
    if missing:
        raise SystemExit(f"missing PDFs: {', '.join(missing)}")

    cases: list[dict[str, object]] = []
    correct_labels = 0
    total_labels = len(ALL_IDS) * (1 + len(RISK_CODES))
    evidence_quote_total = 0
    evidence_quote_correct = 0
    settlement_evidence_total = 0
    settlement_evidence_correct = 0
    source_sha_matches = 0
    expected_sha = _expected_sha256()
    for complex_id in sorted(ALL_IDS):
        downloaded = load_pdf_from_path(paths[complex_id], settings)
        actual_sha256 = hashlib.sha256(downloaded.content).hexdigest()
        source_match = actual_sha256 == expected_sha.get(complex_id)
        source_sha_matches += int(source_match)
        pages = extract_pdf_pages(downloaded.content, settings)
        candidates = select_candidate_pages(
            pages,
            max_pages=settings.max_candidate_pages,
            max_chars=settings.max_candidate_chars,
        )
        grounded = ground_ollama_draft(
            _empty_draft(),
            pages=candidates,
            unit_type_name=None,
            sale_price_manwon=None,
        )
        normalized, _ = normalize_draft(grounded)
        actual_settlement = normalized.interim_loan.settlement_requirement
        expected_settlement = _expected_settlement(complex_id)
        actual_risks = {item.code for item in normalized.risk_clauses}
        expected_risks = _expected_risks(complex_id)
        settlement_match = actual_settlement == expected_settlement
        correct_labels += int(settlement_match)
        correct_labels += sum(
            (code in actual_risks) == (code in expected_risks) for code in RISK_CODES
        )

        page_text = {page.number: "".join(page.text.split()) for page in pages}
        evidence_matches = []
        for risk in normalized.risk_clauses:
            for evidence in risk.evidence:
                evidence_quote_total += 1
                matched = "".join(evidence.raw_text.split()) in page_text[evidence.page]
                evidence_quote_correct += int(matched)
                evidence_matches.append(matched)
        settlement_fields = {
            "/interim_loan/settlement_requirement",
            "/interim_loan/settlement_deadline_text",
        }
        if complex_id == "2026000376":
            settlement_fields = {"/interim_loan/arrangement_status"}
        elif complex_id == "2026000323":
            settlement_fields = set()
        settlement_matches = []
        for evidence in normalized.evidence:
            if evidence.field not in settlement_fields:
                continue
            settlement_evidence_total += 1
            matched = "".join(evidence.raw_text.split()) in page_text[evidence.page]
            settlement_evidence_correct += int(matched)
            settlement_matches.append(matched)
        cases.append(
            {
                "complex_id": complex_id,
                "settlement_expected": expected_settlement.value,
                "settlement_actual": actual_settlement.value,
                "risks_expected": sorted(code.value for code in expected_risks),
                "risks_actual": sorted(code.value for code in actual_risks),
                "exact_match": settlement_match and actual_risks == expected_risks,
                "evidence_quotes_valid": all(evidence_matches),
                "settlement_evidence_quotes_valid": all(settlement_matches),
                "source_sha256_match": source_match,
            }
        )

    exact_cases = sum(bool(case["exact_match"]) for case in cases)
    report = {
        "scope": "27 real announcement PDFs; deterministic settlement and risk labels",
        "reference": "manual page-by-page review on 2026-09-02",
        "model_used_for_this_measurement": False,
        "document_exact_matches": exact_cases,
        "document_count": len(cases),
        "label_accuracy": correct_labels / total_labels,
        "correct_labels": correct_labels,
        "total_labels": total_labels,
        "evidence_quote_accuracy": (
            evidence_quote_correct / evidence_quote_total if evidence_quote_total else None
        ),
        "evidence_quote_correct": evidence_quote_correct,
        "evidence_quote_total": evidence_quote_total,
        "settlement_evidence_quote_accuracy": (
            settlement_evidence_correct / settlement_evidence_total
            if settlement_evidence_total
            else None
        ),
        "settlement_evidence_quote_correct": settlement_evidence_correct,
        "settlement_evidence_quote_total": settlement_evidence_total,
        "source_sha256_matches": source_sha_matches,
        "source_document_count": len(ALL_IDS),
        "known_limits": [
            "TERMS_DIFFER_BY_HOUSING_TYPE has zero positive examples in this corpus.",
            "Bank-guidance document extraction and cross-document comparison are not validated.",
            "This measurement does not replace the separate Qwen core-field evaluation.",
        ],
        "cases": cases,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return (
        0
        if exact_cases == len(cases)
        and correct_labels == total_labels
        and evidence_quote_correct == evidence_quote_total
        and settlement_evidence_total == 51
        and settlement_evidence_correct == settlement_evidence_total
        and source_sha_matches == len(ALL_IDS)
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
