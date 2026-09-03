#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from get_myhome_ai.owned_corpus_extraction import run_owned_corpus_extraction
from get_myhome_ai.pipeline import AnalysisPipeline
from get_myhome_ai.providers.factory import create_provider
from get_myhome_ai.settings import Settings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run resumable current-model extraction for exact owned-corpus targets."
    )
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--complex-id", action="append", dest="complex_ids")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    pipeline = AnalysisPipeline(settings=settings, provider=create_provider(settings))
    report = asyncio.run(
        run_owned_corpus_extraction(
            inventory_path=args.inventory,
            output_dir=args.output_dir,
            settings=settings,
            pipeline=pipeline,
            selected_complex_ids=set(args.complex_ids) if args.complex_ids else None,
            force=args.force,
        )
    )
    return 2 if report["failed_target_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
