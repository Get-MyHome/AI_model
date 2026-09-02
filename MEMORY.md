# Project memory

## Current state

- Batch-first PDF extraction and the thin authenticated FastAPI endpoint are implemented.
- The local Ollama runtime uses `qwen3.5:9b`. Ollama remains loopback-only; the API is exposed through
  Tailscale Funnel at `https://server.tailb23d4f.ts.net:10000/api/analyze`.
- Runtime units and the mode-600 secret environment file live under ignored `.local/runtime/` paths.
  Never commit or print the API key, pre-signed URLs, or full PDF contents.
- Canonical JSON stays additive schema `v0.3`; extractor version is `0.2.0`.
- Extractor 0.2 adds evidence-grounded interim-loan settlement requirements and deterministic funding
  risk clauses. Unknown settlement terms produce `BALANCE_CONVERSION_UNCERTAIN`; missing facts are
  never converted to zero.
- LLMs extract candidate facts only. Settlement/risk classification, HOLDs, summaries, validation,
  derived self-funding, and cross-document comparison are deterministic.
- Announcement-versus-bank-guidance comparison has a tested internal core but no public endpoint. It
  must remain labeled `NOT_VALIDATED_ON_BANK_GUIDANCE` until real bank-guide pairs are manually labeled.
- A public authenticated end-to-end request against the real 2026000372 ApplyHome PDF completed on
  extractor 0.2/Qwen3.5. It returned 10% contract, 60% interim, 40% arranged, 20% uncovered,
  `REPAY_OR_CONVERT_TO_MORTGAGE`, four risk clauses, and `validation.passed=true`.
- The final independent Qwen3.5 source-locked run covers 24 reference PDFs: 260/260 compared core
  labels, 24/24 exact documents, 24/24 field safety, and 4/4 safe abstentions. This excludes unit-level
  amounts/additional-costs/banks/guarantors and does not measure semantic evidence accuracy.
- Separate page-by-page labels for settlement and six deterministic risk classes cover all 27 PDFs:
  27/27 document exact matches, 189/189 labels, and 89/89 literal evidence-page quote checks. This is
  deterministic post-processing accuracy, not Qwen accuracy. The corpus has zero positive
  `TERMS_DIFFER_BY_HOUSING_TYPE` examples.
- Full AI verification at commit `50f556b`: Ruff passed, compileall passed, 110 pytest tests passed, and
  `python scripts/evaluate_risk_settlement.py` exited 0.
- The backend integration worktree branch `codex/ai-v03-integration` now ingests extractor 0.2
  settlement/risk fields and includes arranged interim-loan principal at balance when repayment or
  refinancing is required. Unknown settlement is conservatively carried and blocking-HOLDed.
- Backend commit `56a3db9` passed `./gradlew clean build` with JDK 17: 121 tests, 0 failures/errors,
  one intentionally skipped opt-in live E2E test.
- Both organization pushes are blocked by GitHub HTTP 403 for the current account.

## Handoff artifacts

- AI patches: `/mnt/20t/AI_해커톤/0001-feat-extract-settlement-and-funding-risk-clauses.patch`
  and `/mnt/20t/AI_해커톤/0002-docs-correct-settlement-principal-handoff.patch`
- Complete AI bundle: `/mnt/20t/AI_해커톤/get-myhome-ai-complete.bundle`
- Backend patch: `/mnt/20t/AI_해커톤/backend-ai-v03-integration.patch`
- Backend bundle: `/mnt/20t/AI_해커톤/backend-ai-v03-integration-56a3db9.bundle`

## Claims and boundaries

- Do not claim “27-PDF full-field Qwen accuracy 100%” or “semantic evidence accuracy 100%.”
- Do not call the project-arranged ratio a personal approved-loan ratio.
- Do not multiply `arranged_ratio=0.40` by interim ratio `0.60`; both are sale-price-relative.
- For 2026000372, full 60% interim lending still creates a 67,785-manwon balance shortfall because the
  65,190-manwon principal must be repaid/refinanced. The actual 40% case first breaks at interim by
  21,730 and later has a 46,055 balance gap. Do not compare different-stage gaps as a simple delta.
- Only `REVIEWED` artifacts with exact PDF/complex/unit/price/extractor-version keys may drive user
  funding calculations. Current extractor-0.2 public responses remain `AUTO_EXTRACTED` until a human
  checks the generated review sheet against the PDF.

## Remaining external/human steps

- Obtain one fresh crawler S3 URL, add only its exact bucket hostname to `PDF_ALLOWED_HOSTS`, and run
  crawler → backend → AI. The public API itself and a real ApplyHome URL are already verified.
- A repository owner must import/apply the handoff artifacts or grant write access; current pushes fail
  with 403.
- A human must approve extractor-0.2 review sheets before backend production use.
- Real bank loan-guide PDF pairs are required before enabling document-change detection publicly.
