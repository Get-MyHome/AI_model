from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from pydantic import AnyHttpUrl, TypeAdapter

from get_myhome_ai.evaluation import evaluate_case, summarize_evaluations
from get_myhome_ai.fixtures import load_golden_cases
from get_myhome_ai.models import AnalyzeRequest
from get_myhome_ai.pipeline import AnalysisPipeline
from get_myhome_ai.providers.factory import create_provider
from get_myhome_ai.review import (
    approve_result,
    load_result,
    save_result,
    write_review_sheet,
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
        "--confirm-source-reviewed",
        action="store_true",
        help="PDF 원문을 실제로 확인했음을 명시합니다.",
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
        reviewed = approve_result(load_result(args.input), reviewer=args.reviewer)
        save_result(reviewed, args.output)
        print(args.output)
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
