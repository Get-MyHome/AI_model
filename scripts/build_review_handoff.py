from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from get_myhome_ai.models import AnalysisResponse, ReviewStatus
from get_myhome_ai.normalization import normalize_unit_type_name
from get_myhome_ai.owned_corpus import read_pdf_page_count
from get_myhome_ai.settings import Settings

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SHA256_PREFIX_PATTERN = re.compile(r"^[0-9a-f]{4,64}$")
INVENTORY_SCHEMA_VERSION = "owned_corpus_inventory_v1"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _require_mapping(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _require_list(value: Any, *, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_inventory_documents(documents: list[dict[str, Any]]) -> None:
    """Reject ambiguous or internally inconsistent inventory identities."""

    seen_complex_ids: set[str] = set()
    for document in documents:
        complex_id = document.get("complex_id")
        if not isinstance(complex_id, str) or not complex_id.strip():
            raise ValueError("inventory document complex_id must be a non-empty string")
        if complex_id in seen_complex_ids:
            raise ValueError(f"duplicate inventory complex_id: {complex_id}")
        seen_complex_ids.add(complex_id)

        pdf_available = document.get("pdf_available")
        if not isinstance(pdf_available, bool):
            raise ValueError(f"{complex_id}: inventory pdf_available must be boolean")
        source_sha256 = document.get("source_sha256")
        source_page_count = document.get("source_page_count")
        pdf_path = document.get("pdf_path")
        if pdf_available:
            if not isinstance(source_sha256, str) or not SHA256_PATTERN.fullmatch(
                source_sha256
            ):
                raise ValueError(f"{complex_id}: inventory source_sha256 is invalid")
            if (
                isinstance(source_page_count, bool)
                or not isinstance(source_page_count, int)
                or source_page_count < 1
            ):
                raise ValueError(f"{complex_id}: inventory source_page_count is invalid")
            if not isinstance(pdf_path, str) or not pdf_path.strip():
                raise ValueError(f"{complex_id}: PDF-backed inventory requires pdf_path")
        elif any(value is not None for value in (source_sha256, source_page_count, pdf_path)):
            raise ValueError(
                f"{complex_id}: unavailable PDF must have null path, SHA-256, and page count"
            )

        units = [
            _require_mapping(item, label=f"inventory document {complex_id} unit tuple")
            for item in _require_list(
                document.get("unit_tuples"),
                label=f"inventory document {complex_id} unit_tuples",
            )
        ]
        seen_targets: set[tuple[str, str, int]] = set()
        for unit in units:
            unit_type_id = unit.get("unit_type_id")
            unit_type_name = unit.get("unit_type_name")
            sale_price = unit.get("sale_price_manwon")
            if not isinstance(unit_type_id, str) or not unit_type_id.strip():
                raise ValueError(f"{complex_id}: unit_type_id must be a non-empty string")
            if not isinstance(unit_type_name, str) or not unit_type_name.strip():
                raise ValueError(f"{complex_id}: unit_type_name must be a non-empty string")
            if isinstance(sale_price, bool) or not isinstance(sale_price, int) or sale_price < 1:
                raise ValueError(f"{complex_id}: sale_price_manwon must be a positive integer")
            normalized_name = normalize_unit_type_name(unit_type_name)
            if normalized_name is None:
                raise ValueError(f"{complex_id}: unit_type_name cannot normalize to null")
            target_key = (unit_type_id, normalized_name, sale_price)
            if target_key in seen_targets:
                raise ValueError(
                    f"{complex_id}: duplicate normalized inventory target "
                    f"({unit_type_id}, {normalized_name}, {sale_price})"
                )
            seen_targets.add(target_key)


def _verify_inventory_sources(
    inventory: dict[str, Any],
    documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Re-hash every PDF-backed source before building an allowlist.

    The inventory generator records an absolute source directory and relative
    PDF paths.  A handoff is an operational allowlist, so missing path metadata,
    a path escape, an unreadable file, or a changed digest must fail closed.
    """

    pdf_backed = [document for document in documents if document["pdf_available"]]
    if not pdf_backed:
        return []

    raw_source_directory = inventory.get("source_directory")
    if not isinstance(raw_source_directory, str) or not raw_source_directory.strip():
        raise ValueError("PDF-backed inventory requires source_directory")
    source_directory_path = Path(raw_source_directory).expanduser()
    if not source_directory_path.is_absolute():
        raise ValueError("inventory source_directory must be absolute")
    try:
        source_directory = source_directory_path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("inventory source_directory is unavailable") from exc
    if not source_directory.is_dir():
        raise ValueError("inventory source_directory must be a directory")

    checks: list[dict[str, Any]] = []
    for document in documents:
        complex_id = document["complex_id"]

        if not document["pdf_available"]:
            continue
        expected_sha256 = document["source_sha256"]
        page_count = document["source_page_count"]

        raw_pdf_path = document["pdf_path"]
        relative_pdf_path = Path(raw_pdf_path)
        if relative_pdf_path.is_absolute():
            raise ValueError(f"{complex_id}: inventory pdf_path must be relative")
        try:
            pdf_path = (source_directory / relative_pdf_path).resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"{complex_id}: inventory PDF is unavailable") from exc
        if not pdf_path.is_relative_to(source_directory) or not pdf_path.is_file():
            raise ValueError(f"{complex_id}: inventory PDF path escapes source_directory")

        actual_sha256 = _sha256_file(pdf_path)
        if actual_sha256 != expected_sha256:
            raise ValueError(f"{complex_id}: inventory PDF SHA-256 mismatch")
        actual_page_count = read_pdf_page_count(pdf_path)
        if actual_page_count != page_count:
            raise ValueError(f"{complex_id}: inventory PDF page count mismatch")
        checks.append(
            {
                "complex_id": complex_id,
                "pdf_path": relative_pdf_path.as_posix(),
                "source_sha256": expected_sha256,
                "source_page_count": page_count,
                "status": "SOURCE_IDENTITY_VERIFIED",
            }
        )
    return checks


def _target_from_inventory(document: dict[str, Any], unit: dict[str, Any]) -> dict[str, Any]:
    return {
        "complex_id": document["complex_id"],
        "unit_type_id": unit["unit_type_id"],
        "unit_type_name": unit["unit_type_name"],
        "sale_price_manwon": unit["sale_price_manwon"],
        "source_sha256": document.get("source_sha256"),
        "source_page_count": document.get("source_page_count"),
    }


def _candidate_target(candidate: AnalysisResponse) -> dict[str, Any]:
    return {
        "complex_id": candidate.complex_id,
        "unit_type_id": candidate.target_unit.unit_type_id,
        "unit_type_name": candidate.target_unit.unit_type_name,
        "sale_price_manwon": candidate.target_unit.sale_price_manwon,
        "source_sha256": candidate.meta.source_sha256,
        "source_page_count": candidate.meta.source_page_count,
    }


def _review_eligibility_reasons(
    candidate: AnalysisResponse,
    *,
    schema_version: str,
    extractor_version: str,
) -> list[str]:
    reasons: list[str] = []
    if candidate.review_status != ReviewStatus.REVIEWED:
        reasons.append("NOT_REVIEWED")
    if not candidate.reviewer or not candidate.reviewer.strip():
        reasons.append("REVIEWER_MISSING")
    if candidate.reviewed_at is None:
        reasons.append("REVIEWED_AT_MISSING")
    elif candidate.reviewed_at.utcoffset() is None:
        reasons.append("REVIEWED_AT_TIMEZONE_MISSING")
    if not candidate.validation.passed:
        reasons.append("VALIDATION_FAILED")
    if candidate.meta.schema_version != schema_version:
        reasons.append("SCHEMA_VERSION_MISMATCH")
    if candidate.meta.extractor_version != extractor_version:
        reasons.append("EXTRACTOR_VERSION_MISMATCH")
    if not SHA256_PATTERN.fullmatch(candidate.meta.source_sha256):
        reasons.append("SOURCE_SHA256_INVALID")
    if candidate.meta.source_page_count < 1:
        reasons.append("SOURCE_PAGE_COUNT_INVALID")
    if candidate.target_unit.unit_type_id is None:
        reasons.append("UNIT_TYPE_ID_MISSING")
    unit_type_name = candidate.target_unit.unit_type_name
    if unit_type_name is None or not unit_type_name.strip():
        reasons.append("UNIT_TYPE_NAME_MISSING")
    elif unit_type_name != normalize_unit_type_name(unit_type_name):
        reasons.append("UNIT_TYPE_NAME_NOT_CANONICAL")
    if candidate.target_unit.sale_price_manwon is None:
        reasons.append("SALE_PRICE_MISSING")
    return reasons


def _matches_target(candidate: AnalysisResponse, target: dict[str, Any]) -> bool:
    return (
        candidate.complex_id == target["complex_id"]
        and candidate.target_unit.unit_type_id == target["unit_type_id"]
        and candidate.target_unit.unit_type_name
        == normalize_unit_type_name(target["unit_type_name"])
        and candidate.target_unit.sale_price_manwon == target["sale_price_manwon"]
        and candidate.meta.source_sha256 == target["source_sha256"]
        and candidate.meta.source_page_count == target["source_page_count"]
    )


def _load_reviewed_candidates(
    reviewed_dir: Path,
    *,
    schema_version: str,
    extractor_version: str,
) -> tuple[list[tuple[str, AnalysisResponse]], list[dict[str, Any]]]:
    eligible: list[tuple[str, AnalysisResponse]] = []
    rejected: list[dict[str, Any]] = []
    if not reviewed_dir.is_dir():
        return eligible, rejected

    for path in sorted(reviewed_dir.glob("*.json")):
        try:
            candidate = AnalysisResponse.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            rejected.append(
                {
                    "artifact_file": path.name,
                    "reasons": ["INVALID_ANALYSIS_ARTIFACT"],
                    "detail": type(exc).__name__,
                }
            )
            continue
        reasons = _review_eligibility_reasons(
            candidate,
            schema_version=schema_version,
            extractor_version=extractor_version,
        )
        if reasons:
            rejected.append(
                {
                    "artifact_file": path.name,
                    "target": _candidate_target(candidate),
                    "reasons": reasons,
                }
            )
            continue
        eligible.append((path.name, candidate))
    return eligible, rejected


def _source_selector(observation: dict[str, Any]) -> tuple[str | None, str | None]:
    full_hash = observation.get("source_sha256")
    prefix = observation.get("source_sha256_prefix")
    if full_hash is not None:
        if not isinstance(full_hash, str) or not SHA256_PATTERN.fullmatch(full_hash.lower()):
            raise ValueError("observation source_sha256 must be 64 lowercase hex characters")
        full_hash = full_hash.lower()
    if prefix is not None:
        if not isinstance(prefix, str) or not SHA256_PREFIX_PATTERN.fullmatch(prefix.lower()):
            raise ValueError("observation source_sha256_prefix must be 4-64 hex characters")
        prefix = prefix.lower()
    if full_hash is not None and prefix is not None and not full_hash.startswith(prefix):
        raise ValueError("observation SHA-256 and prefix disagree")
    return full_hash, prefix


def _check_live_observation(
    observation: dict[str, Any],
    *,
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    full_hash, prefix = _source_selector(observation)
    page_count = observation.get("source_page_count")
    if page_count is not None and (not isinstance(page_count, int) or page_count < 1):
        raise ValueError("observation source_page_count must be a positive integer")

    observed_hash_selector = full_hash or prefix
    pdf_documents = [document for document in documents if document["pdf_available"]]

    def matches_observed(document: dict[str, Any]) -> bool:
        digest = document["source_sha256"]
        hash_matches = observed_hash_selector is None or digest.startswith(observed_hash_selector)
        pages_match = page_count is None or document.get("source_page_count") == page_count
        return hash_matches and pages_match

    corpus_matches = [document for document in pdf_documents if matches_observed(document)]
    requested_id = observation.get("complex_id")
    expected = next(
        (document for document in documents if document["complex_id"] == requested_id),
        None,
    )

    hash_matches_expected: bool | None = None
    pages_match_expected: bool | None = None
    if expected and expected.get("source_sha256"):
        if observed_hash_selector is not None:
            hash_matches_expected = expected["source_sha256"].startswith(observed_hash_selector)
        if page_count is not None:
            pages_match_expected = expected.get("source_page_count") == page_count

    if requested_id is not None:
        if expected is None:
            status = "REQUEST_COMPLEX_NOT_IN_INVENTORY"
        elif not expected.get("source_sha256"):
            status = "REQUEST_SOURCE_UNAVAILABLE"
        elif hash_matches_expected is False or pages_match_expected is False:
            status = "REQUEST_SOURCE_MISMATCH"
        elif (
            full_hash is not None
            and hash_matches_expected is True
            and pages_match_expected is True
        ):
            status = "REQUEST_SOURCE_MATCH"
        else:
            status = "REQUEST_SOURCE_INCOMPLETE"
    elif observed_hash_selector is None and page_count is None:
        status = "OBSERVATION_INCOMPLETE"
    elif len(corpus_matches) == 1:
        status = "OWNED_SOURCE_IDENTIFIED"
    elif corpus_matches:
        status = "AMBIGUOUS_OWNED_SOURCE"
    else:
        status = "NOT_IN_OWNED_CORPUS"

    result: dict[str, Any] = {
        "observation_id": observation.get("observation_id"),
        "observed_at": observation.get("observed_at"),
        "requested_target": {
            key: observation.get(key)
            for key in (
                "complex_id",
                "unit_type_id",
                "unit_type_name",
                "sale_price_manwon",
            )
            if observation.get(key) is not None
        },
        "observed_source": {
            "source_sha256": full_hash,
            "source_sha256_prefix": prefix if full_hash is None else None,
            "source_page_count": page_count,
        },
        "status": status,
        "matched_owned_complex_ids": [document["complex_id"] for document in corpus_matches],
    }
    if expected is not None:
        result["expected_source"] = {
            "source_sha256": expected.get("source_sha256"),
            "source_page_count": expected.get("source_page_count"),
        }
        result["expected_comparison"] = {
            "source_sha256_matches": hash_matches_expected,
            "source_page_count_matches": pages_match_expected,
        }
    return result


def build_handoff(
    *,
    inventory: dict[str, Any],
    reviewed_dir: Path,
    schema_version: str,
    extractor_version: str,
    observations: list[dict[str, Any]] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    if inventory.get("schema_version") != INVENTORY_SCHEMA_VERSION:
        raise ValueError(
            f"inventory schema_version must be {INVENTORY_SCHEMA_VERSION}"
        )
    documents = [
        _require_mapping(item, label="inventory document")
        for item in _require_list(inventory.get("documents"), label="inventory.documents")
    ]
    _validate_inventory_documents(documents)
    inventory_source_checks = _verify_inventory_sources(inventory, documents)
    eligible, rejected = _load_reviewed_candidates(
        reviewed_dir,
        schema_version=schema_version,
        extractor_version=extractor_version,
    )

    backend_ready: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    source_unavailable: list[dict[str, Any]] = []
    review_conflicts: list[dict[str, Any]] = []
    matched_artifacts: set[str] = set()
    document_coverage: list[dict[str, Any]] = []

    for document in documents:
        units = [
            _require_mapping(item, label="inventory unit tuple")
            for item in _require_list(
                document.get("unit_tuples"),
                label=f"inventory document {document.get('complex_id')} unit_tuples",
            )
        ]
        reviewed_count = 0
        pending_count = 0
        blocked_count = 0
        conflict_count = 0
        for unit in units:
            target = _target_from_inventory(document, unit)
            if not target["source_sha256"] or not target["source_page_count"]:
                source_unavailable.append(target)
                blocked_count += 1
                continue
            matches = [
                (filename, candidate)
                for filename, candidate in eligible
                if _matches_target(candidate, target)
            ]
            if not matches:
                pending.append(target)
                pending_count += 1
                continue
            if len(matches) > 1:
                matched_artifacts.update(item[0] for item in matches)
                review_conflicts.append(
                    {
                        "target": target,
                        "artifact_files": sorted(item[0] for item in matches),
                        "reason": "MULTIPLE_REVIEWED_ARTIFACTS_FOR_EXACT_TARGET",
                    }
                )
                conflict_count += 1
                continue
            filename, candidate = max(
                matches,
                key=lambda item: item[1].reviewed_at or datetime.min.replace(tzinfo=UTC),
            )
            matched_artifacts.update(item[0] for item in matches)
            backend_ready.append(
                {
                    **target,
                    "normalized_unit_type_name": candidate.target_unit.unit_type_name,
                    "schema_version": candidate.meta.schema_version,
                    "extractor_version": candidate.meta.extractor_version,
                    "review_status": candidate.review_status.value,
                    "reviewed_at": candidate.reviewed_at.isoformat()
                    if candidate.reviewed_at
                    else None,
                    "artifact_file": filename,
                }
            )
            reviewed_count += 1
        document_coverage.append(
            {
                "complex_id": document["complex_id"],
                "source_sha256": document.get("source_sha256"),
                "source_page_count": document.get("source_page_count"),
                "target_tuple_count": len(units),
                "exact_reviewed_target_count": reviewed_count,
                "pending_human_review_target_count": pending_count,
                "source_unavailable_target_count": blocked_count,
                "review_conflict_target_count": conflict_count,
            }
        )

    orphaned = [
        {
            "artifact_file": filename,
            "target": _candidate_target(candidate),
            "reviewed_at": candidate.reviewed_at.isoformat() if candidate.reviewed_at else None,
        }
        for filename, candidate in eligible
        if filename not in matched_artifacts
    ]
    checked_observations = [
        _check_live_observation(item, documents=documents)
        for item in (observations or [])
    ]
    live_status_counts = Counter(item["status"] for item in checked_observations)
    generated = generated_at or datetime.now(UTC)

    return {
        "schema_version": "review_coverage_handoff_v0.1",
        "generated_at": generated.isoformat(),
        "review_contract": {
            "schema_version": schema_version,
            "extractor_version": extractor_version,
            "identity_fields": [
                "source_sha256",
                "complex_id",
                "unit_type_id",
                "normalized_unit_type_name",
                "sale_price_manwon",
            ],
            "requires_review_status": "REVIEWED",
            "requires_validation_passed": True,
        },
        "summary": {
            "document_count": len(documents),
            "pdf_backed_document_count": sum(
                1 for document in documents if document["pdf_available"]
            ),
            "source_unavailable_document_count": sum(
                1 for document in documents if not document["pdf_available"]
            ),
            "target_tuple_count": sum(item["target_tuple_count"] for item in document_coverage),
            "pdf_backed_target_tuple_count": (
                len(backend_ready) + len(pending) + len(review_conflicts)
            ),
            "exact_reviewed_target_tuple_count": len(backend_ready),
            "pending_human_review_target_tuple_count": len(pending),
            "review_conflict_target_tuple_count": len(review_conflicts),
            "source_unavailable_target_tuple_count": len(source_unavailable),
            "ineligible_reviewed_artifact_count": len(rejected),
            "orphaned_eligible_reviewed_artifact_count": len(orphaned),
            "live_source_status_counts": dict(sorted(live_status_counts.items())),
        },
        "backend_ready_targets": backend_ready,
        "pending_human_review_targets": pending,
        "source_unavailable_targets": source_unavailable,
        "conflicting_reviewed_targets": review_conflicts,
        "document_coverage": document_coverage,
        "ineligible_reviewed_artifacts": rejected,
        "orphaned_eligible_reviewed_artifacts": orphaned,
        "live_source_checks": checked_observations,
        "inventory_source_checks": inventory_source_checks,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# REVIEWED coverage backend handoff",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "This report is an allowlist, not a bulk approval. Only rows in "
        "`backend_ready_targets` may be used as reviewed coverage.",
        "",
        "## Coverage",
        "",
        f"- Documents: {summary['document_count']}",
        f"- PDF-backed target tuples: {summary['pdf_backed_target_tuple_count']}",
        f"- Exact REVIEWED target tuples: {summary['exact_reviewed_target_tuple_count']}",
        f"- Pending human review: {summary['pending_human_review_target_tuple_count']}",
        f"- Conflicting REVIEWED tuples: {summary['review_conflict_target_tuple_count']}",
        f"- Source unavailable: {summary['source_unavailable_target_tuple_count']}",
        f"- Re-hashed owned PDF sources: {len(report['inventory_source_checks'])}",
        "",
        "## Backend-ready allowlist",
        "",
        "| complex_id | unit_type_id | unit_type_name | sale_price_manwon | pages | SHA-256 |",
        "|---|---|---|---:|---:|---|",
    ]
    for target in report["backend_ready_targets"]:
        lines.append(
            "| {complex_id} | {unit_type_id} | {unit_type_name} | {sale_price_manwon} | "
            "{source_page_count} | `{source_sha256}` |".format(**target)
        )
    if not report["backend_ready_targets"]:
        lines.append("| _none_ |  |  |  |  |  |")

    lines.extend(["", "## Conflicting REVIEWED tuples (blocked)", ""])
    if report["conflicting_reviewed_targets"]:
        for item in report["conflicting_reviewed_targets"]:
            target = item["target"]
            artifacts = ", ".join(f"`{name}`" for name in item["artifact_files"])
            lines.append(
                f"- `{target['complex_id']}` / `{target['unit_type_id']}` / "
                f"`{normalize_unit_type_name(target['unit_type_name'])}` / "
                f"`{target['sale_price_manwon']}`: **{item['reason']}**; "
                f"artifacts: {artifacts}"
            )
    else:
        lines.append("- None.")

    lines.extend(["", "## Pending tuples by complex", ""])
    pending_counts = Counter(
        item["complex_id"] for item in report["pending_human_review_targets"]
    )
    for complex_id, count in sorted(pending_counts.items()):
        lines.append(f"- `{complex_id}`: {count}")

    lines.extend(["", "## Live source checks", ""])
    if report["live_source_checks"]:
        for item in report["live_source_checks"]:
            matches = ", ".join(item["matched_owned_complex_ids"]) or "none"
            lines.append(
                f"- `{item.get('observation_id') or 'unnamed'}`: "
                f"**{item['status']}**; owned corpus match: {matches}"
            )
    else:
        lines.append("- No live source observation was supplied.")

    lines.extend(
        [
            "",
            "## Required operating rule",
            "",
            "Backend must compare the fresh response `meta.source_sha256` and "
            "`meta.source_page_count` with this allowlist. A mismatch stays "
            "`AUTO_EXTRACTED`/HOLD and must never inherit REVIEWED status from the complex ID.",
            "The builder also re-hashes every local inventory PDF and recounts its physical "
            "pages; any source identity mismatch aborts handoff generation.",
            "",
        ]
    )
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    defaults = Settings.model_fields
    parser = argparse.ArgumentParser(
        description="Build a URL-free REVIEWED coverage allowlist for backend handoff."
    )
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--reviewed-dir", required=True, type=Path)
    parser.add_argument("--observations", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument(
        "--schema-version",
        default=defaults["schema_version"].default,
    )
    parser.add_argument(
        "--extractor-version",
        default=defaults["extractor_version"].default,
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    inventory = _require_mapping(_load_json(args.inventory), label="inventory")
    observations: list[dict[str, Any]] = []
    if args.observations:
        observation_payload = _require_mapping(
            _load_json(args.observations),
            label="observations",
        )
        observations = [
            _require_mapping(item, label="observation")
            for item in _require_list(
                observation_payload.get("observations"),
                label="observations.observations",
            )
        ]
    report = build_handoff(
        inventory=inventory,
        reviewed_dir=args.reviewed_dir,
        schema_version=args.schema_version,
        extractor_version=args.extractor_version,
        observations=observations,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(render_markdown(report), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
