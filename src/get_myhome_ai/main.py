from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from pydantic import AnyHttpUrl, TypeAdapter

from get_myhome_ai.captured_inventory import build_captured_inventory
from get_myhome_ai.evaluation import evaluate_case, summarize_evaluations
from get_myhome_ai.fixtures import load_golden_cases
from get_myhome_ai.models import AnalyzeRequest
from get_myhome_ai.pdf_text import extract_pdf_pages, load_pdf_from_path
from get_myhome_ai.pipeline import AnalysisPipeline
from get_myhome_ai.providers.factory import create_provider
from get_myhome_ai.review import (
    approve_result,
    load_result,
    save_result,
    write_review_sheet,
)
from get_myhome_ai.review_batch import (
    approve_review_batch,
    prepare_review_batch,
    validate_review_batch_approval,
)
from get_myhome_ai.settings import Settings


def _add_target_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--complex-id", required=True)
    parser.add_argument("--unit-type-id")
    parser.add_argument("--unit-type-name")
    parser.add_argument("--sale-price-manwon", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--review-sheet", type=Path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="get-myhome-ai")
    parser.add_argument(
        "--provider",
        choices=("ollama", "openai", "fixture"),
        help="환경변수 AI_PROVIDER를 이 실행에서만 덮어씁니다.",
    )
    parser.add_argument("--fixture-dir", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)

    local = commands.add_parser("analyze-file", help="로컬 PDF를 배치 분석합니다.")
    _add_target_arguments(local)
    local.add_argument("--pdf", required=True)

    remote = commands.add_parser("analyze-url", help="crawler가 준 PDF URL을 분석합니다.")
    _add_target_arguments(remote)
    remote.add_argument("--pdf-url", required=True)

    review = commands.add_parser("review", help="사람 검수 완료본을 별도로 저장합니다.")
    review.add_argument("--input", type=Path, required=True)
    review.add_argument("--output", type=Path, required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument(
        "--pdf",
        required=True,
        help="자동 추출본을 만든 정확한 원본 PDF",
    )
    review.add_argument(
        "--confirm-source-reviewed",
        action="store_true",
        help="PDF 원문을 실제로 확인했음을 명시합니다.",
    )

    prepare_batch = commands.add_parser(
        "prepare-review-batch",
        help="보유 자료의 검수 초안·체크리스트를 준비합니다.",
    )
    prepare_batch.add_argument("--inventory", type=Path, required=True)
    prepare_batch.add_argument(
        "--auto-artifact-dir",
        type=Path,
        action="append",
        required=True,
        help="자동 추출 JSON 디렉터리. 여러 번 지정할 수 있습니다.",
    )
    prepare_batch.add_argument("--reference-dir", type=Path)
    prepare_batch.add_argument("--output-dir", type=Path, required=True)

    captured_inventory = commands.add_parser(
        "build-captured-inventory",
        help="운영 review capture를 exact 검수 인벤토리로 변환합니다.",
    )
    captured_inventory.add_argument("--capture-dir", type=Path, required=True)
    captured_inventory.add_argument("--output", type=Path, required=True)

    validate_batch = commands.add_parser(
        "validate-review-approval",
        help="명시적 배치 승인을 기록하기 전에 원본·초안·매니페스트를 검증합니다.",
    )
    validate_batch.add_argument("--draft-manifest", type=Path, required=True)
    validate_batch.add_argument("--approval-manifest", type=Path, required=True)
    validate_batch.add_argument("--reviewer", required=True)
    validate_batch.add_argument(
        "--confirm-approval-manifest",
        action="store_true",
        help="매니페스트의 APPROVE 항목을 검수자가 명시적으로 확인했습니다.",
    )

    approve_batch = commands.add_parser(
        "approve-review-batch",
        help="검증된 승인 매니페스트의 항목만 REVIEWED로 저장합니다.",
    )
    approve_batch.add_argument("--draft-manifest", type=Path, required=True)
    approve_batch.add_argument("--approval-manifest", type=Path, required=True)
    approve_batch.add_argument("--output-dir", type=Path, required=True)
    approve_batch.add_argument("--reviewer", required=True)
    approve_batch.add_argument(
        "--confirm-approval-manifest",
        action="store_true",
        help="매니페스트의 APPROVE 항목을 검수자가 명시적으로 확인했습니다.",
    )

    evaluate = commands.add_parser("evaluate", help="실제 PDF 골든셋을 회귀 평가합니다.")
    evaluate.add_argument("--pdf-dir", type=Path, required=True)
    evaluate.add_argument("--output", type=Path)

    serve = commands.add_parser("serve", help="선택형 얇은 HTTP API를 실행합니다.")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=9000)
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    overrides = {}
    if args.provider:
        overrides["ai_provider"] = args.provider
    if args.fixture_dir:
        overrides["fixture_dir"] = args.fixture_dir
    return Settings(**overrides)


def _destination(args: argparse.Namespace, settings: Settings) -> Path:
    return args.output or settings.auto_artifact_dir / f"{args.complex_id}.json"


async def _analyze_file(args: argparse.Namespace, settings: Settings) -> int:
    pipeline = AnalysisPipeline(settings=settings, provider=create_provider(settings))
    result = await pipeline.analyze_file(
        complex_id=args.complex_id,
        path=args.pdf,
        unit_type_id=args.unit_type_id,
        unit_type_name=args.unit_type_name,
        sale_price_manwon=args.sale_price_manwon,
    )
    destination = _destination(args, settings)
    save_result(result, destination)
    write_review_sheet(
        result,
        args.review_sheet or destination.with_suffix(".review.md"),
    )
    print(destination)
    return 0 if result.validation.passed else 2


async def _analyze_url(args: argparse.Namespace, settings: Settings) -> int:
    pipeline = AnalysisPipeline(settings=settings, provider=create_provider(settings))
    request = AnalyzeRequest(
        complex_id=args.complex_id,
        pdf_url=TypeAdapter(AnyHttpUrl).validate_python(args.pdf_url),
        unit_type_id=args.unit_type_id,
        unit_type_name=args.unit_type_name,
        sale_price_manwon=args.sale_price_manwon,
    )
    result = await pipeline.analyze_url(request)
    destination = _destination(args, settings)
    save_result(result, destination)
    write_review_sheet(
        result,
        args.review_sheet or destination.with_suffix(".review.md"),
    )
    print(destination)
    return 0 if result.validation.passed else 2


async def _evaluate(args: argparse.Namespace, settings: Settings) -> int:
    cases = load_golden_cases(settings.fixture_dir)
    pipeline = AnalysisPipeline(settings=settings, provider=create_provider(settings))
    evaluations = []
    for case in cases.values():
        result = await pipeline.analyze_file(
            complex_id=case.complex_id,
            path=str(args.pdf_dir / case.pdf_filename),
            unit_type_name=case.unit_type_name,
            sale_price_manwon=case.sale_price_manwon,
        )
        evaluations.append(evaluate_case(result, case.expected))
    report = summarize_evaluations(evaluations, pipeline.provider.name)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(args.output)
    else:
        print(payload, end="")
    return 0 if all(item.exact_match and item.validation_passed for item in evaluations) else 2


def run() -> None:
    parser = _parser()
    args = parser.parse_args()
    settings = _settings(args)

    if args.command == "serve":
        import uvicorn

        from get_myhome_ai.api import create_app

        uvicorn.run(
            create_app(settings=settings),
            host=args.host,
            port=args.port,
        )
        return
    if args.command == "review":
        if not args.confirm_source_reviewed:
            parser.error("review에는 --confirm-source-reviewed가 필요합니다.")
        if args.output.exists():
            parser.error("기존 검수 결과를 보호하기 위해 새 --output 경로가 필요합니다.")
        downloaded = load_pdf_from_path(args.pdf, settings)
        pages = extract_pdf_pages(downloaded.content, settings)
        reviewed = approve_result(
            load_result(args.input),
            reviewer=args.reviewer,
            source_sha256=downloaded.sha256,
            pages=pages,
        )
        save_result(reviewed, args.output)
        print(args.output)
        return
    if args.command == "prepare-review-batch":
        manifest = prepare_review_batch(
            inventory_path=args.inventory,
            auto_artifact_dirs=args.auto_artifact_dir,
            reference_dir=args.reference_dir,
            output_dir=args.output_dir,
            settings=settings,
        )
        print(args.output_dir / "review-draft-manifest.json")
        print(args.output_dir / "review-approval-manifest.template.json")
        print(
            f"drafts={manifest.summary.draft_count} "
            f"version_compatible={manifest.summary.approval_eligible_draft_count} "
            f"unavailable={manifest.summary.unavailable_target_count}"
        )
        return
    if args.command == "build-captured-inventory":
        payload = build_captured_inventory(
            capture_dir=args.capture_dir,
            output_path=args.output,
        )
        print(args.output)
        print(f"targets={len(payload['targets'])}")
        return
    if args.command == "validate-review-approval":
        validated = validate_review_batch_approval(
            draft_manifest_path=args.draft_manifest,
            approval_manifest_path=args.approval_manifest,
            reviewer=args.reviewer,
            settings=settings,
            explicit_confirmation=args.confirm_approval_manifest,
        )
        print(f"validated={len(validated)}")
        return
    if args.command == "approve-review-batch":
        receipt = approve_review_batch(
            draft_manifest_path=args.draft_manifest,
            approval_manifest_path=args.approval_manifest,
            output_dir=args.output_dir,
            reviewer=args.reviewer,
            settings=settings,
            explicit_confirmation=args.confirm_approval_manifest,
        )
        print(args.output_dir / "review-approval-receipt.json")
        print(f"approved={receipt.approved_count}")
        return
    if args.command == "analyze-file":
        raise SystemExit(asyncio.run(_analyze_file(args, settings)))
    if args.command == "analyze-url":
        raise SystemExit(asyncio.run(_analyze_url(args, settings)))
    if args.command == "evaluate":
        raise SystemExit(asyncio.run(_evaluate(args, settings)))
    parser.error(f"지원하지 않는 명령입니다: {args.command}")


if __name__ == "__main__":
    run()
