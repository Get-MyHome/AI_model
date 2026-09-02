# Project memory

## Current state

- Batch-first PDF extraction and the thin authenticated FastAPI endpoint are implemented.
- The local Ollama runtime uses `qwen3.5:9b`. Ollama remains loopback-only; the API is exposed through
  Tailscale Funnel at `https://server.tailb23d4f.ts.net:10000/api/analyze`.
- Runtime units and the mode-600 secret environment file live under ignored `.local/runtime/` paths.
  Never commit or print the API key, pre-signed URLs, or full PDF contents.
- Canonical JSON stays additive schema `v0.3`; extractor version is `0.2.0`.
- App version `0.3.0` adds authenticated `POST /api/funding-stress` as an independent advisory.
  It consumes only an exact extractor-0.2 `REVIEWED` artifact, a `PRE_CONTRACT` cash snapshot, and
  backend-produced alternative loan-route limits. It never replaces or mutates backend verdict logic.
- Funding stress deterministically returns the interim continuity threshold, document-cap margin,
  route-specific stress cases, and first shortfall. Unknown required-cost allocation, sale-price
  inclusion, or unit applicability produces `UNKNOWN` plus a blocking HOLD, never a zero assumption.
- Threshold lookup uses bounded integer-bps binary search and the API runs CPU calculation outside the
  async event loop. Route rule/assumption provenance is preserved in every response case.
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
- Full AI verification on 2026-09-02: Ruff passed, compileall passed, 130 pytest tests passed, and
  `python scripts/evaluate_risk_settlement.py` passed all 27 source hashes, 189/189 deterministic
  labels, 89/89 risk quotes, and 51/51 settlement quotes.
- Review approval ignores editable `derived_fields`, re-grounds deterministic metadata against the
  exact PDF, and regenerates validation, HOLDs, status, and summary before setting `REVIEWED`.
- Backend is a strict no-edit boundary. It remains the production funding/verdict SSOT; AI funding
  stress is additive advisory output only.
- The AI_model deploy key is active and pushes to the Get-MyHome organization repository succeed.
- Production `PDF_ALLOWED_HOSTS` contains both exact crawler bucket forms: the regional
  `getmyhome-pdfs-758862546581.s3.ap-northeast-2.amazonaws.com` host and the global
  `getmyhome-pdfs-758862546581.s3.amazonaws.com` host. Never replace these with a broad S3 wildcard.

## Handoff artifacts

- AI patches: `/mnt/20t/AI_해커톤/0001-feat-extract-settlement-and-funding-risk-clauses.patch`
  and `/mnt/20t/AI_해커톤/0002-docs-correct-settlement-principal-handoff.patch`
- Complete AI bundle: `/mnt/20t/AI_해커톤/get-myhome-ai-complete.bundle`
- Do not create or distribute backend patches from this project. Backend is read-only.

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

- Backend must retry crawler → backend → AI with a fresh pre-signed URL after the global S3 host
  allowlist update. An authenticated probe confirmed that host validation now reaches PDF download.
- A human must approve extractor-0.2 review sheets before backend production use.
- Real bank loan-guide PDF pairs are required before enabling document-change detection publicly.
