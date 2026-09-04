from __future__ import annotations

import argparse
from pathlib import Path

from get_myhome_ai.legacy_review_refresh import prepare_legacy_review_refresh
from get_myhome_ai.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "감사된 구 11건의 사실 필드를 현재 extractor review workspace에 "
            "AUTO_EXTRACTED/PENDING으로 재결합합니다."
        )
    )
    parser.add_argument("--draft-manifest", type=Path, required=True)
    parser.add_argument("--legacy-workspace", type=Path, required=True)
    parser.add_argument("--historical-reviewed-artifact", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    payload = prepare_legacy_review_refresh(
        draft_manifest_path=args.draft_manifest,
        legacy_workspace_dir=args.legacy_workspace,
        historical_reviewed_artifact=args.historical_reviewed_artifact,
        output_dir=args.output_dir,
        settings=Settings(),
    )
    print(args.output_dir / "review-draft-manifest.json")
    print(args.output_dir / "review-approval-manifest.template.json")
    print(args.output_dir / "legacy-review-refresh-manifest.json")
    print(
        f"workspace_drafts={payload['workspace_draft_count']} "
        f"refreshed_candidates={payload['refreshed_candidate_count']} "
        "approval_state=PENDING"
    )


if __name__ == "__main__":
    main()
